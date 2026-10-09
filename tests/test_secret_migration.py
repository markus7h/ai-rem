"""migrate_secrets(): Klartext-Tokens einer Installation < 1.7 wandern in den
Keychain, alle sechs Ablagen werden geleert bzw. auf die stdio-Wrapper der CLI
umgestellt. Zweiter Lauf: still und ohne Aenderung. Alles im tmp, Keychain als Dict."""
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import stat

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

TOK = "tok-aus-datei"  # pragma: allowlist secret
HDR_TOK = "tok-aus-header"  # pragma: allowlist secret
VAULT_TOK = "vault-geheim"  # pragma: allowlist secret
LLM_KEY = "sk-llm-geheim"  # pragma: allowlist secret
VAULT_ENTRY = "/home/u/Code/mykeyvault/mcp/dist/index.js"


def _load():
    loader = importlib.machinery.SourceFileLoader("ai_rem_setup_mig", str(ROOT / "scripts" / "setup.py"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    """Alle sechs Klartext-Stellen einer alten Installation (claude + opencode)."""
    mod = _load()
    claude_home = tmp_path / ".claude"
    cfg_dir = tmp_path / ".config" / "ai-rem"
    oc_dir = tmp_path / ".config" / "opencode"
    for d in (claude_home, cfg_dir, oc_dir):
        d.mkdir(parents=True)
    monkeypatch.setattr(mod, "HOME", str(tmp_path))
    monkeypatch.setattr(mod, "CLAUDE_HOME", str(claude_home))
    monkeypatch.setattr(mod, "CLAUDE_JSON", str(tmp_path / ".claude.json"))
    monkeypatch.setattr(mod, "AIREM_CFG_DIR", str(cfg_dir))
    monkeypatch.setattr(mod, "CLIENT_JSON", str(cfg_dir / "client.json"))
    monkeypatch.setattr(mod, "SNIPPET_DIR", str(cfg_dir / "snippets"))
    monkeypatch.setattr(mod, "OPENCODE_DIR", str(oc_dir))
    monkeypatch.setattr(mod, "LEGACY_TOKEN_FILE", str(cfg_dir / "token"))
    monkeypatch.setattr(mod, "LEGACY_VAULT_TOKEN_FILE", str(cfg_dir / "vault.token"))
    monkeypatch.setattr(mod, "LEGACY_VAULT_ENV", str(claude_home / "ai-rem-vault.env"))
    monkeypatch.setattr(mod, "LOCAL_CLI", str(tmp_path / ".local" / "share" / "ai-rem" / "bin" / "ai-rem"))
    monkeypatch.setattr(mod, "IS_WIN", False)
    monkeypatch.setattr(mod.shutil, "which", lambda n: "/usr/bin/node" if n == "node" else None)
    monkeypatch.delenv("AI_REM_STATE_DIR", raising=False)
    monkeypatch.delenv("NODE_EXTRA_CA_CERTS", raising=False)
    store = {}
    monkeypatch.setattr(mod, "keychain_get", lambda: store.get("t", ""))
    monkeypatch.setattr(mod, "keychain_set", lambda tok: store.__setitem__("t", tok))
    monkeypatch.setattr(mod, "keychain_delete", lambda: store.pop("t", None))
    monkeypatch.setattr(mod, "keychain_backend", lambda: "Fake-Keychain")
    mod._store = store

    (cfg_dir / "token").write_text(TOK + "\n")
    (cfg_dir / "vault.token").write_text(VAULT_TOK + "\n")
    (claude_home / "ai-rem-vault.env").write_text(
        "VAULT_API_URL=http://vault\nVAULT_API_TOKEN=%s\n" % VAULT_TOK)
    (tmp_path / ".claude.json").write_text(json.dumps({
        "numStartups": 3,
        "mcpServers": {
            "ai-rem": {"type": "http", "url": "https://kg/mcp",
                       "headers": {"Authorization": "Bearer " + HDR_TOK}},
            "mykeyvault": {"type": "stdio", "command": "node", "args": [VAULT_ENTRY],
                           "env": {"VAULT_API_URL": "http://vault", "VAULT_API_TOKEN": VAULT_TOK}},
            "tools": {"type": "stdio", "command": "node", "args": ["/t/index.js"],
                      "env": {"TOOLS_REGISTRY_URL": "http://reg"}},
            "fremd": {"type": "http", "url": "https://fremd", "headers": {"X-Key": "bleibt"}},
        }}))
    (claude_home / "settings.json").write_text(json.dumps({
        "env": {"AI_REM_ENDPOINT": "https://kg/mcp", "AI_REM_LLAMA_URL": "http://llm",
                "AI_REM_LLM_API_KEY": LLM_KEY},
        "permissions": {"allow": ["Bash"]}}))
    (cfg_dir / "client.json").write_text(json.dumps({
        "endpoint": "https://kg/mcp", "targets": ["claude", "opencode"],
        "llm_url": "http://llm", "llm_api_key": LLM_KEY, "token_file": str(cfg_dir / "token"),
        "vault_url": "http://vault"}))
    oc_cfg = {"$schema": "https://opencode.ai/config.json", "provider": {"litellm": {"x": 1}},
              "mcp": {"ai-rem": {"type": "remote", "url": "https://kg/mcp", "enabled": True,
                                 "headers": {"Authorization": "Bearer {file:%s}" % (cfg_dir / "token")}},
                      "mykeyvault": {"type": "local", "command": ["/usr/bin/node", VAULT_ENTRY], "enabled": True,
                                     "environment": {"VAULT_API_URL": "http://vault",
                                                     "VAULT_API_TOKEN": "{file:%s}" % (cfg_dir / "vault.token")}},
                      "fremd": {"type": "remote", "url": "u"}},
              "instructions": [str(claude_home / "auto-memory" / "fallback.md")]}
    (oc_dir / "opencode.json").write_text(json.dumps(oc_cfg))
    # Backup der Vor-ai-rem-Config legt die Erstinstallation an; ohne sie wuerde
    # merge_opencode_json den Legacy-Stand als .bak konservieren.
    (oc_dir / "opencode.json.pre-airem.bak").write_text(json.dumps({"provider": {"litellm": {"x": 1}}}))
    (oc_dir / "plugin").mkdir()
    (oc_dir / "plugin" / "ai-rem.ts").write_text("// plugin")
    return mod


def _snapshot(root):
    out = {}
    for p in sorted(pathlib.Path(root).rglob("*")):
        if p.is_file() and p.suffix in (".json", ".env", ".bak", "") and ".local" not in p.parts:
            out[str(p)] = p.read_bytes()
    return out


def _grep(root, needles):
    hits = []
    for path, body in _snapshot(root).items():
        text = body.decode("utf-8", "replace")
        hits += ["%s: %s" % (path, n) for n in needles if n in text]
    return hits


def test_migration_raeumt_alle_klartext_stellen(legacy, tmp_path, capsys):
    mod = legacy
    removed = mod.migrate_secrets()
    out = capsys.readouterr().out

    # Token aus der Datei (hat Vorrang vor dem Header) liegt im Keychain.
    assert mod._store == {"t": TOK}
    for p in (mod.LEGACY_TOKEN_FILE, mod.LEGACY_VAULT_TOKEN_FILE, mod.LEGACY_VAULT_ENV):
        assert not os.path.exists(p), p

    # Kein Secret mehr in irgendeiner Datei unter HOME.
    hits = _grep(tmp_path, ("Authorization", "VAULT_API_TOKEN", "AI_REM_LLM_API_KEY", "llm_api_key",
                            TOK, HDR_TOK, VAULT_TOK, LLM_KEY, "{file:", "token_file"))
    assert hits == [], hits

    cj = json.loads((tmp_path / ".claude.json").read_text())
    srv = cj["mcpServers"]
    assert cj["numStartups"] == 3, "fremde Felder bleiben"
    assert srv["ai-rem"] == {"type": "stdio", "command": mod.LOCAL_CLI, "args": ["mcp-proxy"]}
    assert srv["mykeyvault"] == {"type": "stdio", "command": mod.LOCAL_CLI, "args": ["vault-mcp"]}
    assert srv["tools"]["env"] == {"TOOLS_REGISTRY_URL": "http://reg"}, "tools-Env ist kein Secret"
    assert srv["fremd"]["headers"] == {"X-Key": "bleibt"}, "fremde Server nicht anfassen"

    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert settings["env"] == {"AI_REM_ENDPOINT": "https://kg/mcp", "AI_REM_LLAMA_URL": "http://llm"}
    assert settings["permissions"] == {"allow": ["Bash"]}

    cfg = json.loads(pathlib.Path(mod.CLIENT_JSON).read_text())
    assert cfg["targets"] == ["claude", "opencode"] and cfg["vault_url"] == "http://vault"
    assert cfg["vault_entry"] == VAULT_ENTRY, "Pfad des gebauten MCP aus ~/.claude.json uebernommen"
    assert stat.S_IMODE(os.stat(mod.CLIENT_JSON).st_mode) == 0o600

    oc = json.loads((tmp_path / ".config" / "opencode" / "opencode.json").read_text())
    assert oc["provider"] == {"litellm": {"x": 1}} and oc["mcp"]["fremd"] == {"type": "remote", "url": "u"}
    assert oc["mcp"]["ai-rem"]["type"] == "local"
    assert oc["mcp"]["ai-rem"]["command"] == [mod.LOCAL_CLI, "mcp-proxy"]
    assert oc["mcp"]["mykeyvault"] == {"type": "local", "command": [mod.LOCAL_CLI, "vault-mcp"], "enabled": True}

    # Jede entfernte Stelle genau einmal gemeldet: 3 Dateien, 2x ~/.claude.json,
    # settings.json, 3 client.json-Felder, 2x opencode.json.
    assert out.count("✓ Klartext entfernt:") == len(removed) == 11, out
    for needle in ("mcpServers.ai-rem.headers", "mcpServers.mykeyvault.env", "env.AI_REM_LLM_API_KEY",
                   "client.json llm_api_key", "client.json token_file", "client.json llm_url",
                   "mcp.ai-rem.headers", "mcp.mykeyvault.environment", "ai-rem-vault.env", "vault.token"):
        assert needle in out, needle

    # Zweiter Lauf: nichts zu tun, nichts gesagt, nichts geaendert.
    before = _snapshot(tmp_path)
    assert mod.migrate_secrets() == []
    assert capsys.readouterr().out == ""
    assert _snapshot(tmp_path) == before


def test_header_token_wenn_keine_datei(legacy):
    """Reihenfolge: Token-Datei, dann Bearer aus ~/.claude.json. Ein voller Keychain
    wird nie ueberschrieben."""
    mod = legacy
    os.unlink(mod.LEGACY_TOKEN_FILE)
    mod.migrate_secrets()
    assert mod._store == {"t": HDR_TOK}

    mod2 = legacy
    mod2._store["t"] = "schon-da"
    pathlib.Path(mod2.LEGACY_TOKEN_FILE).write_text(TOK)
    mod2.migrate_secrets()
    assert mod2._store == {"t": "schon-da"}
    assert not os.path.exists(mod2.LEGACY_TOKEN_FILE)


def test_mykeyvault_http_wird_proxy(legacy, tmp_path):
    """HTTP-Fallback mit Bearer -> Proxy auf dieselbe URL; ohne node und ohne
    gebauten MCP fliegt ein stdio-Eintrag ganz raus statt tot liegen zu bleiben."""
    mod = legacy
    cj = json.loads((tmp_path / ".claude.json").read_text())
    cj["mcpServers"]["mykeyvault"] = {"type": "http", "url": "https://mkv",
                                      "headers": {"Authorization": "Bearer " + HDR_TOK}}
    (tmp_path / ".claude.json").write_text(json.dumps(cj))
    mod.migrate_secrets()
    srv = json.loads((tmp_path / ".claude.json").read_text())["mcpServers"]
    assert srv["mykeyvault"] == {"type": "stdio", "command": mod.LOCAL_CLI,
                                 "args": ["mcp-proxy", "--endpoint", "https://mkv"]}


def test_ohne_node_kein_toter_vault_eintrag(legacy, tmp_path, monkeypatch):
    mod = legacy
    monkeypatch.setattr(mod.shutil, "which", lambda n: None)
    mod.migrate_secrets()
    srv = json.loads((tmp_path / ".claude.json").read_text())["mcpServers"]
    assert "mykeyvault" not in srv
    oc = json.loads((tmp_path / ".config" / "opencode" / "opencode.json").read_text())
    assert "mykeyvault" not in oc["mcp"] and "{file:" not in json.dumps(oc)
    assert json.loads(pathlib.Path(mod.CLIENT_JSON).read_text())["vault_entry"] == VAULT_ENTRY


def test_keychain_fehler_loescht_nichts(legacy, monkeypatch):
    """Scheitert der Keychain, bleibt der Klartext-Token liegen — sonst waere er weg."""
    mod = legacy

    def boom(tok):
        raise RuntimeError("gesperrt")
    monkeypatch.setattr(mod, "keychain_set", boom)
    mod.migrate_secrets()
    assert os.path.exists(mod.LEGACY_TOKEN_FILE)
    assert "Authorization" in (pathlib.Path(mod.CLAUDE_JSON).read_text())


def test_frische_installation_ist_still(legacy, tmp_path, capsys):
    mod = legacy
    mod.migrate_secrets()
    capsys.readouterr()
    (tmp_path / ".claude.json").unlink()
    (tmp_path / ".claude" / "settings.json").unlink()
    assert mod.migrate_secrets() == [] and capsys.readouterr().out == ""
