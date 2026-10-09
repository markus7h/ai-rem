"""`ai-rem vault-mcp`: mykeyvault-MCP starten, ohne dass der Vault-Token je auf der
Platte liegt — URL/Token kommen pro Lauf aus /api/client-config, der Pfad zum
gebauten index.js aus client.json (vault_entry)."""
import importlib.machinery
import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib import keychain, mcp_client  # noqa: E402

TOKEN = "tok"  # pragma: allowlist secret
VAULT_TOKEN = "vault-secret"  # pragma: allowlist secret


def _load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture
def cli(monkeypatch, tmp_path):
    monkeypatch.delenv("AI_REM_TOKEN", raising=False)
    monkeypatch.setattr(keychain, "get", lambda: TOKEN)
    monkeypatch.setattr(keychain, "backend_name", lambda: "Fake")
    monkeypatch.setattr(mcp_client, "CLIENT_JSON", str(tmp_path / "client.json"))
    monkeypatch.setattr(shutil, "which", lambda name: "/opt/homebrew/bin/node" if name == "node" else None)
    return _load("ai_rem_cli_vault", ROOT / "bin" / "ai-rem")


def _client(cli, cfg):
    c = cli.MCPClient(endpoint="https://kg.test/mcp")
    c._client_cfg = cfg  # /api/client-config-Antwort vorgeben, kein Netz
    return c


def _write_entry(tmp_path):
    entry = tmp_path / "mykeyvault" / "mcp" / "dist" / "index.js"
    entry.parent.mkdir(parents=True)
    entry.write_text("// mcp")
    (tmp_path / "client.json").write_text(json.dumps(
        {"endpoint": "https://kg.test/mcp", "vault_entry": str(entry)}))
    return entry


def test_execve_mit_vault_env(cli, monkeypatch, tmp_path):
    entry = _write_entry(tmp_path)
    monkeypatch.setenv("NODE_EXTRA_CA_CERTS", "/etc/ssl/own-ca.pem")
    calls = []
    monkeypatch.setattr(cli.os, "execve", lambda path, argv, env: calls.append((path, argv, env)))
    monkeypatch.setattr(subprocess, "call", lambda argv, env=None: calls.append(("call", argv, env)) or 0)
    c = _client(cli, {"version": "1.7.0", "vault_url": "https://vault.test:8223",
                      "vault_token": VAULT_TOKEN})
    try:
        cli.cmd_vault_mcp(c, None)
    except SystemExit as e:  # Windows-Zweig (subprocess.call) endet per sys.exit
        assert e.code == 0
    assert len(calls) == 1
    path, argv, env = calls[0][0], calls[0][1], calls[0][2]
    if path != "call":
        assert path == "/opt/homebrew/bin/node"
    assert argv == ["/opt/homebrew/bin/node", str(entry)]
    assert env["VAULT_API_URL"] == "https://vault.test:8223"
    assert env["VAULT_API_TOKEN"] == VAULT_TOKEN
    assert env["MCP_TRANSPORT"] == "stdio"
    assert env["NODE_EXTRA_CA_CERTS"] == "/etc/ssl/own-ca.pem", "bestehende Env muss erhalten bleiben"


def test_ohne_vault_daten_exit_1(cli, monkeypatch, tmp_path, capsys):
    _write_entry(tmp_path)
    monkeypatch.setattr(cli.os, "execve", lambda *a: pytest.fail("darf nicht starten"))
    for cfg in ({}, {"vault_url": "https://vault.test", "vault_token": ""}):
        with pytest.raises(SystemExit) as e:
            cli.cmd_vault_mcp(_client(cli, cfg), None)
        assert e.value.code == 1
    assert "Vault-Daten nicht verfügbar" in capsys.readouterr().err


def test_ohne_vault_entry_hinweis_auf_update(cli, monkeypatch, tmp_path, capsys):
    (tmp_path / "client.json").write_text(json.dumps({"endpoint": "https://kg.test/mcp"}))
    monkeypatch.setattr(cli.os, "execve", lambda *a: pytest.fail("darf nicht starten"))
    c = _client(cli, {"vault_url": "https://vault.test", "vault_token": VAULT_TOKEN})
    with pytest.raises(SystemExit) as e:
        cli.cmd_vault_mcp(c, None)
    assert e.value.code == 1
    assert "ai-rem update" in capsys.readouterr().err
