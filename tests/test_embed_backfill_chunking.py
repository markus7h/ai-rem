"""Backfill laeuft in Chunks und checkpointet je Chunk.

Der fruehere Einzelcall ueber alle Entities war im Normalbetrieb unauffaellig (es
sind nie viele Vektoren offen), riss aber nach einem Restore den Container: 500+
Texte plus Modell ueber dem mem_limit, und mit groesserem Limit dann der
Kuzu-Buffer-Pool ("buffer pool is full") nach ~290 Writes, weil Kuzu die
Dirty-Pages bis zum Checkpoint haelt. Beides — Chunking UND Checkpoint je Chunk —
ist noetig; darum prueft der Test beide Aufrufmuster, nicht nur das Ergebnis.

Laeuft im SUBPROZESS mit eigener Temp-DB: der Test stubbt Modul-Globals
(EMBED_ENABLED, _embed_texts, _checkpoint_wal), das darf die anderen Testmodule
nicht treffen, die sich ueber den pytest-Modulcache eine server-Instanz teilen.
Der Stub ersetzt das Embedding-Modell, damit hier kein Modell-Download haengt.
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ENTITIES = 20
CHUNK = 8


def _scenario() -> None:
    tmp = tempfile.mkdtemp(prefix="ai-rem-backfill-")
    os.environ["KUZU_DB_PATH"] = os.path.join(tmp, "kg.db")
    os.environ["BACKUP_DIR"] = os.path.join(tmp, "backups")
    os.environ["EMBED_ENABLED"] = "0"      # Aufbau ohne Vektoren + ohne Modell
    os.environ["EMBED_BACKFILL_CHUNK"] = str(CHUNK)
    os.environ.setdefault("AI_REM_API_TOKEN", "test-token")
    sys.path.insert(0, ROOT)

    import server

    for i in range(ENTITIES):
        server.memory_add(f"Ent{i:02d}", "Task", description=f"Beschreibung {i}")

    open_before = server._rows(server.db_exec(
        "MATCH (e:Entity) WHERE (e.embedding IS NULL OR e.embedding = '') "
        "RETURN count(e)"))[0][0]
    assert int(open_before) == ENTITIES, f"Aufbau unerwartet: {open_before}"

    batches, checkpoints = [], []
    server.EMBED_ENABLED = True
    server._embed_texts = lambda texts, prefix: (batches.append(len(texts))
                                                 or [[0.1, 0.2, 0.3] for _ in texts])
    server._checkpoint_wal = lambda force=False: (checkpoints.append(force), True)[1]

    server._embed_backfill()

    assert batches == [CHUNK, CHUNK, ENTITIES - 2 * CHUNK], f"nicht gechunkt: {batches}"
    # len(batches) Chunk-Checkpoints + 1 finaler nach der Schleife (der letzte Chunk
    # hat keinen Nachfolger, der einen Fehlschlag mitmergen wuerde).
    assert checkpoints == [True] * (len(batches) + 1), f"Checkpoint je Chunk fehlt: {checkpoints}"

    filled = server._rows(server.db_exec(
        "MATCH (e:Entity) WHERE e.embedding <> '' RETURN count(e)"))[0][0]
    assert int(filled) == ENTITIES, f"Vektoren fehlen: {filled}"

    # Idempotent: zweiter Lauf findet nichts mehr nachzuholen. Der eine Aufruf ueber
    # einen Text ist die Dimensions-Probe aus _embed_reset_on_dim_change (prueft, ob
    # das Embedding-Backend gewechselt hat) — sie rechnet keine Entity neu.
    batches.clear()
    server._embed_backfill()
    assert batches == [1], f"zweiter Lauf embeddet erneut: {batches}"

    print("OK")


def _scenario_checkpoint_faellt_aus() -> None:
    """Scheitert ein Checkpoint, darf der Lauf NICHT "fertig" melden.

    Vorher schluckte _checkpoint_wal jeden Fehler als WARNING und der Backfill
    meldete Erfolg — die Vektoren hingen aber im vollen Buffer-Pool und waren nach
    dem naechsten Start weg. Genau so lief es am 29.08. durch.
    """
    import logging

    tmp = tempfile.mkdtemp(prefix="ai-rem-backfill-fail-")
    os.environ["KUZU_DB_PATH"] = os.path.join(tmp, "kg.db")
    os.environ["BACKUP_DIR"] = os.path.join(tmp, "backups")
    os.environ["EMBED_ENABLED"] = "0"
    os.environ["EMBED_BACKFILL_CHUNK"] = str(CHUNK)
    os.environ.setdefault("AI_REM_API_TOKEN", "test-token")
    sys.path.insert(0, ROOT)

    import server

    for i in range(ENTITIES):
        server.memory_add(f"Ent{i:02d}", "Task", description=f"Beschreibung {i}")

    meldungen = []

    class _Fang(logging.Handler):
        def emit(self, record):
            meldungen.append((record.levelname, record.getMessage()))

    server.log.addHandler(_Fang())

    rufe = []
    server.EMBED_ENABLED = True
    server._embed_texts = lambda texts, prefix: [[0.1, 0.2, 0.3] for _ in texts]
    # Zweiter Checkpoint scheitert, alle anderen gehen durch.
    server._checkpoint_wal = lambda force=False: (rufe.append(force), len(rufe) != 2)[1]

    server._embed_backfill()

    fertig = [m for lvl, m in meldungen if "Backfill fertig" in m]
    fehler = [m for lvl, m in meldungen if lvl == "ERROR" and "Checkpoint schlug fehl" in m]
    assert not fertig, f"meldet faelschlich Erfolg: {fertig}"
    assert fehler, f"kein ERROR zum Checkpoint-Fehlschlag: {meldungen}"

    # Geschrieben wird trotzdem alles — der Lauf bricht bewusst nicht ab, die
    # bereits gemergten Chunks sind gueltig und der naechste Lauf ist idempotent.
    filled = server._rows(server.db_exec(
        "MATCH (e:Entity) WHERE e.embedding <> '' RETURN count(e)"))[0][0]
    assert int(filled) == ENTITIES, f"Vektoren fehlen: {filled}"

    print("OK")


def _lauf(szenario):
    r = subprocess.run(
        [sys.executable, __file__, szenario],
        capture_output=True, text=True,
        env={**os.environ, "AI_REM_API_TOKEN": "test-token"},
    )
    assert r.returncode == 0, f"Szenario fehlgeschlagen:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    assert "OK" in r.stdout


def test_embed_backfill_chunks_and_checkpoints():
    _lauf("chunks")


def test_backfill_meldet_keinen_erfolg_bei_checkpoint_fehler():
    _lauf("checkpoint_faellt_aus")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "checkpoint_faellt_aus":
        _scenario_checkpoint_faellt_aus()
    else:
        _scenario()
