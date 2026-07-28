#!/usr/bin/env python3
# ai-rem Setup — plattformneutral (macOS, Ubuntu/Linux, WSL, Windows).
# Wird von den Wrappern geholt+gestartet:
#   bash <(curl -s __KG_URL__/setup)          (macOS/Linux/WSL)
#   irm __KG_URL__/setup.ps1 | iex            (Windows PowerShell)
# Harte Abhaengigkeiten: python3, claude CLI. Optional (nur tools-registry): git, node >= 18, npm.
import glob
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

KG_URL = '__KG_URL__'
HOME = os.path.expanduser('~')
_CC = os.environ.get('CLAUDE_CONFIG_DIR', '').strip()
# ponytail: nimmt bei Doppelpunkt-Liste (mehrere Config-Dirs) das erste; reicht fuer den Normalfall
if _CC:
    _CC = _CC.split(os.pathsep)[0]
CLAUDE_HOME = _CC or os.path.join(HOME, '.claude')
CLAUDE_JSON = os.path.join(_CC, '.claude.json') if _CC else os.path.join(HOME, '.claude.json')
IS_WIN = sys.platform == 'win32'

# Windows-Konsole (cp850/cp1252) wuerde sonst an ✓/✗ scheitern.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


def detect_platform():
    if IS_WIN:
        return 'windows'
    if sys.platform == 'darwin':
        return 'macos'
    if sys.platform.startswith('linux'):
        try:
            with open('/proc/version', encoding='utf-8', errors='replace') as f:
                if 'microsoft' in f.read().lower():
                    return 'wsl'
        except OSError:
            pass
        return 'linux'
    return 'other'


PLATFORM = detect_platform()


def rerun_hint():
    # Die jeweils richtige "erneut ausfuehren"-Zeile fuer diese Plattform.
    if PLATFORM == 'windows':
        return 'irm %s/setup.ps1 | iex' % KG_URL
    return 'bash <(curl -s %s/setup)' % KG_URL


def hint_install(apt_pkgs, brew_pkgs=None, winget_pkgs=None):
    brew_pkgs = brew_pkgs or apt_pkgs
    if PLATFORM == 'macos':
        print('    Installieren (macOS):          brew install %s' % brew_pkgs)
    elif PLATFORM == 'wsl':
        print('    Installieren (WSL/Ubuntu):     sudo apt update && sudo apt install -y %s' % apt_pkgs)
        print('    Hinweis WSL: IN der WSL-Distribution installieren, nicht auf der Windows-Seite.')
    elif PLATFORM == 'linux':
        print('    Installieren (Ubuntu/Debian):  sudo apt update && sudo apt install -y %s' % apt_pkgs)
    elif PLATFORM == 'windows' and winget_pkgs:
        print('    Installieren (Windows):        winget install %s' % winget_pkgs)
    else:
        print('    Installieren: %s' % apt_pkgs)


def http_get(url, timeout=10, insecure=False):
    # insecure=True ist NUR die Erreichbarkeits-Probe (Ersatz fuer curl -k) —
    # nie fuer Inhalte, die danach verwendet werden.
    ctx = ssl._create_unverified_context() if insecure else None
    req = urllib.request.Request(url, headers={'User-Agent': 'ai-rem-setup'})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read()


def fetch_to(url, dst):
    # Atomarer Download: erst Temp-Datei im Zielverzeichnis, nur bei Erfolg +
    # nicht-leer per os.replace ersetzen. Verhindert, dass ein transienter
    # Serverfehler eine bestehende Datei truncatet.
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        data = http_get(url)
    except Exception:
        data = b''
    if not data:
        print('✗ Download fehlgeschlagen, %s unveraendert: %s' % (dst, url))
        return False
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dst), suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        os.replace(tmp, dst)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True


def hook_command(path):
    # Unix: Shebang + chmod reichen, der Command ist der nackte Pfad.
    # Windows: kein Shebang-Exec — python explizit davorsetzen. -X utf8, weil
    # die Hooks JSON/MD mit UTF-8-Inhalt ohne explizites encoding= lesen und
    # der Windows-Default (cp1252) daran scheitern wuerde.
    if IS_WIN:
        return '"%s" -X utf8 "%s"' % (sys.executable, path)
    return path


def run(cmd, timeout=120, capture=True, cwd=None):
    return subprocess.run(cmd, capture_output=capture, text=True,
                          timeout=timeout, cwd=cwd)


# ── Preflight: claude CLI ─────────────────────────────────────────────────────

def find_claude():
    claude = shutil.which('claude')
    if claude:
        return claude
    print('✗ claude CLI fehlt - ohne sie kann das Setup nichts registrieren.')
    if PLATFORM == 'windows':
        print('    Installieren (Windows):           irm https://claude.ai/install.ps1 | iex')
    else:
        print('    Installieren (alle Plattformen):  curl -fsSL https://claude.ai/install.sh | bash')
    print('    …oder via npm:                    npm install -g @anthropic-ai/claude-code')
    if PLATFORM == 'wsl':
        print("    Hinweis WSL: claude muss IN der WSL-Distribution installiert sein ('which claude' in WSL pruefen).")
    print('    Danach erneut ausfuehren:  %s' % rerun_hint())
    sys.exit(1)


def register_mcp(claude):
    try:
        listed = run([claude, 'mcp', 'list'], timeout=60).stdout or ''
    except Exception:
        listed = ''
    if 'kg-memory' in listed:
        run([claude, 'mcp', 'remove', 'kg-memory'], timeout=60)
        print('✓ Alte kg-memory Registrierung entfernt')
    if 'ai-rem' in listed:
        print('✓ MCP bereits registriert')
        return
    p = run([claude, 'mcp', 'add', '--transport', 'http', '--scope', 'user',
             'ai-rem', KG_URL + '/mcp'], timeout=120)
    if p.returncode != 0:
        print("✗ 'claude mcp add' fehlgeschlagen - claude CLI zu alt? Aktualisieren mit:  claude update")
        if (p.stderr or '').strip():
            print('  | ' + (p.stderr or '').strip().splitlines()[-1])
        print('  Danach erneut ausfuehren:  %s' % rerun_hint())
        sys.exit(1)
    print('✓ MCP registriert (ai-rem)')


# ── setup-config + TLS-Endpoint-Wahl ─────────────────────────────────────────

def load_setup_config():
    try:
        cfg = json.loads(http_get(KG_URL + '/setup-config').decode('utf-8'))
    except Exception:
        cfg = {}
    if not cfg:
        print('⚠ setup-config nicht ladbar - personalisierte Teile (tools-registry, Vault, Entities) werden uebersprungen')
    return cfg


def choose_mcp_endpoint(setup_cfg):
    # TLS (https) bevorzugt, sonst http-Fallback. Der Bootstrap-Fetch laeuft
    # bewusst weiter ueber http://IP (kein Cert noetig). Den /mcp-Kanal (traegt
    # den Bearer bei JEDEM Call) auf https umstellen, ABER nur wenn der TLS-Host
    # auf DIESER Maschine erreichbar UND vertraut ist — sonst Fallback, damit
    # ein Host ohne Caddy-Root-CA nicht 401/Cert-bricht.
    endpoint = KG_URL + '/mcp'
    https_base = setup_cfg.get('ai_rem_https_url', '')
    if not https_base:
        return endpoint

    def probe(insecure):
        try:
            http_get(https_base + '/health', timeout=6, insecure=insecure)
            return True
        except urllib.error.HTTPError:
            return True  # Antwort vom Server = erreichbar
        except Exception:
            return False

    if probe(insecure=False):
        endpoint = https_base + '/mcp'
        print('✓ TLS-Endpoint nutzbar: %s' % endpoint)
    elif probe(insecure=True):
        # Erreichbar, aber Handshake scheitert ohne insecure => Root-CA fehlt hier.
        print('⚠ TLS-Endpoint %s erreichbar, aber Zertifikat NICHT vertraut - bleibe bei %s' % (https_base, endpoint))
        print('  Fuer TLS die Caddy-Root-CA dieser Maschine bekannt machen')
        print('  (liegt im Caddy-Container unter /data/caddy/pki/authorities/local/root.crt):')
        if PLATFORM == 'macos':
            print('    sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain root.crt')
        elif PLATFORM == 'windows':
            print('    certutil -addstore -f Root root.crt   (Admin-PowerShell)')
            print('    Fuer Node/npm zusaetzlich:  setx NODE_EXTRA_CA_CERTS C:\\pfad\\zu\\root.crt')
        else:
            print('    sudo cp root.crt /usr/local/share/ca-certificates/caddy-root.crt && sudo update-ca-certificates')
            if PLATFORM == 'wsl':
                print('    (in der WSL-Distribution ausfuehren - der Windows-Zertifikatsspeicher zaehlt hier NICHT)')
        print('    Danach Setup erneut ausfuehren - der /mcp-Kanal migriert dann automatisch auf https.')
    else:
        print('ℹ TLS-Endpoint %s nicht erreichbar - bleibe bei %s' % (https_base, endpoint))
    return endpoint


# ── Bootstrap-Secrets per SSH von mystorage ziehen ───────────────────────────
# /setup ist oeffentlich (anonymer Download), Secrets liegen also NICHT im
# Script-Body. Stattdessen zieht der bereits per SSH-Key vertraute Host die
# Tokens direkt aus den .env-Dateien auf dem Server — ai-rem bleibt damit KEIN
# Secret-Verteiler. Override: AI_REM_TOKEN / VAULT_API_TOKEN im Env haben Vorrang.

def pull_secrets(setup_cfg):
    ssh_host = os.environ.get('AI_REM_SSH_HOST') or setup_cfg.get('ssh_host', 'mystorage')
    ssh = shutil.which('ssh')
    ssh_ok = False
    if ssh:
        try:
            ssh_ok = run([ssh, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
                          ssh_host, 'true'], timeout=20).returncode == 0
        except Exception:
            ssh_ok = False
    if not ssh_ok:
        extra = '' if ssh else ' (ssh-Client fehlt)'
        print('⚠ SSH zu %s nicht erreichbar%s — Secrets nur aus Env' % (ssh_host, extra))
        print('  SSH-Key-Anleitung (Schritt fuer Schritt): %s/install' % KG_URL)

    def remote_env(remote_file, key):
        try:
            p = run([ssh, ssh_host,
                     "grep -h '^%s=' %s 2>/dev/null | head -1 | cut -d= -f2-" % (key, remote_file)],
                    timeout=20)
            return (p.stdout or '').strip()
        except Exception:
            return ''

    ai_rem_token = os.environ.get('AI_REM_TOKEN', '')
    if not ai_rem_token and ssh_ok:
        ai_rem_token = remote_env('mydocker/compose-files/ai-rem/.env', 'AI_REM_API_TOKEN')
    vault_token = os.environ.get('VAULT_API_TOKEN', '')
    if not vault_token and ssh_ok:
        vault_token = remote_env('mydocker/compose-files/mykeyvault/.env', 'VAULT_API_TOKEN')
    return ssh_host, ai_rem_token, vault_token


# ── tools-registry (stdio) klonen+bauen, falls in setup-config ────────────────────

def _build_node_mcp(repo, install_dir, entry, subdir, label):
    # Generisch: git clone/pull + npm install/build eines Node-MCP.
    # entry ist install_dir-relativ (z.B. dist/index.js oder mcp/dist/index.js);
    # subdir ist der Ordner mit package.json als npm-cwd ('' = install_dir selbst).
    # Gibt den Entry-Pfad zurueck oder '' bei fehlenden Tools / Build-Fehler.
    miss = [c for c in ('node', 'npm', 'git') if not shutil.which(c)]
    node_major = 0
    if shutil.which('node'):
        try:
            v = run(['node', '-v'], timeout=20).stdout.strip().lstrip('v')
            node_major = int(v.split('.')[0])
        except Exception:
            node_major = 0
    if miss or node_major < 18:
        print('')
        print('================================================================')
        print('!!  %s NICHT eingerichtet - Node.js >= 18 inkl. npm + git wird benoetigt.' % label)
        if miss:
            print('    Fehlende Programme: %s' % ' '.join(miss))
        elif node_major < 18:
            print('    Node.js v%s ist zu alt (mindestens v18 noetig).' % node_major)
        if PLATFORM == 'macos':
            print('    Installieren:  brew install node git')
        elif PLATFORM == 'windows':
            print('    Installieren:  winget install OpenJS.NodeJS.LTS Git.Git')
        elif PLATFORM in ('wsl', 'linux'):
            print('    Ubuntu/Debian-apt liefert oft ein zu altes Node - aktuelles Node via NodeSource:')
            print('      curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs git')
            print('    Alternativ nvm:  https://github.com/nvm-sh/nvm  (nvm install --lts)')
            if PLATFORM == 'wsl':
                print('    Hinweis WSL: Node IN der WSL-Distribution installieren; nicht unter /mnt/c ablegen (langsam, exec-Probleme).')
        else:
            print('    Installieren:  Node.js >= 18 inkl. npm + git')
        print('    Danach erneut ausfuehren:  %s' % rerun_hint())
        print('================================================================')
        return ''

    tdir = os.path.expanduser(install_dir)
    git = shutil.which('git')
    npm = shutil.which('npm')

    if os.path.isdir(os.path.join(tdir, '.git')):
        if run([git, '-C', tdir, 'pull', '--ff-only'], timeout=120).returncode != 0:
            print('⚠ git pull in %s fehlgeschlagen - baue mit vorhandenem Stand' % tdir)
    else:
        os.makedirs(os.path.dirname(tdir), exist_ok=True)
        if run([git, 'clone', '--depth', '1', repo, tdir], timeout=300).returncode != 0:
            print('✗ git clone %s fehlgeschlagen (Netz/Repo-Zugriff pruefen)' % repo)

    build_cwd = os.path.join(tdir, subdir) if subdir else tdir
    build_ok = False
    if os.path.isdir(build_cwd):
        log = ''
        build_ok = True
        for cmd in ([npm, 'install', '--no-audit', '--no-fund'], [npm, 'run', 'build']):
            p = run(cmd, timeout=600, cwd=build_cwd)
            log += (p.stdout or '') + (p.stderr or '')
            if p.returncode != 0:
                build_ok = False
                print('✗ %s npm-Build fehlgeschlagen - letzte Log-Zeilen:' % label)
                for line in log.splitlines()[-12:]:
                    print('    | ' + line)
                if re.search(r'SELF_SIGNED_CERT|UNABLE_TO_GET_ISSUER|UNABLE_TO_VERIFY_LEAF|CERT_UNTRUSTED|certificate', log, re.I):
                    print('  ↳ Zertifikatsproblem (Proxy/eigene Root-CA in der npm-Kette). Abhilfe:')
                    print('      npm config set cafile /pfad/zur/root-ca.pem')
                    print('      oder:  NODE_EXTRA_CA_CERTS=/pfad/zur/root-ca.pem setzen')
                if 'EACCES' in log:
                    print('  ↳ Rechteproblem (EACCES): Besitzer von %s und ~/.npm pruefen - npm nie mit sudo ausfuehren.' % build_cwd)
                break

    entry_path = os.path.join(tdir, *entry.split('/'))
    if os.path.isfile(entry_path):
        if build_ok:
            print('OK %s gebaut: %s' % (label, entry_path))
        else:
            # fail-soft: alter Build bleibt nutzbar, aber ehrlich melden statt "OK"
            print('⚠ %s Build fehlgeschlagen - verwende vorhandenen ALTEN Build: %s' % (label, entry_path))
        return entry_path
    print('!! %s Build fehlgeschlagen - manuell pruefen: cd %s && npm install && npm run build' % (label, build_cwd))
    return ''


def build_tools_mcp(setup_cfg):
    stdio = setup_cfg.get('mcp_register', {}).get('tools', {}).get('stdio', {})
    reg_url = stdio.get('registry_url', '')
    if not reg_url:
        return '', ''
    entry = _build_node_mcp(stdio.get('repo', ''),
                            stdio.get('install_dir') or os.path.join('~', 'Code', 'tools-registry'),
                            stdio.get('entry') or 'dist/index.js', '', 'tools-registry')
    return entry, reg_url


def build_mykeyvault_mcp(setup_cfg):
    # mykeyvault-MCP lokal bauen (stdio, voller Funktionsumfang). '' wenn kein
    # stdio-Block konfiguriert oder Build fehlschlaegt -> HTTP-Fallback greift.
    stdio = setup_cfg.get('mcp_register', {}).get('mykeyvault', {}).get('stdio', {})
    if not stdio.get('repo'):
        return ''
    return _build_node_mcp(stdio['repo'],
                           stdio.get('install_dir') or os.path.join('~', 'Code', 'mykeyvault'),
                           stdio.get('entry') or 'mcp/dist/index.js',
                           stdio.get('subdir', 'mcp'), 'mykeyvault-MCP')


# ── ai-rem Bearer setzen + mykeyvault bootstrappen (atomar in ~/.claude.json) ─
# Damit die ERSTE Session nicht 401t; danach refresht der SessionStart-Hook.

def update_claude_json(setup_cfg, mcp_endpoint, ssh_host, ai_rem_token,
                       vault_token, tools_entry, tools_reg_url, vault_entry=''):
    cj = CLAUDE_JSON
    if not os.path.exists(cj):
        print('⚠ ~/.claude.json fehlt - claude einmal interaktiv starten, dann Setup erneut ausfuehren')
        return ''
    with open(cj, encoding='utf-8') as f:
        cfg = json.load(f)
    servers = cfg.setdefault('mcpServers', {})
    if 'ai-rem' not in servers:
        print('⚠ ai-rem nicht in ~/.claude.json registriert - Bearer/Vault-Bootstrap uebersprungen')
        return ''

    reg = setup_cfg.get('mcp_register', {}).get('mykeyvault', {})
    vault_url = os.environ.get('VAULT_API_URL') or reg.get('vault_url', 'http://mystorage:8223')

    # Runtime-Endpoint setzen (https-mit-Fallback) — migriert auch bestehende
    # http-Registrierungen bei Re-Run auf TLS.
    if mcp_endpoint:
        servers['ai-rem']['url'] = mcp_endpoint

    def from_vault(url, vt):
        req = urllib.request.Request(url.rstrip('/') + '/secret/ai-rem-api-token',
                                     headers={'Authorization': 'Bearer ' + vt})
        return json.loads(urllib.request.urlopen(req, timeout=10).read().decode('utf-8')).get('password', '')

    # (1) ai-rem Bearer: AI_REM_TOKEN (SSH-Pull/Env) > frischer Vault-Read > bestehende Koordinaten
    tok = ai_rem_token
    if not tok and vault_token:
        try:
            tok = from_vault(vault_url, vault_token)
        except Exception:
            pass
    if not tok and 'mykeyvault' in servers:
        try:
            e = servers['mykeyvault']['env']
            tok = from_vault(e['VAULT_API_URL'], e['VAULT_API_TOKEN'])
        except Exception:
            pass

    if tok:
        servers['ai-rem'].setdefault('headers', {})['Authorization'] = 'Bearer ' + tok
        print('✓ ai-rem Bearer-Header gesetzt')
    else:
        print('✗ ai-rem-Token nicht ermittelbar — SSH-Zugang zu %s einrichten oder erneut mit:' % ssh_host)
        if PLATFORM == 'windows':
            print('  $env:AI_REM_TOKEN="<token>"; %s' % rerun_hint())
        else:
            print('  AI_REM_TOKEN=<token> %s' % rerun_hint())

    # (2) mykeyvault registrieren: bevorzugt lokaler stdio-MCP (voller
    # Funktionsumfang inkl. exec/file-Tools), sonst HTTP-Fallback (nur list/create).
    if vault_entry and vault_token:
        existed = 'mykeyvault' in servers
        servers['mykeyvault'] = {'type': 'stdio', 'command': 'node',
                                 'args': [vault_entry],
                                 'env': {'VAULT_API_URL': vault_url,
                                         'VAULT_API_TOKEN': vault_token}}
        print('✓ mykeyvault ' + ('migriert' if existed else 'registriert') + ' (stdio)')
    else:
        # HTTP-Fallback (kein Build/node noetig). Kandidaten nur registrieren, wenn
        # der Host von DIESER Maschine aus antwortet (DNS aufloesbar + TLS vertraut)
        # — eine 4xx-Antwort genuegt als Lebenszeichen.
        def reachable(url):
            try:
                http_get(url, timeout=5)
                return True
            except urllib.error.HTTPError:
                return True
            except Exception:
                return False

        reg_http = reg.get('http') or {}
        ai_https = servers.get('ai-rem', {}).get('url', '').startswith('https')
        mkv_url = os.environ.get('MYKEYVAULT_URL', '')
        if not mkv_url:
            cands = []
            if ai_https and reg_http.get('https_url'):
                cands.append(reg_http['https_url'])
            if reg_http.get('url'):
                cands.append(reg_http['url'])
            for c in cands:
                if reachable(c):
                    mkv_url = c
                    break
                print('⚠ mykeyvault-Kandidat nicht erreichbar/vertraut, ueberspringe: %s' % c)
        if mkv_url and tok:
            existed = 'mykeyvault' in servers
            servers['mykeyvault'] = {'type': 'http', 'url': mkv_url,
                                     'headers': {'Authorization': 'Bearer ' + tok}}
            print('✓ mykeyvault ' + ('migriert' if existed else 'registriert')
                  + (' (https)' if mkv_url.startswith('https') else ' (http)'))
    if vault_token:
        vf = os.path.join(CLAUDE_HOME, 'ai-rem-vault.env')
        fd = os.open(vf, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write('VAULT_API_URL=%s\nVAULT_API_TOKEN=%s\n' % (vault_url, vault_token))

    # (3) tools als stdio-MCP registrieren (gebaut aus Registry-Repo)
    if tools_entry and tools_reg_url:
        existed = 'tools' in servers
        servers['tools'] = {'type': 'stdio', 'command': 'node',
                            'args': [tools_entry],
                            'env': {'TOOLS_REGISTRY_URL': tools_reg_url}}
        print('✓ tools ' + ('migriert' if existed else 'registriert') + ' (stdio)')

    tmp = cj + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, cj)
    return tok


# ── settings-template.json: immer aus setup-config neu schreiben ─────────────
# Damit Config-Aenderungen (Permissions, Deny, SMB, …) bei jedem Re-Run propagieren.

def write_settings_template(setup_cfg, mcp_endpoint):
    tmpl = {
        'version': '2026-05-25',
        'ai_rem_endpoint': mcp_endpoint or (KG_URL + '/mcp'),
        'smb': setup_cfg.get('smb', {}),
        'mcp_stdio_servers': setup_cfg.get('mcp_stdio_servers', {}),
        'tools_scripts_dir': setup_cfg.get('tools_scripts_dir', ''),
        'ollama_url': setup_cfg.get('ollama_url', 'http://myubuntu:11434'),
        'general': {'model': 'opus', 'autoMemoryEnabled': False, 'theme': 'auto'},
        'permissions_allow_portable': setup_cfg.get('permissions_allow_portable', [
            # Nur noch die 4 Kern-MCP-Tools (Issue #32). Admin-Ops laufen über
            # `Bash` (ai-rem CLI / curl POST /api/tool), das ohnehin erlaubt ist.
            'Bash', 'Skill(update-config)', 'Skill(update-config:*)',
            'mcp__ai-rem__memory_get_context', 'mcp__ai-rem__memory_search',
            'mcp__ai-rem__memory_add', 'mcp__ai-rem__memory_relate',
        ]),
        'permissions_allow_path_templates': ['Read(//{HOME}/.claude/**)', 'Read(//{TMP}/**)'],
        'permissions_deny': setup_cfg.get('permissions_deny', []),
        'hooks': {
            'SessionStart': ['system-check.py (ai-rem, SMB, MCP, settings-sync, tools)'],
            'UserPromptSubmit': ['Tool-Discovery'],
            'PreToolUse': ['claude-md-guard.py (warnt bei CLAUDE.md-Edits → ai-rem)'],
            'PostToolUse': ['save-plan.py (ExitPlanMode → offener Task in ai-rem)'],
        },
        'additional_directories_templates': ['{HOME}/.claude', '{HOME}'],
        'path_mappings': setup_cfg.get('path_mappings', {}),
    }
    with open(os.path.join(CLAUDE_HOME, 'settings-template.json'), 'w', encoding='utf-8') as f:
        json.dump(tmpl, f, indent=2, ensure_ascii=False)
        f.write('\n')
    print('✓ settings-template.json aktualisiert')


# ── Hooks deployen ────────────────────────────────────────────────────────────

def install_hooks():
    paths = {}
    for fname, label in (('system-check.py', 'SessionStart-Hook'),
                         ('auto-memory.py', 'Auto-Memory-Hook'),
                         ('claude-md-guard.py', 'CLAUDE.md-Guard-Hook'),
                         ('save-plan.py', 'Plan-Saving-Hook')):
        dst = os.path.join(CLAUDE_HOME, 'hooks', fname)
        if fetch_to(KG_URL + '/hooks/' + fname, dst):
            if not IS_WIN:
                os.chmod(dst, 0o755)
            paths[fname] = dst
            print('✓ %s: %s' % (label, dst))
    return paths


LOCAL_CLI = os.path.join(HOME, '.local', 'share', 'ai-rem', 'bin', 'ai-rem')


def points_at_clone(path):
    """True, wenn der Pfad in einen ai-rem-Clone zeigt (statt in die lokale Kopie)."""
    return path.replace('\\', '/').endswith('/github/ai-rem/bin/ai-rem')


def install_cli():
    """CLI lokal ablegen. Leerer String = Download fehlgeschlagen.

    Vorher zeigte AI_REM_CLI auf den Clone. Lag der auf einem Netzlaufwerk, war
    die CLI beim Session-Ende weg, sobald der Mount hing — der Auto-Memory-Hook
    meldete dann still "CLI not found". Die lokale Kopie kennt kein Mount.
    """
    if not fetch_to(KG_URL + '/bin/ai-rem', LOCAL_CLI):
        return ''
    if not IS_WIN:
        os.chmod(LOCAL_CLI, 0o755)
    print('✓ CLI: %s' % LOCAL_CLI)
    return LOCAL_CLI


# ── settings.json: Permissions, Hooks registrieren, alte Hooks entfernen ─────

def update_settings(setup_cfg, mcp_endpoint, hook_paths):
    path = os.path.join(CLAUDE_HOME, 'settings.json')
    tmpl_path = os.path.join(CLAUDE_HOME, 'settings-template.json')
    data = {}
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    tmpl = {}
    if os.path.exists(tmpl_path):
        with open(tmpl_path, encoding='utf-8') as f:
            tmpl = json.load(f)

    perms = data.setdefault('permissions', {})
    allow = perms.setdefault('allow', [])
    allow[:] = [p.replace('mcp__kg-memory__', 'mcp__ai-rem__') for p in allow]

    allow_set = set(allow)
    added = []
    for p in tmpl.get('permissions_allow_portable', []):
        if p not in allow_set and not any(
            a.endswith('*') and p.startswith(a[:-1]) for a in allow_set
        ):
            allow.append(p)
            added.append(p)

    deny = perms.setdefault('deny', [])
    deny_set = set(deny)
    added_deny = []
    for p in tmpl.get('permissions_deny', []):
        if p not in deny_set:
            deny.append(p)
            added_deny.append(p)

    # autoMemoryEnabled ist ein System-Invariant (Auto-Memory ist deaktiviert) und
    # wird erzwungen; model/theme sind User-Preferences, nur gesetzt falls leer.
    forced = {'autoMemoryEnabled'}
    for key, val in tmpl.get('general', {}).items():
        if key in forced:
            data[key] = val
        else:
            data.setdefault(key, val)

    def hook_group(event, matcher):
        groups = hooks.setdefault(event, [])
        g = next((x for x in groups if x.get('matcher') == matcher), None)
        if g is None:
            g = {'matcher': matcher, 'hooks': []}
            groups.append(g)
        g.setdefault('hooks', [])
        return g

    def has_hook(group, hook_file):
        # Erkennt beide Command-Formen: nackter Pfad (Unix) und
        # 'python "...\\hook.py"' (Windows) — auch ueber Re-Runs hinweg.
        base = os.path.basename(hook_file)
        return any(base in h.get('command', '') for h in group['hooks'])

    hooks = data.setdefault('hooks', {})
    group = hook_group('SessionStart', '*')

    old_hooks = ['ai-rem-bootstrap.py', 'ai-rem-bootstrap.sh', 'settings-sync-check.py']
    old_hooks.extend(setup_cfg.get('old_hooks', []))
    group['hooks'] = [
        h for h in group['hooks']
        if not any(o in h.get('command', '') for o in old_hooks)
    ]

    hook_path = hook_paths.get('system-check.py', '')
    hook_added = False
    if hook_path and not has_hook(group, hook_path):
        group['hooks'].append({'type': 'command', 'command': hook_command(hook_path), 'timeout': 15})
        hook_added = True

    auto_mem = hook_paths.get('auto-memory.py', '')
    auto_mem_added = []
    if auto_mem:
        for event in ('PreCompact', 'SessionEnd'):
            g = hook_group(event, '*')
            if not has_hook(g, auto_mem):
                g['hooks'].append({'type': 'command', 'command': hook_command(auto_mem), 'timeout': 120})
                auto_mem_added.append(event)

    guard = hook_paths.get('claude-md-guard.py', '')
    guard_added = False
    if guard:
        g = hook_group('PreToolUse', 'Write|Edit|MultiEdit')
        if not has_hook(g, guard):
            g['hooks'].append({'type': 'command', 'command': hook_command(guard), 'timeout': 10})
            guard_added = True

    save_plan = hook_paths.get('save-plan.py', '')
    save_plan_added = False
    if save_plan:
        g = hook_group('PostToolUse', 'ExitPlanMode')
        if not has_hook(g, save_plan):
            g['hooks'].append({'type': 'command', 'command': hook_command(save_plan), 'timeout': 10})
            save_plan_added = True

    # Env fuer Hook + CLI hinterlegen, damit Auto-Memory ohne manuelle Env laeuft:
    # - AI_REM_ENDPOINT kennt der Bootstrap bereits (MCP_ENDPOINT, TLS-aufgeloest)
    # - AI_REM_CLI per Discovery (inkl. SMB-Mount /Volumes/<x>/myCode auf macOS)
    # setdefault => bewusste manuelle Overrides bleiben erhalten.
    env = data.setdefault('env', {})
    if mcp_endpoint:
        env.setdefault('AI_REM_ENDPOINT', mcp_endpoint)

    def usable_cli(p):
        # X_OK ist auf Windows bedeutungslos; dort ruft der Hook die CLI eh via python auf.
        return p and os.path.isfile(p) and (IS_WIN or os.access(p, os.X_OK))

    cli = ''
    for c in (os.environ.get('AI_REM_CLI', ''),
              os.path.join(HOME, 'myCode', 'github', 'ai-rem', 'bin', 'ai-rem'),
              LOCAL_CLI):
        if usable_cli(c):
            cli = c
            break
    if not cli and PLATFORM == 'macos':
        for p in sorted(glob.glob('/Volumes/*/myCode/github/ai-rem/bin/ai-rem')):
            if usable_cli(p):
                cli = p
                break
    if cli:
        env.setdefault('AI_REM_CLI', cli)
    # Die frisch deployte lokale Kopie gewinnt gegen jeden Clone-Pfad: die ist die
    # einzige, die keinen Mount braucht. Ein manuell gesetztes AI_REM_CLI, das auf
    # etwas anderes als einen Clone zeigt, bleibt unangetastet. Trenner normalisiert,
    # weil in settings.json unter Windows beide Varianten stehen koennen.
    if usable_cli(LOCAL_CLI) and points_at_clone(env.get('AI_REM_CLI', '')):
        env['AI_REM_CLI'] = LOCAL_CLI

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    for line in ('' if not added else '  +%d allow permissions' % len(added),
                 '' if not added_deny else '  +%d deny rules' % len(added_deny),
                 '  SessionStart-Hook' if hook_added else '',
                 '  Auto-Memory-Hooks: %s' % ', '.join(auto_mem_added) if auto_mem_added else '',
                 '  CLAUDE.md-Guard-Hook' if guard_added else '',
                 '  Plan-Saving-Hook' if save_plan_added else '',
                 '  autoMemoryEnabled=false'):
        if line:
            print(line)
    print('✓ settings.json aktualisiert')


# ── CLAUDE.md: minimaler Pointer auf ai-rem ──────────────────────────────────
# (Regeln kommen ueber MCP Server Instructions)

def update_claude_md():
    path = os.path.join(CLAUDE_HOME, 'CLAUDE.md')
    new_block = '''
## ai-rem
ai-rem ist die einzige Wissensquelle für persistenten Kontext. Auto-Memory ist deaktiviert.
Nutzungsregeln kommen über die MCP Server Instructions, Verhaltensregeln aus den ai-rem Preferences.

<!-- Auto-Memory md-Fallback: bei Ollama-Ausfall befüllt, vom catchup geleert -->
@~/.claude/auto-memory/fallback.md
'''
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = ''
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            text = f.read()

    # Bestehenden ai-rem-Block (alt oder neu) entfernen — auch am Dateianfang
    # (ohne fuehrendes \n) und mehrfach vorhandene Bloecke (Idempotenz).
    for pat in (re.compile(r'(?:^|\n)## Knowledge Graph Memory \(ai-rem\)[\s\S]*?(?=\n## |\Z)'),
                re.compile(r'(?:^|\n)## ai-rem[\s\S]*?(?=\n## |\Z)')):
        text = pat.sub('', text)

    text = text.strip()
    if text:
        text += '\n\n'
    text += new_block.lstrip('\n')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    print('✓ CLAUDE.md aktualisiert (minimaler ai-rem Pointer)')


# ── Slash-Commands installieren ──────────────────────────────────────────────

def install_commands():
    for stale in ('setup-kg-memory.md', os.path.join('ai-rem', 'prefedit.md')):
        path = os.path.join(CLAUDE_HOME, 'commands', stale)
        if os.path.isfile(path):
            os.unlink(path)
            print('✓ Alter Command entfernt: %s' % stale)

    if fetch_to(KG_URL + '/cmd', os.path.join(CLAUDE_HOME, 'commands', 'setup-ai-rem.md')):
        print('✓ /setup-ai-rem Command angelegt')
    if fetch_to(KG_URL + '/cmd/memory-cleanup', os.path.join(CLAUDE_HOME, 'commands', 'memory-cleanup.md')):
        print('✓ /memory-cleanup Command angelegt')
    if fetch_to(KG_URL + '/cmd/migrate-claude-md', os.path.join(CLAUDE_HOME, 'commands', 'migrate-claude-md.md')):
        print('✓ /migrate-claude-md Command angelegt')


# ── Preferences & Tool-Entities direkt via MCP API anlegen ───────────────────
# (kein Claude-Token-Verbrauch)

def create_entities(setup_cfg, ai_rem_token):
    mcp_url = KG_URL + '/mcp'
    token = ai_rem_token or os.environ.get('AI_REM_TOKEN', '')
    if not token:
        try:
            with open(CLAUDE_JSON, encoding='utf-8') as f:
                auth = json.load(f)['mcpServers']['ai-rem']['headers']['Authorization']
            token = auth.split()[-1] if auth else ''
        except Exception:
            token = ''

    sid = {'v': None}

    def post(body, with_sid=True):
        hdrs = {'Content-Type': 'application/json',
                'Accept': 'application/json, text/event-stream'}
        if token:
            hdrs['Authorization'] = 'Bearer ' + token
        if with_sid and sid['v']:
            hdrs['mcp-session-id'] = sid['v']
        req = urllib.request.Request(mcp_url, data=json.dumps(body).encode('utf-8'),
                                     headers=hdrs, method='POST')
        return urllib.request.urlopen(req, timeout=10)

    def parse(resp):
        raw = resp.read().decode('utf-8')
        m = re.search(r'^data: (.+)$', raw, re.MULTILINE)
        try:
            obj = json.loads(m.group(1) if m else raw)
            return obj.get('result', {}).get('content', [{}])[0].get('text', '')
        except Exception:
            return ''

    def session():
        if sid['v']:
            return sid['v']
        resp = post({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                     'params': {'protocolVersion': '2024-11-05', 'capabilities': {},
                                'clientInfo': {'name': 'setup', 'version': '1.0'}}},
                    with_sid=False)
        sid['v'] = resp.headers.get('mcp-session-id')
        resp.read()
        try:
            post({'jsonrpc': '2.0', 'method': 'notifications/initialized'}).read()
        except Exception:
            pass
        return sid['v']

    def tool(name, args):
        session()
        return parse(post({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                           'params': {'name': name, 'arguments': args}}))

    entities = setup_cfg.get('entities', [
        {'name': 'skill_setup_ai_rem', 'type': 'Tool',
         'description': 'Slash-Command /setup-ai-rem: ai-rem MCP-Server auf neuem System einrichten.'},
    ])
    try:
        for e in entities:
            tool('memory_add', e)
        print('✓ %d Preferences & Tool-Entities aktualisiert' % len(entities))
    except Exception as ex:
        print('⚠ Entities: %s' % ex)


# ── Ablauf ────────────────────────────────────────────────────────────────────

def main():
    print('=== ai-rem Setup (%s) ===' % PLATFORM)
    claude = find_claude()
    register_mcp(claude)

    os.makedirs(os.path.join(CLAUDE_HOME, 'hooks'), exist_ok=True)
    os.makedirs(os.path.join(CLAUDE_HOME, 'commands'), exist_ok=True)

    setup_cfg = load_setup_config()
    mcp_endpoint = choose_mcp_endpoint(setup_cfg)
    ssh_host, ai_rem_token, vault_token = pull_secrets(setup_cfg)
    tools_entry, tools_reg_url = build_tools_mcp(setup_cfg)
    vault_entry = build_mykeyvault_mcp(setup_cfg)

    try:
        tok = update_claude_json(setup_cfg, mcp_endpoint, ssh_host, ai_rem_token,
                                 vault_token, tools_entry, tools_reg_url, vault_entry)
    except Exception as ex:
        print('⚠ ~/.claude.json-Update fehlgeschlagen: %s' % ex)
        tok = ai_rem_token

    write_settings_template(setup_cfg, mcp_endpoint)
    hook_paths = install_hooks()
    install_cli()
    update_settings(setup_cfg, mcp_endpoint, hook_paths)

    # Auto-Memory md-Fallback: leere Datei (wird via @import in CLAUDE.md geladen)
    fb = os.path.join(CLAUDE_HOME, 'auto-memory', 'fallback.md')
    os.makedirs(os.path.dirname(fb), exist_ok=True)
    if not os.path.exists(fb):
        open(fb, 'w', encoding='utf-8').close()

    update_claude_md()
    install_commands()
    create_entities(setup_cfg, tok or ai_rem_token)

    # Bestehende CLAUDE.md mit Fremdwissen? Einmalige Migration anbieten (opt-in).
    try:
        cmd_path = os.path.join(CLAUDE_HOME, 'CLAUDE.md')
        with open(cmd_path, encoding='utf-8') as f:
            body = f.read()
        # ai-rem-Pointer-Block + @-Includes + Leerzeilen rausrechnen
        body = re.sub(r'(?:^|\n)## ai-rem[\s\S]*?(?=\n## |\Z)', '', body)
        leftover = '\n'.join(ln for ln in body.splitlines()
                             if ln.strip() and not ln.lstrip().startswith('@')
                             and not ln.lstrip().startswith('<!--'))
        if len(leftover.strip()) > 40:
            print('')
            print('ℹ Deine CLAUDE.md enthält noch eigenes Wissen. Einmalig migrieren:')
            print('  Claude Code starten und  /migrate-claude-md  ausführen.')
    except Exception:
        pass

    print('')
    print('Fertig. Claude Code neu starten - dann ist ai-rem aktiv.')
    print('Auf jeder neuen Maschine:')
    print('  macOS/Linux/WSL:  bash <(curl -s %s/setup)' % KG_URL)
    print('  Windows:          irm %s/setup.ps1 | iex' % KG_URL)


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print('')
        print('✗ Setup abgebrochen (Ctrl-C).')
        sys.exit(130)
    except Exception as ex:
        import traceback
        tb = traceback.extract_tb(sys.exc_info()[2])
        line = tb[-1].lineno if tb else '?'
        print('')
        print('✗ Setup abgebrochen (%s: %s, Skript-Zeile %s).' % (type(ex).__name__, ex, line))
        print('  Nach Behebung erneut ausfuehren:  %s' % rerun_hint())
        sys.exit(1)
