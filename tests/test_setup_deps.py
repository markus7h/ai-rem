"""Installer: fehlende Pakete selbst nachinstallieren, Token-Kette ohne SSH,
stdin nie an Kindprozesse (ssh schluckte eingefuegte Folgezeilen)."""
import importlib.machinery
import importlib.util
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def setup(tmp_path, monkeypatch):
    loader = importlib.machinery.SourceFileLoader("ai_rem_setup_deps", str(ROOT / "scripts" / "setup.py"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    monkeypatch.setattr(mod, "TOKEN_FILE", str(tmp_path / "token"))
    monkeypatch.setattr(mod, "VAULT_TOKEN_FILE", str(tmp_path / "vault.token"))
    monkeypatch.setattr(mod, "REPORT", [])
    return mod


def _which(present):
    return lambda name: "/x/" + name if name in present else None


def test_run_gibt_kein_stdin_weiter(setup, monkeypatch):
    seen = {}
    monkeypatch.setattr(setup.subprocess, "run", lambda *a, **kw: seen.update(kw))
    setup.run(["ssh", "host", "true"])
    assert seen["stdin"] is subprocess.DEVNULL


def test_install_befehle_je_plattform(setup, monkeypatch):
    monkeypatch.setattr(setup, "_brew", lambda: "/opt/homebrew/bin/brew")
    monkeypatch.setattr(setup, "PLATFORM", "macos")
    assert setup.dep_install_cmds(["node", "npm"]) == [["/opt/homebrew/bin/brew", "install", "node"]]
    monkeypatch.setattr(setup, "_brew", lambda: "")
    assert setup.dep_install_cmds(["node"]) is None, "ohne brew kein stiller Fehlversuch"

    monkeypatch.setattr(setup, "PLATFORM", "linux")
    monkeypatch.setattr(setup.shutil, "which", _which({"apt-get"}))
    cmds = setup.dep_install_cmds(["node", "git"])
    assert "nodesource" in cmds[0][-1] and cmds[-1] == ["sudo", "apt-get", "install", "-y", "nodejs", "git"]
    assert setup.dep_install_cmds(["git"]) == [["sudo", "apt-get", "install", "-y", "git"]]

    monkeypatch.setattr(setup, "PLATFORM", "windows")
    monkeypatch.setattr(setup.shutil, "which", _which({"winget"}))
    assert [c[-1] for c in setup.dep_install_cmds(["node", "git"])] == ["OpenJS.NodeJS.LTS", "Git.Git"]


def test_ensure_deps_installiert_nach_zustimmung(setup, monkeypatch):
    state = {"miss": ["node", "npm"]}
    monkeypatch.setattr(setup, "missing_deps", lambda: list(state["miss"]))
    monkeypatch.setattr(setup, "dep_install_cmds", lambda m: [["brew", "install", "node"]])
    calls = []

    def fake_call(cmd):
        calls.append(cmd)
        state["miss"] = []
        return 0
    monkeypatch.setattr(setup.subprocess, "call", fake_call)

    monkeypatch.setattr(setup, "ASSUME_YES", True)
    assert setup.ensure_deps() is True and calls == [["brew", "install", "node"]]


def test_ensure_deps_ohne_zustimmung_installiert_nichts(setup, monkeypatch):
    monkeypatch.setattr(setup, "missing_deps", lambda: ["git"])
    monkeypatch.setattr(setup, "dep_install_cmds", lambda m: [["sudo", "apt-get", "install", "-y", "git"]])
    monkeypatch.setattr(setup.subprocess, "call", lambda c: pytest.fail("ohne Zustimmung installiert"))
    monkeypatch.setattr(setup, "ASSUME_YES", False)
    monkeypatch.setattr(setup.sys.stdin, "isatty", lambda: False)
    assert setup.ensure_deps() is False
    assert setup.REPORT[-1][0] == "node/npm/git" and setup.REPORT[-1][2] == "ai-rem install --yes"


def test_token_kette_koppelt_nur_wenn_noetig(setup, monkeypatch):
    paired = []
    monkeypatch.setattr(setup, "pair_device", lambda: paired.append(1) or
                        {"ai_rem_token": "t-pair", "vault_token": "v-pair", "vault_url": "https://vault"})
    monkeypatch.setattr(setup, "ask_token", lambda: pytest.fail("Eingabe trotz erfolgreicher Kopplung"))

    assert setup.obtain_tokens("t-env", "", "u") == ("t-env", "", "u") and not paired
    pathlib.Path(setup.TOKEN_FILE).write_text("t-datei\n")
    assert setup.obtain_tokens("", "", "u")[0] == "t-datei" and not paired
    pathlib.Path(setup.TOKEN_FILE).unlink()
    assert setup.obtain_tokens("", "", "u") == ("t-pair", "v-pair", "https://vault") and paired
    assert setup.obtain_tokens("t-env", "", "u", force_pair=True)[0] == "t-pair"


def test_ohne_kopplung_fragt_die_eingabe(setup, monkeypatch):
    monkeypatch.setattr(setup, "pair_device", lambda: {})
    monkeypatch.setattr(setup, "ask_token", lambda: "t-eingabe")
    assert setup.obtain_tokens("", "", "u")[0] == "t-eingabe"
    monkeypatch.setattr(setup, "ask_token", lambda: "")
    setup.obtain_tokens("", "", "u")
    assert setup.REPORT[-1] == ("ai-rem-Token", False, "ai-rem pair")
