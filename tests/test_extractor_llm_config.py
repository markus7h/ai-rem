"""configure_llm: LLM-URL/-Key kommen pro Lauf vom Server (/api/client-config),
Env gewinnt, client.json-Felder zaehlen nicht mehr, ein alter Server (kein
Endpoint) laesst den Key leer statt zu crashen."""
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib import extractor  # noqa: E402

KEY = "router-key-123"  # pragma: allowlist secret


class FakeClient:
    def __init__(self, cfg=None, too_old=False):
        self._cfg = cfg if cfg is not None else {}
        self.server_too_old = too_old
        self.calls = 0

    def client_config(self):
        self.calls += 1
        return self._cfg


def _fresh(monkeypatch):
    for var in ("AI_REM_LLAMA_URL", "AI_REM_OLLAMA_URL", "AI_REM_LLM_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(extractor, "_LLM", {"url": extractor.LLAMA_URL, "key": extractor.LLM_API_KEY})
    monkeypatch.setattr(extractor, "_old_server_warned", False)


def test_server_config_setzt_url_und_key(monkeypatch):
    _fresh(monkeypatch)
    fc = FakeClient({"llm_url": "http://router.test:11437/", "llm_api_key": KEY})
    assert extractor.configure_llm(fc) == {"url": "http://router.test:11437", "key": KEY}
    assert extractor._llm_headers()["Authorization"] == "Bearer " + KEY
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append((req.full_url, req.get_header("Authorization")))
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode())
    monkeypatch.setattr(extractor.urllib.request, "urlopen", fake_urlopen)
    extractor.call_llm("t", "m", "s")
    assert seen == [("http://router.test:11437/v1/chat/completions", "Bearer " + KEY)]


def test_env_gewinnt_und_spart_den_roundtrip(monkeypatch):
    _fresh(monkeypatch)
    monkeypatch.setenv("AI_REM_OLLAMA_URL", "http://env.test:1")
    monkeypatch.setenv("AI_REM_LLM_API_KEY", "env-key")
    fc = FakeClient({"llm_url": "http://router.test", "llm_api_key": KEY})
    assert extractor.configure_llm(fc) == {"url": "http://env.test:1", "key": "env-key"}
    assert fc.calls == 0, "mit vollstaendiger Env darf der Server nicht gefragt werden"
    # Nur der Key per Env → URL kommt trotzdem vom Server.
    monkeypatch.delenv("AI_REM_OLLAMA_URL")
    assert extractor.configure_llm(fc) == {"url": "http://router.test", "key": "env-key"}


def test_alter_server_ohne_key_warnt_einmal_und_faellt_zurueck(monkeypatch, capsys):
    _fresh(monkeypatch)
    fc = FakeClient({}, too_old=True)
    assert extractor.configure_llm(fc) == {"url": "http://mystorage.lan:11437", "key": ""}
    assert "Authorization" not in extractor._llm_headers()
    extractor.configure_llm(fc)
    err = capsys.readouterr().err
    assert err.count("Server < 1.7") == 1
    # Ohne Server-Objekt (z.B. Tests, die call_llm direkt nutzen) ebenfalls kein Crash.
    assert extractor.configure_llm(None)["key"] == ""
