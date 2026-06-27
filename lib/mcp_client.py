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


_CLAUDE_JSON = os.path.expanduser("~/.claude.json")


def _resolve_token(timeout: float = 15.0) -> str:
    """ai-rem-API-Token beziehen: Env AI_REM_TOKEN → bereits in ~/.claude.json
    hinterlegter Bearer-Header (vom system-check-Hook geschrieben) → Runtime-Fetch
    aus mykeyvault (vault-api-Koordinaten ebenfalls aus ~/.claude.json).

    Der Header in ~/.claude.json ist der einzige Kanal, über den Claudes built-in
    /mcp-Tool den Token bekommt (statischer Config-Read — kann nicht selbst aus dem
    Vault lesen). Vault = Rotationsquelle, Header = Session-Cache; darum bleibt der
    Header-Sync tragend und nicht entfernbar (vgl. Issue #35)."""
    tok = os.environ.get("AI_REM_TOKEN", "")
    if tok:
        return tok
    try:
        with open(_CLAUDE_JSON) as f:
            servers = json.load(f).get("mcpServers", {})
    except Exception:
        return ""
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


def _endpoint_from_claude_json(server: str = "ai-rem") -> str:
    """MCP-Endpoint-URL aus ~/.claude.json (mcpServers.<server>.url) lesen — derselbe
    Ort, aus dem schon der Token kommt. So muss AI_REM_ENDPOINT nicht gesetzt sein."""
    try:
        with open(_CLAUDE_JSON) as f:
            servers = json.load(f).get("mcpServers", {})
        return (servers.get(server, {}) or {}).get("url", "") or ""
    except Exception:
        return ""


class MCPClient:
    def __init__(self, endpoint: Optional[str] = None, timeout: float = 15.0):
        self.endpoint = (
            endpoint
            or os.environ.get("AI_REM_ENDPOINT")
            or _endpoint_from_claude_json()
            or "http://localhost:3456/mcp"
        )
        self.timeout = timeout
        self.token = _resolve_token(timeout)
        self._sid: Optional[str] = None

    def _auth_header(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

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
                    "clientInfo": {"name": "ai-rem-cli", "version": "1.0"},
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
