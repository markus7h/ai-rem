"""Thin HTTP client for the ai-rem MCP endpoint.

Mirrors the inline _post/_session/_tool pattern used in server.py's setup
script, so the CLI behaves identically.
"""
import json
import os
import re
import urllib.error
import urllib.request
from typing import Optional


class MCPError(RuntimeError):
    pass


def _claude_json_path() -> str:
    # Gleiche Regel wie scripts/setup.py: mit CLAUDE_CONFIG_DIR liegt .claude.json dort.
    cc = os.environ.get("CLAUDE_CONFIG_DIR", "").split(os.pathsep)[0].strip()
    return os.path.join(cc, ".claude.json") if cc else os.path.expanduser("~/.claude.json")


# Client-neutrale Konfiguration, von scripts/setup.py fuer jedes Ziel geschrieben:
# {"endpoint": ".../mcp", "token_file": "...", "targets": ["claude", "opencode"]}.
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


def _resolve_token(timeout: float = 15.0) -> str:
    """ai-rem-API-Token beziehen: Env AI_REM_TOKEN → Token-Datei aus
    ~/.config/ai-rem/client.json → Bearer-Header in ~/.claude.json (vom
    system-check-Hook geschrieben) → Runtime-Fetch aus mykeyvault (Koordinaten aus
    ~/.claude.json).

    Der Header in ~/.claude.json ist der einzige Kanal, über den Claudes built-in
    /mcp-Tool den Token bekommt (statischer Config-Read — kann nicht selbst aus dem
    Vault lesen). Vault = Rotationsquelle, Header = Session-Cache; darum bleibt der
    Header-Sync tragend und nicht entfernbar (vgl. Issue #35). opencode liest den
    Token per {file:…} aus derselben Token-Datei, die hier an zweiter Stelle steht."""
    tok = os.environ.get("AI_REM_TOKEN", "")
    if tok:
        return tok
    tf = load_client_cfg().get("token_file", "")
    if tf:
        try:
            with open(os.path.expanduser(tf), encoding="utf-8") as f:
                tok = f.read().strip()
            if tok:
                return tok
        except OSError:
            pass
    servers = _claude_servers()
    hdr = (servers.get("ai-rem", {}).get("headers", {}) or {}).get("Authorization", "")
    if hdr.lower().startswith("bearer "):
        return hdr[7:].strip()
    try:
        env = servers["mykeyvault"]["env"]
        req = urllib.request.Request(
            env["VAULT_API_URL"].rstrip("/") + "/secret/ai-rem-api-token",
            headers={"Authorization": f"Bearer {env['VAULT_API_TOKEN']}"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode()).get("password", "")
    except Exception:
        return ""


def _default_endpoint(server: str = "ai-rem") -> str:
    """MCP-Endpoint aus client.json, sonst aus ~/.claude.json (mcpServers.<server>.url)
    — derselbe Ort, aus dem schon der Token kommt. So muss AI_REM_ENDPOINT nicht
    gesetzt sein."""
    return (load_client_cfg().get("endpoint", "")
            or (_claude_servers().get(server, {}) or {}).get("url", "") or "")


class MCPClient:
    def __init__(self, endpoint: Optional[str] = None, timeout: float = 15.0):
        self.endpoint = (
            endpoint
            or os.environ.get("AI_REM_ENDPOINT")
            or _default_endpoint()
            or "http://localhost:3456/mcp"
        )
        self.timeout = timeout
        self.token = _resolve_token(timeout)
        # Herkunft fuer extra.client beim Anlegen (Server liest X-AI-REM-Client).
        self.client_name = os.environ.get("AI_REM_CLIENT", "") or "ai-rem-cli"
        self._sid: Optional[str] = None

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
        m = re.search(r"^data: (.+)$", raw, re.MULTILINE)
        payload = m.group(1) if m else raw
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
