"""Thin HTTP client for the ai-rem MCP endpoint.

Mirrors the inline _post/_session/_tool pattern used in server.py's setup
script, so the CLI behaves identically.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Iterable, Iterator, Optional, Tuple

try:
    from . import keychain
except ImportError:  # altes Deployment ohne lib/keychain.py: Stufe wird uebersprungen
    keychain = None  # type: ignore[assignment]


class MCPError(RuntimeError):
    pass


def _claude_json_path() -> str:
    # Gleiche Regel wie scripts/setup.py: mit CLAUDE_CONFIG_DIR liegt .claude.json dort.
    cc = os.environ.get("CLAUDE_CONFIG_DIR", "").split(os.pathsep)[0].strip()
    return os.path.join(cc, ".claude.json") if cc else os.path.expanduser("~/.claude.json")


# Client-neutrale Konfiguration, von scripts/setup.py fuer jedes Ziel geschrieben:
# {"endpoint": ".../mcp", "targets": ["claude", "opencode"], "vault_entry": "..."}.
# token_file/llm_url/llm_api_key sind Altlasten (< 1.7): token_file wird nur noch
# gelesen, die LLM-Felder gar nicht mehr (kommen per /api/client-config).
CLIENT_JSON = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "ai-rem", "client.json")


def load_client_cfg() -> dict:
    try:
        with open(CLIENT_JSON, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def _claude_servers() -> dict:
    try:
        with open(_claude_json_path(), encoding="utf-8") as f:
            return json.load(f).get("mcpServers", {}) or {}
    except Exception:
        return {}


# Einmal pro Prozess, sonst spammt jeder Hook-Aufruf stderr voll.
_legacy_warned = False


def _warn_legacy() -> None:
    global _legacy_warned
    if not _legacy_warned:
        _legacy_warned = True
        print("ai-rem: Token liegt noch im Klartext — `ai-rem update` migriert ihn in den Keychain",
              file=sys.stderr)


def _resolve_token_with_source() -> Tuple[str, str]:
    """ai-rem-API-Token plus Herkunft (fuer `ai-rem doctor`).

    Reihenfolge: Env AI_REM_TOKEN → OS-Keychain (lib/keychain) → Klartext-Altlasten,
    nur lesend: token_file aus client.json, dann der Bearer-Header in ~/.claude.json.
    Die Altlasten bleiben, damit ein Client zwischen Server-Update und `ai-rem update`
    nicht tokenlos dasteht; der Treffer wird einmalig gemeldet.

    Der fruehere Vault-Fallback ist weg: seit 1.7 kommt der Vault-Token selbst nur
    noch per ai-rem-Token ueber /api/client-config — ihn fuer den ai-rem-Token zu
    befragen waere zirkulaer. Keychain-Backend-Fehler (gesperrter Keychain o.ae.)
    zaehlen wie „kein Token“, damit die Legacy-Stufen noch greifen."""
    tok = os.environ.get("AI_REM_TOKEN", "")
    if tok:
        return tok, "Env AI_REM_TOKEN"
    if keychain is not None:
        try:
            tok = keychain.get()
        except Exception:
            tok = ""
        if tok:
            return tok, "Keychain (%s)" % keychain.backend_name()
    tf = load_client_cfg().get("token_file", "")
    if tf:
        try:
            with open(os.path.expanduser(tf), encoding="utf-8") as f:
                tok = f.read().strip()
        except OSError:
            tok = ""
        if tok:
            _warn_legacy()
            return tok, "Klartext-Legacy (%s)" % tf
    hdr = (_claude_servers().get("ai-rem", {}).get("headers", {}) or {}).get("Authorization", "")
    if hdr.lower().startswith("bearer ") and hdr[7:].strip():
        _warn_legacy()
        return hdr[7:].strip(), "Klartext-Legacy (~/.claude.json)"
    return "", ""


def _resolve_token(timeout: float = 15.0) -> str:
    # timeout bleibt in der Signatur fuer aeltere Aufrufer; seit dem Wegfall des
    # Vault-Fallbacks geht hier kein Netz mehr raus.
    return _resolve_token_with_source()[0]


def _default_endpoint(server: str = "ai-rem") -> str:
    """MCP-Endpoint aus client.json, sonst aus ~/.claude.json (mcpServers.<server>.url)
    — derselbe Ort, aus dem schon der Token kommt. So muss AI_REM_ENDPOINT nicht
    gesetzt sein."""
    return (load_client_cfg().get("endpoint", "")
            or (_claude_servers().get(server, {}) or {}).get("url", "") or "")


def iter_sse_events(lines: Iterable) -> Iterator[str]:
    """text/event-stream → ein String pro Event: die data:-Zeilen eines Events mit
    \\n gejoint, Kommentare (":…") und event:/id:-Felder verworfen. Nimmt bytes
    oder str, damit sowohl ein gestreamter Response-Body als auch ein fertiger
    Text zeilenweise durchlaufen kann (mcp-proxy liest live, _parse hinterher)."""
    data = []
    for raw in lines:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        line = raw.rstrip("\r\n")
        if not line:
            if data:
                yield "\n".join(data)
                data = []
            continue
        if line.startswith("data:"):
            payload = line[5:]
            data.append(payload[1:] if payload.startswith(" ") else payload)
    if data:
        yield "\n".join(data)


class MCPClient:
    def __init__(self, endpoint: Optional[str] = None, timeout: float = 15.0):
        self.endpoint = (
            endpoint
            or os.environ.get("AI_REM_ENDPOINT")
            or _default_endpoint()
            or "http://localhost:3456/mcp"
        )
        self.timeout = timeout
        self.token, self.token_source = _resolve_token_with_source()
        # Herkunft fuer extra.client beim Anlegen (Server liest X-AI-REM-Client).
        self.client_name = os.environ.get("AI_REM_CLIENT", "") or "ai-rem-cli"
        self._sid: Optional[str] = None
        # /api/client-config: Ergebnis-Cache plus Diagnose-Flags fuer doctor/extractor.
        self._client_cfg: Optional[dict] = None
        self.server_too_old = False
        self.token_rejected = False

    def _auth_header(self) -> dict:
        h = {"X-AI-REM-Client": self.client_name}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _post(self, body: dict, sid: Optional[str] = None):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        headers.update(self._auth_header())
        if sid:
            headers["mcp-session-id"] = sid
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body).encode(),
            headers=headers,
            method="POST",
        )
        try:
            return urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.URLError as e:
            raise MCPError(f"MCP endpoint unreachable ({self.endpoint}): {e}") from e

    @staticmethod
    def _parse(resp) -> str:
        raw = resp.read().decode()
        # Erstes SSE-Event ist die Antwort; ohne data:-Zeile ist der Body reines JSON.
        payload = next(iter_sse_events(raw.splitlines()), raw)
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            return raw
        if "error" in obj:
            raise MCPError(json.dumps(obj["error"]))
        content = obj.get("result", {}).get("content")
        if isinstance(content, list) and content:
            return content[0].get("text", "")
        return ""

    def _session(self) -> str:
        if self._sid:
            return self._sid
        resp = self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": self.client_name, "version": "1.0"},
                },
            }
        )
        self._sid = resp.headers.get("mcp-session-id")
        resp.read()
        try:
            self._post(
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                sid=self._sid,
            ).read()
        except Exception:
            pass
        if not self._sid:
            raise MCPError("Did not receive mcp-session-id")
        return self._sid

    @property
    def base_url(self) -> str:
        """HTTP base (endpoint ohne /mcp) — fuer REST-Routen wie /export."""
        if self.endpoint.endswith("/mcp"):
            return self.endpoint[:-4]
        return self.endpoint.rstrip("/")

    def export(self) -> dict:
        """Vollen Graph (Entities inkl. voller description + extra, Relations) holen.

        Die MCP-Tools (search/context) kuerzen den Body und liefern kein extra;
        /export gibt alles ungekuerzt zurueck.
        """
        url = self.base_url + "/export"
        try:
            req = urllib.request.Request(url, headers=self._auth_header())
            resp = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.URLError as e:
            raise MCPError(f"export unreachable ({url}): {e}") from e
        return json.loads(resp.read().decode())

    def client_config(self) -> dict:
        """Laufzeit-Config vom Server (GET /api/client-config, Bearer):
        {"version","llm_url","llm_api_key","vault_url","vault_token"}.

        Ersetzt die Klartext-Kopien auf der Workstation (settings.json-env,
        client.json, Vault-Token-Datei): der Client haelt nur den ai-rem-Token und
        holt den Rest pro Lauf. Nie auf Platte schreiben. Fehler liefern {} —
        Aufrufer fallen auf Env/Defaults zurueck; 404 heisst Server < 1.7
        (server_too_old), 401 heisst Token ungueltig (token_rejected). Auch ein
        Fehlschlag wird gecacht: innerhalb eines Laufs aendert sich nichts, und
        ein Hook soll nicht bei jedem Zugriff erneut ins Timeout laufen."""
        if self._client_cfg is not None:
            return self._client_cfg
        url = self.base_url + "/api/client-config"
        cfg: dict = {}
        try:
            req = urllib.request.Request(url, headers=self._auth_header())
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                obj = json.loads(resp.read().decode())
            if isinstance(obj, dict):
                cfg = obj
        except urllib.error.HTTPError as e:
            if e.code == 404:
                self.server_too_old = True
            elif e.code == 401:
                self.token_rejected = True
        except Exception:
            pass
        self._client_cfg = cfg
        return cfg

    def call(self, tool: str, args: Optional[dict] = None) -> str:
        """memory_*-Op über die REST-Route POST /api/tool aufrufen.

        Entkoppelt die CLI/den Extractor davon, welche Tools im MCP-tools/list-
        Surface liegen (Issue #32): die 4 Kern-Tools bleiben dort, die 12 Admin-Ops
        sind nur noch über /api/tool erreichbar — die hier alle bedient werden.
        """
        url = self.base_url + "/api/tool"
        body = json.dumps({"name": tool, "arguments": args or {}}).encode()
        headers = {"Content-Type": "application/json"}
        headers.update(self._auth_header())
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = json.loads(e.read().decode()).get("error", "")
            except Exception:
                pass
            raise MCPError(
                f"{tool} failed (HTTP {e.code})" + (f": {detail}" if detail else "")
            ) from e
        except urllib.error.URLError as e:
            raise MCPError(f"/api/tool unreachable ({url}): {e}") from e
        obj = json.loads(resp.read().decode())
        if isinstance(obj, dict) and obj.get("error"):
            raise MCPError(obj["error"])
        return obj.get("result", "") if isinstance(obj, dict) else ""
