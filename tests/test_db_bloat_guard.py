"""Kuzu-Bloat: Backfill-Guard und Kompaktierung beim Start.

Kuzu gibt beim Ueberschreiben von Properties keinen Speicher zurueck — jeder
Checkpoint schreibt die Column neu und laesst die alte Version liegen. Am
03.09.2026 segfaultete der Container im 60s-Checkpoint, `restart: unless-stopped`
startete ihn 264x neu, und jeder Start schrieb per Backfill alle 1291 Vektoren
erneut: kg.db wuchs von ~680 MB auf 27 GB und fuellte die Partition.

Zwei Mechanismen halten das ein, beide hier geprueft: der Backfill schreibt ab
KG_MAX_MB gar nicht mehr, und _rebuild_db kompaktiert ueber Dump + Import (das
Aequivalent zum fehlenden VACUUM). Der Rebuild bindet dabei `db` und `_pool` neu
— dass die DB danach weiter benutzbar ist, ist der eigentliche Knackpunkt.

Laeuft im SUBPROZESS mit eigener Temp-DB: die Szenarien stubben Modul-Globals,
das darf die anderen Testmodule nicht treffen (geteilter pytest-Modulcache).
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ENTITIES = 6


def _server(prefix: str):
    tmp = tempfile.mkdtemp(prefix=prefix)
    os.environ["KUZU_DB_PATH"] = os.path.join(tmp, "kg.db")
    os.environ["BACKUP_DIR"] = os.path.join(tmp, "backups")
    os.environ["EMBED_ENABLED"] = "0"
    os.environ.setdefault("AI_REM_API_TOKEN", "test-token")
    sys.path.insert(0, ROOT)
    import server
    return server


def _scenario_guard() -> None:
    """Ueber KG_MAX_MB schreibt der Backfill nichts mehr — auch nicht teilweise."""
    import logging

    server = _server("ai-rem-guard-")
    for i in range(ENTITIES):
        server.memory_add(f"Ent{i:02d}", "Task", description=f"Beschreibung {i}")

    meldungen = []

    class _Fang(logging.Handler):
        def emit(self, record):
            meldungen.append((record.levelname, record.getMessage()))

    server.log.addHandler(_Fang())

    rufe = []
    server.EMBED_ENABLED = True
    server._embed_texts = lambda texts, prefix: (rufe.append(len(texts))
                                                 or [[0.1, 0.2, 0.3] for _ in texts])
    server._db_size_mb = lambda: server.KG_MAX_MB + 1

    server._embed_backfill()

    assert rufe == [], f"Backfill schreibt trotz voller DB: {rufe}"
    assert [m for lvl, m in meldungen
            if lvl == "ERROR" and "uebersprungen" in m.replace("ü", "ue")], \
        f"kein ERROR zum uebersprungenen Backfill: {meldungen}"

    # Zweiter Riegel: DB klein, aber die Platte fast voll.
    server._db_size_mb = lambda: 1.0
    server._free_mb = lambda: server.KG_MIN_FREE_MB - 1
    server._embed_backfill()
    assert rufe == [], f"Backfill schreibt trotz voller Platte: {rufe}"

    # Und unter beiden Schwellen laeuft er normal weiter — der Guard darf den
    # Normalbetrieb nicht abwuergen.
    server._free_mb = lambda: server.KG_MIN_FREE_MB + 1
    server._embed_backfill()
    assert sum(rufe) >= ENTITIES, f"Guard blockiert den Normalfall: {rufe}"

    print("OK")


def _scenario_rebuild() -> None:
    """Rebuild haelt Entities + Relationen und laesst die DB benutzbar zurueck."""
    server = _server("ai-rem-rebuild-")
    for i in range(ENTITIES):
        server.memory_add(f"Ent{i:02d}", "Task", description=f"Beschreibung {i}")
    for i in range(ENTITIES - 1):
        server.memory_relate(f"Ent{i:02d}", "haengt_an", f"Ent{i + 1:02d}")

    vorher = server._dump_graph()
    assert len(vorher["entities"]) == ENTITIES
    assert len(vorher["relations"]) == ENTITIES - 1

    server._rebuild_db()

    # Neu gebundener Pool: eine Query nach dem Rebuild muss durchgehen, sonst ist
    # der Server nach der Kompaktierung tot.
    nachher = server._dump_graph()
    assert len(nachher["entities"]) == ENTITIES, f"Entities verloren: {nachher['entities']}"
    assert len(nachher["relations"]) == ENTITIES - 1, \
        f"Relationen verloren: {nachher['relations']}"
    assert {e["name"] for e in nachher["entities"]} == {e["name"] for e in vorher["entities"]}

    # Schreiben geht nach dem Rebuild ebenfalls weiter.
    server.memory_add("NachRebuild", "Task", description="danach angelegt")
    assert len(server._dump_graph()["entities"]) == ENTITIES + 1

    # Die Sicherung liegt VOR dem Loeschen im BACKUP_DIR — ohne sie waere ein
    # fehlgeschlagener Import ein Totalverlust.
    assert server._list_backup_files(), "Rebuild hat kein Backup hinterlassen"

    print("OK")


def _lauf(szenario):
    r = subprocess.run(
        [sys.executable, __file__, szenario],
        capture_output=True, text=True,
        env={**os.environ, "AI_REM_API_TOKEN": "test-token"},
    )
    assert r.returncode == 0, f"Szenario fehlgeschlagen:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    assert "OK" in r.stdout


def test_backfill_guard_bei_voller_db_oder_platte():
    _lauf("guard")


def test_rebuild_erhaelt_graph_und_laesst_db_benutzbar():
    _lauf("rebuild")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "rebuild":
        _scenario_rebuild()
    else:
        _scenario_guard()
