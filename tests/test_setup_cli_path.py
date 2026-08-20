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


def test_leere_datei_gilt_als_erfolg(tmp_path, monkeypatch):
    """lib/__init__.py ist regulaer 0 Bytes. Galt das als Download-Fehler, brach
    install_cli() ab, nachdem bin/ai-rem geschrieben, aber noch nicht ausfuehrbar
    gemacht war — halb installierte CLI, Hook meldete still 'CLI not found'."""
    monkeypatch.setattr(setup, "http_get", lambda url, **kw: b"")
    dst = tmp_path / "lib" / "__init__.py"
    assert setup.fetch_to("http://x/lib/__init__.py", str(dst)) is True
    assert dst.read_bytes() == b""


def test_transportfehler_laesst_bestehende_datei_stehen(tmp_path, monkeypatch):
    def boom(url, **kw):
        raise OSError("timeout")
    monkeypatch.setattr(setup, "http_get", boom)
    dst = tmp_path / "bin" / "ai-rem"
    dst.parent.mkdir(parents=True)
    dst.write_bytes(b"alte funktionierende CLI")
    assert setup.fetch_to("http://x/bin/ai-rem", str(dst)) is False
    assert dst.read_bytes() == b"alte funktionierende CLI"
