"""`ai-rem pair` ohne GUI (scripts/setup.py): ueber SSH kein webbrowser.open,
AI_REM_TOKEN ersetzt die Kopplung, record_client persistiert die Backend-Wahl
und selftest_token meldet einen abgelehnten Token ohne sys.exit."""
import importlib.machinery
import importlib.util
import io
import json
import pathlib
import time
import urllib.error
import webbrowser

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
VERIFY_URL = "http://ai-rem.test/pair?code=K7QF-2M9X"


def _load():
    loader = importlib.machinery.SourceFileLoader("ai_rem_setup_headless", str(ROOT / "scripts" / "setup.py"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture
def setup(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "KG_URL", "http://ai-rem.test")
    monkeypatch.setattr(mod, "AIREM_CFG_DIR", str(tmp_path))
    monkeypatch.setattr(mod, "CLIENT_JSON", str(tmp_path / "client.json"))
    monkeypatch.setattr(mod, "keychain_get", lambda: "")
    monkeypatch.setattr(mod, "keychain_backend", lambda: "Fake-Keychain")
    for var in ("SSH_CONNECTION", "SSH_TTY", "DISPLAY", "WAYLAND_DISPLAY",
                "AI_REM_KEYCHAIN_BACKEND", "AI_REM_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    return mod


def _fake_pairing(mod, monkeypatch):
    """pair/start antwortet, poll ist sofort approved — interessant ist nur der Browser."""
    def post(url, body, timeout=10):
        if url.endswith("/api/pair/start"):
            return {"code": "K7QF-2M9X", "verify_url": VERIFY_URL, "pair_id": "p1", "expires_in": 60}
        return {"state": "approved", "ai_rem_token": "tok-neu"}
    monkeypatch.setattr(mod, "_post_json", post)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    return opened


def test_ssh_oeffnet_keinen_browser(setup, monkeypatch, capsys):
    monkeypatch.setenv("SSH_CONNECTION", "10.0.0.1 50000 10.0.0.2 22")
    opened = _fake_pairing(setup, monkeypatch)
    assert setup.pair_device().get("ai_rem_token") == "tok-neu"
    assert opened == []
    out = capsys.readouterr().out
    assert "SSH-Sitzung: Browser wird nicht geoeffnet" in out and VERIFY_URL in out


def test_mit_display_oeffnet_browser(setup, monkeypatch, capsys):
    monkeypatch.setenv("DISPLAY", ":0")
    opened = _fake_pairing(setup, monkeypatch)
    setup.pair_device()
    assert opened == [VERIFY_URL]
    assert "SSH-Sitzung" not in capsys.readouterr().out


def test_record_client_persistiert_env(setup, monkeypatch):
    def backend():
        setup.record_client("http://ai-rem.test/mcp", ["claude"], {})
        return json.loads(pathlib.Path(setup.CLIENT_JSON).read_text())["keychain_backend"]
    assert backend() == "auto"  # frisch ohne Env
    monkeypatch.setenv("AI_REM_KEYCHAIN_BACKEND", "file")
    assert backend() == "file"
    monkeypatch.delenv("AI_REM_KEYCHAIN_BACKEND")
    assert backend() == "file"  # bleibt — Env war einmalig noetig


def test_pair_only_nimmt_env_token(setup, monkeypatch):
    monkeypatch.setenv("AI_REM_TOKEN", "tok-env")
    monkeypatch.setattr(setup, "pair_device", lambda: pytest.fail("pair_device darf nicht laufen"))
    monkeypatch.setattr(setup, "install_cli", lambda: "/x/bin/ai-rem")
    monkeypatch.setattr(setup, "link_cli", lambda p: None)
    monkeypatch.setattr(setup, "migrate_secrets", lambda: None)
    stored = []
    monkeypatch.setattr(setup, "store_token", lambda tok: stored.append(tok) or True)
    setup.pair_only()
    assert stored == ["tok-env"]


def _http_error(code):
    def get(url, timeout=10, insecure=False, headers=None):
        assert url == "http://ai-rem.test/api/client-config"
        assert headers == {"Authorization": "Bearer tok-alt"}
        if code:
            raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))
        return b"{}"
    return get


def test_selftest_token_401_nennt_backend_ohne_exit(setup, monkeypatch, capsys):
    monkeypatch.setattr(setup, "keychain_get", lambda: "tok-alt")
    monkeypatch.setattr(setup, "http_get", _http_error(401))
    setup.selftest_token()
    out = capsys.readouterr().out
    assert "✗ Selbsttest" in out and "401" in out and "Fake-Keychain" in out and "ai-rem pair" in out


def test_selftest_token_ok_und_alter_server(setup, monkeypatch, capsys):
    monkeypatch.setattr(setup, "keychain_get", lambda: "tok-alt")
    monkeypatch.setattr(setup, "http_get", _http_error(0))
    setup.selftest_token()
    assert "✓ Selbsttest: Token (Fake-Keychain) vom Server akzeptiert" in capsys.readouterr().out
    monkeypatch.setattr(setup, "http_get", _http_error(404))
    setup.selftest_token()
    assert capsys.readouterr().out == ""  # Server < 1.7: still


def test_selftest_token_ohne_token(setup, capsys):
    setup.selftest_token()
    out = capsys.readouterr().out
    assert "kein Token im Fake-Keychain" in out and "AI_REM_TOKEN=<token> ai-rem pair" in out
