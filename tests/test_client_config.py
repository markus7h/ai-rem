"""/api/client-config liefert Secrets nur mit Token; /setup-config liefert keine mehr.

Hintergrund: Clients sollen nur noch den ai-rem-Token speichern (OS-Keychain) und
LLM-Key/Vault-Token pro Lauf abholen. Dafuer muss der neue Endpoint hinter der Auth
liegen — und die oeffentliche /setup-config darf den llm_api_key nicht mehr
mitliefern, sonst waere der Keychain-Umbau wirkungslos.

Laeuft im SUBPROZESS mit eigener Temp-DB und eigenem Env (pytest-Modulcache teilt
server.py sonst zwischen Tests, und AI_REM_LLM_API_KEY muss VOR dem Import stehen).
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LLM_KEY = "cfg-test-key"  # pragma: allowlist secret
TOKEN = "ci-dummy-token"  # pragma: allowlist secret


def _scenario() -> None:
    tmp = tempfile.mkdtemp(prefix="ai-rem-client-config-")
    os.environ["LADYBUG_DB_PATH"] = os.path.join(tmp, "kg.db")
    os.environ["BACKUP_DIR"] = os.path.join(tmp, "backups")
    os.environ["EMBED_ENABLED"] = "0"
    os.environ["AI_REM_API_TOKEN"] = TOKEN
    os.environ["AI_REM_LLM_API_KEY"] = LLM_KEY
    os.environ.pop("AI_REM_PAIR_VAULT_TOKEN", None)
    sys.path.insert(0, ROOT)

    import server
    from starlette.testclient import TestClient

    # Fixture statt Datei auf Platte: auf einer Dev-Maschine liegt evtl. eine echte
    # setup-config.json neben server.py, deren Inhalt der Test nicht kennen darf.
    fixture = {
        "ollama_url": "http://router.test:11437",
        "_comment_llm_api_key": "Doku zum Key — kein Secret, darf public bleiben",
        "llm_api_key": "leak-me-not",  # pragma: allowlist secret
        "foo_token": "leak-me-neither",  # pragma: allowlist secret
        "mcp_register": {"mykeyvault": {"vault_url": "http://vault.test:8223"}},
    }
    server._load_setup_cfg = lambda: dict(fixture)

    # _public_setup_cfg direkt: nur Secret-Suffixe fliegen raus, _comment_* bleibt.
    original = dict(fixture)
    public = server._public_setup_cfg()
    assert set(public) == {"ollama_url", "_comment_llm_api_key", "mcp_register"}, public
    assert fixture == original, "Original-dict wurde mutiert"

    app = server.AuthMiddleware(server.mcp.http_app())
    # X-Forwarded-For entzieht den Loopback-Trust — sonst waere der TestClient
    # (127.0.0.1) tokenfrei und der 401-Fall gar nicht pruefbar.
    anon = TestClient(app, base_url="https://testserver", headers={"X-Forwarded-For": "10.0.0.9"})
    auth = TestClient(app, base_url="https://testserver",
                      headers={"X-Forwarded-For": "10.0.0.9", "Authorization": "Bearer " + TOKEN})

    assert anon.get("/api/client-config").status_code == 401, "client-config ohne Token abrufbar"
    assert anon.get("/api/client-config", headers={"Authorization": "Bearer falsch"}).status_code == 401

    r = auth.get("/api/client-config")
    assert r.status_code == 200, (r.status_code, r.text)
    body = r.json()
    assert set(body) >= {"version", "llm_url", "llm_api_key", "vault_url", "vault_token"}, body
    assert body["version"] == server.VERSION
    assert body["llm_api_key"] == LLM_KEY, body
    assert body["llm_url"] == server.AI_REM_OLLAMA_URL
    assert body["vault_url"] == "http://vault.test:8223"
    assert body["vault_token"] == "", "Vault-Token ohne AI_REM_PAIR_VAULT_TOKEN nicht leer"

    # Oeffentliche /setup-config: erreichbar, aber ohne Secrets.
    r = anon.get("/setup-config")
    assert r.status_code == 200, r.status_code
    cfg = r.json()
    assert "llm_api_key" not in cfg, cfg
    assert "foo_token" not in cfg, cfg
    assert "_comment_llm_api_key" in cfg, cfg
    assert cfg["ollama_url"] == "http://router.test:11437"
    assert "leak-me" not in r.text
    print("OK")


def test_client_config():
    r = subprocess.run([sys.executable, __file__], capture_output=True, text=True,
                       env={**os.environ, "EMBED_ENABLED": "0"})
    assert r.returncode == 0, f"Szenario fehlgeschlagen:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    assert "OK" in r.stdout


if __name__ == "__main__":
    _scenario()
