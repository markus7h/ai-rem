"""scripts/migrate.py — der Weg von Kuzu (<=0.8.32) nach LadybugDB.

Das Skript wird genau einmal pro Instanz benutzt, und zwar dann, wenn die alte
Datenbank schon abgehaengt ist. Ein Tippfehler faellt dort maximal spaet auf,
deshalb laeuft der eingebaute Self-Check (Export→Import gegen einen Stub-Server)
in jedem PR mit.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "migrate.py")


def test_selbstcheck():
    r = subprocess.run([sys.executable, SCRIPT, "--self-check"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    assert "self-check OK" in r.stdout


def test_ohne_token_bricht_ab():
    """Ohne Token liefe jeder Call in ein 401 — das soll vorher auffallen."""
    env = {**os.environ, "AI_REM_API_TOKEN": ""}
    r = subprocess.run([sys.executable, SCRIPT, "export"],
                       capture_output=True, text=True, cwd=ROOT, env=env)
    assert r.returncode != 0
    assert "kein Token" in r.stderr
