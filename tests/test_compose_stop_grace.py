"""Der Container braucht Zeit zum Herunterfahren.

`docker stop` killt nach 10s hart. Der SIGTERM-Handler in server.py merged in
dieser Zeit die WAL in die kg.db — bei ~180 MB reicht das nicht. Am 13.09.2026
erwischte der SIGKILL genau diesen Checkpoint: `kg.db.wal.checkpoint` plus
Lock-Dateien blieben liegen, und der naechste Start scheiterte in einer
Crash-Schleife an "Checksum verification failed, the WAL file is corrupted".

Faellt `stop_grace_period` beim naechsten Compose-Umbau wieder raus, passiert das
beim uebernaechsten Deploy erneut — und sieht dann aus wie ein DB-Defekt.
"""
import pathlib

import yaml

COMPOSE = pathlib.Path(__file__).resolve().parent.parent / "docker-compose.yml"


def test_stop_grace_period_reicht_fuer_den_wal_checkpoint():
    dienst = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["ai-rem"]
    grace = dienst.get("stop_grace_period")
    assert grace, "kein stop_grace_period — SIGKILL nach 10s zerlegt den WAL-Checkpoint"
    assert grace.endswith("s"), grace
    assert int(grace[:-1]) >= 60, "unter 60s ist der Checkpoint bei ~180 MB kg.db nicht sicher"
