"""Backfill schreibt in Chunks und persistiert eine Portion je Kuzu-Session.

Der fruehere Einzelcall ueber alle Entities war im Normalbetrieb unauffaellig (es
sind nie viele Vektoren offen), riss aber nach einem Restore den Container: 500+
Texte plus Modell ueber dem mem_limit, und mit groesserem Limit dann der
Kuzu-Buffer-Pool ("buffer pool is full") nach ~290 Writes, weil Kuzu die
Dirty-Pages bis zum Checkpoint haelt.

Der Checkpoint je Chunk, der daraus folgte, war aber die naechste Falle: Kuzu
0.11.3 verliert Property-Writes, sobald mehrere Checkpoints in DERSELBEN Session
aufeinander folgen. Auf einer frischen DB gemessen (1342 Vektoren, 1024 dim):
Checkpoint je 32er-Chunk → 0 ueberlebten und die Datei wuchs von 3 MB auf 771 MB;
300er-Portionen in einer Session → beim vierten Checkpoint waren alle 1200
vorherigen weg; 300er-Portionen mit _reopen_db() dazwischen → alle 1342 blieben,
bei 151 MB. Darum pruefen die Szenarien hier drei Dinge: dass gechunkt wird
(Speicher), dass je Session genau einmal persistiert wird (Kuzu-Bug), und dass
ein Verlust auffaellt statt still zu passieren.

Laeuft im SUBPROZESS mit eigener Temp-DB: die Szenarien stubben Modul-Globals
(EMBED_ENABLED, _embed_texts, _checkpoint_wal), das darf die anderen Testmodule
nicht treffen, die sich ueber den pytest-Modulcache eine server-Instanz teilen.
Der Stub ersetzt das Embedding-Modell, damit hier kein Modell-Download haengt.
"""
import logging
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ENTITIES = 20
CHUNK = 8
PORTION = 16


def _server(prefix: str):
    tmp = tempfile.mkdtemp(prefix=prefix)
    os.environ["KUZU_DB_PATH"] = os.path.join(tmp, "kg.db")
    os.environ["BACKUP_DIR"] = os.path.join(tmp, "backups")
    os.environ["EMBED_ENABLED"] = "0"      # Aufbau ohne Vektoren + ohne Modell
    os.environ["EMBED_BACKFILL_CHUNK"] = str(CHUNK)
    os.environ["EMBED_BACKFILL_PORTION"] = str(PORTION)
    os.environ.setdefault("AI_REM_API_TOKEN", "test-token")
    sys.path.insert(0, ROOT)

    import server

    for i in range(ENTITIES):
        server.memory_add(f"Ent{i:02d}", "Task", description=f"Beschreibung {i}")
    return server


def _fang(server) -> list:
    meldungen = []

    class _H(logging.Handler):
        def emit(self, record):
            meldungen.append((record.levelname, record.getMessage()))

    server.log.addHandler(_H())
    return meldungen


def _befuellt(server) -> int:
    return int(server._rows(server.db_exec(
        "MATCH (e:Entity) WHERE e.embedding <> '' RETURN count(e)"))[0][0])


def _scenario() -> None:
    server = _server("ai-rem-backfill-")

    offen = server._rows(server.db_exec(
        "MATCH (e:Entity) WHERE (e.embedding IS NULL OR e.embedding = '') "
        "RETURN count(e)"))[0][0]
    assert int(offen) == ENTITIES, f"Aufbau unerwartet: {offen}"

    batches, checkpoints = [], []
    server.EMBED_ENABLED = True
    server._embed_texts = lambda texts, prefix: (batches.append(len(texts))
                                                 or [[0.1, 0.2, 0.3] for _ in texts])
    server._checkpoint_wal = lambda force=False: (checkpoints.append(force), True)[1]

    server._embed_backfill()

    # Im laufenden Betrieb bleibt es bei EINER Portion (zwei volle Chunks) mit genau
    # einem Checkpoint — mehrere Checkpoints je Session waeren der Kuzu-Bug.
    assert batches == [CHUNK, CHUNK], f"nicht auf eine Portion begrenzt: {batches}"
    assert checkpoints == [True], f"nicht genau ein Checkpoint: {checkpoints}"
    assert _befuellt(server) == PORTION, f"Portion unvollstaendig: {_befuellt(server)}"

    # Der naechste Lauf macht am Rest weiter, statt alles neu zu rechnen.
    batches.clear()
    server._embed_backfill()
    assert batches == [1, ENTITIES - PORTION], f"macht nicht am Rest weiter: {batches}"
    assert _befuellt(server) == ENTITIES, f"Vektoren fehlen: {_befuellt(server)}"

    # Idempotent: jetzt ist nichts mehr offen. Der eine Aufruf ueber einen Text ist
    # die Dimensions-Probe aus _embed_reset_on_dim_change (prueft, ob das
    # Embedding-Backend gewechselt hat) — sie rechnet keine Entity neu.
    batches.clear()
    server._embed_backfill()
    assert batches == [1], f"dritter Lauf embeddet erneut: {batches}"

    print("OK")


def _scenario_alle() -> None:
    """alle=True (Startup) arbeitet alle Portionen ab — je Portion eine frische
    Kuzu-Session statt eines weiteren Checkpoints in derselben."""
    server = _server("ai-rem-backfill-alle-")

    batches, reopens, checkpoints = [], [], []
    server.EMBED_ENABLED = True
    server._embed_texts = lambda texts, prefix: (batches.append(len(texts))
                                                 or [[0.1, 0.2, 0.3] for _ in texts])
    orig_reopen = server._reopen_db
    server._reopen_db = lambda: (reopens.append(1), orig_reopen())[1]
    orig_cp = server._checkpoint_wal
    server._checkpoint_wal = lambda force=False: (checkpoints.append(force), orig_cp(force))[1]

    server._embed_backfill(alle=True)

    assert batches == [CHUNK, CHUNK, ENTITIES - PORTION], f"nicht alles: {batches}"
    assert len(reopens) == 2, f"keine frische Session je Portion: {reopens}"
    assert checkpoints == [], f"zusaetzlicher Checkpoint neben dem Reopen: {checkpoints}"
    assert _befuellt(server) == ENTITIES, f"Vektoren fehlen: {_befuellt(server)}"

    print("OK")


def _scenario_checkpoint_faellt_aus() -> None:
    """Scheitert ein Checkpoint, bricht der Lauf ab und meldet NICHT "fertig".

    Vorher schluckte _checkpoint_wal jeden Fehler als WARNING und der Backfill
    meldete Erfolg — die Vektoren hingen aber im vollen Buffer-Pool und waren nach
    dem naechsten Start weg. Genau so lief es am 29.08. durch.
    """
    server = _server("ai-rem-backfill-fail-")
    meldungen = _fang(server)

    rufe = []
    server.EMBED_ENABLED = True
    server._embed_texts = lambda texts, prefix: [[0.1, 0.2, 0.3] for _ in texts]
    server._checkpoint_wal = lambda force=False: (rufe.append(force), False)[1]

    server._embed_backfill()

    assert not [m for _, m in meldungen if "Backfill fertig" in m], "meldet faelschlich Erfolg"
    assert [m for lvl, m in meldungen if lvl == "ERROR" and "Checkpoint" in m], \
        f"kein ERROR zum Checkpoint-Fehlschlag: {meldungen}"

    print("OK")


def _scenario_checkpoint_verwirft() -> None:
    """Kuzu meldet den Checkpoint als erfolgreich und verwirft die Writes trotzdem.

    Genau das passierte am 03.09. in Produktion: "Backfill fertig (1251)" im Log,
    danach 1210 Entities weiterhin ohne Vektor. Ohne Nachzaehlen bleibt das
    unsichtbar — der Lauf muss abbrechen und es sagen.
    """
    server = _server("ai-rem-backfill-verwirft-")
    meldungen = _fang(server)

    server.EMBED_ENABLED = True
    server._embed_texts = lambda texts, prefix: [[0.1, 0.2, 0.3] for _ in texts]

    def _verwerfender_checkpoint(force=False):
        server.db_exec("MATCH (e:Entity) SET e.embedding = ''")
        return True

    server._checkpoint_wal = _verwerfender_checkpoint

    server._embed_backfill()

    assert not [m for _, m in meldungen if "Backfill fertig" in m], "meldet faelschlich Erfolg"
    assert [m for lvl, m in meldungen if lvl == "ERROR" and "verworfen" in m], \
        f"kein ERROR zu verworfenen Vektoren: {meldungen}"
    # Nach der ersten verworfenen Portion ist Schluss — nicht 40 Portionen lang
    # weiterrechnen, deren Ergebnis derselbe Checkpoint gleich wieder wegwirft.
    assert [m for _, m in meldungen if "abgebrochen" in m], f"lief weiter: {meldungen}"

    print("OK")


def _lauf(szenario):
    r = subprocess.run(
        [sys.executable, __file__, szenario],
        capture_output=True, text=True,
        env={**os.environ, "AI_REM_API_TOKEN": "test-token"},
    )
    assert r.returncode == 0, f"Szenario fehlgeschlagen:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    assert "OK" in r.stdout


def test_embed_backfill_chunks_und_checkpoint_je_portion():
    _lauf("chunks")


def test_startup_backfill_holt_alles_in_frischen_sessions():
    _lauf("alle")


def test_backfill_bricht_bei_checkpoint_fehler_ab():
    _lauf("checkpoint_faellt_aus")


def test_backfill_bricht_ab_wenn_checkpoint_vektoren_verwirft():
    _lauf("checkpoint_verwirft")


if __name__ == "__main__":
    szenario = sys.argv[1] if len(sys.argv) > 1 else "chunks"
    if szenario == "alle":
        _scenario_alle()
    elif szenario == "checkpoint_faellt_aus":
        _scenario_checkpoint_faellt_aus()
    elif szenario == "checkpoint_verwirft":
        _scenario_checkpoint_verwirft()
    else:
        _scenario()
