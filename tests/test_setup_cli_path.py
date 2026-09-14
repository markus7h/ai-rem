"""points_at_clone entscheidet, ob das Setup AI_REM_CLI auf die lokale Kopie
umbiegt. Faellt der Check falsch aus, bleibt ein Clone-Pfad auf einem Netzlaufwerk
stehen — und der Auto-Memory-Hook stirbt still, sobald der Mount haengt."""
import importlib.util
import pathlib
import sys

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


def test_update_flag_ueberspringt_den_bootstrap(monkeypatch, tmp_path):
    """`--update` darf nur Dateien auffrischen. Zoege es den Bootstrap mit, brauchte
    ein simples Update wieder SSH, git und npm — und genau deshalb hat es bisher
    niemand ausgefuehrt."""
    gerufen = []
    for name in ("find_claude", "register_mcp", "pull_secrets", "build_tools_mcp",
                 "build_mykeyvault_mcp", "update_claude_json", "create_entities",
                 "update_claude_md", "load_setup_config", "choose_mcp_endpoint",
                 "write_settings_template", "install_hooks", "install_cli",
                 "link_cli", "update_settings", "install_commands"):
        monkeypatch.setattr(setup, name,
                            (lambda n: lambda *a, **kw: gerufen.append(n))(name))
    monkeypatch.setattr(setup, "KG_URL", "http://kg.test")
    monkeypatch.setattr(setup, "CLAUDE_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["setup.py", "--update"])

    setup.main()

    assert gerufen == ["load_setup_config", "choose_mcp_endpoint",
                       "write_settings_template", "install_hooks", "install_cli",
                       "link_cli", "update_settings", "install_commands"]


def test_link_cli_legt_symlink(tmp_path, monkeypatch):
    """Ohne Eintrag im PATH ist `ai-rem update` nicht tippbar — genau der Befehl,
    der den Client aktuell haelt."""
    monkeypatch.setattr(setup, "SHIM_DIR", str(tmp_path / "bin"))
    cli = tmp_path / "share" / "ai-rem" / "bin" / "ai-rem"
    cli.parent.mkdir(parents=True)
    cli.write_text("#!/usr/bin/env python3\n")

    setup.link_cli(str(cli))

    shim = tmp_path / "bin" / "ai-rem"
    assert shim.is_symlink()
    assert pathlib.Path(shim).resolve() == cli.resolve()


def test_link_cli_ersetzt_alten_symlink(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "SHIM_DIR", str(tmp_path / "bin"))
    (tmp_path / "bin").mkdir()
    veraltet = tmp_path / "bin" / "ai-rem"
    veraltet.symlink_to(tmp_path / "weg" / "ai-rem")  # Ziel existiert nicht
    cli = tmp_path / "neu" / "ai-rem"
    cli.parent.mkdir()
    cli.write_text("x")

    setup.link_cli(str(cli))

    assert pathlib.Path(veraltet).resolve() == cli.resolve()


def test_link_cli_laesst_fremde_datei_stehen(tmp_path, monkeypatch, capsys):
    """Eine echte Datei an der Stelle kann eine bewusst installierte andere CLI
    sein. Ueberschreiben waere Datenverlust ohne Rueckfrage."""
    monkeypatch.setattr(setup, "SHIM_DIR", str(tmp_path / "bin"))
    (tmp_path / "bin").mkdir()
    fremd = tmp_path / "bin" / "ai-rem"
    fremd.write_text("fremde CLI")

    setup.link_cli(str(tmp_path / "neu"))

    assert fremd.read_text() == "fremde CLI"
    assert "nicht angefasst" in capsys.readouterr().out


def test_link_cli_ohne_cli_pfad_macht_nichts(tmp_path, monkeypatch):
    """install_cli() gibt bei fehlgeschlagenem Download '' zurueck — dann darf kein
    Symlink ins Leere zeigen."""
    monkeypatch.setattr(setup, "SHIM_DIR", str(tmp_path / "bin"))
    setup.link_cli("")
    assert not (tmp_path / "bin").exists()
