"""`ai-rem svc`: Kopplung, Token-Aufloesung und Proxy gegen einen echten svc_auth-Server.

Der Server laeuft lokal (http.server + lib/svc_auth.stdlib_guard); die Peer-IP wird
auf eine LAN-Adresse umgebogen, sonst wuerde die Loopback-Ausnahme jeden Request
durchwinken und der Test bewiese nichts.
"""
import argparse
import importlib.machinery
import importlib.util
import io
import json
import pathlib
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.svc_auth import SvcAuth, stdlib_guard  # noqa: E402

TOKEN = "svc-test-token"  # pragma: allowlist secret
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                   "clientInfo": {"name": "t", "version": "1"}}}


def _load_cli():
    loader = importlib.machinery.SourceFileLoader("ai_rem_cli_svc", str(ROOT / "bin" / "ai-rem"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture()
def server():
    auth = SvcAuth("demo", TOKEN)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _lan(self):
            self.client_address = ("192.168.1.50", 1)

        def do_POST(self):  # noqa: N802
            self._lan()
            if stdlib_guard(auth, self):
                return
            n = int(self.headers.get("Content-Length") or 0)
            msg = json.loads(self.rfile.read(n))
            body = json.dumps({"jsonrpc": "2.0", "id": msg.get("id"),
                               "result": {"protocolVersion": "2025-03-26"}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("mcp-session-id", "s1")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield auth, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture()
def cli(tmp_path, monkeypatch):
    mod = _load_cli()
    monkeypatch.setattr(mod, "CLIENT_JSON", str(tmp_path / "ai-rem" / "client.json"))
    monkeypatch.setattr(mod, "load_client_cfg", lambda: (
        json.loads((tmp_path / "ai-rem" / "client.json").read_text())
        if (tmp_path / "ai-rem" / "client.json").exists() else {}))
    store = {}
    monkeypatch.setattr(mod.keychain, "set", lambda v, account=None: store.__setitem__(account, v))
    monkeypatch.setattr(mod.keychain, "get", lambda account=None: store.get(account, ""))
    monkeypatch.setattr(mod.keychain, "backend_name", lambda: "Test")
    monkeypatch.delenv("DEMO_TOKEN", raising=False)
    mod._store = store
    return mod


def test_url_normalisierung(cli):
    assert cli._svc_url("x", "https://a.lan/mcp/") == "https://a.lan"
    assert cli._svc_url("x", "https://a.lan/") == "https://a.lan"
    assert cli._svc_url("unbekannt") == ""


def test_token_env_vor_keychain(cli, monkeypatch):
    cli._store["svc-demo"] = "aus-keychain"
    assert (cli._svc_token("demo"), cli._svc_token_source("demo")) == ("aus-keychain", "Keychain (Test)")
    monkeypatch.setenv("DEMO_TOKEN", "aus-env")
    assert (cli._svc_token("demo"), cli._svc_token_source("demo")) == ("aus-env", "Env DEMO_TOKEN")
    monkeypatch.delenv("DEMO_TOKEN")
    del cli._store["svc-demo"]
    assert (cli._svc_token("demo"), cli._svc_token_source("demo")) == ("", "")


def test_pair_holt_token_und_merkt_url(cli, server, monkeypatch):
    auth, base = server
    monkeypatch.setattr("webbrowser.open", lambda url: True)
    real_sleep = time.sleep

    def approve(_s):
        # Statt 3 s zu warten: die offene Kopplung freigeben, als haette der User geklickt.
        real_sleep(0.01)
        for e in auth._pairs.values():
            e["state"] = "approved"

    monkeypatch.setattr(time, "sleep", approve)
    cli.cmd_svc_pair(None, argparse.Namespace(name="demo", url=base + "/mcp"))
    assert cli._store["svc-demo"] == TOKEN
    assert json.loads(pathlib.Path(cli.CLIENT_JSON).read_text())["services"] == {"demo": base}
    assert cli._svc_status_lines() == [f"demo: {base} · token ✓ Keychain (Test)"]


def _proxy(cli, base, token):
    out = io.BytesIO()
    p = cli._McpProxy(base + "/mcp", token, stdin=io.BytesIO((json.dumps(INIT) + "\n").encode()),
                      stdout=out, pair_hint="ai-rem svc pair demo")
    p.run()
    return [json.loads(line) for line in out.getvalue().splitlines()]


def test_proxy_mit_und_ohne_token(cli, server):
    _auth, base = server
    assert _proxy(cli, base, TOKEN)[0]["result"]["protocolVersion"] == "2025-03-26"
    err = _proxy(cli, base, "falsch")[0]["error"]
    assert err["code"] == -32001 and "ai-rem svc pair demo" in err["message"]
