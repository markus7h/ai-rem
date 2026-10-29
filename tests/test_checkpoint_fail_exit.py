"""Scheitert der WAL-Checkpoint dauerhaft, beendet sich der Prozess.

Ab dem 26.10.2026 lief der Buffer-Pool voll, jeder minütliche Checkpoint brach mit
"buffer pool is full" ab und liess ~3 MB Seiten in kg.db liegen: 187 MB → 10 GB.
Der Scheduler zählt Fehlschläge in Folge und ruft ab AI_REM_CHECKPOINT_FAIL_EXIT
os._exit(1); ein Erfolg dazwischen setzt den Zähler zurück.

Läuft im SUBPROZESS: das Szenario stubbt Modul-Globals.
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _scenario() -> None:
    tmp = tempfile.mkdtemp(prefix="ai-rem-cpexit-")
    os.environ["LADYBUG_DB_PATH"] = os.path.join(tmp, "kg.db")
    os.environ["BACKUP_DIR"] = os.path.join(tmp, "backups")
    os.environ["EMBED_ENABLED"] = "0"
    sys.path.insert(0, ROOT)
    import server

    class _Exit(Exception):
        pass

    def _exit(code):
        raise _Exit(code)

    def _lauf(ergebnisse):
        """Scheduler mit vorgegebenen Checkpoint-Ergebnissen; Rückgabe: Ticks bis Exit
        oder None, wenn er ohne Exit durchlief."""
        folge = iter(ergebnisse)
        ticks = []

        class _Stop:
            def wait(self, _):
                return len(ticks) >= len(ergebnisse)

        def _cp(force=False):
            ticks.append(1)
            return next(folge)

        server._shutdown = _Stop()
        server._checkpoint_wal = _cp
        server._load_backup_cfg = lambda: {}
        server.os._exit = _exit
        try:
            server._scheduler_loop()
        except _Exit as e:
            assert e.args == (1,)
            return len(ticks)
        return None

    server.LADYBUG_CHECKPOINT_FAIL_EXIT = 3
    assert _lauf([False] * 5) == 3
    # Ein Erfolg setzt den Zähler zurück: nie 3 in Folge.
    assert _lauf([False, False, True, False, False, True]) is None
    server.LADYBUG_CHECKPOINT_FAIL_EXIT = 0
    assert _lauf([False] * 10) is None
    print("OK")


def test_dauerhaft_scheiternder_checkpoint_beendet_prozess():
    r = subprocess.run(
        [sys.executable, __file__],
        capture_output=True, text=True,
        env={**os.environ, "AI_REM_API_TOKEN": "test-token"},
    )
    assert r.returncode == 0, f"Szenario fehlgeschlagen:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    assert "OK" in r.stdout


if __name__ == "__main__":
    _scenario()
