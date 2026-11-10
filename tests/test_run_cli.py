"""`ai-rem run --secret ITEM=ENV -- cmd`: Secrets aus mykeyvault landen nur im Env des
exec-Aufrufs, nie auf stdout/stderr; fehlt eines, bricht der Start mit Exit 1 ab."""
import importlib.machinery
import importlib.util
import io
import pathlib
import shutil
import sys
import urllib.error

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib import keychain, mcp_client  # noqa: E402

SECRET = "s3cr3t-value"  # pragma: allowlist secret
CFG = {"version": "1.7.0", "vault_url": "https://vault.test:8223/", "vault_token": "vt"}


def _load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture
def cli(monkeypatch, tmp_path):
    monkeypatch.delenv("AI_REM_TOKEN", raising=False)
    monkeypatch.setattr(keychain, "get", lambda: "tok")
    monkeypatch.setattr(mcp_client, "CLIENT_JSON", str(tmp_path / "client.json"))
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/node" if name == "node" else None)
    return _load("ai_rem_cli_run", ROOT / "bin" / "ai-rem")


def _args(cli, secrets, command):
    return cli.build_parser().parse_args(
        ["run", *(x for s in secrets for x in ("--secret", s)), *command])


def _client(cli, cfg=CFG):
    c = cli.MCPClient(endpoint="https://kg.test/mcp")
    c._client_cfg = cfg
    return c


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def _fake_urlopen(monkeypatch, secrets):
    """GET <vault>/secret/<item> → {"password": …}; unbekanntes Item → 404."""
    seen = []

    def urlopen(req, timeout=None):
        seen.append((req.full_url, req.get_header("Authorization")))
        item = req.full_url.rsplit("/secret/", 1)[1]
        if item not in secrets:
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
        return _Resp(b'{"password": "%s"}' % secrets[item].encode())

    monkeypatch.setattr(mcp_client.urllib.request, "urlopen", urlopen)
    return seen


def test_secrets_nur_im_env_des_exec(cli, monkeypatch, capsys):
    seen = _fake_urlopen(monkeypatch, {"tools-registry%20api%20token": SECRET, "paperless-token": "p2"})
    calls = []
    monkeypatch.setattr(cli.os, "execve", lambda path, argv, env: calls.append((path, argv, env)))
    monkeypatch.setattr(cli.subprocess, "call", lambda argv, env=None: calls.append((argv[0], argv, env)) or 0)
    monkeypatch.setenv("NODE_EXTRA_CA_CERTS", "/etc/ssl/own-ca.pem")
    a = _args(cli, ["tools-registry api token=TOOLS_REGISTRY_TOKEN", "paperless-token=PAPERLESS_TOKEN"],
              ["--", "node", "server/index.mjs", "--flag"])
    try:
        cli.cmd_run(_client(cli), a)
    except SystemExit as e:  # Windows-Zweig
        assert e.code == 0
    assert len(calls) == 1
    path, argv, env = calls[0]
    assert path == "/usr/bin/node"
    assert argv == ["/usr/bin/node", "server/index.mjs", "--flag"], "`--` entfernt, Rest unveraendert"
    assert env["TOOLS_REGISTRY_TOKEN"] == SECRET
    assert env["PAPERLESS_TOKEN"] == "p2"
    assert env["NODE_EXTRA_CA_CERTS"] == "/etc/ssl/own-ca.pem", "bestehende Env bleibt erhalten"
    # Leerzeichen URL-kodiert, kein doppelter Slash, Bearer = vault_token
    assert seen[0] == ("https://vault.test:8223/secret/tools-registry%20api%20token", "Bearer vt")
    out = capsys.readouterr()
    assert SECRET not in out.out + out.err and "p2" not in out.out + out.err


def test_main_ohne_vault_daten_und_404(cli, monkeypatch, capsys):
    monkeypatch.setattr(cli.os, "execve", lambda *a: pytest.fail("darf nicht starten"))
    monkeypatch.setattr(cli.MCPClient, "client_config", lambda self: {})
    monkeypatch.setattr(sys, "argv", ["ai-rem", "run", "--secret", "paperless-token=P", "--", "true"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 1
    assert "Vault-Daten nicht verfügbar" in capsys.readouterr().err

    _fake_urlopen(monkeypatch, {})
    monkeypatch.setattr(cli.MCPClient, "client_config", lambda self: CFG)
    monkeypatch.setattr(sys, "argv", ["ai-rem", "run", "--secret", "gibt-es-nicht=X", "--", "true"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 1
    err = capsys.readouterr().err
    assert "gibt-es-nicht" in err and "404" in err and "vt" not in err


def test_leeres_kommando_und_kaputtes_secret_exit_2(cli, monkeypatch, capsys):
    monkeypatch.setattr(cli.os, "execve", lambda *a: pytest.fail("darf nicht starten"))
    with pytest.raises(SystemExit) as e:
        cli.cmd_run(_client(cli), _args(cli, ["a=B"], ["--"]))
    assert e.value.code == 2 and "Kommando fehlt" in capsys.readouterr().err
    with pytest.raises(SystemExit) as e:
        cli.cmd_run(_client(cli), _args(cli, ["ohne-gleich"], ["--", "true"]))
    assert e.value.code == 2 and "ITEM=ENV" in capsys.readouterr().err
