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
    server._checkpoint_wal = lambda force=False: checkpoints.append(force)

    server._embed_backfill()

    assert batches == [CHUNK, CHUNK, ENTITIES - 2 * CHUNK], f"nicht gechunkt: {batches}"
    assert checkpoints == [True] * len(batches), f"Checkpoint je Chunk fehlt: {checkpoints}"

    filled = server._rows(server.db_exec(
        "MATCH (e:Entity) WHERE e.embedding <> '' RETURN count(e)"))[0][0]
    assert int(filled) == ENTITIES, f"Vektoren fehlen: {filled}"

    # Idempotent: zweiter Lauf findet nichts mehr und embeddet nicht erneut.
    batches.clear()
    server._embed_backfill()
    assert batches == [], f"zweiter Lauf embeddet erneut: {batches}"

    print("OK")


def test_embed_backfill_chunks_and_checkpoints():
    r = subprocess.run(
        [sys.executable, __file__],
        capture_output=True, text=True,
        env={**os.environ, "AI_REM_API_TOKEN": "test-token"},
    )
    assert r.returncode == 0, f"Szenario fehlgeschlagen:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    assert "OK" in r.stdout


if __name__ == "__main__":
    _scenario()
