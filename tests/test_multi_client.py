"""Multi-Client: opencode-Transcripts, Setup-Ziel opencode, CLI-Drift je Ziel,
Token-Suche. Alles ohne Netz — Downloads werden auf Stubs umgebogen."""
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
    monkeypatch.setattr(mod, "TOKEN_FILE", str(cfg / "ai-rem" / "token"))
    monkeypatch.setattr(mod, "VAULT_TOKEN_FILE", str(cfg / "ai-rem" / "vault.token"))
    monkeypatch.setattr(mod, "SNIPPET_DIR", str(cfg / "ai-rem" / "snippets"))
    monkeypatch.setattr(mod, "OPENCODE_DIR", str(cfg / "opencode"))
    monkeypatch.setattr(mod, "HOME", str(tmp_path))
    monkeypatch.delenv("AI_REM_STATE_DIR", raising=False)

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
    setup.install_opencode("https://kg.test/mcp", "", "", "", "")
    setup.install_opencode("https://kg.test/mcp", "", "", "", "")  # idempotent

    data = json.loads((oc / "opencode.json").read_text())
    assert data["provider"] == {"litellm": {"x": 1}}, "Provider verloren"
    assert "fremd" in data["mcp"], "fremder MCP-Server verloren"
    ai = data["mcp"]["ai-rem"]
    assert ai["type"] == "remote" and ai["url"] == "https://kg.test/mcp"
    assert ai["headers"]["Authorization"] == "Bearer {file:%s}" % setup.TOKEN_FILE
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
    monkeypatch.setattr(setup.shutil, "which", lambda n: "/opt/homebrew/bin/node" if n == "node" else None)
    setup.write_secret(setup.VAULT_TOKEN_FILE, "vt")
    e = setup.opencode_mcp_entries("u", "https://vault", "/v/index.js", "/t/index.js", "http://reg")
    assert e["mykeyvault"]["command"] == ["/opt/homebrew/bin/node", "/v/index.js"]
    assert e["mykeyvault"]["environment"]["VAULT_API_TOKEN"] == "{file:%s}" % setup.VAULT_TOKEN_FILE
    assert e["tools"]["command"] == ["/opt/homebrew/bin/node", "/t/index.js"]
    monkeypatch.setattr(setup.shutil, "which", lambda n: None)
    assert set(setup.opencode_mcp_entries("u", "v", "/v", "/t", "r")) == {"ai-rem"}, \
        "ohne node darf kein kaputter stdio-Eintrag entstehen"


def test_token_datei_und_client_json(setup):
    setup.record_client("https://kg.test/mcp", ["opencode"], {"ollama_url": "http://llm"}, token="tok")
    setup.record_client("https://kg.test/mcp", ["claude"], {})
    mode = stat.S_IMODE(os.stat(setup.TOKEN_FILE).st_mode)
    if os.name != "nt":
        assert mode == 0o600, oct(mode)
    cfg = json.loads(pathlib.Path(setup.CLIENT_JSON).read_text())
    assert cfg["targets"] == ["opencode", "claude"], "Ziele verdraengt statt ergaenzt"
    assert cfg["token_file"] == setup.TOKEN_FILE and cfg["llm_url"] == "http://llm"


def test_uninstall_opencode(setup):
    setup.install_opencode("https://kg.test/mcp", "", "", "", "")
    setup.record_client("https://kg.test/mcp", ["opencode", "claude"], {})
    setup.uninstall(["opencode"])
    oc = pathlib.Path(setup.OPENCODE_DIR)
    data = json.loads((oc / "opencode.json").read_text())
    assert "ai-rem" not in data["mcp"] and "instructions" not in data
    assert not (oc / "plugin" / "ai-rem.ts").exists()
    assert setup.AGENTS_BEGIN not in (oc / "AGENTS.md").read_text()
    assert json.loads(pathlib.Path(setup.CLIENT_JSON).read_text())["targets"] == ["claude"]


def test_parse_args(setup, monkeypatch):
    monkeypatch.setattr(setup.shutil, "which", lambda n: "/x" if n == "opencode" else None)
    assert setup.parse_args(["--client", "auto"])["targets"] == ["opencode"]
    assert setup.parse_args(["--client=claude,opencode", "--update"]) == \
        {"update": True, "uninstall": False, "targets": ["claude", "opencode"]}
    monkeypatch.setattr(setup.shutil, "which", lambda n: None)
    assert setup.detect_targets() == ["generic"]


# ── CLI: Drift nur fuer installierte Ziele ───────────────────────────────────

def test_cli_drift_beachtet_ziele(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    cli = _load("ai_rem_cli_mc", ROOT / "bin" / "ai-rem")
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

def test_token_aus_client_json_vor_claude_json(tmp_path, monkeypatch):
    import lib.mcp_client as mc
    tf = tmp_path / "token"
    tf.write_text("aus-datei\n")
    cj = tmp_path / "client.json"
    cj.write_text(json.dumps({"token_file": str(tf), "endpoint": "https://kg/mcp"}))
    monkeypatch.setattr(mc, "CLIENT_JSON", str(cj))
    monkeypatch.delenv("AI_REM_TOKEN", raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "nix"))
    assert mc._resolve_token() == "aus-datei"
    assert mc._default_endpoint() == "https://kg/mcp"
    monkeypatch.setenv("AI_REM_TOKEN", "env")
    assert mc._resolve_token() == "env", "Env muss gewinnen"
