"""Backup-Restore-Drill (Issue #28, Tier 3).

Ein geschriebenes Backup ist nur dann ein Backup, wenn es verlustfrei zurückspielbar
ist. Läuft im SUBPROZESS mit eigener Temp-DB — der Restore wiped die DB
(_apply_import mode=replace → DETACH DELETE), das darf die anderen Testmodule, die
sich über den pytest-Modulcache eine server-Instanz teilen, nicht treffen.

ponytail: Roundtrip im selben Prozess (dump→wipe→reimport) IST der Drill; ein separater
Wegwerf-DB-Prozess fügt nichts hinzu. Stille Prod-Disk-Korruption deckt das bewusst nicht
ab — dafür gäbe es die Voll-Variante (Restore-Daemon), erst auf Ansage.
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _scenario() -> None:
    tmp = tempfile.mkdtemp(prefix="ai-rem-restore-")
    os.environ["KUZU_DB_PATH"] = os.path.join(tmp, "kg.db")
    os.environ["BACKUP_DIR"] = os.path.join(tmp, "backups")
    os.environ["EMBED_ENABLED"] = "0"
    os.environ.setdefault("AI_REM_API_TOKEN", "test-token")
    sys.path.insert(0, ROOT)

    import server

    def counts():
        e = server._rows(server.db_exec("MATCH (e:Entity) RETURN count(e)"))[0][0]
        r = server._rows(server.db_exec("MATCH ()-[r:Rel]->() RETURN count(r)"))[0][0]
        return int(e), int(r)

    server.memory_add("Alpha", "Project", description="Projekt A", context="work")
    server.memory_add("Beta", "Task", description="Aufgabe B", pinned=True)
    server.memory_add("Gamma", "Tool", description="Werkzeug C", extra={"k": "v"})
    server.memory_relate("Alpha", "HAT_TASK", "Beta")
    server.memory_relate("Alpha", "NUTZT", "Gamma")

    before = counts()
    assert before == (3, 2), f"Aufbau unerwartet: {before}"

    fn = server._do_backup()
    path = os.path.join(server.BACKUP_DIR, fn)
    assert os.path.exists(path)

    raw = open(path, "rb").read()
    if fn.endswith(".enc"):
        raw = server._decrypt_backup(raw, server._backup_key())
    body = json.loads(raw)

    result = server._apply_import(body, mode="replace")  # wipe + verlustfreier Restore

    after = counts()
    assert after == before, f"Counts driften: vorher {before}, nachher {after}"
    assert result["entities_created"] == 3 and result["relations_created"] == 2

    # v2-Felder überleben den Roundtrip (Restore war früher lossy).
    row = server._rows(server.db_exec("MATCH (e:Entity {id:'beta'}) RETURN e.descr, e.pinned"))
    assert row[0] == ["Aufgabe B", "true"], f"pinned/descr verloren: {row}"
    ctx = server._rows(server.db_exec("MATCH (e:Entity {id:'alpha'}) RETURN e.context"))
    assert ctx[0][0] == "work", f"context verloren: {ctx}"

    print("OK")


def test_backup_restore_roundtrip_preserves_graph():
    r = subprocess.run(
        [sys.executable, __file__],
        capture_output=True, text=True,
        env={**os.environ, "EMBED_ENABLED": "0", "AI_REM_API_TOKEN": "test-token"},
    )
    assert r.returncode == 0, f"Szenario fehlgeschlagen:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    assert "OK" in r.stdout


if __name__ == "__main__":
    _scenario()
