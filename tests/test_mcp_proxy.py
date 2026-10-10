"""`ai-rem mcp-proxy`: stdio ↔ Streamable-HTTP-Bruecke, komplett ohne Netz.

Claude Code/opencode starten die CLI als stdio-MCP-Server; der Proxy traegt den
Token aus dem Keychain in die HTTP-Requests. Geprueft wird das Protokollverhalten:
Session-ID ab dem zweiten Request, 202 ohne Ausgabe, SSE-Events als je eine
NDJSON-Zeile, 401 als JSON-RPC-Error mit der richtigen id, DELETE beim EOF und
Exit 1 bei verlorener Session.
"""
import importlib.machinery
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib import keychain, mcp_client  # noqa: E402

TOKEN = "tok"  # pragma: allowlist secret
ENDPOINT = "https://kg.test/mcp"

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                   "clientInfo": {"name": "claude-code", "version": "2.0"}}}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
CALL = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "memory_status", "arguments": {}}}


def _load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class FakeResponse:
    """Minimaler urlopen-Rueckgabewert: Header, Status, zeilenweise lesbarer Body,
    Context-Manager — mehr nutzt der Proxy nicht."""

    def __init__(self, status=200, headers=None, body=b""):
        self.status = status
        self.headers = headers or {}
        self._buf = io.BytesIO(body)

    def getcode(self):
        return self.status

    def read(self):
        return self._buf.read()

    def readline(self):
        return self._buf.readline()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code, body=b""):
    return urllib.error.HTTPError(ENDPOINT, code, "err", {}, io.BytesIO(body))


class FakeServer:
    """Ersatz fuer urllib.request.urlopen: zeichnet Requests auf und antwortet je
    nach JSON-RPC-Methode wie ein Streamable-HTTP-Server (fastmcp)."""

    def __init__(self, sid="sess-1"):
        self.sid = sid
        self.requests = []  # (http_method, headers_lowercase, body_dict|None)
        self.reject_token = False
        self.lose_session = False

    def __call__(self, req, timeout=None):
        headers = {k.lower(): v for k, v in req.header_items()}
        body = json.loads(req.data) if req.data else None
        self.requests.append((req.get_method(), headers, body))
        if req.get_method() == "DELETE":
            return FakeResponse(200)
        if self.reject_token:
            raise _http_error(401, b'{"error":"unauthorized"}')
        if self.lose_session and headers.get("mcp-session-id"):
            raise _http_error(404, b"Session not found")
        method = body.get("method") if isinstance(body, dict) else None
        if method == "initialize":
            result = {"jsonrpc": "2.0", "id": body["id"],
                      "result": {"protocolVersion": "2025-03-26", "capabilities": {},
                                 "serverInfo": {"name": "ai-rem", "version": "1.7.0"}}}
            return FakeResponse(200, {"Content-Type": "application/json",
                                      "mcp-session-id": self.sid},
                                json.dumps(result).encode())
        if "id" not in body:
            return FakeResponse(202, {"Content-Type": "text/plain"})
        # Zwei Events pro Request: eine Log-Notification und das Result, letzteres
        # pretty-printed ueber mehrere data:-Zeilen (SSE joint sie mit \n).
        note = {"jsonrpc": "2.0", "method": "notifications/message",
                "params": {"level": "info", "data": "läuft"}}
        result = {"jsonrpc": "2.0", "id": body["id"],
                  "result": {"content": [{"type": "text", "text": "ok ✓"}]}}
        res_lines = json.dumps(result, ensure_ascii=False, indent=1).splitlines()
        sse = (": keepalive\r\n\r\n"
               "event: message\r\ndata: " + json.dumps(note, ensure_ascii=False) + "\r\n\r\n"
               "event: message\r\n" + "".join("data: %s\r\n" % ln for ln in res_lines) + "\r\n")
        return FakeResponse(200, {"Content-Type": "text/event-stream; charset=utf-8"},
                            sse.encode("utf-8"))


def _ndjson(msgs):
    return b"".join(json.dumps(m, ensure_ascii=False).encode("utf-8") + b"\n" for m in msgs)


def _run(monkeypatch, server, msgs, token=TOKEN):
    """Proxy direkt mit BytesIO-Enden fahren; Rueckgabe (exit_code, stdout-Zeilen als JSON)."""
    cli = _load("ai_rem_cli_proxy", ROOT / "bin" / "ai-rem")
    monkeypatch.setattr(urllib.request, "urlopen", server)
    out = io.BytesIO()
    proxy = cli._McpProxy(ENDPOINT, token, stdin=io.BytesIO(_ndjson(msgs)), stdout=out)
    rc = proxy.run()
    raw_lines = out.getvalue().decode("utf-8").splitlines()
    return rc, [json.loads(line) for line in raw_lines]


def test_session_header_ab_zweitem_request_und_delete_bei_eof(monkeypatch):
    server = FakeServer()
    rc, lines = _run(monkeypatch, server, [INIT, INITIALIZED, CALL])
    assert rc == 0
    posts = [r for r in server.requests if r[0] == "POST"]
    assert [r[2].get("method") for r in posts] == ["initialize", "notifications/initialized", "tools/call"]
    assert "mcp-session-id" not in posts[0][1]
    assert all(r[1]["mcp-session-id"] == "sess-1" for r in posts[1:]), "Session-ID fehlt ab 2. Request"
    assert all(r[1]["authorization"] == "Bearer " + TOKEN for r in posts)
    assert all(r[1]["accept"] == "application/json, text/event-stream" for r in posts)
    # Protokollversion aus der initialize-Antwort wandert in die Folge-Requests.
    assert all(r[1]["mcp-protocol-version"] == "2025-03-26" for r in posts[1:])
    # EOF → DELETE mit Session-ID, als letzter Request.
    assert server.requests[-1][0] == "DELETE"
    assert server.requests[-1][1]["mcp-session-id"] == "sess-1"


def test_202_schweigt_und_sse_events_werden_zeilen(monkeypatch):
    rc, lines = _run(monkeypatch, FakeServer(), [INIT, INITIALIZED, CALL])
    assert rc == 0
    # initialize-Antwort + 2 SSE-Events; die Notification (202) erzeugt nichts.
    assert len(lines) == 3, lines
    assert lines[0]["id"] == 1 and lines[0]["result"]["protocolVersion"] == "2025-03-26"
    assert lines[1]["method"] == "notifications/message"
    assert lines[2]["id"] == 2
    assert lines[2]["result"]["content"][0]["text"] == "ok ✓", "mehrzeiliges data: nicht zusammengefuegt"


def test_401_wird_jsonrpc_error_mit_richtiger_id(monkeypatch, capsys):
    server = FakeServer()
    server.reject_token = True
    rc, lines = _run(monkeypatch, server, [INIT, INITIALIZED, CALL])
    assert rc == 0
    errors = {line["id"]: line["error"] for line in lines}
    assert set(errors) == {1, 2}, "Notification darf keine Antwort bekommen"
    assert errors[1]["code"] == -32001 and "ai-rem pair" in errors[1]["message"]
    assert errors[2]["code"] == -32001
    assert "ai-rem pair" in capsys.readouterr().err


def test_session_verloren_404_beendet_mit_exit_1(monkeypatch):
    server = FakeServer()
    rc, lines = _run(monkeypatch, server, [INIT])
    assert rc == 0 and lines[0]["id"] == 1
    server.lose_session = True
    rc, lines = _run(monkeypatch, server, [INIT, CALL])
    # initialize geht ohne Session durch, der tools/call mit Session kriegt 404.
    assert rc == 1
    assert lines[-1]["id"] == 2 and lines[-1]["error"]["code"] == -32000


def test_cmd_mcp_proxy_nutzt_keychain_token_und_stdio(monkeypatch):
    """Voller Weg ueber cmd_mcp_proxy: Token aus dem Keychain, sys.stdin/stdout."""
    cli = _load("ai_rem_cli_proxy_cmd", ROOT / "bin" / "ai-rem")
    server = FakeServer()
    monkeypatch.setattr(urllib.request, "urlopen", server)
    monkeypatch.delenv("AI_REM_TOKEN", raising=False)
    monkeypatch.delenv("AI_REM_CLIENT", raising=False)
    monkeypatch.setattr(keychain, "get", lambda: TOKEN)
    monkeypatch.setattr(keychain, "backend_name", lambda: "Fake")
    stdin = io.TextIOWrapper(io.BytesIO(_ndjson([INIT, CALL])), encoding="utf-8")
    stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    client = cli.MCPClient(endpoint=ENDPOINT)
    assert client.token == TOKEN and client.token_source == "Keychain (Fake)"
    args = cli.build_parser().parse_args(["mcp-proxy", "--endpoint", ENDPOINT])

    def _exit(code):
        raise SystemExit(code)
    monkeypatch.setattr(cli.os, "_exit", _exit)  # echtes os._exit beendete pytest
    try:
        cli.cmd_mcp_proxy(client, args)
    except SystemExit as e:
        assert e.code == 0
    out = stdout.buffer.getvalue().decode("utf-8").splitlines()
    assert [json.loads(line)["id"] for line in out if '"id"' in line] == [1, 2]
    posts = [r for r in server.requests if r[0] == "POST"]
    assert posts[0][1]["authorization"] == "Bearer " + TOKEN
    # Ohne AI_REM_CLIENT kein X-AI-REM-Client: der Server soll clientInfo.name nehmen.
    assert "x-ai-rem-client" not in posts[0][1]


def test_ausstieg_bei_offenem_stdin_ohne_abort(tmp_path):
    """Regression: nach HTTP 404 beendet sich der Proxy, waehrend der stdin-Leser noch
    in readline() haengt (Claude haelt stdin offen). sys.exit lief in den
    Interpreter-Shutdown, der den Lock von sys.stdin.buffer nicht bekam → abort(),
    SIGABRT, "Python quit unexpectedly". Erwartet: sauberer Exit 1."""
    prog = (
        "import importlib.machinery, importlib.util, os, sys, threading, time\n"
        f"loader = importlib.machinery.SourceFileLoader('cli', {str(ROOT / 'bin' / 'ai-rem')!r})\n"
        "spec = importlib.util.spec_from_loader('cli', loader)\n"
        "cli = importlib.util.module_from_spec(spec); loader.exec_module(cli)\n"
        "def run(self):\n"
        "    threading.Thread(target=sys.stdin.buffer.readline, daemon=True).start()\n"
        "    time.sleep(0.3)\n"
        "    return 1\n"
        "cli._McpProxy.run = run\n"
        "c = cli.MCPClient(endpoint='https://kg.test/mcp')\n"
        "cli.cmd_mcp_proxy(c, cli.build_parser().parse_args(['mcp-proxy']))\n"
    )
    env = {**os.environ, "AI_REM_TOKEN": TOKEN}
    p = subprocess.Popen([sys.executable, "-c", prog], stdin=subprocess.PIPE,
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env)
    # Nicht communicate(): das schliesst stdin sofort, der Leser bekaeme EOF und
    # der Fehler traete nie auf. stdin bleibt offen wie bei Claude Code.
    try:
        p.wait(timeout=30)
        err = p.stderr.read()
    finally:
        p.kill()
        p.stdin.close()
    assert p.returncode == 1, err.decode()[-400:]


def test_iter_sse_events_joint_data_und_ignoriert_kommentare():
    body = b": ping\n\nevent: message\ndata: {\"a\":\ndata:1}\n\ndata: {\"b\":2}\n"
    assert list(mcp_client.iter_sse_events(io.BytesIO(body))) == ['{"a":\n1}', '{"b":2}']
    # _parse nutzt denselben Parser: erstes Event ist die Antwort.
    resp = FakeResponse(200, {}, b'data: {"result":{"content":[{"type":"text","text":"hi"}]}}\n\n')
    assert mcp_client.MCPClient._parse(resp) == "hi"
