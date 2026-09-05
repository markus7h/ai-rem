"""Migrations-Test gegen ein Alt-Schema-Fixture (Issue #28, Tier 3).

Läuft in einem SUBPROZESS: server initialisiert die DB beim Import (init_schema()),
und pytest cached `import server` über alle Testmodule hinweg — d.h. der erste
Importeur gewinnt. Um den echten Startup-Migrationspfad gegen ein selbstgebautes
Alt-Schema zu testen (Fixture VOR dem Import), braucht es einen frischen Prozess.

Das Szenario baut eine Kuzu-DB im ur-alten 7-Spalten-Zustand (Entity ohne
context/pinned/sort_order/archived/embedding) mit einer Legacy-Entity, deren Context
nur im extra-JSON steckt, importiert dann server (→ Migration) und prüft Spalten,
context-Backfill, Pre-Migration-Backup und Idempotenz eines zweiten init_schema().
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_NEW_COLUMNS = ["context", "pinned", "sort_order", "archived", "embedding"]


def _scenario() -> None:
    tmp = tempfile.mkdtemp(prefix="ai-rem-migr-")
    db_path = os.path.join(tmp, "kg.db")
    backup_dir = os.path.join(tmp, "backups")
    os.environ["LADYBUG_DB_PATH"] = db_path
    os.environ["BACKUP_DIR"] = backup_dir
    os.environ["EMBED_ENABLED"] = "0"
    os.environ.setdefault("AI_REM_API_TOKEN", "test-token")
    sys.path.insert(0, ROOT)

    # Alt-Schema-Fixture bauen, BEVOR server importiert wird.
    import ladybug
    fdb = ladybug.Database(db_path)
    fconn = ladybug.Connection(fdb)
    fconn.execute(
        """CREATE NODE TABLE Entity(
               id STRING PRIMARY KEY, name STRING, type STRING, descr STRING,
               extra STRING, created_at STRING, updated_at STRING
           )"""
    )
    fconn.execute(
        "CREATE REL TABLE Rel(FROM Entity TO Entity, name STRING, extra STRING, created_at STRING)"
    )
    fconn.execute(
        """CREATE (:Entity {id:'legacy', name:'Legacy', type:'Topic', descr:'alt',
                            extra:'{"context":"work"}',
                            created_at:'2025-01-01T00:00:00', updated_at:'2025-01-01T00:00:00'})"""
    )
    fconn.close()
    fdb.close()

    import server  # Import triggert init_schema() → Migration der Alt-DB.

    # 1) Alle neuen Spalten vorhanden.
    for col in _NEW_COLUMNS:
        assert server._entity_has_column(col), f"Spalte {col} fehlt nach Migration"

    # 2) context aus extra backfilled.
    rows = server._rows(server.db_exec("MATCH (e:Entity {id:'legacy'}) RETURN e.context"))
    assert rows and rows[0][0] == "work", f"context nicht backfilled: {rows}"

    # 3) Pre-Migration-Backup geschrieben + gültiges v1-JSON mit der Legacy-Entity.
    files = os.listdir(backup_dir)
    pre = [f for f in files if f.startswith("backup_pre_context_")]
    assert pre, f"kein Pre-Migration-Backup in {files}"
    raw = open(os.path.join(backup_dir, pre[0]), "rb").read()
    if pre[0].endswith(".enc"):
        raw = server._decrypt_backup(raw, server._backup_key())
    data = json.loads(raw)
    assert data["version"] == 1
    assert any(e["id"] == "legacy" for e in data["entities"])

    # 4) Idempotenz: zweiter init_schema()-Lauf wirft nicht, lässt Spalten + Daten unverändert.
    server.init_schema()
    for col in _NEW_COLUMNS:
        assert server._entity_has_column(col)
    rows = server._rows(server.db_exec("MATCH (e:Entity {id:'legacy'}) RETURN e.context, e.descr"))
    assert rows[0] == ["work", "alt"], f"Daten nach 2. init_schema verändert: {rows}"

    print("OK")


def test_migration_against_old_schema():
    r = subprocess.run(
        [sys.executable, __file__],
        capture_output=True, text=True,
        env={**os.environ, "EMBED_ENABLED": "0", "AI_REM_API_TOKEN": "test-token"},
    )
    assert r.returncode == 0, f"Szenario fehlgeschlagen:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    assert "OK" in r.stdout


if __name__ == "__main__":
    _scenario()
