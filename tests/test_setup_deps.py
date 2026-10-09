"""Installer: fehlende Pakete selbst nachinstallieren, Token-Kette ohne SSH
(Keychain statt Token-Datei), MCP-Registrierung als stdio-Proxy, stdin nie an
Kindprozesse (ssh schluckte eingefuegte Folgezeilen)."""
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
    # Keychain als Dict statt echtem Backend; Legacy-Pfade ins tmp.
    store = {}
    monkeypatch.setattr(mod, "keychain_get", lambda: store.get("t", ""))
    monkeypatch.setattr(mod, "keychain_set", lambda tok: store.__setitem__("t", tok))
    monkeypatch.setattr(mod, "keychain_delete", lambda: store.pop("t", None))
    monkeypatch.setattr(mod, "keychain_backend", lambda: "Fake-Keychain")
    monkeypatch.setattr(mod, "LEGACY_TOKEN_FILE", str(tmp_path / "token"))
    monkeypatch.setattr(mod, "LEGACY_VAULT_TOKEN_FILE", str(tmp_path / "vault.token"))
    monkeypatch.setattr(mod, "LEGACY_VAULT_ENV", str(tmp_path / "ai-rem-vault.env"))
    monkeypatch.setattr(mod, "LOCAL_CLI", str(tmp_path / "bin" / "ai-rem"))
    monkeypatch.setattr(mod, "REPORT", [])
    mod._store = store
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
    """Env/SSH > Keychain > Kopplung > Eingabe. "Gespeichert" heisst Keychain —
    ein neuer Token landet dort, der Vault-Token aus der Kopplung nirgends."""
    paired = []
    monkeypatch.setattr(setup, "pair_device", lambda: paired.append(1) or
                        {"ai_rem_token": "t-pair", "vault_token": "v-pair",  # pragma: allowlist secret
                         "vault_url": "https://vault"})
    monkeypatch.setattr(setup, "ask_token", lambda: pytest.fail("Eingabe trotz erfolgreicher Kopplung"))
    store = setup._store

    assert setup.obtain_tokens("t-env", "u") == ("t-env", "u") and not paired
    assert store["t"] == "t-env", "Env-Token muss im Keychain landen"
    store["t"] = "t-keychain"
    assert setup.obtain_tokens("", "u") == ("t-keychain", "u") and not paired
    store.clear()
    assert setup.obtain_tokens("", "u") == ("t-pair", "https://vault") and paired
    assert store == {"t": "t-pair"}, "nur der ai-rem-Token wird gespeichert, kein Vault-Token"
    assert setup.obtain_tokens("t-env", "u", force_pair=True)[0] == "t-pair"


def test_ohne_kopplung_fragt_die_eingabe(setup, monkeypatch):
    monkeypatch.setattr(setup, "pair_device", lambda: {})
    monkeypatch.setattr(setup, "ask_token", lambda: "t-eingabe")
    assert setup.obtain_tokens("")[0] == "t-eingabe"
    assert setup._store["t"] == "t-eingabe"
    setup._store.clear()
    monkeypatch.setattr(setup, "ask_token", lambda: "")
    setup.obtain_tokens("")
    assert setup.REPORT[-1] == ("ai-rem-Token", False, "ai-rem pair")


def test_keychain_fehler_bricht_nicht_ab(setup, monkeypatch, capsys):
    def boom(tok):
        raise RuntimeError("keychain gesperrt")
    monkeypatch.setattr(setup, "keychain_set", boom)
    assert setup.obtain_tokens("t-env")[0] == "t-env", "Token dieses Laufs bleibt nutzbar"
    assert "AI_REM_TOKEN" in capsys.readouterr().out
    assert ("Keychain", False, "ai-rem pair") in setup.REPORT


def test_register_mcp_als_stdio_proxy(setup, monkeypatch, tmp_path):
    """Kein --transport http und kein Bearer mehr: Claude Code startet `ai-rem mcp-proxy`."""
    calls = []

    class P:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return P()
    monkeypatch.setattr(setup, "run", fake_run)
    monkeypatch.setattr(setup, "IS_WIN", False)
    setup.register_mcp("/x/claude")
    add = [c for c in calls if c[1:3] == ["mcp", "add"]]
    assert len(add) == 1
    assert "--transport" not in add[0] and "http" not in add[0]
    assert add[0][-3:] == ["--", setup.LOCAL_CLI, "mcp-proxy"]
    assert "--scope" in add[0] and add[0][add[0].index("--scope") + 1] == "user"

    monkeypatch.setattr(setup, "IS_WIN", True)
    calls.clear()
    setup.register_mcp("/x/claude")
    add = [c for c in calls if c[1:3] == ["mcp", "add"]][0]
    assert add[add.index("--") + 1:] == [setup.sys.executable, "-X", "utf8", setup.LOCAL_CLI, "mcp-proxy"]


def test_register_mcp_erkennt_ai_rem_nur_als_servernamen(setup, monkeypatch):
    """Der CLI-Pfad (…/ai-rem/bin/ai-rem) steht seit den stdio-Wrappern auch in
    der mykeyvault-Zeile von `claude mcp list` — ein Substring-Match haette
    ai-rem dann nie (neu) registriert."""
    calls = []

    class P:
        returncode, stderr = 0, ""
        stdout = "mykeyvault: /u/.local/share/ai-rem/bin/ai-rem vault-mcp - ✓ Connected\n"

    monkeypatch.setattr(setup, "run", lambda cmd, **kw: calls.append(cmd) or P())
    setup.register_mcp("/x/claude")
    assert any(c[1:3] == ["mcp", "add"] for c in calls), "ai-rem fehlt, muss registriert werden"
    calls.clear()
    P.stdout = "ai-rem: /u/.local/share/ai-rem/bin/ai-rem mcp-proxy - ✓ Connected\n"
    setup.register_mcp("/x/claude")
    assert not any(c[1:3] == ["mcp", "add"] for c in calls)


def test_help_und_unbekannte_option_starten_kein_setup(setup, capsys):
    """`setup.py --help` war ein kompletter Setup-Lauf (Option still ignoriert)."""
    with pytest.raises(SystemExit) as e:
        setup.parse_args(["--help"])
    assert e.value.code == 0 and "--pair-only" in capsys.readouterr().out
    with pytest.raises(SystemExit) as e:
        setup.parse_args(["--dry-run"])
    assert e.value.code == 2 and "Unbekannte Option" in capsys.readouterr().out
    assert setup.parse_args(["--client=claude", "-y"])["yes"] is True
