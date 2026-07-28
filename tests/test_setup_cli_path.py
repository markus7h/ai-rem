"""points_at_clone entscheidet, ob das Setup AI_REM_CLI auf die lokale Kopie
umbiegt. Faellt der Check falsch aus, bleibt ein Clone-Pfad auf einem Netzlaufwerk
stehen — und der Auto-Memory-Hook stirbt still, sobald der Mount haengt."""
import importlib.util
import pathlib

_spec = importlib.util.spec_from_file_location(
    "ai_rem_setup", pathlib.Path(__file__).resolve().parent.parent / "scripts" / "setup.py")
setup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(setup)


def test_clone_pfade_werden_ersetzt():
    for p in ("/home/u/myCode/github/ai-rem/bin/ai-rem",
              "/home/u/mystorage/myCode/github/ai-rem/bin/ai-rem",
              "/Volumes/x/myCode/github/ai-rem/bin/ai-rem",
              r"C:\Users\u\myCode\github\ai-rem\bin\ai-rem"):
        assert setup.points_at_clone(p), p


def test_lokale_kopie_und_leer_bleiben():
    for p in ("", "/home/u/.local/share/ai-rem/bin/ai-rem", "/usr/local/bin/ai-rem"):
        assert not setup.points_at_clone(p), p
