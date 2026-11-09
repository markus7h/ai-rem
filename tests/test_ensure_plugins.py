"""ensure_plugins(): ai-rem/mykeyvault werden als Plugin installiert; scheitert das,
greift der mcpServers-Fallback (plugin_enabled bleibt False)."""
import importlib.machinery
import importlib.util
import json
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load():
    loader = importlib.machinery.SourceFileLoader("ai_rem_setup_plug", str(ROOT / "scripts" / "setup.py"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _setup(tmp_path, monkeypatch, install_ok):
    mod = _load()
    home = tmp_path / ".claude"
    home.mkdir()
    settings = home / "settings.json"
    settings.write_text(json.dumps({"enabledPlugins": {}}))
    monkeypatch.setattr(mod, "CLAUDE_HOME", str(home))
    calls = []

    def fake_run(cmd, timeout=120, capture=True, cwd=None):
        calls.append(cmd[1:])
        rc = 0
        if cmd[1:3] == ["plugin", "install"]:
            if install_ok:
                cfg = json.loads(settings.read_text())
                cfg["enabledPlugins"][cmd[3]] = True
                settings.write_text(json.dumps(cfg))
            else:
                rc = 1
        out = "tools-registry\n" if cmd[1:4] == ["plugin", "marketplace", "list"] else ""
        return subprocess.CompletedProcess(cmd, rc, out, "")

    monkeypatch.setattr(mod, "run", fake_run)
    return mod, calls


def test_installs_missing_plugins(tmp_path, monkeypatch):
    mod, calls = _setup(tmp_path, monkeypatch, install_ok=True)
    mod.ensure_plugins("claude")
    assert ["plugin", "install", "ai-rem@tools-registry"] in calls
    assert ["plugin", "install", "mykeyvault@tools-registry"] in calls
    assert mod.plugin_enabled("ai-rem") and mod.plugin_enabled("mykeyvault")
    servers = {"ai-rem": {}, "mykeyvault": {}, "tools": {}}
    mod.drop_plugin_duplicates(servers)
    assert list(servers) == ["tools"]


def test_install_failure_keeps_fallback(tmp_path, monkeypatch):
    mod, _ = _setup(tmp_path, monkeypatch, install_ok=False)
    mod.ensure_plugins("claude")
    assert not mod.plugin_enabled("ai-rem")


def test_noop_when_already_enabled(tmp_path, monkeypatch):
    mod, calls = _setup(tmp_path, monkeypatch, install_ok=True)
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps(
        {"enabledPlugins": {"ai-rem@x": True, "mykeyvault@x": True}}))
    mod.ensure_plugins("claude")
    assert calls == []
