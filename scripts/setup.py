#!/usr/bin/env python3
# ai-rem Setup — plattformneutral (macOS, Ubuntu/Linux, WSL, Windows).
# Wird von den Wrappern geholt+gestartet:
#   bash <(curl -s __KG_URL__/setup)          (macOS/Linux/WSL)
#   irm __KG_URL__/setup.ps1 | iex            (Windows PowerShell)
# Harte Abhaengigkeit: python3. Ziele (--client, Default auto = was installiert ist):
#   claude    Claude Code (claude CLI): MCP, Hooks, CLAUDE.md-Pointer, Slash-Commands
#   opencode  opencode: opencode.json-mcp, AGENTS.md-Pointer, Plugin, Commands
#   generic   andere Frontends (Codex, Gemini CLI, Cursor …): nur Snippets zum Einfuegen
# Optional (mykeyvault/tools-registry als stdio-MCP): git, node >= 18, npm.
#
#   setup.py [--client claude,opencode]   einrichten (idempotent, auch zum Nachruesten)
#   setup.py --update [--client …]        nur ausgelieferte Dateien auffrischen
#   setup.py --uninstall --client X       ein Ziel wieder entfernen
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

KG_URL = os.environ.get('KG_URL') or '__KG_URL__'
HOME = os.path.expanduser('~')
_CC = os.environ.get('CLAUDE_CONFIG_DIR', '').strip()
# ponytail: nimmt bei Doppelpunkt-Liste (mehrere Config-Dirs) das erste; reicht fuer den Normalfall
if _CC:
    _CC = _CC.split(os.pathsep)[0]
CLAUDE_HOME = _CC or os.path.join(HOME, '.claude')
CLAUDE_JSON = os.path.join(_CC, '.claude.json') if _CC else os.path.join(HOME, '.claude.json')
IS_WIN = sys.platform == 'win32'

# Client-neutrale Ablage (XDG): client.json (Endpoint, Ziele, vault_entry — KEINE
# Secrets). Das einzige Secret pro Geraet, der ai-rem-Token, liegt im OS-Keychain
# (lib/keychain.py); alles Weitere holt die CLI zur Laufzeit ueber /api/client-config.
CONFIG_HOME = os.environ.get('XDG_CONFIG_HOME') or os.path.join(HOME, '.config')
AIREM_CFG_DIR = os.path.join(CONFIG_HOME, 'ai-rem')
CLIENT_JSON = os.path.join(AIREM_CFG_DIR, 'client.json')
SNIPPET_DIR = os.path.join(AIREM_CFG_DIR, 'snippets')
OPENCODE_DIR = os.path.join(CONFIG_HOME, 'opencode')
TARGETS = ('claude', 'opencode', 'generic')
# Klartext-Ablagen aelterer Installationen (< 1.7): werden nur noch gelesen
# (Token-Uebernahme in den Keychain) und von migrate_secrets() geloescht.
LEGACY_TOKEN_FILE = os.path.join(AIREM_CFG_DIR, 'token')
LEGACY_VAULT_TOKEN_FILE = os.path.join(AIREM_CFG_DIR, 'vault.token')
LEGACY_VAULT_ENV = os.path.join(CLAUDE_HOME, 'ai-rem-vault.env')
# Lokale CLI-Kopie; lib/ (inkl. keychain.py) liegt daneben unter ../lib.
LOCAL_CLI = os.path.join(HOME, '.local', 'share', 'ai-rem', 'bin', 'ai-rem')

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
    # Atomarer Download: erst Temp-Datei im Zielverzeichnis, nur bei Erfolg per
    # os.replace ersetzen. Verhindert, dass ein transienter Serverfehler eine
    # bestehende Datei truncatet.
    #
    # Fehler ist allein die Exception (Timeout, 4xx/5xx, DNS) — NICHT ein leerer
    # Body: lib/__init__.py ist regulaer 0 Bytes. Das fruehere "if not data"
    # brach install_cli() genau dort ab, nachdem bin/ai-rem schon geschrieben,
    # aber noch nicht ausfuehrbar gemacht war -> halb installierte CLI, und der
    # Auto-Memory-Hook meldete still "CLI not found".
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        data = http_get(url)
    except Exception as e:
        print('✗ Download fehlgeschlagen, %s unveraendert: %s (%s)' % (dst, url, e))
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


def write_json_atomic(path, data, mode=None):
    # Tmp-Datei im Zielverzeichnis + os.replace: ein Abbruch mitten im Schreiben
    # laesst die alte Datei intakt (bei ~/.claude.json waere ein halbes JSON fatal —
    # Claude Code startet dann nicht mehr). mode=0o600 fuer client.json.
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write('\n')
    if mode is not None and not IS_WIN:
        os.chmod(tmp, mode)
    os.replace(tmp, path)


def hook_command(path):
    # Unix: Shebang + chmod reichen, der Command ist der nackte Pfad.
    # Windows: kein Shebang-Exec — python explizit davorsetzen. -X utf8, weil
    # die Hooks JSON/MD mit UTF-8-Inhalt ohne explizites encoding= lesen und
    # der Windows-Default (cp1252) daran scheitern wuerde.
    if IS_WIN:
        return '"%s" -X utf8 "%s"' % (sys.executable, path)
    return path


def cli_invocation(*args):
    """(command, args) fuer einen MCP-Server-Eintrag, der die lokale CLI startet —
    Gegenstueck zu hook_command() fuer Configs, die command und args getrennt
    erwarten (~/.claude.json, opencode.json, Snippets)."""
    if IS_WIN:
        return sys.executable, ['-X', 'utf8', LOCAL_CLI] + list(args)
    return LOCAL_CLI, list(args)


# ── Keychain: das einzige Secret pro Geraet ──────────────────────────────────
# lib/keychain.py kommt mit der CLI (install_cli) — darum laeuft install_cli() im
# Ablauf VOR obtain_tokens(). Fallback auf ../lib relativ zu diesem Skript, damit
# Checkout und Tests ohne installierte CLI funktionieren. Die Wrapper sind die
# Monkeypatch-Punkte der Tests (kein echter Keychain in der CI).

_KEYCHAIN = None


def _keychain():
    global _KEYCHAIN
    if _KEYCHAIN is not None:
        return _KEYCHAIN
    import importlib.util
    here = os.path.dirname(os.path.abspath(globals().get('__file__') or sys.argv[0]))
    cands = [os.path.join(os.path.dirname(os.path.dirname(LOCAL_CLI)), 'lib', 'keychain.py'),
             os.path.join(here, '..', 'lib', 'keychain.py')]
    path = next((p for p in cands if os.path.isfile(p)), '')
    if not path:
        print('✗ lib/keychain.py fehlt (erwartet: %s) — CLI-Download fehlgeschlagen?' % cands[0])
        print('  Erneut ausfuehren:  %s' % rerun_hint())
        sys.exit(1)
    spec = importlib.util.spec_from_file_location('ai_rem_keychain', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _KEYCHAIN = mod
    return mod


def keychain_get():
    return _keychain().get()


def keychain_set(tok):
    _keychain().set(tok)


def keychain_delete():
    _keychain().delete()


def keychain_backend():
    return _keychain().backend_name()


def store_token(tok):
    """Token in den Keychain legen; False (mit Meldung) bei Backend-Fehler, damit
    der Aufrufer entscheiden kann, ob es ohne weitergeht."""
    try:
        keychain_set(tok)
    except Exception as ex:
        print('✗ Token konnte nicht im Keychain abgelegt werden: %s' % ex)
        print('  Notloesung: AI_REM_TOKEN im Env setzen (gilt dann nur fuer diese Shell).')
        REPORT.append(('Keychain', False, 'ai-rem pair'))
        return False
    print('✓ Token im %s gespeichert' % keychain_backend())
    return True


def report_keychain():
    try:
        keychain_get()  # erst nach get() weiss libsecret, ob der Token in der Datei liegt
    except Exception:
        pass
    backend = keychain_backend()
    REPORT.append(('Keychain (%s)' % backend, True, ''))
    if not backend.startswith('Datei'):
        return
    path = os.path.join(AIREM_CFG_DIR, 'keyring')
    if backend == _keychain()._FileBackend.name:
        print('⚠ Kein OS-Keychain gefunden — der Token liegt als Datei (0600) unter %s.' % path)
        print('  Besser: libsecret installieren (apt install libsecret-tools), dann  ai-rem pair')
    else:
        # secret-tool ist da, der Keyring nimmt aber nichts an — typisch fuer einen
        # Server ohne Desktop-Login. Nachinstallieren hilft nicht, die Datei ist hier richtig.
        reason = backend.split(' — ', 1)[-1].replace(', Fallback', '')
        print('ℹ Token als Datei (0600) unter %s — %s.' % (path, reason))
        print('  Auf einem Server ohne Desktop-Login ist das so vorgesehen.')


def run(cmd, timeout=120, capture=True, cwd=None):
    # stdin=DEVNULL: ssh liest sonst vom Terminal und schluckt Zeilen, die der User
    # nach dem Setup-Befehl gleich mit eingefuegt hat (die liefen dann nie).
    return subprocess.run(cmd, capture_output=capture, text=True,
                          timeout=timeout, cwd=cwd, stdin=subprocess.DEVNULL)


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


# ── Plugins: MCP-Server, die ein Claude-Code-Plugin mitbringt ────────────────
# Der tools-registry-Marketplace liefert ai-rem ({command: ai-rem, args: [mcp-proxy]})
# und mykeyvault ({command: ai-rem, args: [vault-mcp]}) auch als Plugin. Ist eines
# aktiv, waere ein gleichnamiger Eintrag in ~/.claude.json ein zweiter Server mit
# demselben Namen — das Setup legt ihn dann nicht an bzw. entfernt ihn.

PLUGIN_SERVERS = ('ai-rem', 'mykeyvault')


def plugin_enabled(name):
    """True, wenn in settings.json → enabledPlugins ein Plugin "<name>@<marketplace>"
    aktiv (true) ist. Der Marketplace-Name ist bewusst egal (Fork/Umbenennung)."""
    try:
        with open(os.path.join(CLAUDE_HOME, 'settings.json'), encoding='utf-8') as f:
            plugins = json.load(f).get('enabledPlugins') or {}
        return any(isinstance(k, str) and k.split('@', 1)[0] == name and v is True
                   for k, v in plugins.items())
    except (OSError, ValueError, AttributeError):
        return False


def drop_plugin_duplicates(servers):
    """Eintraege entfernen, die ein aktives Plugin selbst liefert. Gibt die
    Namen der Server zurueck, die vom Plugin kommen."""
    from_plugin = [n for n in PLUGIN_SERVERS if plugin_enabled(n)]
    for name in from_plugin:
        if name in servers:
            del servers[name]
            print('✓ %s aus ~/.claude.json entfernt - das Plugin %s@… liefert den Server' % (name, name))
    return from_plugin


def dedupe_claude_json():
    """Fuer --update (ohne update_claude_json): Plugin-Duplikate austragen."""
    try:
        with open(CLAUDE_JSON, encoding='utf-8') as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return
    servers = cfg.get('mcpServers') if isinstance(cfg, dict) else None
    if not isinstance(servers, dict):
        return
    before = len(servers)
    drop_plugin_duplicates(servers)
    if len(servers) != before:
        write_json_atomic(CLAUDE_JSON, cfg)


def mykeyvault_registered():
    """mykeyvault laeuft in Claude Code — als Eintrag in ~/.claude.json oder per Plugin.
    Davon haengt der vault-secret-reminder-Hook ab: ohne Vault waere sein Rat
    ("Secret aus mykeyvault holen") in einem Nur-ai-rem-Setup falsch."""
    if plugin_enabled('mykeyvault'):
        return True
    try:
        with open(CLAUDE_JSON, encoding='utf-8') as f:
            return 'mykeyvault' in (json.load(f).get('mcpServers') or {})
    except (OSError, ValueError, AttributeError):
        return False


def register_mcp(claude):
    if plugin_enabled('ai-rem'):
        print('✓ ai-rem-MCP kommt vom Plugin ai-rem@… - keine eigene Registrierung')
        return
    try:
        listed = run([claude, 'mcp', 'list'], timeout=60).stdout or ''
    except Exception:
        listed = ''
    # Zeilenanfang pruefen, nicht Substring: `claude mcp list` zeigt pro Server
    # "name: command …", und der CLI-Pfad (…/ai-rem/bin/ai-rem) steht seit den
    # stdio-Wrappern auch in der mykeyvault-Zeile.
    def listed_as(name):
        return re.search(r'^%s:' % re.escape(name), listed, re.M) is not None

    if listed_as('kg-memory'):
        run([claude, 'mcp', 'remove', 'kg-memory'], timeout=60)
        print('✓ Alte kg-memory Registrierung entfernt')
    if listed_as('ai-rem'):
        # Eine alte http-Registrierung stellt update_claude_json() auf stdio um.
        print('✓ MCP bereits registriert')
        return
    # stdio statt http: Claude Code startet `ai-rem mcp-proxy`, der den Token aus
    # dem Keychain holt — kein Bearer mehr in ~/.claude.json.
    cmd, cargs = cli_invocation('mcp-proxy')
    p = run([claude, 'mcp', 'add', '--scope', 'user', 'ai-rem', '--', cmd] + cargs, timeout=120)
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


# ── Bootstrap-Token per SSH von mystorage ziehen ─────────────────────────────
# /setup ist oeffentlich (anonymer Download), Secrets liegen also NICHT im
# Script-Body. Ein bereits per SSH-Key vertrauter Host zieht den ai-rem-Token
# direkt aus der .env auf dem Server — ai-rem bleibt damit KEIN Secret-Verteiler.
# Override: AI_REM_TOKEN im Env hat Vorrang. Den Vault-Token braucht das Setup
# seit 1.7 nicht mehr: `ai-rem vault-mcp` holt ihn zur Laufzeit ueber
# /api/client-config, nichts davon landet auf Platte.

def pull_secrets(setup_cfg):
    ai_rem_token = os.environ.get('AI_REM_TOKEN', '')
    if ai_rem_token:
        return ai_rem_token
    ssh_host = os.environ.get('AI_REM_SSH_HOST') or setup_cfg.get('ssh_host', 'mystorage')
    ssh = shutil.which('ssh')
    if not ssh:
        return ''
    # Kein SSH ist kein Fehler: die Geraete-Kopplung (pair_device) holt den Token
    # dann ueber den Browser. Darum hier keine Warnung.
    try:
        if run([ssh, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', ssh_host, 'true'],
               timeout=20).returncode != 0:
            return ''
        p = run([ssh, ssh_host,
                 "grep -h '^AI_REM_API_TOKEN=' mydocker/compose-files/ai-rem/.env 2>/dev/null"
                 " | head -1 | cut -d= -f2-"], timeout=20)
        return (p.stdout or '').strip()
    except Exception:
        return ''


# ── Voraussetzungen selbst installieren ──────────────────────────────────────
# Frueher: "Node fehlt — brew install node, danach erneut ausfuehren". Jetzt fragt
# das Setup einmal und installiert selbst; der Build laeuft im selben Lauf weiter.

REPORT = []          # (Bestandteil, ok, Befehl zur Behebung) fuer den Abschlussbericht
ASSUME_YES = False   # --yes
NODE_MIN = 18


def _node_major():
    node = shutil.which('node')
    if not node:
        return 0
    try:
        return int(run([node, '-v'], timeout=20).stdout.strip().lstrip('v').split('.')[0])
    except Exception:
        return 0


def missing_deps():
    miss = [c for c in ('git', 'npm') if not shutil.which(c)]
    if _node_major() < NODE_MIN:
        miss.insert(0, 'node')
    return miss


def _brew():
    for p in (shutil.which('brew'), '/opt/homebrew/bin/brew', '/usr/local/bin/brew'):
        if p and os.path.isfile(p):
            return p
    return ''


def dep_install_cmds(miss):
    """Befehle, die die fehlenden Pakete nachinstallieren — None, wenn es auf dieser
    Plattform keinen Weg ohne Handarbeit gibt (dann steht der Hinweis im Bericht)."""
    want_node = 'node' in miss or 'npm' in miss
    want_git = 'git' in miss
    if PLATFORM == 'macos':
        brew = _brew()
        if not brew:
            return None
        return [[brew, 'install'] + (['node'] if want_node else []) + (['git'] if want_git else [])]
    if PLATFORM in ('linux', 'wsl') and shutil.which('apt-get'):
        cmds = []
        if want_node:
            # apt liefert oft ein zu altes Node — NodeSource hat das aktuelle LTS.
            cmds.append(['bash', '-c', 'curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -'])
        pkgs = (['nodejs'] if want_node else []) + (['git'] if want_git else [])
        cmds.append(['sudo', 'apt-get', 'install', '-y'] + pkgs)
        return cmds
    if PLATFORM == 'windows' and shutil.which('winget'):
        cmds = []
        if want_node:
            cmds.append(['winget', 'install', '-e', '--id', 'OpenJS.NodeJS.LTS'])
        if want_git:
            cmds.append(['winget', 'install', '-e', '--id', 'Git.Git'])
        return cmds
    return None


def confirm(question):
    if ASSUME_YES:
        return True
    if not sys.stdin.isatty():
        return False
    try:
        return input('%s [J/n] ' % question).strip().lower() in ('', 'j', 'ja', 'y', 'yes')
    except EOFError:
        return False


def ensure_deps():
    """node >= 18, npm, git sicherstellen. True, wenn danach alles da ist."""
    miss = missing_deps()
    if not miss:
        return True
    cmds = dep_install_cmds(miss)
    if cmds is None:
        if PLATFORM == 'macos':
            fix = '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
            print('⚠ Es fehlen %s, und Homebrew ist nicht installiert (braucht einmal das Admin-Passwort):'
                  % ', '.join(miss))
            print('    ' + fix)
        else:
            fix = 'node >= %d, npm und git installieren' % NODE_MIN
            print('⚠ Es fehlen %s — kein unterstuetzter Paketmanager gefunden.' % ', '.join(miss))
        REPORT.append(('node/npm/git', False, fix))
        return False
    print('Es fehlen: %s (fuer mykeyvault und tools).' % ', '.join(miss))
    for c in cmds:
        print('    ' + ' '.join(c))
    if not confirm('Jetzt installieren?'):
        REPORT.append(('node/npm/git', False, 'ai-rem install --yes'))
        return False
    for c in cmds:
        # Interaktiv (sudo/winget fragen ggf. nach Passwort bzw. Zustimmung).
        if subprocess.call(c) != 0:
            print('✗ fehlgeschlagen: %s' % ' '.join(c))
            break
    # Frisch installierte Programme sind im PATH dieses Prozesses noch nicht bekannt.
    extra = ['/opt/homebrew/bin', '/usr/local/bin'] if PLATFORM == 'macos' else []
    if PLATFORM == 'windows':
        extra = [os.path.join(os.environ.get('ProgramFiles', r'C:\Program Files'), d)
                 for d in ('nodejs', os.path.join('Git', 'cmd'))]
    os.environ['PATH'] = os.pathsep.join(extra + [os.environ.get('PATH', '')])
    still = missing_deps()
    if still:
        REPORT.append(('node/npm/git', False, 'fehlt weiterhin: ' + ', '.join(still)))
        return False
    print('✓ node, npm, git vorhanden')
    return True


# ── Geraete-Kopplung: Token ohne SSH ─────────────────────────────────────────

def _post_json(url, body, timeout=10):
    req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), method='POST',
                                 headers={'Content-Type': 'application/json',
                                          'User-Agent': 'ai-rem-setup'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def pair_device():
    """Token per Freigabe in der Web-UI holen (wie `gh auth login`). Gibt
    {'ai_rem_token', 'vault_token', 'vault_url'} zurueck oder {} bei Abbruch."""
    import platform as _pf
    import socket
    import time
    import webbrowser
    try:
        start = _post_json(KG_URL + '/api/pair/start',
                           {'device': socket.gethostname(),
                            'platform': '%s %s' % (PLATFORM, _pf.release())})
    except Exception as ex:
        print('⚠ Kopplung nicht moeglich (%s) — Server zu alt oder nicht erreichbar.' % ex)
        return {}
    print('')
    print('┌─ Geraet koppeln ─────────────────────────────────────────')
    print('│  Code:  %s' % start['code'])
    print('│  Im Browser freigeben (eingeloggt in der ai-rem-Web-UI, geht auch am Handy):')
    print('│  %s' % start['verify_url'])
    print('└──────────────────────────────────────────────────────────')
    try:
        webbrowser.open(start['verify_url'])
    except Exception:
        pass
    deadline = time.time() + int(start.get('expires_in', 600))
    try:
        while time.time() < deadline:
            time.sleep(3)
            try:
                r = _post_json(KG_URL + '/api/pair/poll', {'pair_id': start['pair_id']})
            except Exception:
                continue
            if r.get('state') == 'approved':
                print('✓ Geraet freigegeben')
                return r
            if r.get('state') in ('denied', 'expired'):
                print('✗ Kopplung %s' % ('abgelehnt' if r['state'] == 'denied' else 'abgelaufen'))
                return {}
    except KeyboardInterrupt:
        print('')
        print('✗ Kopplung abgebrochen')
        return {}
    print('✗ Kopplung abgelaufen')
    return {}


def ask_token():
    """Letzter Ausweg ohne Browser: Token einfuegen (z.B. aus `ai-rem token`)."""
    if not sys.stdin.isatty():
        return ''
    import getpass
    try:
        return getpass.getpass('ai-rem-Token einfuegen (leer = abbrechen): ').strip()
    except (EOFError, KeyboardInterrupt):
        return ''


def obtain_tokens(ai_rem_token, vault_url='', force_pair=False):
    """Token-Kette: Env/SSH (pull_secrets) > Keychain > Kopplung > Eingabe.
    Gibt (ai_rem_token, vault_url) zurueck. Ein neu erhaltener Token landet genau
    hier im Keychain — der einzige Ort, an dem das Setup ein Secret ablegt. Den
    vault_token aus /api/pair/poll ignorieren wir bewusst: er wuerde nur auf Platte
    landen, die CLI holt ihn pro Lauf vom Server."""
    stored = '' if force_pair else keychain_get()
    ai_rem_token = ('' if force_pair else ai_rem_token) or stored
    if not ai_rem_token:
        paired = pair_device()
        ai_rem_token = paired.get('ai_rem_token', '')
        vault_url = paired.get('vault_url') or vault_url
    if not ai_rem_token:
        ai_rem_token = ask_token()
    if ai_rem_token and ai_rem_token != stored:
        store_token(ai_rem_token)
    REPORT.append(('ai-rem-Token', bool(ai_rem_token), 'ai-rem pair'))
    return ai_rem_token, vault_url


def print_report():
    if not REPORT:
        return
    print('')
    print('── Ergebnis ──────────────────────────────────────────────')
    seen = set()
    for name, ok, fix in REPORT:
        if name in seen:
            continue
        seen.add(name)
        print(('  ✓ %s' % name) if ok else ('  ✗ %-20s → %s' % (name, fix)))


# ── tools-registry (stdio) klonen+bauen, falls in setup-config ────────────────────

def _build_node_mcp(repo, install_dir, entry, subdir, label):
    # Generisch: git clone/pull + npm install/build eines Node-MCP.
    # entry ist install_dir-relativ (z.B. dist/index.js oder mcp/dist/index.js);
    # subdir ist der Ordner mit package.json als npm-cwd ('' = install_dir selbst).
    # Gibt den Entry-Pfad zurueck oder '' bei fehlenden Tools / Build-Fehler.
    # Fehlende Pakete installiert ensure_deps() vorab (einmal, mit Rueckfrage).
    # Kommt der Build trotzdem hier ohne sie an, hat der User abgelehnt.
    if missing_deps():
        print('⚠ %s uebersprungen — es fehlen: %s' % (label, ', '.join(missing_deps())))
        REPORT.append((label, False, 'ai-rem install --yes'))
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


def needs_node_builds(setup_cfg):
    """True, wenn mcp_register einen Build verlangt: mykeyvault.stdio (lokaler
    Vault-MCP) oder tools.stdio.registry_url (tools-registry). Nur dann braucht
    das Setup node/npm/git."""
    reg = setup_cfg.get('mcp_register') or {}
    return bool(((reg.get('mykeyvault') or {}).get('stdio'))
                or ((reg.get('tools') or {}).get('stdio') or {}).get('registry_url'))


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


# ── ai-rem + mykeyvault + tools als stdio-Server in ~/.claude.json ───────────
# Seit 1.7 ohne Secrets: ai-rem laeuft ueber `ai-rem mcp-proxy` (Token aus dem
# Keychain), mykeyvault ueber `ai-rem vault-mcp` (Vault-Zugang pro Lauf vom
# Server). Eine bestehende http-Registrierung mit Bearer wird hier migriert.

def update_claude_json(setup_cfg, mcp_endpoint, tools_entry, tools_reg_url, vault_entry=''):
    cj = CLAUDE_JSON
    if not os.path.exists(cj):
        print('⚠ ~/.claude.json fehlt - claude einmal interaktiv starten, dann Setup erneut ausfuehren')
        return False
    with open(cj, encoding='utf-8') as f:
        cfg = json.load(f)
    servers = cfg.setdefault('mcpServers', {})
    # Aktive Plugins liefern ai-rem/mykeyvault selbst: keine Doppel-Eintraege.
    from_plugin = drop_plugin_duplicates(servers)

    cmd, cargs = cli_invocation()

    # (1) ai-rem: stdio-Proxy. url/headers einer alten http-Registrierung fallen weg.
    # Fehlt der Eintrag (Plugin oder abweichender Config-Pfad), laufen Hooks und
    # Settings trotzdem weiter — der Aufrufer bricht hier nicht mehr ab.
    if 'ai-rem' in from_plugin:
        print('✓ ai-rem-MCP kommt vom Plugin - kein Eintrag in ~/.claude.json')
    else:
        old = servers.get('ai-rem') if isinstance(servers.get('ai-rem'), dict) else {}
        was_http = 'url' in old or 'headers' in old
        servers['ai-rem'] = {'type': 'stdio', 'command': cmd, 'args': cargs + ['mcp-proxy']}
        print('✓ ai-rem ' + ('von http auf stdio-Proxy migriert' if was_http else 'als stdio-Proxy eingetragen'))

    # (2) mykeyvault: bevorzugt lokaler stdio-MCP via `ai-rem vault-mcp` (voller
    # Funktionsumfang inkl. exec/file-Tools), sonst HTTP-Fallback ueber den Proxy
    # (nur list/create) — beides ohne Token in der Datei.
    reg = setup_cfg.get('mcp_register', {}).get('mykeyvault', {})
    if 'mykeyvault' in from_plugin:
        print('✓ mykeyvault-MCP kommt vom Plugin - kein Eintrag in ~/.claude.json')
    elif vault_entry:
        existed = 'mykeyvault' in servers
        servers['mykeyvault'] = {'type': 'stdio', 'command': cmd, 'args': cargs + ['vault-mcp']}
        print('✓ mykeyvault ' + ('migriert' if existed else 'registriert') + ' (stdio via ai-rem vault-mcp)')
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
        ai_https = (mcp_endpoint or '').startswith('https')
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
        if mkv_url:
            existed = 'mykeyvault' in servers
            servers['mykeyvault'] = {'type': 'stdio', 'command': cmd,
                                     'args': cargs + ['mcp-proxy', '--endpoint', mkv_url]}
            print('✓ mykeyvault ' + ('migriert' if existed else 'registriert')
                  + ' (Proxy auf %s)' % mkv_url)

    # (3) tools als stdio-MCP registrieren (gebaut aus Registry-Repo)
    if tools_entry and tools_reg_url:
        existed = 'tools' in servers
        servers['tools'] = {'type': 'stdio', 'command': 'node',
                            'args': [tools_entry],
                            'env': {'TOOLS_REGISTRY_URL': tools_reg_url}}
        print('✓ tools ' + ('migriert' if existed else 'registriert') + ' (stdio)')

    write_json_atomic(cj, cfg)
    return True


# ── settings-template.json: immer aus setup-config neu schreiben ─────────────
# Damit Config-Aenderungen (Permissions, Deny, SMB, …) bei jedem Re-Run propagieren.

def write_settings_template(setup_cfg, mcp_endpoint):
    tmpl = {
        'version': '2026-09-04',
        'ai_rem_endpoint': mcp_endpoint or (KG_URL + '/mcp'),
        'smb': setup_cfg.get('smb', {}),
        'mcp_stdio_servers': setup_cfg.get('mcp_stdio_servers', {}),
        'tools_scripts_dir': setup_cfg.get('tools_scripts_dir', ''),
        'ollama_url': setup_cfg.get('ollama_url', 'http://mystorage.lan:11437'),
        'general': {'model': 'opus', 'autoMemoryEnabled': False, 'theme': 'auto',
                    # Plan Mode + Auto Mode: Bash laeuft im Plan Mode ueber den
                    # Auto-Mode-Klassifizierer statt ueber Einzel-Prompts.
                    # Schreibzugriffe bleiben blockiert, der Plan bleibt bestaetigungspflichtig.
                    'skipAutoPermissionPrompt': True, 'useAutoModeDuringPlan': True},
        'permissions_allow_portable': setup_cfg.get('permissions_allow_portable', [
            # Nur noch die 4 Kern-MCP-Tools (Issue #32). Admin-Ops laufen über
            # `Bash` (ai-rem CLI / curl POST /api/tool), das ohnehin erlaubt ist.
            'Bash', 'Skill(update-config)', 'Skill(update-config:*)',
            'mcp__ai-rem__memory_get_context', 'mcp__ai-rem__memory_search',
            'mcp__ai-rem__memory_add', 'mcp__ai-rem__memory_relate',
        ]),
        'permissions_allow_path_templates': ['Read(//{HOME}/.claude/**)', 'Read(//{TMP}/**)'],
        'permissions_default_mode': setup_cfg.get('permissions_default_mode', 'plan'),
        'permissions_deny': setup_cfg.get('permissions_deny', []),
        'hooks': {
            'SessionStart': ['system-check.py (ai-rem, SMB, MCP, settings-sync, tools)'],
            'UserPromptSubmit': ['Tool-Discovery'],
            'PreToolUse': ['claude-md-guard.py (warnt bei CLAUDE.md-Edits → ai-rem)'],
            'PostToolUse': ['save-plan.py (ExitPlanMode → offener Task in ai-rem)',
                            'vault-secret-reminder.py (Bash: Auth-Fehler → Secret aus dem Vault)'],
        },
        'additional_directories_templates': ['{HOME}/.claude', '{HOME}'],
        'path_mappings': setup_cfg.get('path_mappings', {}),
    }
    with open(os.path.join(CLAUDE_HOME, 'settings-template.json'), 'w', encoding='utf-8') as f:
        json.dump(tmpl, f, indent=2, ensure_ascii=False)
        f.write('\n')
    print('✓ settings-template.json aktualisiert')


# ── Hooks deployen ────────────────────────────────────────────────────────────

def install_hooks(vault_reminder=True):
    """vault_reminder=False: Nur-ai-rem-Setup ohne mykeyvault — der Hook wuerde bei
    jedem Auth-Fehler auf einen Vault verweisen, den es nicht gibt."""
    paths = {}
    hooks = [('system-check.py', 'SessionStart-Hook'),
             ('auto-memory.py', 'Auto-Memory-Hook'),
             ('claude-md-guard.py', 'CLAUDE.md-Guard-Hook'),
             ('save-plan.py', 'Plan-Saving-Hook')]
    if vault_reminder:
        hooks.append(('vault-secret-reminder.py', 'Vault-Secret-Reminder-Hook'))
    for fname, label in hooks:
        dst = os.path.join(CLAUDE_HOME, 'hooks', fname)
        if fetch_to(KG_URL + '/hooks/' + fname, dst):
            if not IS_WIN:
                os.chmod(dst, 0o755)
            paths[fname] = dst
            print('✓ %s: %s' % (label, dst))
    return paths


def points_at_clone(path):
    """True, wenn der Pfad in einen ai-rem-Clone zeigt (statt in die lokale Kopie)."""
    return path.replace('\\', '/').endswith('/github/ai-rem/bin/ai-rem')


def install_cli():
    """CLI lokal ablegen. Leerer String = Download fehlgeschlagen.

    Vorher zeigte AI_REM_CLI auf den Clone. Lag der auf einem Netzlaufwerk, war
    die CLI beim Session-Ende weg, sobald der Mount hing — der Auto-Memory-Hook
    meldete dann still "CLI not found". Die lokale Kopie kennt kein Mount.

    bin/ai-rem allein reicht nicht: es legt sein Parent-Verzeichnis auf sys.path
    und importiert lib/ (mcp_client immer, extractor bei ingest/catchup). Ohne
    diese Module scheitert schon `ai-rem status` am ModuleNotFoundError.
    keychain.py braucht zusaetzlich dieses Setup selbst (_keychain()).
    """
    if not fetch_to(KG_URL + '/bin/ai-rem', LOCAL_CLI):
        return ''
    lib_dir = os.path.join(os.path.dirname(os.path.dirname(LOCAL_CLI)), 'lib')
    for name in ('__init__.py', 'mcp_client.py', 'extractor.py', 'extractor_heuristic.py',
                 'keychain.py'):
        if not fetch_to(KG_URL + '/lib/' + name, os.path.join(lib_dir, name)):
            return ''
    if not IS_WIN:
        os.chmod(LOCAL_CLI, 0o755)
    print('✓ CLI: %s' % LOCAL_CLI)
    return LOCAL_CLI


SHIM_DIR = os.path.join(HOME, '.local', 'bin')


def link_cli(cli_path):
    """CLI unter ~/.local/bin verfuegbar machen, damit `ai-rem` tippbar ist.

    AI_REM_CLI in der settings.json reicht den Hooks, nicht dem Menschen: der
    Befehl, der den Client aktuell haelt (`ai-rem update`), war genau der, den
    niemand aufrufen konnte. ~/.local/bin ist der XDG-Ort dafuer und auf
    Debian/Ubuntu ueber ~/.profile bereits im PATH.

    ponytail: Symlink statt Eintrag in .bashrc/.zshrc — kein Fremd-Editieren von
    Shell-Configs, und liegt das Verzeichnis wider Erwarten nicht im PATH, sagen
    wir es und der Nutzer entscheidet.
    """
    if not cli_path:
        return
    os.makedirs(SHIM_DIR, exist_ok=True)
    shim = os.path.join(SHIM_DIR, 'ai-rem.cmd' if IS_WIN else 'ai-rem')

    # Eine echte Datei fremder Herkunft bleibt unangetastet — die koennte eine
    # bewusst installierte andere CLI sein. Nur eigene Symlinks/Shims ersetzen wir.
    if os.path.lexists(shim) and not (os.path.islink(shim) or IS_WIN):
        print('⚠ %s existiert und ist kein Symlink — nicht angefasst.' % shim)
        return
    try:
        if os.path.lexists(shim):
            os.unlink(shim)
        if IS_WIN:
            with open(shim, 'w', encoding='utf-8') as f:
                f.write('@echo off\r\n"%s" "%s" %%*\r\n' % (sys.executable, cli_path))
        else:
            os.symlink(cli_path, shim)
    except OSError as ex:
        print('⚠ %s konnte nicht angelegt werden: %s' % (shim, ex))
        return
    print('✓ Befehl: %s' % shim)

    paths = [os.path.normcase(os.path.normpath(p))
             for p in os.environ.get('PATH', '').split(os.pathsep) if p]
    if os.path.normcase(os.path.normpath(SHIM_DIR)) not in paths:
        print('  ℹ %s liegt nicht im PATH. Ergaenzen mit:' % SHIM_DIR)
        if IS_WIN:
            print('    setx PATH "%%PATH%%;%s"' % SHIM_DIR)
        else:
            print('    echo \'export PATH="$HOME/.local/bin:$PATH"\' >> ~/.bashrc')
        print('  (Auf vielen Systemen zieht ~/.profile das Verzeichnis beim naechsten'
              ' Login automatisch.)')


# ── settings.json: Permissions, Hooks registrieren, alte Hooks entfernen ─────

def update_settings(setup_cfg, mcp_endpoint, hook_paths, vault_reminder=True):
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

    default_mode = tmpl.get('permissions_default_mode')
    if default_mode:
        perms.setdefault('defaultMode', default_mode)

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

    # Erinnert bei Auth-/401-Fehlern daran, das Secret aus dem Vault zu holen statt
    # den User um Token/Login zu bitten (Bash-Matcher, gleiche Gruppe wie andere
    # Bash-PostToolUse-Hooks).
    # Ohne mykeyvault (vault_reminder=False) einen Eintrag frueherer Laeufe austragen.
    vault_reminder_path = hook_paths.get('vault-secret-reminder.py', '')
    vault_reminder_added = False
    vault_reminder_removed = False
    if vault_reminder and vault_reminder_path:
        g = hook_group('PostToolUse', 'Bash')
        if not has_hook(g, vault_reminder_path):
            g['hooks'].append({'type': 'command', 'command': hook_command(vault_reminder_path), 'timeout': 5})
            vault_reminder_added = True
    elif not vault_reminder:
        groups = hooks.get('PostToolUse', [])
        for g in groups:
            kept = [h for h in g.get('hooks', [])
                    if 'vault-secret-reminder.py' not in h.get('command', '')]
            if len(kept) != len(g.get('hooks', [])):
                g['hooks'] = kept
                vault_reminder_removed = True
        if vault_reminder_removed:
            hooks['PostToolUse'] = [g for g in groups if g.get('hooks')]
            if not hooks['PostToolUse']:
                del hooks['PostToolUse']

    # Env fuer Hook + CLI hinterlegen, damit Auto-Memory ohne manuelle Env laeuft:
    # - AI_REM_ENDPOINT kennt der Bootstrap bereits (MCP_ENDPOINT, TLS-aufgeloest)
    # - AI_REM_CLI per Discovery (inkl. SMB-Mount /Volumes/<x>/myCode auf macOS)
    # setdefault => bewusste manuelle Overrides bleiben erhalten.
    #
    # LLM-Zugang (URL + Key) kommt seit 1.7 als Paar ueber /api/client-config.
    # AI_REM_LLM_API_KEY wird nie mehr geschrieben (migrate_secrets() raeumt den
    # Altbestand weg). AI_REM_LLAMA_URL schreiben wir konsequenterweise auch nicht
    # mehr neu: Env gewinnt gegen den Server, der Key kaeme aber von dort — zeigt
    # der Server spaeter auf einen anderen Router, passten URL und Key nicht mehr
    # zusammen. Ein vorhandener Eintrag bleibt als bewusster Override stehen.
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

    write_json_atomic(path, data)
    for line in ('' if not added else '  +%d allow permissions' % len(added),
                 '' if not added_deny else '  +%d deny rules' % len(added_deny),
                 '  SessionStart-Hook' if hook_added else '',
                 '  Auto-Memory-Hooks: %s' % ', '.join(auto_mem_added) if auto_mem_added else '',
                 '  CLAUDE.md-Guard-Hook' if guard_added else '',
                 '  Plan-Saving-Hook' if save_plan_added else '',
                 '  Vault-Secret-Reminder-Hook' if vault_reminder_added else '',
                 '  Vault-Secret-Reminder-Hook ausgetragen (kein mykeyvault)' if vault_reminder_removed else '',
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
ai-rem ist die einzige Wissensquelle für persistenten Kontext. Claude Codes natives Markdown-Auto-Memory ist deaktiviert.
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
    if fetch_to(KG_URL + '/cmd/ai-rem-update', os.path.join(CLAUDE_HOME, 'commands', 'ai-rem-update.md')):
        print('✓ /ai-rem-update Command angelegt')


# ── Preferences & Tool-Entities direkt via MCP API anlegen ───────────────────
# (kein Claude-Token-Verbrauch)

def create_entities(setup_cfg, ai_rem_token):
    mcp_url = KG_URL + '/mcp'
    token = ai_rem_token or keychain_get()

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



# ── Ziele (claude / opencode / generic) + client.json ────────────────────────

def state_dir():
    # Muss lib/extractor.py LOG_DIR entsprechen: dort liegen fallback.md & Co.
    if os.environ.get('AI_REM_STATE_DIR'):
        return os.environ['AI_REM_STATE_DIR']
    if os.path.isdir(CLAUDE_HOME):
        return os.path.join(CLAUDE_HOME, 'auto-memory')
    return os.path.join(HOME, '.local', 'share', 'ai-rem', 'state')


def load_client_cfg():
    try:
        with open(CLIENT_JSON, encoding='utf-8') as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def save_client_cfg(cfg):
    # 0600, obwohl keine Secrets drinstehen: Endpoint und Pfade gehen niemand
    # anderen auf der Maschine etwas an, und ein spaeteres Feld soll nicht erst
    # die Rechte korrigieren muessen.
    write_json_atomic(CLIENT_JSON, cfg, mode=0o600)


def read_secret(path):
    # Nur noch fuer Klartext-Altlasten (LEGACY_TOKEN_FILE); geschrieben wird nicht mehr.
    try:
        with open(path, encoding='utf-8') as f:
            return f.read().strip()
    except OSError:
        return ''


def installed_targets():
    """Eingerichtete Ziele: client.json, sonst an den installierten Dateien erkannt
    (Installationen von vor client.json kennen nur Claude Code)."""
    targets = [t for t in load_client_cfg().get('targets', []) if t in TARGETS]
    if targets:
        return targets
    found = []
    if os.path.isfile(os.path.join(CLAUDE_HOME, 'hooks', 'system-check.py')):
        found.append('claude')
    if os.path.isfile(os.path.join(OPENCODE_DIR, 'plugin', 'ai-rem.ts')):
        found.append('opencode')
    return found


def detect_targets():
    found = [t for t in ('claude', 'opencode') if shutil.which(t)]
    return found or ['generic']


USAGE = '''ai-rem Setup — Claude Code / opencode / weitere Frontends einrichten

  setup.py [--client claude,opencode,generic|auto] [--yes] [--pair]
  setup.py --update [--client …]     nur ausgelieferte Dateien auffrischen
  setup.py --pair-only               nur den Geraete-Token neu holen
  setup.py --uninstall --client X    ein Ziel wieder entfernen

Ohne Option laeuft das komplette Setup und baut die Installation um.'''


def parse_args(argv):
    # ponytail: ohne diesen Zweig war `setup.py --help` ein kompletter Setup-Lauf
    # (unbekannte Optionen wurden still ignoriert) — genau das, was niemand will,
    # der nur die Optionen sehen wollte.
    if '-h' in argv or '--help' in argv:
        print(USAGE)
        sys.exit(0)
    unknown = [a for a in argv if a.startswith('-') and a not in
               ('--update', '--uninstall', '--yes', '-y', '--pair', '--pair-only', '--client')
               and not a.startswith('--client=')]
    if unknown:
        print('✗ Unbekannte Option: %s' % ', '.join(unknown))
        print(USAGE)
        sys.exit(2)
    args = {'update': '--update' in argv, 'uninstall': '--uninstall' in argv, 'targets': [],
            'yes': '--yes' in argv or '-y' in argv, 'pair': '--pair' in argv,
            'pair_only': '--pair-only' in argv}
    for i, a in enumerate(argv):
        val = ''
        if a == '--client' and i + 1 < len(argv):
            val = argv[i + 1]
        elif a.startswith('--client='):
            val = a.split('=', 1)[1]
        for t in (x.strip() for x in val.split(',') if x.strip()):
            if t == 'auto':
                args['targets'] += detect_targets()
            elif t in TARGETS:
                args['targets'].append(t)
            else:
                print('✗ Unbekanntes Ziel: %s (erlaubt: %s, auto)' % (t, ', '.join(TARGETS)))
                sys.exit(2)
    args['targets'] = list(dict.fromkeys(args['targets']))
    return args


# Felder aus client.json < 1.7, die Secrets oder deren Pfade trugen.
LEGACY_CLIENT_FIELDS = ('llm_api_key', 'llm_url', 'token_file')


def record_client(mcp_endpoint, targets, setup_cfg, vault_url='', vault_entry=None):
    """client.json schreiben — ohne Secrets. Ziele werden ergaenzt, nie verdraengt.
    vault_entry: absoluter Pfad des gebauten mykeyvault-MCP ('' = nicht gebaut,
    None = Feld unveraendert lassen, z.B. beim --update ohne Build). keychain:
    Backend-Name, damit `ai-rem doctor` sagen kann, wo der Token liegt."""
    cfg = load_client_cfg()
    cfg['endpoint'] = mcp_endpoint or cfg.get('endpoint') or KG_URL + '/mcp'
    cfg['targets'] = list(dict.fromkeys(cfg.get('targets', []) + list(targets)))
    if vault_url:
        cfg['vault_url'] = vault_url
    if vault_entry is not None:
        cfg['vault_entry'] = vault_entry
    cfg['keychain'] = keychain_backend()
    for k in LEGACY_CLIENT_FIELDS:
        cfg.pop(k, None)
    save_client_cfg(cfg)
    return cfg


def resolve_token(ai_rem_token):
    """Env/SSH/Kopplung > Keychain."""
    return ai_rem_token or keychain_get()


# ── opencode ─────────────────────────────────────────────────────────────────

AGENTS_BEGIN = '<!-- ai-rem:begin -->'
AGENTS_END = '<!-- ai-rem:end -->'
AGENTS_BLOCK = AGENTS_BEGIN + """
## ai-rem
ai-rem ist die einzige Wissensquelle für persistenten Kontext (MCP-Server `ai-rem`).
Zu Beginn jeder Session einmal `memory_get_context()` aufrufen — opencode hat keinen
Session-Start-Hook. Nutzungsregeln kommen über die MCP Server Instructions,
Verhaltensregeln aus den ai-rem Preferences („Routinen & Anweisungen“).
""" + AGENTS_END


def opencode_config_path():
    for name in ('opencode.json', 'opencode.jsonc'):
        p = os.path.join(OPENCODE_DIR, name)
        if os.path.isfile(p):
            return p
    return os.path.join(OPENCODE_DIR, 'opencode.json')


def opencode_mcp_entries(vault_entry, tools_entry, tools_reg_url):
    """mcp-Block fuer opencode. stdio heisst dort "local" (command als EIN Array),
    env "environment". ai-rem und mykeyvault laufen ueber die stdio-Wrapper der
    CLI — kein Token und kein {file:…} mehr in der opencode.json.
    AI_REM_CLIENT=opencode: der Proxy macht daraus X-AI-REM-Client, damit der
    Server Entities dem richtigen Frontend zuordnet."""
    cmd, cargs = cli_invocation()
    entries = {'ai-rem': {'type': 'local', 'command': [cmd] + cargs + ['mcp-proxy'],
                          'enabled': True, 'environment': {'AI_REM_CLIENT': 'opencode'}}}
    node = shutil.which('node')  # Homebrew: /opt/homebrew/bin/node, nicht /usr/bin/node
    if vault_entry and node:
        entry = {'type': 'local', 'command': [cmd] + cargs + ['vault-mcp'], 'enabled': True}
        if os.environ.get('NODE_EXTRA_CA_CERTS'):
            entry['environment'] = {'NODE_EXTRA_CA_CERTS': os.environ['NODE_EXTRA_CA_CERTS']}
        entries['mykeyvault'] = entry
    elif vault_entry:
        print('⚠ opencode: mykeyvault uebersprungen (node fehlt)')
    if tools_entry and node:
        entries['tools'] = {'type': 'local', 'command': [node, tools_entry], 'enabled': True,
                            'environment': {'TOOLS_REGISTRY_URL': tools_reg_url}}
    return entries


def merge_opencode_json(entries, only=None):
    """mcp-Eintraege und den Fallback-Pfad in opencode.json mergen. Provider, Modelle
    und fremde MCP-Server bleiben unberuehrt. JSONC mit Kommentaren wird nicht
    umgeschrieben (Kommentare gingen verloren) — dann liegt ein Snippet bereit.
    only: nur diese Namen anfassen. Eintrag None entfernt den Server."""
    path = opencode_config_path()
    fallback = os.path.join(state_dir(), 'fallback.md')
    data = {}
    if os.path.isfile(path):
        with open(path, encoding='utf-8') as f:
            raw = f.read()
        try:
            data = json.loads(raw) if raw.strip() else {}
        except ValueError:
            os.makedirs(SNIPPET_DIR, exist_ok=True)
            snip = os.path.join(SNIPPET_DIR, 'opencode-mcp.json')
            with open(snip, 'w', encoding='utf-8') as f:
                json.dump({'mcp': entries, 'instructions': [fallback]}, f, indent=2, ensure_ascii=False)
            print('⚠ %s ist kein reines JSON (Kommentare?) — nicht angefasst.' % path)
            print('  Den Block aus %s von Hand einfuegen.' % snip)
            return False
        bak = path + '.pre-airem.bak'
        if not os.path.exists(bak):
            shutil.copy2(path, bak)
    data.setdefault('$schema', 'https://opencode.ai/config.json')
    mcp = data.setdefault('mcp', {})
    for name, entry in entries.items():
        if only is not None and name not in only:
            continue
        if entry is None:
            mcp.pop(name, None)
        else:
            mcp[name] = entry
    instr = [i for i in data.get('instructions', [])
             if not (isinstance(i, str) and i.endswith('fallback.md')
                     and ('ai-rem' in i or 'auto-memory' in i))]
    data['instructions'] = instr + [fallback]
    write_json_atomic(path, data)
    print('✓ %s: mcp %s' % (path, ', '.join(sorted(n for n in entries if n in mcp))))
    return True


def replace_marked_block(path, block):
    """Markierten ai-rem-Block ersetzen/anhaengen (block='' entfernt ihn)."""
    text = ''
    if os.path.isfile(path):
        with open(path, encoding='utf-8') as f:
            text = f.read()
    text = re.sub(re.escape(AGENTS_BEGIN) + r'[\s\S]*?' + re.escape(AGENTS_END), '', text).strip()
    if block:
        text = (text + '\n\n' if text else '') + block
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text + '\n' if text else '')


OPENCODE_COMMANDS = ('setup-ai-rem', 'memory-cleanup', 'ai-rem-update')


def install_opencode_files():
    ok = fetch_to(KG_URL + '/clients/opencode/ai-rem.ts', os.path.join(OPENCODE_DIR, 'plugin', 'ai-rem.ts'))
    for name in OPENCODE_COMMANDS:
        ok = fetch_to(KG_URL + '/cmd/opencode/' + name,
                      os.path.join(OPENCODE_DIR, 'command', name + '.md')) and ok
    replace_marked_block(os.path.join(OPENCODE_DIR, 'AGENTS.md'), AGENTS_BLOCK)
    fb = os.path.join(state_dir(), 'fallback.md')
    os.makedirs(os.path.dirname(fb), exist_ok=True)
    if not os.path.exists(fb):
        open(fb, 'w', encoding='utf-8').close()
    if ok:
        print('✓ opencode: Plugin, Commands (%s), AGENTS.md-Pointer'
              % ', '.join('/' + c for c in OPENCODE_COMMANDS))


def install_opencode(vault_entry, tools_entry, tools_reg_url):
    print('--- opencode ---')
    if not shutil.which('opencode'):
        print('ℹ opencode nicht im PATH — Konfiguration wird trotzdem geschrieben.')
    merge_opencode_json(opencode_mcp_entries(vault_entry, tools_entry, tools_reg_url))
    install_opencode_files()


def update_opencode():
    # Nur ai-rem selbst nachziehen; mykeyvault/tools brauchen git+npm und bleiben
    # beim Update wie sie sind (Neu-Einrichtung: ai-rem install --client opencode).
    merge_opencode_json(opencode_mcp_entries('', '', ''), only=('ai-rem',))
    install_opencode_files()


def uninstall_opencode():
    path = opencode_config_path()
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        data.get('mcp', {}).pop('ai-rem', None)
        instr = [i for i in data.get('instructions', [])
                 if not (isinstance(i, str) and i.endswith('fallback.md'))]
        if instr:
            data['instructions'] = instr
        else:
            data.pop('instructions', None)
        write_json_atomic(path, data)
        print('✓ %s: ai-rem entfernt (mykeyvault/tools bleiben stehen)' % path)
    except (OSError, ValueError):
        print('⚠ %s nicht lesbar — mcp-Eintrag ggf. von Hand entfernen' % path)
    replace_marked_block(os.path.join(OPENCODE_DIR, 'AGENTS.md'), '')
    rels = [os.path.join('plugin', 'ai-rem.ts')] + [os.path.join('command', c + '.md') for c in OPENCODE_COMMANDS]
    for rel in rels:
        try:
            os.unlink(os.path.join(OPENCODE_DIR, rel))
        except OSError:
            pass
    print('✓ opencode: Plugin, Commands und AGENTS.md-Block entfernt')


# ── generic: Snippets fuer weitere Frontends ─────────────────────────────────

def install_generic(mcp_endpoint):
    """Schreibt KEINE fremden Configs — nur Vorlagen. Auto-Ingest gibt es dort nicht;
    MCP + Instruktionen + Pointer-Text reichen fuer Lesen/Schreiben ins Gedaechtnis."""
    print('--- generic ---')
    os.makedirs(SNIPPET_DIR, exist_ok=True)
    # stdio ueber die CLI: der Token bleibt im Keychain, kein Bearer im Snippet.
    # Codex liest den Token per Env-Var — dort bleibt `ai-rem token` der Weg.
    cmd, cargs = cli_invocation('mcp-proxy')
    stdio = {'mcpServers': {'ai-rem': {'command': cmd, 'args': cargs}}}
    files = {
        'codex-config.toml': (
            '# ~/.codex/config.toml — Token per Env: export AI_REM_TOKEN="$(ai-rem token)"\n'
            '[mcp_servers.ai-rem]\nurl = "%s"\nbearer_token_env_var = "AI_REM_TOKEN"\n' % mcp_endpoint),
        'gemini-settings.json': json.dumps(stdio, indent=2) + '\n',
        'cursor-mcp.json': json.dumps(stdio, indent=2) + '\n',
        'AGENTS.md': AGENTS_BLOCK.replace('opencode hat keinen', 'die meisten Frontends haben keinen') + '\n',
    }
    for name, body in files.items():
        with open(os.path.join(SNIPPET_DIR, name), 'w', encoding='utf-8') as f:
            f.write(body)
    print('✓ Snippets: %s (%s)' % (SNIPPET_DIR, ', '.join(sorted(files))))
    print('  AGENTS.md-Block in die globale Instruktionsdatei des Frontends kopieren')
    print('  (Codex: ~/.codex/AGENTS.md, Gemini CLI: ~/.gemini/GEMINI.md).')


def uninstall_generic():
    shutil.rmtree(SNIPPET_DIR, ignore_errors=True)
    print('✓ Snippets entfernt')


# ── Claude Code entfernen ────────────────────────────────────────────────────

CLAUDE_HOOK_FILES = ('system-check.py', 'auto-memory.py', 'claude-md-guard.py',
                     'save-plan.py', 'vault-secret-reminder.py')
CLAUDE_COMMAND_FILES = ('setup-ai-rem.md', 'memory-cleanup.md', 'migrate-claude-md.md',
                        'ai-rem-update.md')


def uninstall_claude():
    claude = shutil.which('claude')
    if claude:
        run([claude, 'mcp', 'remove', '--scope', 'user', 'ai-rem'], timeout=60)
        print('✓ MCP-Registrierung ai-rem entfernt')
    path = os.path.join(CLAUDE_HOME, 'settings.json')
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        hooks = data.get('hooks', {})
        for event, groups in list(hooks.items()):
            for g in groups:
                g['hooks'] = [h for h in g.get('hooks', [])
                              if not any(n in h.get('command', '') for n in CLAUDE_HOOK_FILES)]
            hooks[event] = [g for g in groups if g.get('hooks')]
            if not hooks[event]:
                del hooks[event]
        write_json_atomic(path, data)
        print('✓ settings.json: ai-rem-Hooks ausgetragen')
    except (OSError, ValueError):
        pass
    cm = os.path.join(CLAUDE_HOME, 'CLAUDE.md')
    if os.path.isfile(cm):
        with open(cm, encoding='utf-8') as f:
            text = f.read()
        text = re.sub(r'(?:^|\n)## ai-rem[\s\S]*?(?=\n## |\Z)', '', text).strip()
        with open(cm, 'w', encoding='utf-8') as f:
            f.write(text + '\n' if text else '')
    for d, names in (('hooks', CLAUDE_HOOK_FILES), ('commands', CLAUDE_COMMAND_FILES)):
        for n in names:
            try:
                os.unlink(os.path.join(CLAUDE_HOME, d, n))
            except OSError:
                pass
    print('✓ Claude Code: Hooks, Commands und CLAUDE.md-Pointer entfernt')


def uninstall(targets):
    if not targets:
        print('✗ --uninstall braucht --client <claude|opencode|generic>')
        sys.exit(2)
    for t in targets:
        {'claude': uninstall_claude, 'opencode': uninstall_opencode,
         'generic': uninstall_generic}[t]()
    cfg = load_client_cfg()
    cfg['targets'] = [t for t in cfg.get('targets', []) if t not in targets]
    save_client_cfg(cfg)
    if not cfg['targets']:
        # Letztes Ziel weg => das Geraet braucht den Token nicht mehr.
        try:
            keychain_delete()
            print('✓ Token aus %s entfernt' % keychain_backend())
        except Exception as ex:
            print('⚠ Token nicht aus dem Keychain entfernt: %s (ai-rem token --forget)' % ex)
    print('Fertig. Verbleibende Ziele: %s' % (', '.join(cfg['targets']) or '—'))


# ── Migration: Klartext-Secrets aelterer Installationen einsammeln ───────────
# Vor 1.7 lagen der ai-rem-Token und der Vault-Token an bis zu sechs Stellen im
# Klartext. Jetzt gibt es genau ein Secret pro Geraet (Keychain); alles andere
# wird hier geloescht bzw. auf die stdio-Wrapper umgestellt. Laeuft bei jedem
# Setup/Update/Pair und ist idempotent: ohne Altlasten passiert nichts, und es
# wird nichts ausgegeben.

def _legacy_header_token(servers):
    auth = ((servers.get('ai-rem') or {}).get('headers') or {}).get('Authorization', '')
    return auth.split()[-1] if isinstance(auth, str) and auth.strip() else ''


def _is_cli_command(cmd):
    # Eintraege, die bereits auf die CLI zeigen (Pfad oder Shim), nicht nochmal anfassen.
    return isinstance(cmd, str) and os.path.basename(cmd).startswith('ai-rem')


def migrate_secrets():
    removed = []

    def gone(what):
        removed.append(what)
        print('✓ Klartext entfernt: %s' % what)

    cmd, cargs = cli_invocation()
    client_cfg = load_client_cfg()
    vault_entry = client_cfg.get('vault_entry') or ''

    # ~/.claude.json einmal lesen; Token-Uebernahme braucht den Header VOR dem Loeschen.
    cj = None
    try:
        with open(CLAUDE_JSON, encoding='utf-8') as f:
            cj = json.load(f)
    except (OSError, ValueError):
        pass
    servers = (cj.get('mcpServers') if isinstance(cj, dict) else None) or {}

    # (a) Keychain befuellen, falls leer: Token-Datei, dann Bearer aus ~/.claude.json.
    try:
        have = keychain_get()
    except Exception as ex:
        print('⚠ Keychain nicht lesbar (%s) — Klartext-Altlasten bleiben vorerst liegen' % ex)
        return removed
    if not have:
        legacy = read_secret(LEGACY_TOKEN_FILE) or _legacy_header_token(servers)
        if legacy:
            print('ℹ Token aus Klartext-Altbestand uebernommen')
            if not store_token(legacy):
                return removed  # ohne Keychain nichts loeschen — sonst waere der Token weg

    # (b) Token-Dateien
    for p in (LEGACY_TOKEN_FILE, LEGACY_VAULT_TOKEN_FILE, LEGACY_VAULT_ENV):
        if os.path.lexists(p):
            try:
                os.unlink(p)
                gone(p)
            except OSError as ex:
                print('⚠ %s nicht loeschbar: %s' % (p, ex))

    # (c) ~/.claude.json: ai-rem http+Bearer -> stdio-Proxy; mykeyvault env/headers weg.
    if cj is not None and servers:
        changed = False
        ai = servers.get('ai-rem')
        if isinstance(ai, dict) and ('headers' in ai or 'url' in ai):
            if 'headers' in ai:
                gone('%s mcpServers.ai-rem.headers' % CLAUDE_JSON)
            servers['ai-rem'] = {'type': 'stdio', 'command': cmd, 'args': cargs + ['mcp-proxy']}
            changed = True
        mkv = servers.get('mykeyvault')
        if isinstance(mkv, dict) and ('env' in mkv or 'headers' in mkv):
            if 'env' in mkv:
                gone('%s mcpServers.mykeyvault.env' % CLAUDE_JSON)
                # Pfad des gebauten MCP merken: `ai-rem vault-mcp` braucht ihn, und
                # ein --update baut nicht neu.
                args = mkv.get('args') or []
                if not vault_entry and args and str(args[0]).endswith('.js') and not _is_cli_command(mkv.get('command')):
                    vault_entry = args[0]
            if 'headers' in mkv:
                gone('%s mcpServers.mykeyvault.headers' % CLAUDE_JSON)
            if vault_entry and shutil.which('node'):
                servers['mykeyvault'] = {'type': 'stdio', 'command': cmd, 'args': cargs + ['vault-mcp']}
            elif mkv.get('url'):
                servers['mykeyvault'] = {'type': 'stdio', 'command': cmd,
                                         'args': cargs + ['mcp-proxy', '--endpoint', mkv['url']]}
            else:
                servers.pop('mykeyvault')
                print('  mykeyvault ohne gebauten MCP entfernt — `ai-rem install` registriert ihn neu')
            changed = True
        if changed:
            write_json_atomic(CLAUDE_JSON, cj)

    # (d) settings.json: LLM-Key kommt vom Server
    spath = os.path.join(CLAUDE_HOME, 'settings.json')
    try:
        with open(spath, encoding='utf-8') as f:
            sdata = json.load(f)
        env = sdata.get('env') if isinstance(sdata, dict) else None
        if isinstance(env, dict) and 'AI_REM_LLM_API_KEY' in env:
            env.pop('AI_REM_LLM_API_KEY')
            write_json_atomic(spath, sdata)
            gone('%s env.AI_REM_LLM_API_KEY' % spath)
    except (OSError, ValueError):
        pass

    # (e) client.json: Secret-Felder raus, vault_entry ggf. aus (c) nachtragen
    if client_cfg:
        stale = [k for k in LEGACY_CLIENT_FIELDS if k in client_cfg]
        if stale or (vault_entry and client_cfg.get('vault_entry') != vault_entry):
            for k in stale:
                client_cfg.pop(k)
                gone('%s %s' % (CLIENT_JSON, k))
            if vault_entry:
                client_cfg['vault_entry'] = vault_entry
            save_client_cfg(client_cfg)

    # (f) opencode.json: {file:…}-Referenzen und Vault-Env -> stdio-Wrapper
    if 'opencode' in installed_targets():
        _migrate_opencode_secrets(vault_entry, gone)
    return removed


def _migrate_opencode_secrets(vault_entry, gone):
    path = opencode_config_path()
    try:
        with open(path, encoding='utf-8') as f:
            mcp = (json.load(f) or {}).get('mcp') or {}
    except (OSError, ValueError):
        return
    ai = mcp.get('ai-rem') or {}
    mkv = mcp.get('mykeyvault') or {}
    legacy_ai = 'headers' in ai or ai.get('type') == 'remote'
    legacy_mkv = bool(mkv) and ('VAULT_API_TOKEN' in (mkv.get('environment') or {})
                                or 'headers' in mkv)
    if not (legacy_ai or legacy_mkv):
        return
    new = opencode_mcp_entries(vault_entry, '', '')
    entries = {}
    if legacy_ai:
        gone('%s mcp.ai-rem.headers' % path)
        entries['ai-rem'] = new['ai-rem']
    if legacy_mkv:
        gone('%s mcp.mykeyvault.environment' % path if 'headers' not in mkv
             else '%s mcp.mykeyvault.headers' % path)
        # Ohne node/gebauten MCP lieber raus als ein Eintrag, der auf eine
        # geloeschte Token-Datei zeigt.
        entries['mykeyvault'] = new.get('mykeyvault')
    merge_opencode_json(entries, only=tuple(entries))


# ── Ablauf ────────────────────────────────────────────────────────────────────

def update_claude(setup_cfg, mcp_endpoint):
    """Claude-Teil des Updates. write_settings_template() holt Neues vom Server ins
    Template, update_settings() merged es additiv in die settings.json. Das Template
    gehoert dem Server und wird komplett neu geschrieben; Handaenderungen gehoeren in
    die settings.json."""
    print('--- Claude Code ---')
    os.makedirs(os.path.join(CLAUDE_HOME, 'hooks'), exist_ok=True)
    os.makedirs(os.path.join(CLAUDE_HOME, 'commands'), exist_ok=True)
    dedupe_claude_json()
    write_settings_template(setup_cfg, mcp_endpoint)
    vault = mykeyvault_registered()
    hook_paths = install_hooks(vault_reminder=vault)
    update_settings(setup_cfg, mcp_endpoint, hook_paths, vault_reminder=vault)
    install_commands()


def update_only(targets):
    """Nur die ausgelieferten Dateien auffrischen — kein Bootstrap.

    Uebersprungen: register_mcp (laengst registriert), pull_secrets (SSH),
    build_tools_mcp/build_mykeyvault_mcp (git+npm), update_claude_json,
    create_entities (brauchen den Token), update_claude_md. Alles, was hier laeuft,
    ist idempotent, und fetch_to() schreibt atomar — ein Serverfehler laesst die
    bestehende Datei stehen. migrate_secrets() laeuft mit: ein Update von < 1.7
    muss die Klartext-Tokens einsammeln, sonst bleiben sie liegen.
    """
    targets = targets or installed_targets() or ['claude']
    print('=== ai-rem Update (%s; %s) ===' % (PLATFORM, ', '.join(targets)))
    setup_cfg = load_setup_config()
    mcp_endpoint = choose_mcp_endpoint(setup_cfg)
    # CLI zuerst: der Keychain-Code (lib/keychain.py) kommt mit ihr.
    link_cli(install_cli())
    migrate_secrets()
    if 'claude' in targets:
        update_claude(setup_cfg, mcp_endpoint)
    if 'opencode' in targets:
        print('--- opencode ---')
        update_opencode()
    if 'generic' in targets:
        install_generic(mcp_endpoint)
    record_client(mcp_endpoint, targets, setup_cfg)
    print('')
    print('Fertig. Clients neu starten - Hooks und Plugins werden nur beim Start geladen.')


def pair_only():
    """`ai-rem pair`: nur den Token neu holen (z.B. nach Rotation). Der Token
    geht allein in den Keychain; die MCP-Eintraege kennen keinen Bearer mehr,
    also gibt es nichts mitzuziehen — nur Altlasten einzusammeln."""
    # CLI zuerst: der Keychain-Code (lib/keychain.py) kommt mit ihr.
    link_cli(install_cli())
    paired = pair_device() or {}
    tok = paired.get('ai_rem_token') or ask_token()
    if not tok or not store_token(tok):
        sys.exit(1)
    migrate_secrets()
    cfg = load_client_cfg()
    record_client(cfg.get('endpoint', ''), [], {}, vault_url=paired.get('vault_url', ''))


def install_claude(setup_cfg, mcp_endpoint, tools_entry, tools_reg_url, vault_entry):
    print('--- Claude Code ---')
    claude = find_claude()
    register_mcp(claude)
    os.makedirs(os.path.join(CLAUDE_HOME, 'hooks'), exist_ok=True)
    os.makedirs(os.path.join(CLAUDE_HOME, 'commands'), exist_ok=True)
    try:
        update_claude_json(setup_cfg, mcp_endpoint, tools_entry, tools_reg_url, vault_entry)
    except Exception as ex:
        print('⚠ ~/.claude.json-Update fehlgeschlagen: %s' % ex)

    write_settings_template(setup_cfg, mcp_endpoint)
    # Nach update_claude_json: erst jetzt steht fest, ob mykeyvault registriert ist.
    vault = mykeyvault_registered()
    hook_paths = install_hooks(vault_reminder=vault)
    update_settings(setup_cfg, mcp_endpoint, hook_paths, vault_reminder=vault)

    # Auto-Memory md-Fallback: leere Datei (wird via @import in CLAUDE.md geladen)
    fb = os.path.join(CLAUDE_HOME, 'auto-memory', 'fallback.md')
    os.makedirs(os.path.dirname(fb), exist_ok=True)
    if not os.path.exists(fb):
        open(fb, 'w', encoding='utf-8').close()

    update_claude_md()
    install_commands()

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


def main():
    # ponytail: KG_URL wird erst beim Ausliefern ersetzt (server.py). Aus einem
    # lokalen Checkout gestartet bliebe der Platzhalter stehen und landete als
    # kaputte MCP-URL in ~/.claude.json. Lieber hier abbrechen als still falsch
    # konfigurieren. Lokal testen: KG_URL per Env ueberschreiben.
    if KG_URL.startswith('__'):
        sys.stderr.write(
            'ai-rem: KG_URL ist ein nicht ersetzter Platzhalter (%s).\n'
            'Setup ueber den Server starten: bash <(curl -s <kg-url>/setup)\n' % KG_URL)
        sys.exit(2)
    global ASSUME_YES
    args = parse_args(sys.argv[1:])
    ASSUME_YES = args['yes']
    if args['pair_only']:
        return pair_only()
    if args['uninstall']:
        return uninstall(args['targets'])
    if args['update']:
        return update_only(args['targets'])
    targets = args['targets'] or detect_targets()
    print('=== ai-rem Setup (%s; Ziele: %s) ===' % (PLATFORM, ', '.join(targets)))

    setup_cfg = load_setup_config()
    mcp_endpoint = choose_mcp_endpoint(setup_cfg)
    # CLI zuerst: der Keychain-Code (lib/keychain.py) kommt mit ihr, und die Hooks,
    # das opencode-Plugin und die MCP-Eintraege (mcp-proxy/vault-mcp) rufen sie auf.
    link_cli(install_cli())
    ai_rem_token = pull_secrets(setup_cfg)
    vault_url = (os.environ.get('VAULT_API_URL')
                 or setup_cfg.get('mcp_register', {}).get('mykeyvault', {}).get('vault_url', 'http://mystorage:8223'))
    # Token VOR allem anderen: ohne SSH/Env laeuft hier die Kopplung im Browser.
    ai_rem_token, vault_url = obtain_tokens(ai_rem_token, vault_url, force_pair=args['pair'])
    report_keychain()
    migrate_secrets()
    # vault_entry None = kein Build versucht (z.B. setup-config nicht ladbar) —
    # dann bleibt ein vorhandener Eintrag in client.json stehen statt auf ''
    # ueberschrieben zu werden; migrate_secrets() hat ihn ggf. gerade erst gerettet.
    tools_entry, tools_reg_url, vault_entry = '', '', None
    if ('claude' in targets or 'opencode' in targets) and setup_cfg.get('mcp_register'):
        # node/npm/git nur, wenn wirklich ein Build ansteht — ein Nur-ai-rem-Setup
        # (mcp_register ohne stdio-Bloecke) braucht sie nicht und soll auch nicht
        # nach ihnen fragen.
        if needs_node_builds(setup_cfg):
            ensure_deps()
        tools_entry, tools_reg_url = build_tools_mcp(setup_cfg)
        vault_entry = build_mykeyvault_mcp(setup_cfg)
        if tools_reg_url:
            REPORT.append(('tools', bool(tools_entry), 'ai-rem install --yes'))
        if setup_cfg['mcp_register'].get('mykeyvault'):
            REPORT.append(('mykeyvault', bool(vault_entry), 'ai-rem install --yes'))
    # vault_entry VOR install_claude/install_opencode: `ai-rem vault-mcp` liest ihn
    # aus client.json.
    record_client(mcp_endpoint, targets, setup_cfg, vault_url=vault_url, vault_entry=vault_entry)
    vault_entry = vault_entry or load_client_cfg().get('vault_entry', '')

    if 'claude' in targets:
        install_claude(setup_cfg, mcp_endpoint, tools_entry, tools_reg_url, vault_entry)
    tok = resolve_token(ai_rem_token)

    if 'opencode' in targets:
        install_opencode(vault_entry, tools_entry, tools_reg_url)
    if 'generic' in targets:
        install_generic(mcp_endpoint)
    for t in targets:
        REPORT.append(('Frontend ' + t, True, ''))
    if tok:
        create_entities(setup_cfg, tok)

    print_report()
    if tok and os.path.isfile(LOCAL_CLI):
        print('')
        sys.stdout.flush()
        subprocess.call([sys.executable, LOCAL_CLI, 'doctor'], env=dict(os.environ, KG_URL=KG_URL),
                        stdin=subprocess.DEVNULL)
    print('')
    print('Fertig. Clients neu starten - dann ist ai-rem aktiv.')
    print('Weiteres Frontend nachruesten:  ai-rem install --client <claude|opencode|generic>')
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
