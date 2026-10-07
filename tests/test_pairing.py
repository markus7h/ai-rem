"""Geraete-Kopplung: der Installer bekommt den Token nur nach Freigabe durch einen
eingeloggten User, genau einmal, und nie ohne Freigabe. Laeuft im SUBPROZESS mit
eigener Temp-DB (pytest-Modulcache teilt server.py sonst zwischen Tests)."""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _scenario() -> None:
    tmp = tempfile.mkdtemp(prefix="ai-rem-pair-")
    os.environ["LADYBUG_DB_PATH"] = os.path.join(tmp, "kg.db")
    os.environ["BACKUP_DIR"] = os.path.join(tmp, "backups")
    os.environ["EMBED_ENABLED"] = "0"
    os.environ["AI_REM_API_TOKEN"] = "pair-test-token"
    os.environ["AI_REM_PAIR_VAULT_TOKEN"] = "vault-test-token"
    sys.path.insert(0, ROOT)

    import server
    from starlette.testclient import TestClient

    app = server.AuthMiddleware(server.mcp.http_app())
    # Fremder Client (kein Loopback-Bypass): X-Forwarded-For entzieht den Loopback-Trust.
    anon = TestClient(app, base_url="https://testserver", headers={"X-Forwarded-For": "10.0.0.9"})
    user = TestClient(app, base_url="https://testserver", headers={"X-Forwarded-For": "10.0.0.2"})
    user.cookies.set(server._UI_COOKIE, server._UI_SESSION_VALUE)

    start = anon.post("/api/pair/start", json={"device": "macbook", "platform": "macos 24"}).json()
    pid, code = start["pair_id"], start["code"]
    assert len(code) == 9 and code[4] == "-", code
    assert start["verify_url"].endswith("/pair?code=" + code)

    assert anon.post("/api/pair/poll", json={"pair_id": pid}).json() == {"state": "pending"}
    assert anon.post("/api/pair/approve", json={"code": code, "approve": True}).status_code == 401, \
        "Freigabe ohne Login moeglich"
    assert anon.get("/api/pair/info", params={"code": code}).status_code == 401

    r = anon.get("/pair", params={"code": code}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("/login?next=%2Fpair"), r.headers

    info = user.get("/api/pair/info", params={"code": code.lower()}).json()
    assert info["device"] == "macbook" and info["state"] == "pending"
    assert user.post("/api/pair/approve", json={"code": code, "approve": True}).json() == {"status": "approved"}

    got = anon.post("/api/pair/poll", json={"pair_id": pid}).json()
    assert got["state"] == "approved" and got["ai_rem_token"] == "pair-test-token"
    assert got["vault_token"] == "vault-test-token"
    assert anon.post("/api/pair/poll", json={"pair_id": pid}).json() == {"state": "expired"}, \
        "Token zweimal abholbar"
    assert anon.post("/api/pair/poll", json={"pair_id": "geraten"}).json() == {"state": "expired"}

    # Ablehnen
    s2 = anon.post("/api/pair/start", json={}).json()
    user.post("/api/pair/approve", json={"code": s2["code"], "approve": False})
    assert anon.post("/api/pair/poll", json={"pair_id": s2["pair_id"]}).json() == {"state": "denied"}
    assert user.post("/api/pair/approve", json={"code": s2["code"], "approve": True}).status_code == 404, \
        "abgelehnte Kopplung nachtraeglich freigebbar"

    # Ablauf
    s3 = anon.post("/api/pair/start", json={}).json()
    server._pairs[s3["pair_id"]]["expires"] = 0
    assert anon.post("/api/pair/poll", json={"pair_id": s3["pair_id"]}).json() == {"state": "expired"}

    # Rate-Limit je IP
    flood = TestClient(app, base_url="https://testserver", headers={"X-Forwarded-For": "10.6.6.6"})
    codes = [flood.post("/api/pair/start", json={}).status_code for _ in range(server.PAIR_START_PER_IP + 1)]
    assert codes[-1] == 429 and set(codes[:-1]) == {200}, codes

    hist = user.get("/api/pair/history").json()
    assert [h["state"] for h in hist[:2]] == ["denied", "approved"]

    # Login springt auf das next-Ziel zurueck, aber nie auf fremde Hosts.
    assert server._safe_next("/pair?code=X") == "/pair?code=X"
    assert server._safe_next("//evil.example") == "/ui"
    assert server._safe_next("https://evil.example") == "/ui"
    print("OK")


def test_pairing():
    r = subprocess.run([sys.executable, __file__], capture_output=True, text=True,
                       env={**os.environ, "EMBED_ENABLED": "0"})
    assert r.returncode == 0, f"Szenario fehlgeschlagen:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    assert "OK" in r.stdout


if __name__ == "__main__":
    _scenario()
