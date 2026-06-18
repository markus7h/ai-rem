"""Smoke-Test: gebauter mykeyvault-MCP listet die stdio-only exec/file-Tools.

Startet das gebaute index.js im stdio-Modus, macht den MCP-Handshake und prüft,
dass die drei lokal-ausführenden Tools verfügbar sind. Kein Netz/Secret nötig —
prüft genau das Ziel der stdio-Registrierung. Skippt, wenn node oder ein
gebautes dist/ fehlt.
"""
import json
import os
import shutil
import subprocess
import sys

EXEC_TOOLS = {'vault_write_secret', 'vault_run_with_secret', 'vault_run_with_secret_file'}

# Kandidaten für ein gebautes index.js (Setup-Install-Dir zuerst, dann lokaler Klon).
CANDIDATES = [
    os.path.expanduser('~/Code/mykeyvault/mcp/dist/index.js'),
    os.path.expanduser('~/mystorage/myCode/github/mykeyvault/mcp/dist/index.js'),
]


def _entry():
    for c in CANDIDATES:
        if os.path.isfile(c):
            return c
    return ''


def test_stdio_lists_exec_tools():
    if not shutil.which('node'):
        print('SKIP: node nicht installiert')
        return
    entry = _entry()
    if not entry:
        print('SKIP: kein gebautes mcp/dist/index.js gefunden (npm run build im mcp/-Ordner)')
        return

    # stdio-Default + Dummy-Env (Tools werden nur gelistet, nicht aufgerufen → kein echtes Secret).
    env = {**os.environ, 'MCP_TRANSPORT': 'stdio',
           'VAULT_API_URL': 'http://localhost:1', 'VAULT_API_TOKEN': 'x'}
    p = subprocess.Popen(['node', entry], env=env, text=True,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def send(obj):
        p.stdin.write(json.dumps(obj) + '\n')
        p.stdin.flush()

    def recv():
        # MCP-stdio: newline-delimited JSON, eine Nachricht pro Zeile.
        for line in p.stdout:
            line = line.strip()
            if line:
                return json.loads(line)
        raise AssertionError('keine Antwort vom MCP-Server')

    try:
        send({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
              'params': {'protocolVersion': '2024-11-05', 'capabilities': {},
                         'clientInfo': {'name': 'smoke', 'version': '0'}}})
        recv()  # initialize-Result
        send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        send({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
        names = {t['name'] for t in recv()['result']['tools']}
    finally:
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()

    missing = EXEC_TOOLS - names
    assert not missing, 'exec/file-Tools fehlen im stdio-Modus: %s' % missing
    print('OK: stdio listet %s' % ', '.join(sorted(EXEC_TOOLS)))


if __name__ == '__main__':
    test_stdio_lists_exec_tools()
    sys.exit(0)
