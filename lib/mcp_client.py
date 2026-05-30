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


class MCPClient:
    def __init__(self, endpoint: Optional[str] = None, timeout: float = 15.0):
        self.endpoint = endpoint or os.environ.get(
            "AI_REM_ENDPOINT", "http://192.168.2.15:3456/mcp"
        )
        self.timeout = timeout
        self._sid: Optional[str] = None

    def _post(self, body: dict, sid: Optional[str] = None):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
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

    def call(self, tool: str, args: Optional[dict] = None) -> str:
        resp = self._post(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool, "arguments": args or {}},
            },
            sid=self._session(),
        )
        return self._parse(resp)
