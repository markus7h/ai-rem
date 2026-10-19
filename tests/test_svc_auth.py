"""lib/svc_auth: Auth-Gate fuer doc-graph/case-assist (Matrix, Cookie, Login, Kopplung, Adapter)."""
import asyncio
import hashlib
import hmac
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib import svc_auth  # noqa: E402
from lib.svc_auth import AuthMiddleware, SvcAuth, safe_next, stdlib_guard  # noqa: E402

TOKEN = "t0k3n-for-tests"  # pragma: allowlist secret
LAN = "192.168.1.20"


def _auth(**kw):
    kw.setdefault("public_paths", ("/health",))
    kw.setdefault("https_base", "https://svc.lan")
    return SvcAuth("doc-graph", TOKEN, **kw)


def _session(a):
    return hmac.new(TOKEN.encode(), b"doc-graph-ui-session", hashlib.sha256).hexdigest()


def test_matrix():
    a = _auth()
    assert a.authorized("/health", LAN, {})
    assert a.authorized("/login", LAN, {})
    assert not a.authorized("/health-intern", LAN, {})       # Segment-, kein String-Praefix
    assert not a.authorized("/mcp", LAN, {})
    assert a.authorized("/mcp", "127.0.0.1", {})
    assert a.authorized("/mcp", "::ffff:127.0.0.1", {})       # Dual-Stack-Socket
    assert not a.authorized("/mcp", "127.0.0.1", {"x-forwarded-for": "1.2.3.4"})
    assert a.authorized("/mcp", LAN, {"authorization": f"Bearer {TOKEN}"})
    assert not a.authorized("/mcp", LAN, {"authorization": "Bearer falsch"})
    assert not a.authorized("/mcp", LAN, {"authorization": "Bearer "})


def test_cookie_ist_abgeleitet_nicht_der_token():
    a = _auth()
    assert a.cookie == "doc_graph_session"
    assert a.session_value == _session(a) != TOKEN
    assert a.authorized("/x", LAN, {"cookie": f"foo=1; doc_graph_session={a.session_value}"})
    assert not a.authorized("/x", LAN, {"cookie": f"doc_graph_session={TOKEN}"})
    # Falscher Bearer schlaegt auch bei gueltigem Cookie fehl (Header hat Vorrang).
    assert not a.authorized("/x", LAN, {"authorization": "Bearer x",
                                        "cookie": f"doc_graph_session={a.session_value}"})


def test_ohne_token_alles_zu():
    a = SvcAuth("doc-graph", "")
    assert not a.configured
    assert not a.authorized("/mcp", LAN, {"authorization": "Bearer "})
    assert not a.authorized("/mcp", LAN, {"cookie": "doc_graph_session="})


def test_deny_browser_vs_api():
    a = _auth()
    r = a.deny("GET", "/proj/graph.html", "a=1", {"accept": "text/html"})
    assert r.status == 302 and dict(r.headers)["Location"] == "/login?next=%2Fproj%2Fgraph.html%3Fa%3D1"
    r = a.deny("POST", "/delete", "", {"accept": "text/html"})
    assert r.status == 401 and dict(r.headers)["WWW-Authenticate"] == "Bearer"


def test_login_logout():
    a = _auth()
    r = a.handle("POST", "/login", "", {}, b"token=falsch&next=/x", LAN)
    assert r.status == 401 and r.delay > 0
    r = a.handle("POST", "/login", "", {}, f"token={TOKEN}&next=//evil.com".encode(), LAN)
    h = dict(r.headers)
    assert r.status == 302 and h["Location"] == "/"
    assert h["Set-Cookie"].startswith(f"doc_graph_session={a.session_value};")
    for flag in ("HttpOnly", "Secure", "SameSite=Strict"):
        assert flag in h["Set-Cookie"]
    r = a.handle("GET", "/logout", "", {}, b"", LAN)
    assert "Max-Age=0" in dict(r.headers)["Set-Cookie"]


def test_safe_next():
    assert safe_next("/a?b=1") == "/a?b=1"
    for bad in ("//evil", "https://evil", "/\\evil", ""):
        assert safe_next(bad) == "/"


def _start(a, ip=LAN):
    r = a.handle("POST", "/api/pair/start", "", {}, b'{"device":"mac","platform":"darwin"}', ip)
    return r.status, json.loads(r.body or b"{}")


def test_pair_flow():
    a = _auth()
    _, s = _start(a)
    assert s["verify_url"] == f"https://svc.lan/pair?code={s['code']}"
    poll = lambda: json.loads(a.handle("POST", "/api/pair/poll", "", {},  # noqa: E731
                                       json.dumps({"pair_id": s["pair_id"]}).encode(), LAN).body)
    assert poll() == {"state": "pending"}
    # Freigabe nur eingeloggt.
    body = json.dumps({"code": s["code"].lower(), "approve": True}).encode()
    assert a.handle("POST", "/api/pair/approve", "", {}, body, LAN).status == 401
    ck = {"cookie": f"doc_graph_session={a.session_value}"}
    assert a.handle("GET", "/api/pair/info", "code=" + s["code"], ck, b"", LAN).status == 200
    assert a.handle("POST", "/api/pair/approve", "", ck, body, LAN).status == 200
    assert poll() == {"state": "approved", "token": TOKEN}
    assert poll() == {"state": "expired"}                    # Einmal-Abholung
    hist = json.loads(a.handle("GET", "/api/pair/history", "", ck, b"", LAN).body)
    assert hist[0]["device"] == "mac" and hist[0]["state"] == "approved"


def test_pair_ablauf_und_ablehnung(monkeypatch):
    a = _auth()
    _, s = _start(a)
    ck = {"cookie": f"doc_graph_session={a.session_value}"}
    a.handle("POST", "/api/pair/approve", "", ck,
             json.dumps({"code": s["code"], "approve": False}).encode(), LAN)
    r = a.handle("POST", "/api/pair/poll", "", {}, json.dumps({"pair_id": s["pair_id"]}).encode(), LAN)
    assert json.loads(r.body) == {"state": "denied"}
    _, s2 = _start(a)
    now = svc_auth.time.time()
    monkeypatch.setattr(svc_auth.time, "time", lambda: now + svc_auth.PAIR_TTL_SEC + 1)
    r = a.handle("POST", "/api/pair/poll", "", {}, json.dumps({"pair_id": s2["pair_id"]}).encode(), LAN)
    assert json.loads(r.body) == {"state": "expired"}


def test_pair_rate_limit_nach_letztem_xff():
    a = _auth()
    for _ in range(svc_auth.PAIR_START_PER_IP):
        assert _start(a)[0] == 200
    assert _start(a)[0] == 429
    # Ein gefaelschter vorderer XFF-Eintrag hilft nicht: gezaehlt wird der letzte (Proxy).
    r = a.handle("POST", "/api/pair/start", "", {"x-forwarded-for": f"9.9.9.9, {LAN}"}, b"{}", "127.0.0.1")
    assert r.status == 429


def test_pair_seite_verlangt_login():
    a = _auth()
    r = a.handle("GET", "/pair", "code=AB12-CD34", {}, b"", LAN)
    assert r.status == 302 and dict(r.headers)["Location"].startswith("/login?next=%2Fpair%3Fcode%3DAB12-CD34")


def test_history_datei(tmp_path):
    a = _auth(history_path=str(tmp_path / "sub" / "paired-devices.json"))
    _, s = _start(a)
    ck = {"cookie": f"doc_graph_session={a.session_value}"}
    a.handle("POST", "/api/pair/approve", "", ck, json.dumps({"code": s["code"], "approve": True}).encode(), LAN)
    assert json.loads((tmp_path / "sub" / "paired-devices.json").read_text())[0]["device"] == "mac"


def test_asgi_adapter():
    a = _auth()
    seen = []

    async def app(scope, receive, send):
        seen.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = AuthMiddleware(app, auth=a)

    def call(method, path, headers=(), body=b"", client=(LAN, 1)):
        out = []

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(msg):
            out.append(msg)

        scope = {"type": "http", "method": method, "path": path, "query_string": b"",
                 "headers": [(k.encode(), v.encode()) for k, v in headers], "client": client}
        asyncio.run(mw(scope, receive, send))
        return out[0]["status"], out[-1]["body"]

    assert call("POST", "/mcp")[0] == 401
    assert call("POST", "/mcp", [("Authorization", f"Bearer {TOKEN}")]) == (200, b"ok")
    assert call("GET", "/health")[0] == 200
    st, body = call("POST", "/api/pair/start", body=b"{}")
    assert st == 200 and "pair_id" in json.loads(body)
    assert seen == ["/mcp", "/health"]                      # Auth-Routen erreichen die App nicht


class _FakeHandler:
    def __init__(self, method, path, headers=None, body=b"", client=(LAN, 1)):
        self.command, self.path, self.client_address = method, path, client
        self.headers = dict(headers or {})
        if body:
            self.headers["Content-Length"] = str(len(body))
        self.rfile, self.wfile = io.BytesIO(body), io.BytesIO()
        self.status, self.sent = None, {}

    def send_response(self, code):
        self.status = code

    def send_header(self, k, v):
        self.sent[k] = v

    def end_headers(self):
        pass


def test_stdlib_adapter():
    a = _auth()
    h = _FakeHandler("POST", "/delete")
    assert stdlib_guard(a, h) and h.status == 401
    h = _FakeHandler("GET", "/", {"Accept": "text/html"})
    assert stdlib_guard(a, h) and h.status == 302
    h = _FakeHandler("POST", "/delete", {"Cookie": f"doc_graph_session={a.session_value}"})
    assert stdlib_guard(a, h) is False                       # Handler macht weiter
    h = _FakeHandler("GET", "/x/export", {"Authorization": f"Bearer {TOKEN}"})
    assert stdlib_guard(a, h) is False
    h = _FakeHandler("POST", "/login", body=f"token={TOKEN}".encode())
    assert stdlib_guard(a, h) and h.status == 302 and "Set-Cookie" in h.sent
