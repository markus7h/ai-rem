"""OAuth-Resource-Server: JWTs eines externen IdP neben den Static-Tokens.
Läuft im SUBPROZESS mit eigener Temp-DB und Fake-JWKS (kein Netz)."""
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ISS = "https://idp.example"
AUD = "https://ai-rem.example/mcp"


def _scenario() -> None:
    tmp = tempfile.mkdtemp(prefix="ai-rem-oauth-")
    os.environ.update({
        "LADYBUG_DB_PATH": os.path.join(tmp, "kg.db"), "BACKUP_DIR": os.path.join(tmp, "b"),
        "EMBED_ENABLED": "0", "AI_REM_API_TOKEN": "admin-token", "AI_REM_ADMIN_USER": "markus",
        "AI_REM_OAUTH_ISSUER": ISS, "KG_PUBLIC_URL": "https://ai-rem.example",
        "AI_REM_OAUTH_USERS": "markus,anna"})
    sys.path.insert(0, ROOT)
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    import server
    from starlette.testclient import TestClient

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class FakeJWKS:
        def get_signing_key_from_jwt(self, _tok):
            return type("K", (), {"key": key.public_key()})()

    server._jwks_client = FakeJWKS()

    def tok(k=key, alg="RS256", **over):
        c = {"iss": ISS, "aud": AUD, "exp": int(time.time()) + 300,
             "preferred_username": "anna", **over}
        return jwt.encode(c, k, algorithm=alg)

    u = server._user_for_token
    assert u(tok()) == "anna"
    assert u(tok(preferred_username="Markus")) == "markus"            # Claim normalisiert
    assert u(tok(aud="https://other/mcp")) == ""                       # fremde Audience
    assert u(tok(iss="https://evil")) == ""
    assert u(tok(exp=int(time.time()) - 3600)) == ""                   # abgelaufen
    assert u(tok(k=other)) == ""                                       # falsche Signatur
    assert u(tok(preferred_username="eve")) == ""                      # nicht in Allowlist
    assert u(tok(preferred_username="../x")) == ""                     # ungültiger Name
    assert u(jwt.encode({"iss": ISS, "aud": AUD, "exp": int(time.time()) + 300,
                         "preferred_username": "anna"}, None, algorithm="none")) == ""
    assert u(jwt.encode({"iss": ISS, "aud": AUD, "exp": int(time.time()) + 300,
                         "preferred_username": "anna"}, "s" * 32, algorithm="HS256")) == ""
    assert u("admin-token") == "markus"                                # Static bleibt

    app = server.AuthMiddleware(server.mcp.http_app())
    xff = {"X-Forwarded-For": "10.0.0.9"}
    c = TestClient(app, base_url="https://testserver", headers=xff)
    r = c.get("/.well-known/oauth-protected-resource")
    assert r.status_code == 200 and r.json() == {
        "resource": AUD, "authorization_servers": [ISS], "bearer_methods_supported": ["header"]}
    r = c.get("/api/tasks")
    assert r.status_code == 401
    assert 'resource_metadata="https://ai-rem.example/.well-known/oauth-protected-resource"' \
        in r.headers["www-authenticate"]
    r = c.get("/api/tasks", headers={"Authorization": f"Bearer {tok()}"})
    assert r.status_code == 200, r.text
    assert c.get("/export", headers={"Authorization": f"Bearer {tok()}"}).status_code == 403
    print("OK")


def test_oauth():
    r = subprocess.run([sys.executable, __file__], capture_output=True, text=True,
                       env={**os.environ, "EMBED_ENABLED": "0"})
    assert r.returncode == 0, f"STDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    assert "OK" in r.stdout


def test_oauth_off_by_default():
    """Ohne Issuer: kein JWT-Pfad, Discovery 404, 401 ohne WWW-Authenticate."""
    code = ("import os,sys,tempfile;t=tempfile.mkdtemp();"
            "os.environ.update(LADYBUG_DB_PATH=t+'/kg.db',BACKUP_DIR=t+'/b',EMBED_ENABLED='0',"
            "AI_REM_API_TOKEN='admin-token');sys.path.insert(0,%r);import server;"
            "from starlette.testclient import TestClient;"
            "c=TestClient(server.AuthMiddleware(server.mcp.http_app()),"
            "headers={'X-Forwarded-For':'10.0.0.9'});"
            "assert c.get('/.well-known/oauth-protected-resource').status_code==404;"
            "r=c.get('/api/tasks');assert r.status_code==401 and 'www-authenticate' not in r.headers;"
            "assert server._user_for_token('a.b.c')=='';print('OK')") % ROOT
    env = {k: v for k, v in os.environ.items() if not k.startswith("AI_REM_OAUTH")}
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr


if __name__ == "__main__":
    _scenario()
