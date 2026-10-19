"""svc_auth — gemeinsames Auth-Gate für weitere MCP-Dienste neben ai-rem.

Kanonische Quelle: ai-rem/lib/svc_auth.py. In den Diensten liegt eine KOPIE — dort nicht
ändern, sondern hier und dann `scripts/sync-svc-auth.sh` laufen lassen. Vendoring statt
Paket, weil die Images unabhängig bauen und keine Build-Abhängigkeit auf ai-rem haben sollen.

Sicherheitsmodell wie ai-rem (docs/authentication.md), fail-closed:
  1) öffentlicher Pfad (Login, Kopplung, vom Dienst freigegebene Pfade),
  2) Loopback OHNE X-Forwarded-For (Healthcheck im Container; hinter Caddy zählt es nicht),
  3) `Authorization: Bearer <token>` — konstant-zeitlich geprüft vom Verifier,
  4) Browser-Cookie `<name>_session` = HMAC(token, "<name>-ui-session") — der rohe Token
     liegt nie im Browser, Token-Rotation loggt automatisch aus.
Kopplung (Device-Flow wie `gh auth login`): `ai-rem svc pair <name>` startet, die eingeloggte
Web-UI gibt frei, der Client holt den Token genau einmal ab.

Die Credential-Prüfung steckt hinter einer Verifier-Schnittstelle (`verify(cred) -> bool`):
heute StaticTokenVerifier, für den SaaS-Betrieb später ein JWT/OIDC-Verifier.

Framework-neutral: `SvcAuth.handle()` arbeitet auf (method, path, query, headers, body) und
liefert eine `Resp`; Adapter gibt es für pure ASGI (FastMCP/Starlette, SSE ungepuffert) und
für http.server (stdlib-Viewer). Reine stdlib, Python 3.9+.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import json
import logging
import os
import secrets
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Mapping, Optional

SVC_AUTH_VERSION = "1.0.0"

log = logging.getLogger("svc_auth")

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"}
PAIR_TTL_SEC = 600
PAIR_START_PER_IP = 10        # Kopplungen je IP und TTL-Fenster
PAIR_MAX_PENDING = 50
# Code-Alphabet ohne 0/O/1/I (Verwechslung beim Abtippen).
_PAIR_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # pragma: allowlist secret
_MAX_BODY = 64 * 1024
_LOGIN_DELAY = 0.5            # milde Brute-Force-Bremse (Token hat 256 Bit Entropie)

# Immer öffentlich: ohne sie kommt niemand zum Login bzw. kein Installer zur Kopplung.
_BUILTIN_PUBLIC = ("/login", "/logout", "/pair", "/api/pair/start", "/api/pair/poll")


class StaticTokenVerifier:
    """Ein geteilter Instanz-Token (Stufe 1). `secret()` ist, was die Kopplung ausliefert."""

    def __init__(self, token: str):
        self.token = token or ""

    def verify(self, cred: str) -> bool:
        return bool(self.token) and hmac.compare_digest(cred.encode(), self.token.encode())

    def secret(self) -> str:
        return self.token


@dataclass
class Resp:
    status: int
    headers: list = field(default_factory=list)   # [(name, value)]
    body: bytes = b""
    delay: float = 0.0                            # Adapter wartet vor dem Senden


def _json(status: int, obj, extra=()) -> Resp:
    return Resp(status, [("Content-Type", "application/json"), *extra],
                json.dumps(obj, ensure_ascii=False).encode())


def _html(status: int, text: str, extra=()) -> Resp:
    return Resp(status, [("Content-Type", "text/html; charset=utf-8"), *extra], text.encode())


def _redirect(location: str, extra=()) -> Resp:
    return Resp(302, [("Location", location), *extra])


def _path_match(path: str, prefixes) -> bool:
    # Exakter Treffer oder echtes Pfad-Segment darunter — nicht bloßes String-Präfix,
    # damit /pair nicht /pairing-intern mit freigibt.
    return any(path == p or path.startswith(p.rstrip("/") + "/") for p in prefixes)


def safe_next(next_url: str) -> str:
    """Nur lokale Pfade als Login-Ziel — kein Open-Redirect auf fremde Hosts."""
    if next_url.startswith("/") and not next_url.startswith("//") and "\\" not in next_url:
        return next_url
    return "/"


class SvcAuth:
    def __init__(self, name: str, token: str = "", *, verifier=None, title: str = "",
                 public_paths=(), https_base: str = "", history_path: str = "",
                 session_ttl: int = 30 * 24 * 3600):
        self.name = name
        self.title = title or name
        self.verifier = verifier or StaticTokenVerifier(token)
        secret = self.verifier.secret()
        self.cookie = name.replace("-", "_") + "_session"
        self.session_value = (hmac.new(secret.encode(), f"{name}-ui-session".encode(),
                                       hashlib.sha256).hexdigest() if secret else "")
        self.session_ttl = session_ttl
        self.public_paths = tuple(_BUILTIN_PUBLIC) + tuple(public_paths)
        self.https_base = https_base.rstrip("/")
        self.history_path = history_path
        self._pairs: dict = {}   # pair_id → {code, state, device, platform, ip, expires}
        self._lock = threading.Lock()
        self._history_mem: list = []

    @classmethod
    def from_env(cls, name: str, prefix: str, **kw) -> "SvcAuth":
        """Konfiguration pro Instanz aus der Env: <PREFIX>_API_TOKEN, <PREFIX>_PUBLIC_URL,
        <PREFIX>_UI_SESSION_TTL — keine globalen Konstanten (Instanz pro Kunde)."""
        kw.setdefault("https_base", os.environ.get(f"{prefix}_PUBLIC_URL", ""))
        kw.setdefault("session_ttl", int(os.environ.get(f"{prefix}_UI_SESSION_TTL", str(30 * 24 * 3600))))
        return cls(name, os.environ.get(f"{prefix}_API_TOKEN", "").strip(), **kw)

    @property
    def configured(self) -> bool:
        return bool(self.verifier.secret())

    def assert_configured(self, env_name: str) -> None:
        if not self.configured:
            log.error("%s ist nicht gesetzt — Start abgebrochen (fail-closed). Token aus "
                      "mykeyvault (Item %s-api-token) per deploy.sh in die .env schreiben.",
                      env_name, self.name)
            sys.exit(1)

    # ── Gate ───────────────────────────────────────────────────────────────
    def is_public(self, path: str) -> bool:
        return _path_match(path, self.public_paths)

    def authorized(self, path: str, peer_ip: str, headers: Mapping[str, str]) -> bool:
        """headers: Namen klein geschrieben. peer_ip: direkte Gegenstelle, nicht XFF."""
        if self.is_public(path):
            return True
        if peer_ip in LOOPBACK_HOSTS and "x-forwarded-for" not in headers:
            # XFF kann Trust nur entziehen, nie gewähren: hinter einem same-host-Proxy ist
            # die Peer-IP zwar Loopback, der Request kommt aber von außen.
            return True
        return self.authed(headers)

    def authed(self, headers: Mapping[str, str]) -> bool:
        """Gültiger Bearer ODER gültiges Session-Cookie (ohne Pfad-/Loopback-Ausnahmen)."""
        auth = headers.get("authorization", "")
        if auth:
            tok = auth[7:].strip() if auth.lower().startswith("bearer ") else auth.strip()
            return self.verifier.verify(tok)
        val = self._cookie(headers)
        return val is not None and bool(self.session_value) and \
            hmac.compare_digest(val.encode(), self.session_value.encode())

    def _cookie(self, headers) -> Optional[str]:
        for part in headers.get("cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == self.cookie:
                return v
        return None

    def deny(self, method: str, path: str, query: str, headers: Mapping[str, str]) -> Resp:
        """Browser-Navigation → Login mit Rücksprung, alles andere → 401 JSON."""
        if method == "GET" and "text/html" in headers.get("accept", "") \
                and "authorization" not in headers:
            target = path + ("?" + query if query else "")
            return _redirect("/login?next=" + urllib.parse.quote(target, safe=""))
        return _json(401, {"error": "unauthorized"}, [("WWW-Authenticate", "Bearer")])

    # ── Auth-Routen ────────────────────────────────────────────────────────
    _ROUTES = {("GET", "/login"), ("POST", "/login"), ("GET", "/logout"), ("GET", "/pair"),
               ("POST", "/api/pair/start"), ("POST", "/api/pair/poll"),
               ("GET", "/api/pair/info"), ("POST", "/api/pair/approve"),
               ("GET", "/api/pair/history")}

    def is_auth_route(self, method: str, path: str) -> bool:
        return (method, path) in self._ROUTES

    def handle(self, method: str, path: str, query: str, headers: Mapping[str, str],
               body: bytes, peer_ip: str) -> Resp:
        q = urllib.parse.parse_qs(query)
        if path == "/login":
            return self._login(method, q, body, headers)
        if path == "/logout":
            return _redirect("/login", [("Set-Cookie", f"{self.cookie}=; Path=/; Max-Age=0; "
                                                       "HttpOnly; Secure; SameSite=Strict")])
        if path == "/pair":
            if not self.authed(headers):
                target = "/pair?code=" + urllib.parse.quote(q.get("code", [""])[0])
                return _redirect("/login?next=" + urllib.parse.quote(target, safe=""))
            return _html(200, _PAIR_HTML.replace("__TITLE__", html.escape(self.title)))
        ip = self._client_ip(peer_ip, headers)
        if path == "/api/pair/start":
            return self._pair_start(body, ip, headers)
        if path == "/api/pair/poll":
            return self._pair_poll(body)
        # info/approve/history: nur eingeloggt (UI-Cookie oder Bearer).
        if not self.authed(headers):
            return _json(401, {"error": "unauthorized"})
        if path == "/api/pair/info":
            return self._pair_info(q.get("code", [""])[0])
        if path == "/api/pair/approve":
            return self._pair_approve(body)
        return _json(200, self._history()[:10])

    def _login(self, method, q, body, headers) -> Resp:
        if method == "GET":
            next_url = q.get("next", [""])[0]
            if self.authed(headers):
                return _redirect(safe_next(next_url))
            return _html(200, self._login_page("", next_url))
        form = urllib.parse.parse_qs(body.decode("utf-8", "ignore"))
        token, next_url = form.get("token", [""])[0], form.get("next", [""])[0]
        if self.verifier.verify(token):
            cookie = (f"{self.cookie}={self.session_value}; Path=/; Max-Age={self.session_ttl}; "
                      "HttpOnly; Secure; SameSite=Strict")
            return _redirect(safe_next(next_url), [("Set-Cookie", cookie)])
        r = _html(401, self._login_page("Falscher Token.", next_url))
        r.delay = _LOGIN_DELAY
        return r

    def _login_page(self, error: str, next_url: str) -> str:
        return (_LOGIN_HTML.replace("__TITLE__", html.escape(self.title))
                .replace("__ERROR__", html.escape(error))
                .replace("__NEXT__", html.escape(safe_next(next_url), quote=True)))

    # ── Kopplung ───────────────────────────────────────────────────────────
    @staticmethod
    def _client_ip(peer_ip: str, headers) -> str:
        # Letzter XFF-Eintrag = den hat unser Reverse-Proxy angehängt; die vorderen kann
        # der Client frei setzen und damit das Rate-Limit je IP umgehen.
        xff = headers.get("x-forwarded-for", "")
        return xff.split(",")[-1].strip() if xff else peer_ip

    def _public_base(self, headers) -> str:
        if self.https_base:
            return self.https_base
        proto = headers.get("x-forwarded-proto", "https")
        return f"{proto}://{headers.get('host', 'localhost')}"

    def _gc(self, now: float) -> None:
        for pid in [p for p, e in self._pairs.items() if e["expires"] < now]:
            del self._pairs[pid]

    def _by_code(self, code: str):
        code = code.strip().upper()
        for pid, e in self._pairs.items():
            if hmac.compare_digest(e["code"], code):
                return pid, e
        return None, None

    @staticmethod
    def _body_json(body: bytes) -> dict:
        try:
            obj = json.loads(body.decode("utf-8") or "{}")
        except ValueError:
            return {}
        return obj if isinstance(obj, dict) else {}

    def _pair_start(self, body, ip, headers) -> Resp:
        b = self._body_json(body)
        now = time.time()
        with self._lock:
            self._gc(now)
            if sum(1 for e in self._pairs.values() if e["ip"] == ip) >= PAIR_START_PER_IP \
                    or len(self._pairs) >= PAIR_MAX_PENDING:
                return _json(429, {"error": "zu viele offene Kopplungen, später erneut"})
            code = "".join(secrets.choice(_PAIR_ALPHABET) for _ in range(8))
            code = code[:4] + "-" + code[4:]
            pid = secrets.token_urlsafe(32)
            self._pairs[pid] = {"code": code, "state": "pending", "ip": ip,
                                "device": str(b.get("device", ""))[:80],
                                "platform": str(b.get("platform", ""))[:80],
                                "expires": now + PAIR_TTL_SEC}
        log.info("Kopplung gestartet: %s (%s, %s)", code, self._pairs[pid]["device"], ip)
        return _json(200, {"pair_id": pid, "code": code, "expires_in": PAIR_TTL_SEC,
                           "verify_url": f"{self._public_base(headers)}/pair?code={code}"})

    def _pair_poll(self, body) -> Resp:
        pid = str(self._body_json(body).get("pair_id", ""))
        with self._lock:
            self._gc(time.time())
            e = self._pairs.get(pid)
            if e is None:
                return _json(200, {"state": "expired"})
            if e["state"] != "approved":
                return _json(200, {"state": e["state"]})
            del self._pairs[pid]  # Einmal-Abholung
        return _json(200, {"state": "approved", "token": self.verifier.secret()})

    def _pair_info(self, code: str) -> Resp:
        with self._lock:
            self._gc(time.time())
            _pid, e = self._by_code(code)
            if e is None:
                return _json(404, {"error": "Unbekannter oder abgelaufener Code."})
            return _json(200, {"code": e["code"], "state": e["state"], "device": e["device"],
                               "platform": e["platform"], "ip": e["ip"],
                               "expires_in": int(e["expires"] - time.time())})

    def _pair_approve(self, body) -> Resp:
        b = self._body_json(body)
        with self._lock:
            self._gc(time.time())
            _pid, e = self._by_code(str(b.get("code", "")))
            if e is None or e["state"] != "pending":
                return _json(404, {"error": "Unbekannter, abgelaufener oder schon entschiedener Code."})
            e["state"] = "approved" if b.get("approve") else "denied"
            entry = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "device": e["device"],
                     "platform": e["platform"], "ip": e["ip"], "state": e["state"]}
        self._history_add(entry)
        log.info("Kopplung %s: %s (%s)", entry["state"], entry["device"], entry["ip"])
        return _json(200, {"status": entry["state"]})

    def _history(self) -> list:
        if not self.history_path:
            return list(self._history_mem)
        try:
            with open(self.history_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return []

    def _history_add(self, entry: dict) -> None:
        hist = [entry, *self._history()][:50]
        if not self.history_path:
            self._history_mem = hist
            return
        try:
            os.makedirs(os.path.dirname(self.history_path) or ".", exist_ok=True)
            tmp = self.history_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(hist, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.history_path)
        except OSError as e:  # Historie ist Komfort, darf die Freigabe nicht kippen
            log.warning("Kopplungs-Historie nicht schreibbar (%s): %s", self.history_path, e)


# ── Adapter: pure ASGI ─────────────────────────────────────────────────────

def _asgi_headers(scope) -> dict:
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}


async def _asgi_send(send, r: Resp) -> None:
    if r.delay:
        await asyncio.sleep(r.delay)
    await send({"type": "http.response.start", "status": r.status,
                "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in r.headers]})
    await send({"type": "http.response.body", "body": r.body})


class AuthMiddleware:
    """Pure-ASGI-Gate vor allen HTTP-Routen (auch /mcp). Pure ASGI statt
    BaseHTTPMiddleware, damit der SSE-Stream von /mcp nicht gepuffert wird.
    Einbinden: `app.add_middleware(AuthMiddleware, auth=svc_auth)`."""

    def __init__(self, app, auth: SvcAuth):
        self.app = app
        self.auth = auth

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        method, path = scope.get("method", "GET"), scope.get("path", "")
        query = scope.get("query_string", b"").decode("latin-1")
        headers = _asgi_headers(scope)
        peer = (scope.get("client") or ("", 0))[0]
        if self.auth.is_auth_route(method, path):
            body = b""
            while True:
                msg = await receive()
                body += msg.get("body", b"")
                if len(body) > _MAX_BODY:
                    return await _asgi_send(send, _json(413, {"error": "body too large"}))
                if not msg.get("more_body"):
                    break
            return await _asgi_send(send, self.auth.handle(method, path, query, headers, body, peer))
        if self.auth.authorized(path, peer, headers):
            return await self.app(scope, receive, send)
        await _asgi_send(send, self.auth.deny(method, path, query, headers))


# ── Adapter: http.server (stdlib) ──────────────────────────────────────────

def stdlib_guard(auth: SvcAuth, handler) -> bool:
    """Am Anfang von do_GET/do_POST aufrufen. True = Request ist erledigt (Auth-Route
    beantwortet oder abgewiesen), der Handler muss dann sofort zurückkehren."""
    method = handler.command
    parsed = urllib.parse.urlsplit(handler.path)
    headers = {k.lower(): v for k, v in handler.headers.items()}
    peer = handler.client_address[0] if handler.client_address else ""
    if auth.is_auth_route(method, parsed.path):
        n = int(handler.headers.get("Content-Length") or 0)
        if n > _MAX_BODY:
            _stdlib_send(handler, _json(413, {"error": "body too large"}))
            return True
        body = handler.rfile.read(n) if n > 0 else b""
        _stdlib_send(handler, auth.handle(method, parsed.path, parsed.query, headers, body, peer))
        return True
    if auth.authorized(parsed.path, peer, headers):
        return False
    _stdlib_send(handler, auth.deny(method, parsed.path, parsed.query, headers))
    return True


def _stdlib_send(handler, r: Resp) -> None:
    if r.delay:
        time.sleep(r.delay)
    handler.send_response(r.status)
    for k, v in r.headers:
        handler.send_header(k, v)
    handler.send_header("Content-Length", str(len(r.body)))
    handler.end_headers()
    if handler.command != "HEAD":
        handler.wfile.write(r.body)


# ── Seiten (inline, damit die Kopie eine einzige Datei bleibt) ─────────────

_STYLE = """body{background:#fafafa;color:#333;font-family:"Source Sans 3","Source Sans Pro",Arial,sans-serif;letter-spacing:.15pt;font-size:14px;display:flex;flex-direction:column;min-height:100vh;align-items:center;justify-content:center;margin:0;padding:16px;box-sizing:border-box}
.box{background:#fff;border:1px solid #ececec;border-left:3px solid #388e3c;border-radius:12px;padding:28px;width:100%;max-width:420px;box-sizing:border-box}
h1{font-size:20px;margin:0 0 4px;color:#000}p.sub{color:#666;font-size:13px;margin:0 0 20px}
label{display:block;font-size:12px;color:#666;margin-bottom:6px}
input{width:100%;box-sizing:border-box;background:#fafafa;border:1px solid #d0d0d0;color:#333;border-radius:6px;padding:10px;font-size:14px;margin:0 0 16px}
button{width:100%;background:#388e3c;color:#fff;border:none;border-radius:6px;padding:10px;font-size:14px;font-weight:500;cursor:pointer}
button:hover{background:#2e7d32}.err{color:#dd3333;font-size:13px;margin-bottom:14px;min-height:18px}
.code{font-size:28px;font-weight:700;letter-spacing:3px;text-align:center;margin:8px 0 16px;color:#000}
dl{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;margin:0 0 20px;font-size:13px}dt{color:#666}dd{margin:0;word-break:break-all}
.row{display:flex;gap:10px}.row button{flex:1}.no{background:#fff;color:#dd3333;border:1px solid #dd3333}.no:hover{background:#fff5f5}
.msg{margin-top:16px;font-size:13px;min-height:18px}.warn{color:#8a6d00;font-size:12px;margin:0 0 16px}
h2{font-size:14px;margin:24px 0 8px;color:#000}ul{list-style:none;padding:0;margin:0;font-size:12px;color:#666}li{padding:4px 0;border-top:1px solid #f0f0f0}"""

_LOGIN_HTML = """<!DOCTYPE html>
<html lang="de"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__ · Login</title><style>""" + _STYLE + """</style></head>
<body><form class="box" method="POST" action="/login">
<h1>__TITLE__</h1><p class="sub">Anmelden</p>
<div class="err">__ERROR__</div>
<label for="token">API-Token</label>
<input type="password" id="token" name="token" autofocus autocomplete="current-password">
<input type="hidden" name="next" value="__NEXT__">
<button type="submit">Anmelden</button>
</form></body></html>"""

_PAIR_HTML = """<!DOCTYPE html>
<html lang="de"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__ · Gerät koppeln</title><style>""" + _STYLE + """</style></head>
<body><div class="box">
<h1>Gerät koppeln</h1><p class="sub">Ein Client bittet um Zugang zu __TITLE__.</p>
<div id="req"></div><div class="msg" id="msg"></div>
<h2>Zuletzt gekoppelte Geräte</h2><ul id="hist"><li>—</li></ul>
</div>
<script>
const code = new URLSearchParams(location.search).get('code') || '';
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function load() {
  const r = await fetch('/api/pair/info?code=' + encodeURIComponent(code));
  const d = await r.json();
  if (!r.ok) {
    $('req').innerHTML = '<p>' + esc(d.error || 'Unbekannter oder abgelaufener Code.') + '</p>';
  } else {
    $('req').innerHTML =
      '<div class="code">' + esc(d.code) + '</div>' +
      '<p class="warn">Nur freigeben, wenn derselbe Code gerade im Terminal angezeigt wird.</p>' +
      '<dl><dt>Gerät</dt><dd>' + esc(d.device) + '</dd><dt>System</dt><dd>' + esc(d.platform) +
      '</dd><dt>IP</dt><dd>' + esc(d.ip) + '</dd><dt>Läuft ab</dt><dd>in ' + Math.round(d.expires_in / 60) + ' min</dd></dl>' +
      (d.state === 'pending'
        ? '<div class="row"><button onclick="decide(true)">Freigeben</button><button class="no" onclick="decide(false)">Ablehnen</button></div>'
        : '<p>Status: ' + esc(d.state) + '</p>');
  }
  const h = await (await fetch('/api/pair/history')).json();
  $('hist').innerHTML = h.length ? h.map(e => '<li>' + esc(e.ts) + ' · ' + esc(e.device) + ' · ' + esc(e.platform) + ' · ' + esc(e.ip) + ' · ' + esc(e.state) + '</li>').join('') : '<li>—</li>';
}
async function decide(approve) {
  const r = await fetch('/api/pair/approve', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({code, approve})});
  const d = await r.json();
  $('msg').textContent = r.ok ? (approve ? 'Freigegeben — der Client läuft jetzt weiter.' : 'Abgelehnt.') : (d.error || 'Fehler');
  load();
}
load();
</script></body></html>"""
