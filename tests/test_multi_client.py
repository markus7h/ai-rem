"""Multi-Client: opencode-Transcripts, Setup-Ziel opencode (stdio-Wrapper statt
{file:}-Secrets), CLI-Drift je Ziel, Token-Suche. Alles ohne Netz — Downloads
werden auf Stubs umgebogen, der Keychain ist ein Dict."""
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import stat
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.extractor import flatten_transcript  # noqa: E402


def _load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture
def setup(tmp_path, monkeypatch):
    mod = _load("ai_rem_setup_mc", ROOT / "scripts" / "setup.py")
    cfg = tmp_path / "config"
    monkeypatch.setattr(mod, "KG_URL", "http://kg.test")
    monkeypatch.setattr(mod, "CLAUDE_HOME", str(tmp_path / "claude-fehlt"))
    monkeypatch.setattr(mod, "AIREM_CFG_DIR", str(cfg / "ai-rem"))
    monkeypatch.setattr(mod, "CLIENT_JSON", str(cfg / "ai-rem" / "client.json"))
    monkeypatch.setattr(mod, "LEGACY_TOKEN_FILE", str(cfg / "ai-rem" / "token"))
    monkeypatch.setattr(mod, "LEGACY_VAULT_TOKEN_FILE", str(cfg / "ai-rem" / "vault.token"))
    monkeypatch.setattr(mod, "LEGACY_VAULT_ENV", str(tmp_path / "claude-fehlt" / "ai-rem-vault.env"))
    monkeypatch.setattr(mod, "SNIPPET_DIR", str(cfg / "ai-rem" / "snippets"))
    monkeypatch.setattr(mod, "OPENCODE_DIR", str(cfg / "opencode"))
    monkeypatch.setattr(mod, "LOCAL_CLI", str(tmp_path / ".local" / "share" / "ai-rem" / "bin" / "ai-rem"))
    monkeypatch.setattr(mod, "HOME", str(tmp_path))
    monkeypatch.setattr(mod, "IS_WIN", False)
    monkeypatch.delenv("AI_REM_STATE_DIR", raising=False)
    monkeypatch.delenv("NODE_EXTRA_CA_CERTS", raising=False)
    # Keychain als Dict statt echtem Backend.
    store = {}
    monkeypatch.setattr(mod, "keychain_get", lambda: store.get("t", ""))
    monkeypatch.setattr(mod, "keychain_set", lambda tok: store.__setitem__("t", tok))
    monkeypatch.setattr(mod, "keychain_delete", lambda: store.pop("t", None))
    monkeypatch.setattr(mod, "keychain_backend", lambda: "Fake-Keychain")
    mod._store = store

    def fake_fetch(url, dst):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        pathlib.Path(dst).write_text("// " + url)
        return True
    monkeypatch.setattr(mod, "fetch_to", fake_fetch)
    return mod


# ── Transcript ────────────────────────────────────────────────────────────────

def test_opencode_transcript_wird_geflacht(tmp_path):
    doc = {"format": "opencode", "session": "ses_1", "messages": [
        {"info": {"role": "user"}, "parts": [{"type": "text", "text": "Baue X"},
                                             {"type": "text", "text": "intern", "synthetic": True}]},
        {"info": {"role": "assistant"}, "parts": [
            {"type": "reasoning", "text": "erst lesen"},
            {"type": "tool", "tool": "bash", "state": {"output": "GEHEIM"}},
            {"type": "text", "text": "Erledigt"}]},
    ]}
    p = tmp_path / "opencode-ses_1.json"
    p.write_text(json.dumps(doc))
    flat = flatten_transcript(p)
    assert flat.startswith("USER: Baue X")
    assert "[thinking] erst lesen" in flat and "ASSISTANT:" in flat and "Erledigt" in flat
    assert "GEHEIM" not in flat, "Tool-Output gehoert nicht ins Transcript"
    assert "intern" not in flat, "synthetische Parts gehoeren nicht ins Transcript"


def test_claude_jsonl_bleibt_unveraendert(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps({"type": "user", "message": {"content": "hallo"}}) + "\n")
    assert flatten_transcript(p) == "USER: hallo"


# ── Setup: opencode ───────────────────────────────────────────────────────────

def test_opencode_json_wird_gemergt_nicht_ersetzt(setup, tmp_path):
    oc = pathlib.Path(setup.OPENCODE_DIR)
    oc.mkdir(parents=True)
    (oc / "opencode.json").write_text(json.dumps(
        {"provider": {"litellm": {"x": 1}}, "mcp": {"fremd": {"type": "remote", "url": "u"}},
         "instructions": ["eigene.md"]}))
    setup.install_opencode("", "", "")
    setup.install_opencode("", "", "")  # idempotent

    data = json.loads((oc / "opencode.json").read_text())
    assert data["provider"] == {"litellm": {"x": 1}}, "Provider verloren"
    assert "fremd" in data["mcp"], "fremder MCP-Server verloren"
    ai = data["mcp"]["ai-rem"]
    assert ai["type"] == "local" and ai["command"] == [setup.LOCAL_CLI, "mcp-proxy"]
    assert ai["environment"] == {"AI_REM_CLIENT": "opencode"}
    assert "headers" not in ai and "{file:" not in json.dumps(data), "kein Token-Verweis mehr"
    fallbacks = [i for i in data["instructions"] if i.endswith("fallback.md")]
    assert data["instructions"][0] == "eigene.md" and len(fallbacks) == 1
    assert (oc / "opencode.json.pre-airem.bak").exists()
    assert (oc / "plugin" / "ai-rem.ts").exists()
    assert (oc / "command" / "memory-cleanup.md").exists()
    agents = (oc / "AGENTS.md").read_text()
    assert agents.count(setup.AGENTS_BEGIN) == 1, "AGENTS.md-Block doppelt"
    assert "memory_get_context()" in agents


def test_jsonc_wird_nicht_umgeschrieben(setup):
    oc = pathlib.Path(setup.OPENCODE_DIR)
    oc.mkdir(parents=True)
    raw = '{\n  // Kommentar\n  "model": "x"\n}\n'
    (oc / "opencode.jsonc").write_text(raw)
    assert setup.merge_opencode_json({"ai-rem": {}}) is False
    assert (oc / "opencode.jsonc").read_text() == raw
    assert (pathlib.Path(setup.SNIPPET_DIR) / "opencode-mcp.json").exists()


def test_stdio_server_nutzen_node_aus_dem_path(setup, monkeypatch, tmp_path):
    """mykeyvault laeuft ueber `ai-rem vault-mcp` (holt Vault-Zugang vom Server);
    node muss trotzdem da sein, sonst entsteht ein toter Eintrag. tools bleibt
    direkt node (kein Secret)."""
    monkeypatch.setattr(setup.shutil, "which", lambda n: "/opt/homebrew/bin/node" if n == "node" else None)
    e = setup.opencode_mcp_entries("/v/index.js", "/t/index.js", "http://reg")
    assert e["ai-rem"]["type"] == "local" and e["ai-rem"]["command"] == [setup.LOCAL_CLI, "mcp-proxy"]
    assert e["mykeyvault"]["command"] == [setup.LOCAL_CLI, "vault-mcp"]
    assert "environment" not in e["mykeyvault"], "kein Vault-Token/-URL in der opencode.json"
    assert "{file:" not in json.dumps(e)
    assert e["tools"]["command"] == ["/opt/homebrew/bin/node", "/t/index.js"]
    monkeypatch.setenv("NODE_EXTRA_CA_CERTS", "/ca.pem")
    assert setup.opencode_mcp_entries("/v", "", "")["mykeyvault"]["environment"] == {"NODE_EXTRA_CA_CERTS": "/ca.pem"}
    monkeypatch.setattr(setup.shutil, "which", lambda n: None)
    assert set(setup.opencode_mcp_entries("/v", "/t", "r")) == {"ai-rem"}, \
        "ohne node darf kein kaputter stdio-Eintrag entstehen"


def test_client_json_ohne_secrets(setup):
    """client.json traegt Endpoint, Ziele, vault_entry, Keychain-Backend — nie
    Token, LLM-Key oder Token-Pfad. Alte Felder werden beim Schreiben entfernt."""
    pathlib.Path(setup.AIREM_CFG_DIR).mkdir(parents=True)
    pathlib.Path(setup.CLIENT_JSON).write_text(json.dumps(
        {"targets": ["claude"], "llm_api_key": "sk-alt", "llm_url": "http://llm",  # pragma: allowlist secret
         "token_file": "/alt/token"}))
    setup.record_client("https://kg.test/mcp", ["opencode"], {"ollama_url": "http://llm", "llm_api_key": "x"},
                        vault_url="https://vault", vault_entry="/v/mcp/dist/index.js")
    setup.record_client("https://kg.test/mcp", ["claude"], {})  # vault_entry=None: bleibt
    cfg = json.loads(pathlib.Path(setup.CLIENT_JSON).read_text())
    assert cfg["targets"] == ["claude", "opencode"], "Ziele verdraengt statt ergaenzt"
    assert cfg["vault_entry"] == "/v/mcp/dist/index.js" and cfg["vault_url"] == "https://vault"
    assert cfg["keychain"] == "Fake-Keychain" and cfg["endpoint"] == "https://kg.test/mcp"
    assert not {"llm_api_key", "llm_url", "token_file"} & set(cfg)
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(setup.CLIENT_JSON).st_mode) == 0o600
    assert not pathlib.Path(setup.LEGACY_TOKEN_FILE).exists(), "keine Token-Datei mehr"


def test_uninstall_opencode(setup):
    setup.install_opencode("", "", "")
    setup.record_client("https://kg.test/mcp", ["opencode", "claude"], {})
    setup._store["t"] = "tok"
    setup.uninstall(["opencode"])
    oc = pathlib.Path(setup.OPENCODE_DIR)
    data = json.loads((oc / "opencode.json").read_text())
    assert "ai-rem" not in data["mcp"] and "instructions" not in data
    assert not (oc / "plugin" / "ai-rem.ts").exists()
    assert setup.AGENTS_BEGIN not in (oc / "AGENTS.md").read_text()
    assert json.loads(pathlib.Path(setup.CLIENT_JSON).read_text())["targets"] == ["claude"]
    assert setup._store == {"t": "tok"}, "Token bleibt, solange ein Ziel uebrig ist"
    setup.uninstall(["claude"])
    assert setup._store == {}, "letztes Ziel weg => Token aus dem Keychain"


def test_generic_snippets_ohne_bearer(setup):
    setup.install_generic("https://kg.test/mcp")
    snip = pathlib.Path(setup.SNIPPET_DIR)
    for name in ("gemini-settings.json", "cursor-mcp.json"):
        d = json.loads((snip / name).read_text())
        assert d["mcpServers"]["ai-rem"] == {"command": setup.LOCAL_CLI, "args": ["mcp-proxy"]}
    assert "$(ai-rem token)" in (snip / "codex-config.toml").read_text()


def test_parse_args(setup, monkeypatch):
    monkeypatch.setattr(setup.shutil, "which", lambda n: "/x" if n == "opencode" else None)
    assert setup.parse_args(["--client", "auto"])["targets"] == ["opencode"]
    a = setup.parse_args(["--client=claude,opencode", "--update", "-y"])
    assert (a["update"], a["uninstall"], a["targets"], a["yes"], a["pair"]) == \
        (True, False, ["claude", "opencode"], True, False)
    monkeypatch.setattr(setup.shutil, "which", lambda n: None)
    assert setup.detect_targets() == ["generic"]


# ── CLI: Drift nur fuer installierte Ziele ───────────────────────────────────

def test_cli_drift_beachtet_ziele(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    cli = _load("ai_rem_cli_mc", ROOT / "bin" / "ai-rem")
    # lib.mcp_client legt CLIENT_JSON beim ersten Import fest (echtes HOME, ggf.
    # schon durch einen frueheren Test) — load_client_cfg() liest sonst die
    # client.json der Workstation, und deren targets schlagen die Datei-Erkennung.
    mc = sys.modules[cli.load_client_cfg.__module__]
    monkeypatch.setattr(mc, "CLIENT_JSON", str(tmp_path / "cfg" / "ai-rem" / "client.json"))
    body = b"x"
    sha = hashlib.sha256(body).hexdigest()
    share = tmp_path / ".local" / "share" / "ai-rem" / "bin"
    share.mkdir(parents=True)
    (share / "ai-rem").write_bytes(body)
    shim = tmp_path / ".local" / "bin"
    shim.mkdir(parents=True)
    (shim / "ai-rem").write_bytes(body)
    manifest = {"files": {"bin/ai-rem": sha, "hooks/system-check.py": sha,
                          "opencode/plugin/ai-rem.ts": sha}}

    assert [r for r, _ in cli._drift(manifest, ["opencode"])] == ["opencode/plugin/ai-rem.ts"]
    assert [r for r, _ in cli._drift(manifest, ["claude"])] == ["hooks/system-check.py"]
    plug = tmp_path / "cfg" / "opencode" / "plugin"
    plug.mkdir(parents=True)
    (plug / "ai-rem.ts").write_bytes(body)
    assert cli._drift(manifest, ["opencode"]) == []
    assert cli._installed_targets() == ["opencode"], "Ziel nicht an der Plugin-Datei erkannt"


# ── Token-Suche ───────────────────────────────────────────────────────────────

def test_token_reihenfolge_env_keychain_legacy(tmp_path, monkeypatch, capsys):
    """Env > Keychain > Klartext-Altlasten (token_file, dann ~/.claude.json-Header).
    Die Altlasten werden nur gelesen und einmal pro Prozess gemeldet; der alte
    Vault-Fallback existiert nicht mehr."""
    import lib.mcp_client as mc
    from lib import keychain
    tf = tmp_path / "token"
    tf.write_text("aus-datei\n")
    cj = tmp_path / "client.json"
    cj.write_text(json.dumps({"token_file": str(tf), "endpoint": "https://kg/mcp"}))
    monkeypatch.setattr(mc, "CLIENT_JSON", str(cj))
    monkeypatch.setattr(mc, "_legacy_warned", False)
    monkeypatch.delenv("AI_REM_TOKEN", raising=False)
    cdir = tmp_path / "claude"
    cdir.mkdir()
    (cdir / ".claude.json").write_text(json.dumps({"mcpServers": {
        "ai-rem": {"headers": {"Authorization": "Bearer aus-header"}},  # pragma: allowlist secret
        "mykeyvault": {"env": {"VAULT_API_URL": "http://vault", "VAULT_API_TOKEN": "vt"}}}}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cdir))
    monkeypatch.setattr(keychain, "get", lambda: "")
    monkeypatch.setattr(keychain, "backend_name", lambda: "Fake")

    assert mc._resolve_token_with_source() == ("aus-datei", "Klartext-Legacy (%s)" % tf)
    assert "Klartext" in capsys.readouterr().err
    assert mc._default_endpoint() == "https://kg/mcp"
    tf.unlink()
    assert mc._resolve_token_with_source() == ("aus-header", "Klartext-Legacy (~/.claude.json)")
    assert capsys.readouterr().err == "", "Legacy-Hinweis nur einmal pro Prozess"

    monkeypatch.setattr(keychain, "get", lambda: "aus-keychain")
    assert mc._resolve_token_with_source() == ("aus-keychain", "Keychain (Fake)")
    monkeypatch.setenv("AI_REM_TOKEN", "env")
    assert mc._resolve_token() == "env", "Env muss gewinnen"

    # Nichts gefunden: leer statt Vault-Roundtrip (zirkulaer seit 1.7).
    monkeypatch.delenv("AI_REM_TOKEN")
    monkeypatch.setattr(keychain, "get", lambda: "")
    (cdir / ".claude.json").write_text(json.dumps({"mcpServers": {
        "mykeyvault": {"env": {"VAULT_API_URL": "http://vault", "VAULT_API_TOKEN": "vt"}}}}))
    monkeypatch.setattr(mc.urllib.request, "urlopen",
                        lambda *a, **kw: pytest.fail("kein Netz bei der Token-Suche"))
    assert mc._resolve_token_with_source() == ("", "")
