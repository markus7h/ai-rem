"""Nur-ai-rem-Betrieb (ohne mykeyvault/tools) und Plugin-Modus.

- vault-secret-reminder.py nur mit mykeyvault installieren/registrieren, sonst
  einen alten Registry-Eintrag austragen.
- ensure_deps() (node/npm/git) nur, wenn ein mykeyvault-/tools-Build ansteht.
- system-check: nur registrierte stdio-Server (oder aktive Plugins) pruefen.
- Aktives Plugin ai-rem@…/mykeyvault@… liefert den Server selbst: kein
  gleichnamiger Eintrag in ~/.claude.json, ein vorhandener wird entfernt.
"""
import importlib.machinery
import importlib.util
import json
import os
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
REMINDER = "vault-secret-reminder.py"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    loader = importlib.machinery.SourceFileLoader("ai_rem_setup_nur", str(ROOT / "scripts" / "setup.py"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    home = tmp_path / ".claude"
    (home / "hooks").mkdir(parents=True)
    monkeypatch.setattr(mod, "CLAUDE_HOME", str(home))
    monkeypatch.setattr(mod, "CLAUDE_JSON", str(tmp_path / ".claude.json"))
    monkeypatch.setattr(mod, "LOCAL_CLI", str(tmp_path / "bin" / "ai-rem"))
    monkeypatch.setattr(mod, "KG_URL", "http://kg.test")
    monkeypatch.setattr(mod, "REPORT", [])
    monkeypatch.delenv("MYKEYVAULT_URL", raising=False)
    return mod


def _write(path, data):
    pathlib.Path(path).write_text(json.dumps(data))


def _read(path):
    return json.loads(pathlib.Path(path).read_text())


def _settings(setup):
    return os.path.join(setup.CLAUDE_HOME, "settings.json")


def _reminder_cmds(data):
    return [h["command"] for g in data.get("hooks", {}).get("PostToolUse", [])
            for h in g.get("hooks", []) if REMINDER in h.get("command", "")]


# ── (a) vault-secret-reminder nur mit mykeyvault ─────────────────────────────

def test_install_hooks_ohne_vault_laedt_den_reminder_nicht(setup, monkeypatch):
    fetched = []
    def fetch(url, dst):
        fetched.append(os.path.basename(dst))
        pathlib.Path(dst).write_text("#!/usr/bin/env python3\n")
        return True

    monkeypatch.setattr(setup, "fetch_to", fetch)
    paths = setup.install_hooks(vault_reminder=False)
    assert REMINDER not in fetched and REMINDER not in paths
    fetched.clear()
    assert REMINDER in setup.install_hooks(vault_reminder=True)


def test_reminder_wird_ohne_mykeyvault_ausgetragen(setup):
    other = {"type": "command", "command": "/x/anderer-bash-hook.py"}
    _write(_settings(setup), {"hooks": {"PostToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "/h/" + REMINDER}, other]},
        {"matcher": "Foo", "hooks": [{"type": "command", "command": "/h/" + REMINDER}]},
    ]}})
    _write(setup.CLAUDE_JSON, {"mcpServers": {"ai-rem": {}}})
    assert setup.mykeyvault_registered() is False
    setup.update_settings({}, "", {}, vault_reminder=False)
    data = _read(_settings(setup))
    assert _reminder_cmds(data) == []
    # fremder Hook bleibt, die leere Gruppe faellt weg
    assert data["hooks"]["PostToolUse"] == [{"matcher": "Bash", "hooks": [other]}]


def test_reminder_mit_mykeyvault_registriert(setup):
    _write(_settings(setup), {})
    _write(setup.CLAUDE_JSON, {"mcpServers": {"ai-rem": {}, "mykeyvault": {}}})
    assert setup.mykeyvault_registered() is True
    setup.update_settings({}, "", {REMINDER: "/h/" + REMINDER}, vault_reminder=True)
    assert _reminder_cmds(_read(_settings(setup))) == [setup.hook_command("/h/" + REMINDER)]


# ── (b) ensure_deps nur bei anstehenden Builds ───────────────────────────────

@pytest.mark.parametrize("reg, need", [
    ({}, False),
    ({"mykeyvault": {"http": {"url": "http://v/mcp"}}}, False),
    ({"tools": {"stdio": {"repo": "r"}}}, False),
    ({"mykeyvault": {"stdio": {"repo": "r"}}}, True),
    ({"tools": {"stdio": {"registry_url": "http://reg"}}}, True),
])
def test_needs_node_builds(setup, reg, need):
    assert setup.needs_node_builds({"mcp_register": reg}) is need


@pytest.mark.parametrize("reg, called", [
    ({"mykeyvault": {"http": {"url": "http://v/mcp"}}}, False),
    ({"mykeyvault": {"stdio": {"repo": "r"}}}, True),
])
def test_main_ruft_ensure_deps_nur_bei_build(setup, monkeypatch, reg, called):
    calls = []
    stubs = {
        "load_setup_config": lambda: {"mcp_register": reg},
        "choose_mcp_endpoint": lambda cfg: "http://kg.test/mcp",
        "install_cli": lambda: "", "link_cli": lambda p: None,
        "pull_secrets": lambda cfg: "", "obtain_tokens": lambda t, u, force_pair=False: ("", u),
        "report_keychain": lambda: None, "migrate_secrets": lambda: [],
        "ensure_deps": lambda: calls.append("deps") or True,
        "build_tools_mcp": lambda cfg: ("", ""), "build_mykeyvault_mcp": lambda cfg: "",
        "record_client": lambda *a, **k: {}, "load_client_cfg": lambda: {},
        "install_claude": lambda *a: None, "resolve_token": lambda t: "",
        "print_report": lambda: None,
    }
    for name, fn in stubs.items():
        monkeypatch.setattr(setup, name, fn)
    monkeypatch.setattr(setup.sys, "argv", ["setup.py", "--client", "claude"])
    setup.main()
    assert calls == (["deps"] if called else [])


# ── (c) system-check prueft nur registrierte stdio-Server ────────────────────

def _load_system_check(monkeypatch):
    src = (ROOT / "hooks" / "system-check.py").read_text()
    cut = src.index("\ncheck_ai_rem()\n")
    spec = importlib.util.spec_from_loader("system_check_nur", loader=None)
    sc = importlib.util.module_from_spec(spec)
    monkeypatch.setenv("AI_REM_TOKEN", "load-dummy")  # pragma: allowlist secret
    exec(compile(src[:cut], "system-check.py", "exec"), sc.__dict__)
    return sc


def test_system_check_ignoriert_nicht_registrierte_server(tmp_path, monkeypatch):
    sc = _load_system_check(monkeypatch)
    cj, st = tmp_path / ".claude.json", tmp_path / "settings.json"
    _write(cj, {"mcpServers": {"ai-rem": {}, "tools": {}}})
    _write(st, {"enabledPlugins": {"mykeyvault@irgendein-marketplace": True,
                                   "gibtsnicht@x": False}})
    sc.CLAUDE_JSON, sc.SETTINGS = str(cj), str(st)
    sc.MCP_STDIO_SERVERS = {"tools": "/t.js", "mykeyvault": "/v.js", "gibtsnicht": "/g.js"}
    checked = []
    sc._check_one_stdio = lambda n, p: checked.append(n) or n != "tools"
    sc.results = []
    sc.check_mcp_servers()
    assert sorted(checked) == ["mykeyvault", "tools"]
    assert sc.results == ["MCP: 1/2, ❌ tools"]

    # Nur-ai-rem: nichts davon registriert -> keine MCP-Zeile, kein ❌
    _write(cj, {"mcpServers": {"ai-rem": {}}})
    _write(st, {})
    checked.clear()
    sc.results = []
    sc.check_mcp_servers()
    assert checked == [] and sc.results == []


# ── (e) Plugin-Modus: keine zwei Server gleichen Namens ─────────────────────

def _plugins(setup, plugins):
    _write(_settings(setup), {"enabledPlugins": plugins})


def test_plugin_enabled_beliebiger_marketplace(setup):
    _plugins(setup, {"ai-rem@tools-registry": True, "mykeyvault@fork": False, "x@y": True})
    assert setup.plugin_enabled("ai-rem")
    assert not setup.plugin_enabled("mykeyvault")
    _plugins(setup, {"mykeyvault@fork": True})
    assert setup.plugin_enabled("mykeyvault")


def test_plugin_eintraege_werden_entfernt(setup):
    _plugins(setup, {"ai-rem@tools-registry": True, "mykeyvault@tools-registry": True})
    _write(setup.CLAUDE_JSON, {"mcpServers": {"ai-rem": {"type": "http", "url": "u"},
                                              "mykeyvault": {"type": "stdio"}, "tools": {}}})
    setup.update_claude_json({}, "https://x/mcp", "", "", vault_entry="/v/index.js")
    assert list(_read(setup.CLAUDE_JSON)["mcpServers"]) == ["tools"]
    # Plugin mykeyvault zaehlt als registriert (vault-secret-reminder bleibt)
    assert setup.mykeyvault_registered() is True


def test_plugin_ohne_ai_rem_eintrag_kein_abbruch(setup):
    _plugins(setup, {"ai-rem@tools-registry": True})
    _write(setup.CLAUDE_JSON, {"mcpServers": {}})
    assert setup.update_claude_json({}, "https://x/mcp", "/t/index.js", "http://reg") is True
    servers = _read(setup.CLAUDE_JSON)["mcpServers"]
    assert "ai-rem" not in servers and "tools" in servers


def test_ohne_plugin_ai_rem_als_stdio_proxy(setup):
    _plugins(setup, {})
    _write(setup.CLAUDE_JSON, {"mcpServers": {"ai-rem": {"type": "http", "url": "u",
                                                         "headers": {"Authorization": "Bearer x"}}}})
    setup.update_claude_json({}, "https://x/mcp", "", "")
    assert _read(setup.CLAUDE_JSON)["mcpServers"]["ai-rem"] == {
        "type": "stdio", "command": setup.LOCAL_CLI, "args": ["mcp-proxy"]}


def test_register_mcp_ueberspringt_bei_plugin(setup, monkeypatch):
    _plugins(setup, {"ai-rem@tools-registry": True})
    monkeypatch.setattr(setup, "run", lambda *a, **k: pytest.fail("claude mcp darf nicht laufen"))
    setup.register_mcp("/x/claude")


def test_update_traegt_plugin_duplikat_aus(setup):
    _plugins(setup, {"mykeyvault@tools-registry": True})
    _write(setup.CLAUDE_JSON, {"mcpServers": {"ai-rem": {}, "mykeyvault": {}}})
    setup.dedupe_claude_json()
    assert list(_read(setup.CLAUDE_JSON)["mcpServers"]) == ["ai-rem"]
