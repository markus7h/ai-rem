"""
Knowledge Graph Memory MCP Server
Langzeit-Gedächtnis für Claude via Kuzu embedded graph database.
"""

import asyncio
import atexit
import fcntl
import glob
import hashlib
import json
import logging
import os
import queue
import re
import threading
from datetime import datetime
from typing import Optional

import kuzu
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

VERSION = "0.1.2"
DB_PATH = os.getenv("KUZU_DB_PATH", "/data/kg.db")
BACKUP_DIR = os.getenv("BACKUP_DIR", "/backups")
MAX_BACKUPS = int(os.getenv("MAX_BACKUPS", "10"))
KUZU_POOL_SIZE = max(1, int(os.getenv("KUZU_POOL_SIZE", "4")))
_BACKUP_CONFIG = os.path.join(BACKUP_DIR, ".config.json")

# ─── Setup-Endpunkt Inhalte ──────────────────────────────────────────────────

_KG_URL = os.getenv("KG_PUBLIC_URL", "http://localhost:3456")

_SETUP_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "setup-config.json")

SYSTEM_CHECK_PY = r'''#!/usr/bin/env python3
"""Unified SessionStart system check for Claude Code.

Order: ai-rem → SMB → MCP (functional) → Settings (auto-sync) → Tools (count)
Config is read from ~/.claude/settings-template.json — no hardcoded paths.
"""
import json
import os
import platform
import re
import subprocess
import sys
import threading
import time
import urllib.request

SETTINGS = os.path.expanduser("~/.claude/settings.json")
TEMPLATE = os.path.expanduser("~/.claude/settings-template.json")


def _load_template():
    if os.path.exists(TEMPLATE):
        try:
            with open(TEMPLATE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


TMPL = _load_template()

AI_REM_ENDPOINT = os.environ.get(
    "AI_REM_ENDPOINT", TMPL.get("ai_rem_endpoint", "")
)
AI_REM_TIMEOUT = 5

SMB_CFG = TMPL.get("smb", {})
SMB_MOUNT = SMB_CFG.get("mount", "")
SMB_URL = SMB_CFG.get("url", "")
SMB_RETRIES = 5

MCP_STDIO_SERVERS = TMPL.get("mcp_stdio_servers", {})
MCP_STDIO_TIMEOUT = 3

TOOLS_SCRIPTS = TMPL.get("tools_scripts_dir", "")

results = []

INIT_MSG = json.dumps({
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05", "capabilities": {},
        "clientInfo": {"name": "system-check", "version": "1.0"},
    },
}) + "\n"


def emit(msg):
    print(json.dumps({"systemMessage": msg, "suppressOutput": True}))
    sys.exit(0)


def check_ai_rem():
    if not AI_REM_ENDPOINT:
        return

    def post(body, sid=None):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if sid:
            headers["mcp-session-id"] = sid
        req = urllib.request.Request(
            AI_REM_ENDPOINT, data=json.dumps(body).encode(),
            headers=headers, method="POST",
        )
        return urllib.request.urlopen(req, timeout=AI_REM_TIMEOUT)

    try:
        resp = post({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "system-check", "version": "1.0"},
            },
        })
        sid = resp.headers.get("mcp-session-id")
        resp.read()
        if not sid:
            results.append("ai-rem: nicht erreichbar")
            return

        try:
            post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid=sid).read()
        except Exception:
            pass

        resp = post({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "memory_status", "arguments": {}},
        }, sid=sid)
        raw = resp.read().decode()
        m = re.search(r"^data: (.+)$", raw, re.MULTILINE)
        body = m.group(1) if m else raw
        obj = json.loads(body)
        text = obj.get("result", {}).get("content", [{}])[0].get("text", "")
        results.append(text if text else "ai-rem: nicht erreichbar")
    except Exception:
        results.append("ai-rem: nicht erreichbar")


def check_smb():
    if platform.system() != "Darwin" or not SMB_MOUNT or not SMB_URL:
        return

    def is_mounted():
        try:
            return f"on {SMB_MOUNT} " in subprocess.check_output(
                ["mount"], text=True, timeout=3,
            )
        except Exception:
            return False

    if is_mounted():
        results.append("SMB ✓")
        return

    try:
        subprocess.Popen(
            ["open", SMB_URL],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        results.append("SMB ✗")
        return

    for _ in range(SMB_RETRIES):
        time.sleep(1)
        if is_mounted():
            results.append("SMB ✓")
            return

    results.append("SMB ✗ (timeout)")


def _check_one_stdio(name, path):
    if not os.path.exists(path):
        return False
    try:
        proc = subprocess.Popen(
            ["node", path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        proc.stdin.write(INIT_MSG.encode())
        proc.stdin.flush()

        output = []

        def reader():
            try:
                for _ in range(10):
                    line = proc.stdout.readline()
                    if not line:
                        break
                    output.append(line.decode().strip())
                    try:
                        obj = json.loads(output[-1])
                        if "result" in obj:
                            return
                    except (json.JSONDecodeError, KeyError):
                        pass
            except Exception:
                pass

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        t.join(timeout=MCP_STDIO_TIMEOUT)

        proc.kill()
        try:
            proc.wait(timeout=1)
        except Exception:
            pass

        for line in output:
            try:
                if "result" in json.loads(line):
                    return True
            except (json.JSONDecodeError, KeyError):
                pass
        return False
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
        return False


def check_mcp_servers():
    if not MCP_STDIO_SERVERS:
        return

    server_results = {}

    def check_one(name, path):
        server_results[name] = _check_one_stdio(name, path)

    threads = []
    for name, path in MCP_STDIO_SERVERS.items():
        t = threading.Thread(target=check_one, args=(name, path))
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=MCP_STDIO_TIMEOUT + 2)

    ok = [n for n, v in server_results.items() if v]
    fail = [n for n in MCP_STDIO_SERVERS if not server_results.get(n)]
    total = len(MCP_STDIO_SERVERS)

    if fail:
        results.append(f"MCP: {len(ok)}/{total}, ✗ {', '.join(fail)}")
    else:
        results.append(f"MCP: {total}/{total} ✓")


def check_and_sync_settings():
    if not os.path.exists(TEMPLATE) or not os.path.exists(SETTINGS):
        if not os.path.exists(SETTINGS):
            results.append("settings: keine settings.json")
        return

    try:
        with open(SETTINGS) as f:
            local = json.load(f)
    except Exception:
        return

    changes = []

    for key, expected in TMPL.get("general", {}).items():
        actual = local.get(key)
        if actual != expected:
            local[key] = expected
            changes.append(f"{key}: {actual}→{expected}")

    local_allow = local.setdefault("permissions", {}).setdefault("allow", [])
    local_allow_set = set(local_allow)
    added_allow = []
    for p in TMPL.get("permissions_allow_portable", []):
        if p not in local_allow_set and not any(
            a.endswith("*") and p.startswith(a[:-1]) for a in local_allow_set
        ):
            local_allow.append(p)
            added_allow.append(p)
    if added_allow:
        changes.append(f"+{len(added_allow)} allow")

    local_deny = local.setdefault("permissions", {}).setdefault("deny", [])
    local_deny_set = set(local_deny)
    added_deny = []
    for p in TMPL.get("permissions_deny", []):
        if p not in local_deny_set:
            local_deny.append(p)
            added_deny.append(p)
    if added_deny:
        changes.append(f"+{len(added_deny)} deny")

    if changes:
        with open(SETTINGS, "w") as f:
            json.dump(local, f, indent=2, ensure_ascii=False)
            f.write("\n")
        results.append(f"settings: {', '.join(changes)}")
    else:
        results.append("settings ✓")


def check_tools():
    if not TOOLS_SCRIPTS or not os.path.isdir(TOOLS_SCRIPTS):
        return
    count = sum(
        1 for e in os.listdir(TOOLS_SCRIPTS)
        if os.path.exists(os.path.join(TOOLS_SCRIPTS, e, "manifest.yaml"))
    )
    if count:
        results.append(f"{count} tools")


check_ai_rem()
check_smb()
check_mcp_servers()
check_and_sync_settings()
check_tools()

if results:
    emit(" | ".join(results))
'''

SETUP_SCRIPT = r"""#!/usr/bin/env bash
# ai-rem Setup - plattformunabhaengig (macOS + Linux).
# Abhaengigkeiten: bash, curl, python3, claude CLI.
set -e
KG_URL="__KG_URL__"
CLAUDE_HOME="$HOME/.claude"
HOOK_PATH="$CLAUDE_HOME/hooks/system-check.py"

echo "=== ai-rem Setup ==="

command -v python3 >/dev/null 2>&1 || { echo "✗ python3 fehlt - bitte installieren"; exit 1; }
command -v curl    >/dev/null 2>&1 || { echo "✗ curl fehlt - bitte installieren"; exit 1; }

# Alte kg-memory Registrierung entfernen
if claude mcp list 2>/dev/null | grep -q "kg-memory"; then
    claude mcp remove kg-memory
    echo "✓ Alte kg-memory Registrierung entfernt"
fi

# Neue ai-rem Registrierung
if claude mcp list 2>/dev/null | grep -q "ai-rem"; then
    echo "✓ MCP bereits registriert"
else
    claude mcp add --transport http --scope user ai-rem "$KG_URL/mcp"
    echo "✓ MCP registriert (ai-rem)"
fi

mkdir -p "$CLAUDE_HOME/hooks" "$CLAUDE_HOME/commands"

# Setup-Config laden (persoenliche Entities, Permissions — nicht im Repo)
SETUP_CFG=$(curl -sf "$KG_URL/setup-config" 2>/dev/null || echo '{}')
export SETUP_CFG

# settings-template.json: Basis-Template anlegen (falls nicht vorhanden)
TEMPLATE_PATH="$CLAUDE_HOME/settings-template.json"
if [ ! -f "$TEMPLATE_PATH" ]; then
    python3 -c "
import json, os
cfg = json.loads(os.environ.get('SETUP_CFG', '{}'))
tmpl = {
    'version': '2026-05-25',
    'ai_rem_endpoint': '$KG_URL/mcp',
    'smb': cfg.get('smb', {}),
    'mcp_stdio_servers': cfg.get('mcp_stdio_servers', {}),
    'tools_scripts_dir': cfg.get('tools_scripts_dir', ''),
    'general': {'model': 'opus', 'autoMemoryEnabled': False, 'theme': 'auto'},
    'permissions_allow_portable': cfg.get('permissions_allow_portable', [
        'Bash', 'Skill(update-config)', 'Skill(update-config:*)',
        'mcp__ai-rem__memory_status', 'mcp__ai-rem__memory_get_context',
        'mcp__ai-rem__memory_search', 'mcp__ai-rem__memory_add',
        'mcp__ai-rem__memory_list', 'mcp__ai-rem__memory_get_relations',
        'mcp__ai-rem__memory_relate', 'mcp__ai-rem__memory_delete',
    ]),
    'permissions_allow_path_templates': ['Read(//{HOME}/.claude/**)', 'Read(//{TMP}/**)'],
    'permissions_deny': cfg.get('permissions_deny', []),
    'hooks': {
        'SessionStart': ['system-check.py (ai-rem, SMB, MCP, settings-sync, tools)'],
        'UserPromptSubmit': ['Tool-Discovery'],
    },
    'additional_directories_templates': ['{HOME}/.claude', '{HOME}'],
    'path_mappings': cfg.get('path_mappings', {}),
}
with open(os.path.expanduser('~/.claude/settings-template.json'), 'w') as f:
    json.dump(tmpl, f, indent=2, ensure_ascii=False); f.write('\n')
print('✓ settings-template.json angelegt')
"
else
    echo "✓ settings-template.json bereits vorhanden"
fi

# SessionStart-Hook: konsolidiertes system-check.py
curl -sf "$KG_URL/hooks/system-check.py" > "$HOOK_PATH"
chmod +x "$HOOK_PATH"
echo "✓ SessionStart-Hook: $HOOK_PATH"

# settings.json: Permissions, konsolidierter Hook, alte Hooks entfernen
HOOK_PATH="$HOOK_PATH" python3 - << 'PYEOF'
import json
import os

path = os.path.expanduser("~/.claude/settings.json")
hook_path = os.environ["HOOK_PATH"]
tmpl_path = os.path.expanduser("~/.claude/settings-template.json")
data = json.load(open(path)) if os.path.exists(path) else {}
tmpl = json.load(open(tmpl_path)) if os.path.exists(tmpl_path) else {}

perms = data.setdefault("permissions", {})
allow = perms.setdefault("allow", [])
allow[:] = [p.replace("mcp__kg-memory__", "mcp__ai-rem__") for p in allow]

allow_set = set(allow)
added = []
for p in tmpl.get("permissions_allow_portable", []):
    if p not in allow_set and not any(
        a.endswith("*") and p.startswith(a[:-1]) for a in allow_set
    ):
        allow.append(p)
        added.append(p)

deny = perms.setdefault("deny", [])
deny_set = set(deny)
added_deny = []
for p in tmpl.get("permissions_deny", []):
    if p not in deny_set:
        deny.append(p)
        added_deny.append(p)

for key, val in tmpl.get("general", {}).items():
    data[key] = val

hooks = data.setdefault("hooks", {})
sessions = hooks.setdefault("SessionStart", [])
group = next((g for g in sessions if g.get("matcher") == "*"), None)
if group is None:
    group = {"matcher": "*", "hooks": []}
    sessions.append(group)
group.setdefault("hooks", [])

OLD_HOOKS = [
    "ai-rem-bootstrap.py", "ai-rem-bootstrap.sh",
    "settings-sync-check.py",
]
OLD_HOOKS.extend(json.loads(os.environ.get("SETUP_CFG", "{}")).get("old_hooks", []))
group["hooks"] = [
    h for h in group["hooks"]
    if not any(h.get("command", "").endswith(o) for o in OLD_HOOKS)
]

hook_added = False
if not any(h.get("command") == hook_path for h in group["hooks"]):
    group["hooks"].append({"type": "command", "command": hook_path, "timeout": 15})
    hook_added = True

json.dump(data, open(path, "w"), indent=2, ensure_ascii=False)
print("\n".join([p for p in ("" if not added else f"  +{len(added)} allow permissions",
                             "" if not added_deny else f"  +{len(added_deny)} deny rules",
                             "  SessionStart-Hook" if hook_added else "",
                             "  autoMemoryEnabled=false") if p]))
print("✓ settings.json aktualisiert")
PYEOF

# CLAUDE.md: minimaler Pointer auf ai-rem (Regeln kommen ueber MCP Server Instructions)
python3 - << 'PYEOF'
import os
import re

path = os.path.expanduser("~/.claude/CLAUDE.md")
new_block = '''
## ai-rem
ai-rem ist die einzige Wissensquelle für persistenten Kontext. Auto-Memory ist deaktiviert.
Nutzungsregeln kommen über die MCP Server Instructions, Verhaltensregeln aus den ai-rem Preferences.
'''

os.makedirs(os.path.dirname(path), exist_ok=True)
text = open(path).read() if os.path.exists(path) else ""

# Bestehenden ai-rem-Block (alt oder neu) entfernen
for pat in [
    re.compile(r"\n## Knowledge Graph Memory \(ai-rem\)[\s\S]*?(?=\n## |\Z)"),
    re.compile(r"\n## ai-rem[\s\S]*?(?=\n## |\Z)"),
]:
    text, n = pat.subn("", text)

if not text.endswith("\n"):
    text += "\n"
text += new_block

open(path, "w").write(text)
print("✓ CLAUDE.md aktualisiert (minimaler ai-rem Pointer)")
PYEOF

# Legacy-Slash-Command entfernen
LEGACY="$CLAUDE_HOME/commands/setup-kg-memory.md"
[ -f "$LEGACY" ] && rm "$LEGACY" && echo "✓ Alter /setup-kg-memory Command entfernt"

# Slash-Commands installieren
curl -sf "$KG_URL/cmd" > "$CLAUDE_HOME/commands/setup-ai-rem.md"
echo "✓ /setup-ai-rem Command angelegt"

mkdir -p "$CLAUDE_HOME/commands/ai-rem" "$CLAUDE_HOME/ai-rem"
curl -sf "$KG_URL/cmd/prefedit" > "$CLAUDE_HOME/commands/ai-rem/prefedit.md"
echo "✓ /ai-rem:prefedit Command angelegt"

curl -sf "$KG_URL/tools/pref-tui.py" > "$CLAUDE_HOME/ai-rem/pref-tui.py"
chmod +x "$CLAUDE_HOME/ai-rem/pref-tui.py"
echo "✓ pref-tui.py installiert: $CLAUDE_HOME/ai-rem/pref-tui.py"

# Preferences & Tool-Entities direkt via MCP API anlegen (kein Claude-Token-Verbrauch)
KG_URL="$KG_URL" python3 - << 'PYSETUP'
import json, os, re, sys, urllib.request

BASE    = os.environ["KG_URL"]
MCP_URL = BASE + "/mcp"
_SID    = None

def _post(body, sid=None):
    hdrs = {"Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"}
    if sid:
        hdrs["mcp-session-id"] = sid
    req = urllib.request.Request(
        MCP_URL, data=json.dumps(body).encode(), headers=hdrs, method="POST")
    return urllib.request.urlopen(req, timeout=10)

def _parse(resp):
    raw = resp.read().decode()
    m = re.search(r"^data: (.+)$", raw, re.MULTILINE)
    try:
        obj = json.loads(m.group(1) if m else raw)
        return obj.get("result", {}).get("content", [{}])[0].get("text", "")
    except Exception:
        return ""

def _session():
    global _SID
    if _SID: return _SID
    resp = _post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                             "clientInfo": {"name": "setup", "version": "1.0"}}})
    _SID = resp.headers.get("mcp-session-id")
    resp.read()
    try: _post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid=_SID).read()
    except Exception: pass
    return _SID

def _tool(name, args):
    resp = _post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                  "params": {"name": name, "arguments": args}}, sid=_session())
    return _parse(resp)

cfg = json.loads(os.environ.get("SETUP_CFG", "{}"))
ENTITIES = cfg.get("entities", [
    {"name": "skill_setup_ai_rem", "type": "Tool",
     "description": "Slash-Command /setup-ai-rem: ai-rem MCP-Server auf neuem System einrichten."},
    {"name": "skill_ai_rem_prefedit", "type": "Tool",
     "description": "Slash-Command /ai-rem:prefedit: interaktiver Preferences-Manager."},
])

try:
    for e in ENTITIES:
        _tool("memory_add", e)
    print(f"✓ {len(ENTITIES)} Preferences & Tool-Entities aktualisiert")
except Exception as ex:
    print(f"⚠ Entities: {ex}")
PYSETUP

echo ""
echo "Fertig. Claude Code neu starten - dann ist ai-rem aktiv."
echo "Auf jeder neuen Maschine: bash <(curl -s __KG_URL__/setup)"
""".replace("__KG_URL__", _KG_URL)

CMD_MD = """\
# ai-rem einrichten

Führe auf jeder neuen Maschine aus:

```bash
bash <(curl -s __KG_URL__/setup)
```

Das Skript erledigt automatisch:
- MCP-Server registrieren
- Konsolidiertes system-check.py Hook deployen (ai-rem, SMB, MCP, Settings-Sync, Tools)
- settings-template.json + settings.json konfigurieren (Permissions, Deny-Rules, Hooks)
- CLAUDE.md aktualisieren
- Slash-Commands installieren (`/setup-ai-rem`, `/ai-rem:prefedit`)
- Preferences & Tool-Entities im Knowledge Graph anlegen

Danach Claude Code neu starten — fertig.
""".replace("__KG_URL__", _KG_URL)

PREFEDIT_CMD_MD = """\
# Preferences verwalten

Antworte dem User mit genau diesem Text (URL nicht verändern):

Preferences-Manager: __KG_URL__/prefs
""".replace("__KG_URL__", _KG_URL)

PREF_TUI_SCRIPT = r'''#!/usr/bin/env python3
"""ai-rem Preference Manager — läuft direkt im Terminal, kein Claude-Token-Verbrauch."""
import json, os, re, sys, urllib.request

BASE    = os.environ.get("AI_REM_URL", "__BASE__")
API_URL = BASE + "/api/preferences"
MCP_URL = BASE + "/mcp"
_SID    = None


def _post(body, sid=None):
    hdrs = {"Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"}
    if sid:
        hdrs["mcp-session-id"] = sid
    req = urllib.request.Request(
        MCP_URL, data=json.dumps(body).encode(), headers=hdrs, method="POST")
    return urllib.request.urlopen(req, timeout=10)


def _parse(resp):
    raw = resp.read().decode()
    m = re.search(r"^data: (.+)$", raw, re.MULTILINE)
    try:
        obj = json.loads(m.group(1) if m else raw)
        return obj.get("result", {}).get("content", [{}])[0].get("text", "")
    except Exception:
        return raw


def _session():
    global _SID
    if _SID:
        return _SID
    resp = _post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                             "clientInfo": {"name": "pref-tui", "version": "1.0"}}})
    _SID = resp.headers.get("mcp-session-id")
    resp.read()
    try:
        _post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid=_SID).read()
    except Exception:
        pass
    return _SID


def _tool(name, args):
    resp = _post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                  "params": {"name": name, "arguments": args}}, sid=_session())
    return _parse(resp)


def load():
    resp = urllib.request.urlopen(API_URL, timeout=10)
    return json.loads(resp.read().decode())


def show(prefs):
    print()
    print(f"  {'#':>2}  {'P':2}  {'Pos':>3}  {'Context':<8}  Name")
    print(f"  {'─'*2}  {'─'*2}  {'─'*3}  {'─'*8}  {'─'*50}")
    for i, p in enumerate(prefs, 1):
        pin = "📌" if p["pinned"] else "  "
        pos = str(p["sort_order"]) if p["sort_order"] is not None else "─"
        ctx = (p["context"] or "global")[:8]
        print(f"  {i:>2}  {pin}  {pos:>3}  {ctx:<8}  {p['name'][:50]}")
    print()
    print("  p <#>           pin/unpin")
    print("  c <#> <ctx>     context: work | private | global")
    print("  s <#> <pos>     position (1=oben, leer=auto)")
    print("  d <#>           löschen")
    print("  q               beenden")
    print()


def run():
    print("=== ai-rem Preferences ===")
    try:
        prefs = load()
    except Exception as e:
        sys.exit(f"Fehler: {e}")

    while True:
        show(prefs)
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nTschüss.")
            break

        if not line or line == "q":
            break

        parts = line.split()
        cmd   = parts[0].lower()

        if cmd not in ("p", "c", "s", "d") or len(parts) < 2:
            print("  ?")
            continue

        try:
            p = prefs[int(parts[1]) - 1]
        except (IndexError, ValueError):
            print("  Ungültige Nummer.")
            continue

        if cmd == "p":
            new_pin = not p["pinned"]
            _tool("memory_preference_update", {"name": p["name"], "pinned": new_pin})
            print(f"  {'📌' if new_pin else '  '} {p['name']}")
        elif cmd == "c":
            ctx = parts[2] if len(parts) > 2 else "global"
            _tool("memory_preference_update",
                  {"name": p["name"], "context": "" if ctx == "global" else ctx})
            print(f"  context={ctx}: {p['name']}")
        elif cmd == "s":
            try:
                pos = int(parts[2]) if len(parts) > 2 and parts[2] else None
            except ValueError:
                pos = None
            _tool("memory_preference_update",
                  {"name": p["name"], "sort_order": pos})
            print(f"  pos={pos}: {p['name']}")
        elif cmd == "d":
            try:
                confirm = input(f"  '{p['name']}' löschen? [j/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                confirm = ""
            if confirm == "j":
                _tool("memory_delete", {"name": p["name"]})
                print("  Gelöscht.")
            else:
                print("  Abgebrochen.")

        prefs = load()


if __name__ == "__main__":
    run()
'''

db = kuzu.Database(DB_PATH)

# Kuzu Connection objects are not thread-safe, but a Database can host many.
# A small pool lets independent requests run truly concurrently — under the
# previous single-conn + global lock, every request serialized on the same lock,
# blocking the event loop for the duration of each query.
_pool: queue.Queue = queue.Queue(maxsize=KUZU_POOL_SIZE)
for _ in range(KUZU_POOL_SIZE):
    _pool.put(kuzu.Connection(db))


def db_exec(query: str, params: dict | None = None) -> kuzu.QueryResult:
    c = _pool.get()
    try:
        return c.execute(query, params or {})
    finally:
        _pool.put(c)


async def db_exec_async(query: str, params: dict | None = None) -> kuzu.QueryResult:
    """Run db_exec off the event loop. Use from `async def` route handlers."""
    return await asyncio.to_thread(db_exec, query, params)


# Tiny helpers that are needed at module-import time (init_schema → migration).
# Other helpers (_id, _ctx_match, _ctx_clause, _apply_import) live further down
# in the "helpers" section because they're only invoked from tool/route bodies.


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _rows(result: kuzu.QueryResult) -> list[list]:
    rows = []
    while result.has_next():
        rows.append(result.get_next())
    return rows


def _ensure_backup_dir() -> None:
    os.makedirs(BACKUP_DIR, exist_ok=True)


def init_schema() -> None:
    stmts = [
        """CREATE NODE TABLE IF NOT EXISTS Entity(
               id     STRING PRIMARY KEY,
               name   STRING,
               type   STRING,
               descr  STRING,
               extra  STRING,
               context STRING DEFAULT '',
               created_at STRING,
               updated_at STRING
           )""",
        """CREATE REL TABLE IF NOT EXISTS Rel(
               FROM Entity TO Entity,
               name       STRING,
               extra      STRING,
               created_at STRING
           )""",
    ]
    for stmt in stmts:
        try:
            db_exec(stmt)
        except Exception as e:
            log.warning("Schema stmt skipped: %s", e)
    _migrate_context_column()
    _migrate_pinned_column()
    _migrate_sort_order_column()
    log.info("Schema ready — DB at %s", DB_PATH)


def _entity_has_column(column: str) -> bool:
    try:
        result = db_exec("CALL TABLE_INFO('Entity') RETURN *")
    except Exception as e:
        log.warning("TABLE_INFO probe failed: %s", e)
        return True
    for row in _rows(result):
        for cell in row:
            if isinstance(cell, str) and cell == column:
                return True
    return False


def _entity_has_context_column() -> bool:
    """Probe whether Entity already has the `context` column."""
    return _entity_has_column("context")


def _legacy_dump_pre_context() -> dict:
    """Dump the graph as it looked BEFORE the context column existed. Used only
    by the schema migration to write a safety backup against the pre-ALTER schema."""
    _ensure_backup_dir()
    entities = _rows(db_exec(
        "MATCH (e:Entity) RETURN e.id, e.name, e.type, e.descr, e.extra, "
        "e.created_at, e.updated_at"
    ))
    relations = _rows(db_exec(
        "MATCH (a:Entity)-[r:Rel]->(b:Entity) RETURN a.id, r.name, b.id, r.extra, r.created_at"
    ))
    return {
        "version": 1,
        "exported_at": _now(),
        "entities": [
            {"id": r[0], "name": r[1], "type": r[2], "description": r[3],
             "extra": json.loads(r[4] or "{}"),
             "created_at": r[5], "updated_at": r[6]}
            for r in entities
        ],
        "relations": [
            {"from_id": r[0], "relation": r[1], "to_id": r[2],
             "extra": json.loads(r[3] or "{}"), "created_at": r[4]}
            for r in relations
        ],
    }


def _migrate_context_column() -> None:
    """One-off migration: add `context` column to Entity and backfill from extra."""
    if _entity_has_context_column():
        return
    # Pre-migration safety backup (using a dump that doesn't touch the new column).
    try:
        data = _legacy_dump_pre_context()
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup_path = os.path.join(BACKUP_DIR, f"backup_pre_context_{ts}.json")
        tmp = backup_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, backup_path)
        log.info("Pre-migration backup written: %s", os.path.basename(backup_path))
    except Exception as e:
        log.warning("Pre-migration backup failed (continuing): %s", e)

    try:
        db_exec("ALTER TABLE Entity ADD context STRING DEFAULT ''")
    except Exception as e:
        log.error("ALTER TABLE Entity ADD context failed: %s", e)
        return

    rows = _rows(db_exec("MATCH (e:Entity) RETURN e.id, e.extra"))
    backfilled = 0
    for eid, extra_raw in rows:
        try:
            ctx = json.loads(extra_raw or "{}").get("context", "") or ""
        except json.JSONDecodeError:
            ctx = ""
        if not ctx:
            continue
        db_exec(
            "MATCH (e:Entity {id: $id}) SET e.context = $ctx",
            {"id": eid, "ctx": ctx},
        )
        backfilled += 1
    log.info("Schema migration: context column added, %d entities backfilled", backfilled)


def _migrate_pinned_column() -> None:
    """One-off migration: add `pinned` column to Entity (no backfill needed)."""
    if _entity_has_column("pinned"):
        return
    try:
        db_exec("ALTER TABLE Entity ADD pinned STRING DEFAULT ''")
        log.info("Schema migration: pinned column added")
    except Exception as e:
        log.error("ALTER TABLE Entity ADD pinned failed: %s", e)


def _migrate_sort_order_column() -> None:
    """One-off migration: add `sort_order` column to Entity (no backfill needed)."""
    if _entity_has_column("sort_order"):
        return
    try:
        db_exec("ALTER TABLE Entity ADD sort_order STRING DEFAULT ''")
        log.info("Schema migration: sort_order column added")
    except Exception as e:
        log.error("ALTER TABLE Entity ADD sort_order failed: %s", e)


init_schema()


# ─── Backup ─────────────────────────────────────────────────────────────────


def _safe_backup_path(name: str) -> Optional[str]:
    """Resolve `name` under BACKUP_DIR and reject anything escaping it."""
    if not name or not name.endswith(".json"):
        return None
    candidate = os.path.realpath(os.path.join(BACKUP_DIR, name))
    root = os.path.realpath(BACKUP_DIR)
    if not (candidate == root or candidate.startswith(root + os.sep)):
        return None
    if os.path.dirname(candidate) != root:
        return None
    return candidate


def _load_backup_cfg() -> dict:
    _ensure_backup_dir()
    try:
        with open(_BACKUP_CONFIG) as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"enabled": False, "interval": "daily", "last_backup": None}


def _save_backup_cfg(cfg: dict) -> None:
    _ensure_backup_dir()
    # Write through a temp file and atomically replace, with an exclusive lock
    # on the destination to serialize concurrent writers (scheduler + HTTP).
    fd = os.open(_BACKUP_CONFIG, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        tmp = _BACKUP_CONFIG + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, _BACKUP_CONFIG)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _dump_graph() -> dict:
    """Serialize the entire Entity+Rel graph into a plain dict (JSON-ready)."""
    entities = _rows(db_exec(
        "MATCH (e:Entity) RETURN e.id, e.name, e.type, e.descr, e.extra, "
        "e.context, e.created_at, e.updated_at"
    ))
    relations = _rows(db_exec(
        "MATCH (a:Entity)-[r:Rel]->(b:Entity) RETURN a.id, r.name, b.id, r.extra, r.created_at"
    ))
    return {
        "version": 1,
        "exported_at": _now(),
        "entities": [
            {"id": r[0], "name": r[1], "type": r[2], "description": r[3],
             "extra": json.loads(r[4] or "{}"), "context": r[5] or "",
             "created_at": r[6], "updated_at": r[7]}
            for r in entities
        ],
        "relations": [
            {"from_id": r[0], "relation": r[1], "to_id": r[2],
             "extra": json.loads(r[3] or "{}"), "created_at": r[4]}
            for r in relations
        ],
    }


def _graph_signature() -> dict:
    """Fingerprint that changes iff the graph changed. Cheap (4 aggregate queries)."""
    e_count = _rows(db_exec("MATCH (e:Entity) RETURN count(e)"))[0][0]
    r_count = _rows(db_exec("MATCH ()-[r:Rel]->() RETURN count(r)"))[0][0]
    max_e = _rows(db_exec("MATCH (e:Entity) RETURN max(e.updated_at)"))[0][0] or ""
    max_r = _rows(db_exec("MATCH ()-[r:Rel]->() RETURN max(r.created_at)"))[0][0] or ""
    return {"entities": int(e_count), "relations": int(r_count),
            "max_entity_updated": max_e, "max_relation_created": max_r}


def _do_backup() -> str:
    _ensure_backup_dir()
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"backup_{ts}.json"
    filepath = os.path.join(BACKUP_DIR, filename)
    tmp = filepath + ".tmp"

    data = _dump_graph()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, filepath)

    cfg = _load_backup_cfg()
    cfg["last_backup"] = _now()
    cfg["last_backup_file"] = filename
    cfg["last_backup_signature"] = _graph_signature()
    _save_backup_cfg(cfg)

    files = sorted(glob.glob(os.path.join(BACKUP_DIR, "backup_*.json")), reverse=True)
    for old in files[MAX_BACKUPS:]:
        try:
            os.remove(old)
        except FileNotFoundError:
            pass

    log.info("Backup created: %s", filename)
    return filename


_shutdown = threading.Event()


def _scheduler_loop() -> None:
    thresholds = {"hourly": 3600, "daily": 86400, "weekly": 604800}
    while not _shutdown.wait(60):
        cfg = _load_backup_cfg()
        if not cfg.get("enabled"):
            continue
        last = cfg.get("last_backup")
        if last:
            try:
                delta = (datetime.now() - datetime.fromisoformat(last)).total_seconds()
                if delta < thresholds.get(cfg.get("interval", "daily"), 86400):
                    continue
            except ValueError as e:
                log.warning("Corrupt last_backup timestamp %r, ignoring: %s", last, e)
        try:
            if cfg.get("last_backup_signature") == _graph_signature():
                log.info("Scheduled backup skipped: no graph changes since last backup")
                continue
            _do_backup()
        except Exception as e:
            log.error("Scheduled backup failed: %s", e)
    log.info("Scheduler stopped")


threading.Thread(target=_scheduler_loop, daemon=True, name="backup-scheduler").start()
atexit.register(_shutdown.set)


# ─── Web UI ──────────────────────────────────────────────────────────────────

_PREFS_HTML = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ai-rem · Preferences</title>
<style>
:root{--bg:#0f1117;--card:#1a1d27;--border:#2a2d3e;--accent:#6366f1;--ah:#818cf8;--text:#e2e8f0;--muted:#94a3b8;--ok:#22c55e;--err:#ef4444;--pin:#f59e0b}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;line-height:1.6;padding:28px;max-width:900px;margin:0 auto}
h1{font-size:22px;font-weight:700;margin-bottom:4px}
.sub{color:var(--muted);font-size:13px;margin-bottom:28px}
a{color:var(--accent);text-decoration:none}a:hover{color:var(--ah)}
table{width:100%;border-collapse:collapse}
th{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);padding:8px 10px;text-align:left;border-bottom:1px solid var(--border)}
td{padding:9px 10px;border-bottom:1px solid var(--border);vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:rgba(255,255,255,.02)}
.pin-btn{background:none;border:none;font-size:16px;cursor:pointer;opacity:.35;transition:opacity .15s;padding:0 4px}
.pin-btn.active{opacity:1}
.pin-btn:hover{opacity:.8}
select,input[type=number]{background:var(--bg);border:1px solid var(--border);color:var(--text);border-radius:5px;padding:4px 7px;font-size:12px;width:100%}
input[type=number]{width:60px}
.del{background:none;border:1px solid var(--border);color:var(--muted);border-radius:5px;padding:4px 10px;font-size:12px;cursor:pointer;transition:all .15s}
.del:hover{border-color:var(--err);color:var(--err)}
.name{font-size:13px;font-weight:500;max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;cursor:pointer}
.name:hover{color:var(--ah)}
.descr{font-size:11px;color:var(--muted);max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-top:1px}
.full-descr{display:none;font-size:12px;color:var(--muted);white-space:pre-wrap;word-break:break-word;margin-top:6px;padding:8px 10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;line-height:1.5}
.date{font-size:11px;color:var(--muted)}
.toast{position:fixed;bottom:24px;right:24px;background:var(--card);border:1px solid var(--border);border-radius:8px;padding:10px 16px;font-size:13px;opacity:0;transition:opacity .3s;pointer-events:none}
.toast.show{opacity:1}.toast.ok{border-color:var(--ok);color:var(--ok)}.toast.err{border-color:var(--err);color:var(--err)}
</style>
</head>
<body>
<h1>Preferences</h1>
<p class="sub"><a href="/ui">← ai-rem</a> &nbsp;·&nbsp; <span id="cnt">—</span> Einträge &nbsp;·&nbsp; 📌 = immer in Session-Kontext</p>
<table>
  <thead><tr>
    <th style="width:32px">📌</th>
    <th>Name</th>
    <th style="width:110px">Context</th>
    <th style="width:70px">Position</th>
    <th style="width:90px">Datum</th>
    <th style="width:60px"></th>
  </tr></thead>
  <tbody id="rows"><tr><td colspan="6" style="color:var(--muted);padding:20px 10px">Lade…</td></tr></tbody>
</table>
<div class="toast" id="toast"></div>
<script>
let prefs=[];

async function load(){
  prefs=await fetch('/api/preferences').then(r=>r.json()).catch(()=>[]);
  document.getElementById('cnt').textContent=prefs.length;
  const tb=document.getElementById('rows');
  if(!prefs.length){tb.innerHTML='<tr><td colspan="6" style="color:var(--muted);padding:20px 10px">Keine Preferences.</td></tr>';return;}
  tb.innerHTML=prefs.map((p,i)=>`
    <tr>
      <td><button class="pin-btn ${p.pinned?'active':''}" onclick="togglePin(${i})" title="${p.pinned?'Unpin':'Pin'}">📌</button></td>
      <td><div class="name" onclick="toggleDescr(${i})" title="Klicken zum Aufklappen">${esc(p.name)}</div><div class="descr">${esc(p.descr)}</div><div class="full-descr" id="fd${i}">${esc(p.descr)}</div></td>
      <td>
        <select onchange="setCtx(${i},this.value)">
          <option value="" ${!p.context?'selected':''}>global</option>
          <option value="private" ${p.context==='private'?'selected':''}>private</option>
          <option value="work" ${p.context==='work'?'selected':''}>work</option>
        </select>
      </td>
      <td><input type="number" min="1" value="${p.sort_order??''}" placeholder="─"
           onchange="setPos(${i},this.value)" onblur="setPos(${i},this.value)"></td>
      <td class="date">${p.updated_at?p.updated_at.slice(0,10):'—'}</td>
      <td><button class="del" onclick="del(${i})">Löschen</button></td>
    </tr>`).join('');
}

function esc(s){return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');}

function toggleDescr(i){
  const el=document.getElementById('fd'+i);
  el.style.display=el.style.display==='block'?'none':'block';
}

async function api(action,body){
  const r=await fetch('/api/preferences/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  return r.json();
}

async function togglePin(i){
  const p=prefs[i];
  await api('update',{name:p.name,pinned:!p.pinned});
  toast((!p.pinned?'📌 Gepinnt: ':'Unpinned: ')+p.name,'ok');
  load();
}

async function setCtx(i,ctx){
  const p=prefs[i];
  await api('update',{name:p.name,context:ctx});
  toast('Context → '+(ctx||'global')+': '+p.name,'ok');
  load();
}

async function setPos(i,val){
  const p=prefs[i];
  const pos=val===''||val===null?null:parseInt(val);
  if(pos===p.sort_order||(pos===null&&p.sort_order===null))return;
  await api('update',{name:p.name,sort_order:pos});
  toast('Position → '+(pos??'auto')+': '+p.name,'ok');
  load();
}

async function del(i){
  const p=prefs[i];
  if(!confirm('Löschen: '+p.name+'?'))return;
  await api('delete',{name:p.name});
  toast('Gelöscht: '+p.name,'ok');
  load();
}

function toast(msg,type){
  const el=document.getElementById('toast');
  el.textContent=msg;el.className='toast show '+type;
  setTimeout(()=>el.className='toast',3000);
}

load();
</script>
</body>
</html>"""

_UI_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ai-rem</title>
<style>
:root{--bg:#0f1117;--card:#1a1d27;--border:#2a2d3e;--accent:#6366f1;--ah:#818cf8;--text:#e2e8f0;--muted:#94a3b8;--ok:#22c55e;--err:#ef4444}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;line-height:1.6;padding:28px;max-width:820px;margin:0 auto}
h1{font-size:22px;font-weight:700;margin-bottom:4px}
h2{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:14px}
.sub{color:var(--muted);font-size:13px;margin-bottom:32px}
.grid{display:grid;gap:16px}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:22px}
.row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
select,input[type=file]{background:var(--bg);border:1px solid var(--border);color:var(--text);border-radius:6px;padding:6px 10px;font-size:13px}
button{background:var(--accent);color:#fff;border:none;border-radius:6px;padding:7px 16px;font-size:13px;font-weight:500;cursor:pointer;transition:background .15s}
button:hover{background:var(--ah)}
button.sec{background:transparent;border:1px solid var(--border);color:var(--text)}
button.sec:hover{border-color:var(--muted)}
button.del{background:transparent;border:1px solid var(--err);color:var(--err)}
button.del:hover{background:var(--err);color:#fff}
button:disabled{opacity:.45;cursor:not-allowed}
.toggle{display:flex;align-items:center;gap:10px}
.toggle input{width:36px;height:20px;appearance:none;background:var(--border);border-radius:10px;cursor:pointer;position:relative;transition:background .2s}
.toggle input:checked{background:var(--accent)}
.toggle input::after{content:'';position:absolute;width:16px;height:16px;background:#fff;border-radius:50%;top:2px;left:2px;transition:left .2s}
.toggle input:checked::after{left:18px}
.files{display:flex;flex-direction:column;gap:8px;margin-top:12px}
.fi{display:flex;align-items:center;gap:10px;padding:10px 12px;background:var(--bg);border:1px solid var(--border);border-radius:7px}
.fn{flex:1;font-family:monospace;font-size:12px;color:var(--muted)}
.fsz{font-size:12px;color:var(--muted);min-width:64px;text-align:right}
.empty{color:var(--muted);font-size:13px;padding:8px 0}
.hint{font-size:12px;color:var(--muted);margin-top:8px}
.sep{width:1px;height:18px;background:var(--border)}
.toast{position:fixed;bottom:24px;right:24px;background:var(--card);border:1px solid var(--border);border-radius:8px;padding:12px 18px;font-size:13px;opacity:0;transition:opacity .3s;pointer-events:none;max-width:340px}
.toast.show{opacity:1}
.toast.ok{border-color:var(--ok);color:var(--ok)}
.toast.err{border-color:var(--err);color:var(--err)}
</style>
</head>
<body>
<h1>ai-rem</h1>
<p class="sub">Knowledge Graph Memory &nbsp;·&nbsp; <span id="ec">—</span> entities &nbsp;·&nbsp; <span id="rc">—</span> relations &nbsp;·&nbsp; <a href="/prefs">Preferences →</a></p>
<div class="grid">

  <div class="card">
    <h2>Manual Backup</h2>
    <div class="row">
      <button id="bb" onclick="backupNow()">Backup now</button>
      <span style="font-size:12px;color:var(--muted)" id="lb">Last backup: —</span>
    </div>
  </div>

  <div class="card">
    <h2>Automatic Backup</h2>
    <div class="row">
      <div class="toggle"><input type="checkbox" id="se"><label for="se">Enable schedule</label></div>
      <div class="sep"></div>
      <label for="si" style="color:var(--muted);font-size:13px">Interval</label>
      <select id="si">
        <option value="hourly">Hourly</option>
        <option value="daily" selected>Daily</option>
        <option value="weekly">Weekly</option>
      </select>
      <button onclick="saveSchedule()">Save</button>
    </div>
  </div>

  <div class="card">
    <h2>Backup Files</h2>
    <div id="fl" class="files"><p class="empty">Loading…</p></div>
  </div>

  <div class="card">
    <h2>Restore</h2>
    <div class="row">
      <input type="file" id="rf" accept=".json">
      <select id="rm">
        <option value="merge">Merge</option>
        <option value="replace">Replace (wipe first)</option>
      </select>
      <button onclick="doRestore()">Restore</button>
    </div>
    <p class="hint">Merge adds missing entries. Replace deletes the entire graph before importing.</p>
  </div>

</div>
<div class="toast" id="toast"></div>
<script>
async function j(url,o){const r=await fetch(url,o);return r.json();}

async function loadStatus(){
  const r=await j('/api/status').catch(()=>({}));
  document.getElementById('ec').textContent=r.entities??'—';
  document.getElementById('rc').textContent=r.relations??'—';
  if(r.last_backup){
    document.getElementById('lb').textContent='Last backup: '+new Date(r.last_backup).toLocaleString();
  }
}

async function loadSchedule(){
  const r=await j('/api/backup/config').catch(()=>({}));
  document.getElementById('se').checked=r.enabled??false;
  document.getElementById('si').value=r.interval??'daily';
}

async function loadFiles(){
  const files=await j('/api/backup/files').catch(()=>[]);
  const el=document.getElementById('fl');
  if(!files.length){el.innerHTML='<p class="empty">No backups yet.</p>';return;}
  el.innerHTML=files.map(f=>`
    <div class="fi">
      <span class="fn">${f.name}</span>
      <span class="fsz">${(f.size/1024).toFixed(1)} KB</span>
      <a href="/api/backup/download?file=${encodeURIComponent(f.name)}">
        <button class="sec">Download</button></a>
      <button class="del" onclick="delFile('${f.name}')">Delete</button>
    </div>`).join('');
}

async function backupNow(){
  const b=document.getElementById('bb');
  b.disabled=true;b.textContent='Running…';
  const r=await j('/api/backup/now',{method:'POST'}).catch(e=>({error:e.message}));
  b.disabled=false;b.textContent='Backup now';
  if(r.error){toast(r.error,'err');return;}
  toast('Created: '+r.file,'ok');
  loadStatus();loadFiles();
}

async function saveSchedule(){
  const r=await j('/api/backup/config',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({enabled:document.getElementById('se').checked,interval:document.getElementById('si').value})
  }).catch(e=>({error:e.message}));
  r.error?toast(r.error,'err'):toast('Schedule saved','ok');
}

async function delFile(name){
  if(!confirm('Delete '+name+'?'))return;
  const r=await j('/api/backup/delete',{
    method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({file:name})
  }).catch(e=>({error:e.message}));
  r.error?toast(r.error,'err'):(toast('Deleted','ok'),loadFiles());
}

async function doRestore(){
  const fi=document.getElementById('rf');
  if(!fi.files.length){toast('Select a file first','err');return;}
  const fd=new FormData();
  fd.append('file',fi.files[0]);
  fd.append('mode',document.getElementById('rm').value);
  const r=await fetch('/api/restore',{method:'POST',body:fd}).then(r=>r.json()).catch(e=>({error:e.message}));
  r.error?toast(r.error,'err'):toast(`Restored: ${r.entities_created} entities, ${r.relations_created} relations`,'ok');
  loadStatus();
}

function toast(msg,type){
  const el=document.getElementById('toast');
  el.textContent=msg;el.className='toast show '+type;
  setTimeout(()=>el.className='toast',3500);
}

loadStatus();loadSchedule();loadFiles();
</script>
</body>
</html>"""


mcp = FastMCP(
    "ai-rem",
    instructions=(
        "Langzeit-Gedächtnis als Knowledge Graph. Einzige Quelle für persistenten Kontext — Auto-Memory ist deaktiviert.\n\n"
        "## Kontext holen\n"
        "memory_get_context für offene Tasks/Projekte/letzte Einträge, memory_search für gezielte Themen. "
        "Vor Rückfragen immer erst in ai-rem prüfen ob die Info schon da ist.\n\n"
        "## Speichern — proaktiv, ohne Nachfrage\n"
        "memory_add + memory_relate. Vor neuem Eintrag prüfen ob Entity schon existiert — updaten statt duplizieren.\n\n"
        "Entity-Typen: Person | Project | Task | Tool | Problem | Solution | Decision | Preference | Topic\n"
        "- Preference: User-Präferenzen, Arbeitsweisen, Feedback. Feedback-Einträge mit Präfix 'Feedback: …'. "
        "Body bei Regeln: Regel + Why: + How to apply:.\n"
        "- Project: laufende Arbeit, Ziele. Relative Daten → absolute.\n"
        "- Topic: Pointer auf externe Systeme/Referenzen.\n"
        "- Task/Decision/Problem/Solution/Tool: offene Aufgaben, Architektur, Bugs, Lösungen, Tools.\n\n"
        "## Nicht speichern\n"
        "Code-Patterns/Architektur/Pfade (aus Code ableitbar), git-Historie (git log/blame), "
        "Fix-Rezepte (Code+Commit), ephemere Sitzungsdetails. "
        "Auch wenn der User darum bittet — rückfragen was überraschend/nicht-offensichtlich war.\n\n"
        "## Vor Empfehlung aus Memory\n"
        "Datei-Pfade/Funktionsnamen aus Memory verifizieren (existiert noch?). "
        "Memory ist Behauptung über damals, nicht über jetzt. Bei Konflikt: Code vertrauen, Memory updaten.\n\n"
        "## Konventionen\n"
        "context='private' für private Inhalte; globale Entities ohne context-Tag. "
        "Verwandte Entities verlinken via memory_relate."
    ),
)


@mcp.custom_route("/setup", methods=["GET"])
async def setup_route(request: Request) -> PlainTextResponse:
    return PlainTextResponse(SETUP_SCRIPT, media_type="text/plain")


@mcp.custom_route("/hooks/system-check.py", methods=["GET"])
async def system_check_hook_route(request: Request) -> PlainTextResponse:
    return PlainTextResponse(SYSTEM_CHECK_PY, media_type="text/x-python")


@mcp.custom_route("/setup-config", methods=["GET"])
async def setup_config_route(request: Request) -> JSONResponse:
    if os.path.exists(_SETUP_CONFIG_PATH):
        with open(_SETUP_CONFIG_PATH) as f:
            return JSONResponse(json.load(f))
    return JSONResponse({})


@mcp.custom_route("/cmd", methods=["GET"])
async def cmd_route(request: Request) -> PlainTextResponse:
    return PlainTextResponse(CMD_MD, media_type="text/plain")


@mcp.custom_route("/cmd/prefedit", methods=["GET"])
async def cmd_prefedit_route(request: Request) -> PlainTextResponse:
    return PlainTextResponse(PREFEDIT_CMD_MD, media_type="text/plain")


@mcp.custom_route("/tools/pref-tui.py", methods=["GET"])
async def pref_tui_route(request: Request) -> PlainTextResponse:
    script = PREF_TUI_SCRIPT.replace("__BASE__", _KG_URL)
    return PlainTextResponse(script, media_type="text/plain")


@mcp.custom_route("/api/preferences", methods=["GET"])
async def api_preferences(request: Request) -> JSONResponse:
    rows = _rows(db_exec(
        "MATCH (e:Entity {type: 'Preference'}) "
        "RETURN e.id, e.name, e.context, e.pinned, e.sort_order, e.descr, e.updated_at"
    ))
    prefs = [
        {"id": r[0], "name": r[1], "context": r[2] or "",
         "pinned": r[3] == "true",
         "sort_order": int(r[4]) if r[4] else None,
         "descr": r[5] or "", "updated_at": r[6] or ""}
        for r in rows
    ]

    def _key(p):
        return (0 if p["pinned"] else 1,
                (0, p["sort_order"]) if p["sort_order"] is not None else (1, 0),
                p["updated_at"])

    prefs.sort(key=_key)
    return JSONResponse(prefs)


@mcp.custom_route("/api/preferences/update", methods=["POST"])
async def api_preferences_update(request: Request) -> JSONResponse:
    body = await request.json()
    name = body.get("name")
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)
    result = await asyncio.to_thread(
        memory_preference_update,
        name=name,
        context=body.get("context"),
        pinned=body.get("pinned"),
        sort_order=body.get("sort_order"),
    )
    return JSONResponse({"result": result})


@mcp.custom_route("/api/preferences/delete", methods=["POST"])
async def api_preferences_delete(request: Request) -> JSONResponse:
    body = await request.json()
    name = body.get("name")
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)
    result = await asyncio.to_thread(memory_delete, name=name)
    return JSONResponse({"result": result})


@mcp.custom_route("/prefs", methods=["GET"])
async def prefs_route(request: Request) -> Response:
    return Response(content=_PREFS_HTML, media_type="text/html")


@mcp.custom_route("/export", methods=["GET"])
async def export_route(request: Request) -> JSONResponse:
    return JSONResponse(await asyncio.to_thread(_dump_graph))


@mcp.custom_route("/import", methods=["POST"])
async def import_route(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except json.JSONDecodeError as e:
        log.warning("invalid JSON in /import: %s", e)
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    mode = request.query_params.get("mode", "merge")
    if mode not in ("merge", "replace"):
        return JSONResponse({"error": "mode must be 'merge' or 'replace'"}, status_code=400)

    result = await asyncio.to_thread(_apply_import, body, mode)
    return JSONResponse(result)


@mcp.custom_route("/ui", methods=["GET"])
async def ui_route(request: Request) -> Response:
    return Response(content=_UI_HTML, media_type="text/html")


@mcp.custom_route("/api/status", methods=["GET"])
async def api_status(request: Request) -> JSONResponse:
    e_count = _rows(await db_exec_async("MATCH (e:Entity) RETURN count(e)"))[0][0]
    r_count = _rows(await db_exec_async("MATCH ()-[r:Rel]->() RETURN count(r)"))[0][0]
    cfg = _load_backup_cfg()
    return JSONResponse({"entities": e_count, "relations": r_count, "last_backup": cfg.get("last_backup")})


@mcp.custom_route("/api/backup/config", methods=["GET"])
async def api_backup_config_get(request: Request) -> JSONResponse:
    return JSONResponse(_load_backup_cfg())


@mcp.custom_route("/api/backup/config", methods=["POST"])
async def api_backup_config_post(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    cfg = _load_backup_cfg()
    cfg["enabled"] = bool(body.get("enabled", cfg.get("enabled")))
    cfg["interval"] = body.get("interval", cfg.get("interval", "daily"))
    if cfg["interval"] not in ("hourly", "daily", "weekly"):
        return JSONResponse({"error": "interval must be hourly, daily or weekly"}, status_code=400)
    _save_backup_cfg(cfg)
    return JSONResponse({"status": "ok", **cfg})


@mcp.custom_route("/api/backup/now", methods=["POST"])
async def api_backup_now(request: Request) -> JSONResponse:
    try:
        filename = await asyncio.to_thread(_do_backup)
        return JSONResponse({"status": "ok", "file": filename})
    except Exception as e:
        log.error("Manual backup failed: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.custom_route("/api/backup/files", methods=["GET"])
async def api_backup_files(request: Request) -> JSONResponse:
    _ensure_backup_dir()
    files = sorted(glob.glob(os.path.join(BACKUP_DIR, "backup_*.json")), reverse=True)
    result = [{"name": os.path.basename(f), "size": os.path.getsize(f)} for f in files]
    return JSONResponse(result)


@mcp.custom_route("/api/backup/download", methods=["GET"])
async def api_backup_download(request: Request) -> Response:
    path = _safe_backup_path(request.query_params.get("file", ""))
    if not path:
        return JSONResponse({"error": "invalid filename"}, status_code=400)
    if not os.path.exists(path):
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path, filename=os.path.basename(path), media_type="application/json")


@mcp.custom_route("/api/backup/delete", methods=["POST"])
async def api_backup_delete(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except json.JSONDecodeError as e:
        log.warning("invalid JSON in /api/backup/delete: %s", e)
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    path = _safe_backup_path(body.get("file", ""))
    if not path:
        return JSONResponse({"error": "invalid filename"}, status_code=400)
    try:
        os.remove(path)
    except FileNotFoundError:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/api/restore", methods=["POST"])
async def api_restore(request: Request) -> JSONResponse:
    try:
        form = await request.form()
    except Exception as e:
        log.warning("invalid form data in /api/restore: %s", e)
        return JSONResponse({"error": "invalid form data"}, status_code=400)
    file = form.get("file")
    if not file:
        return JSONResponse({"error": "no file uploaded"}, status_code=400)
    mode = form.get("mode", "merge")
    if mode not in ("merge", "replace"):
        return JSONResponse({"error": "mode must be merge or replace"}, status_code=400)
    try:
        content = await file.read()
        body = json.loads(content)
    except json.JSONDecodeError as e:
        log.warning("invalid JSON in restore upload: %s", e)
        return JSONResponse({"error": "invalid JSON in file"}, status_code=400)

    result = await asyncio.to_thread(_apply_import, body, mode)
    return JSONResponse(result)


# ─── helpers ────────────────────────────────────────────────────────────────


def _id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9_]", "_", name.lower().strip())
    if len(slug) <= 64:
        return slug
    # Names longer than 64 chars: keep readable prefix, append hash of full name
    # to avoid silent collisions when two distinct names share a 64-char prefix.
    suffix = hashlib.blake2b(name.encode("utf-8"), digest_size=4).hexdigest()
    return f"{slug[:55]}_{suffix}"


def _ctx_match(extra_json: str, context: str) -> bool:
    """True wenn Entity zum gesuchten Context passt. Ungetaggte Entities (global) passen immer.

    Legacy helper kept for callers that still parse extra JSON (Phase 3 transitional);
    new code should push the context predicate into Cypher via _ctx_clause().
    """
    if not context:
        return True
    return json.loads(extra_json or "{}").get("context", "") in ("", context)


def _ctx_clause(alias: str, ctx: str, *, where: bool = False) -> str:
    """Cypher fragment limiting `alias` to the given context, or '' when ctx is empty.

    where=False (default) → fragment starts with ' AND' to chain into an existing WHERE.
    where=True            → fragment starts with ' WHERE' to introduce a new clause.
    """
    if not ctx:
        return ""
    keyword = "WHERE" if where else "AND"
    return f" {keyword} ({alias}.context = $ctx OR {alias}.context = '')"


def _apply_import(body: dict, mode: str) -> dict:
    """Apply an export-format `body` to the graph in 'merge' or 'replace' mode.

    Pre-fetches all existing entity ids and relation tuples to avoid per-row
    existence queries. Returns a summary dict.
    """
    if mode == "replace":
        db_exec("MATCH (e:Entity) DETACH DELETE e")
        existing_eids: set[str] = set()
        existing_rels: set[tuple] = set()
    else:
        existing_eids = {
            r[0] for r in _rows(db_exec("MATCH (e:Entity) RETURN e.id"))
        }
        existing_rels = {
            (r[0], r[1], r[2])
            for r in _rows(db_exec(
                "MATCH (a:Entity)-[r:Rel]->(b:Entity) RETURN a.id, r.name, b.id"
            ))
        }

    ts = _now()
    entities_created = entities_skipped = 0
    relations_created = relations_skipped = 0

    for entity in body.get("entities", []):
        eid = entity.get("id") or _id(entity["name"])
        if eid in existing_eids:
            entities_skipped += 1
            continue
        extra = entity.get("extra", {}) or {}
        # Prefer top-level `context` (new format); fall back to extra.context
        # (older backups) so restore stays compatible.
        ctx = entity.get("context") or extra.get("context", "") or ""
        extra_json = json.dumps(extra, ensure_ascii=False)
        db_exec(
            """CREATE (:Entity {id: $id, name: $name, type: $type,
                                descr: $descr, extra: $extra, context: $ctx,
                                created_at: $ts, updated_at: $ts})""",
            {
                "id": eid, "name": entity["name"],
                "type": entity.get("type", "Unknown"),
                "descr": entity.get("description", ""), "extra": extra_json,
                "ctx": ctx,
                "ts": entity.get("created_at", ts),
            },
        )
        existing_eids.add(eid)
        entities_created += 1

    for rel in body.get("relations", []):
        from_id, to_id, relation = rel["from_id"], rel["to_id"], rel["relation"]
        if from_id not in existing_eids or to_id not in existing_eids:
            relations_skipped += 1
            continue
        key = (from_id, relation, to_id)
        if key in existing_rels:
            relations_skipped += 1
            continue
        extra_json = json.dumps(rel.get("extra", {}), ensure_ascii=False)
        db_exec(
            """MATCH (a:Entity {id: $fid}), (b:Entity {id: $tid})
               CREATE (a)-[:Rel {name: $rel, extra: $extra, created_at: $ts}]->(b)""",
            {"fid": from_id, "tid": to_id, "rel": relation, "extra": extra_json,
             "ts": rel.get("created_at", ts)},
        )
        existing_rels.add(key)
        relations_created += 1

    return {
        "status": "ok", "mode": mode,
        "entities_created": entities_created,
        "entities_skipped": entities_skipped,
        "relations_created": relations_created,
        "relations_skipped": relations_skipped,
    }


# ─── tools ──────────────────────────────────────────────────────────────────


@mcp.tool()
def memory_add(
    name: str,
    type: str,
    description: str = "",
    extra: Optional[dict] = None,
    context: str = "",
    pinned: bool = False,
) -> str:
    """Entity im Knowledge Graph anlegen oder aktualisieren.

    type-Werte: Person | Project | Task | Tool | Problem | Solution | Decision | Preference | Topic
    extra: beliebige JSON-Properties (z.B. {"status": "offen", "priority": "hoch"})
    context: "work" | "private" | "" (global, default — erscheint in allen Context-Abfragen)
    pinned: True → Preference erscheint immer ganz oben in get_context, unabhängig von updated_at
    """
    eid = _id(name)
    merged = dict(extra or {})
    if context:
        merged["context"] = context
    extra_json = json.dumps(merged, ensure_ascii=False)
    pinned_val = "true" if pinned else ""
    ts = _now()

    existed = bool(_rows(
        db_exec("MATCH (e:Entity {id: $id}) RETURN e.id", {"id": eid})
    ))
    db_exec(
        """MERGE (e:Entity {id: $id})
           ON CREATE SET e.name = $name, e.type = $type, e.descr = $descr,
                         e.extra = $extra, e.context = $ctx, e.pinned = $pinned,
                         e.created_at = $ts, e.updated_at = $ts
           ON MATCH  SET e.name = $name, e.type = $type, e.descr = $descr,
                         e.extra = $extra, e.context = $ctx, e.pinned = $pinned,
                         e.updated_at = $ts""",
        {"id": eid, "name": name, "type": type,
         "descr": description, "extra": extra_json,
         "ctx": context or "", "pinned": pinned_val, "ts": ts},
    )
    verb = "Aktualisiert" if existed else "Angelegt"
    pin_marker = " 📌" if pinned else ""
    return f"{verb}: [{type}] {name}{pin_marker}"


@mcp.tool()
def memory_preference_update(
    name: str,
    context: Optional[str] = None,
    pinned: Optional[bool] = None,
    sort_order: Optional[int] = None,
) -> str:
    """Felder einer Preference gezielt ändern ohne andere Felder zu überschreiben.

    Nur übergebene Parameter (nicht None) werden aktualisiert.
    context: "work" | "private" | "" (global)
    pinned: True → immer oben in get_context
    sort_order: manuelle Reihenfolge (1 = ganz oben); None/leer = nach updated_at
    """
    eid = _id(name)
    row = _rows(db_exec(
        "MATCH (e:Entity {id: $id}) RETURN e.context, e.pinned, e.sort_order, e.type",
        {"id": eid},
    ))
    if not row:
        return f"Nicht gefunden: {name}"

    cur_ctx, cur_pin, cur_so, cur_type = row[0]
    new_ctx = context if context is not None else (cur_ctx or "")
    new_pin = ("true" if pinned else "") if pinned is not None else (cur_pin or "")
    new_so  = str(sort_order) if sort_order is not None else (cur_so or "")
    ts = _now()

    db_exec(
        """MATCH (e:Entity {id: $id})
           SET e.context = $ctx, e.pinned = $pin,
               e.sort_order = $so, e.updated_at = $ts""",
        {"id": eid, "ctx": new_ctx, "pin": new_pin, "so": new_so, "ts": ts},
    )
    parts = []
    if context  is not None: parts.append(f"context={new_ctx!r}")
    if pinned   is not None: parts.append(f"pinned={new_pin!r}")
    if sort_order is not None: parts.append(f"sort_order={new_so!r}")
    return f"[{cur_type}] {name}: {', '.join(parts) or 'keine Änderung'}"


@mcp.tool()
def memory_relate(
    from_name: str,
    relation: str,
    to_name: str,
    extra: Optional[dict] = None,
) -> str:
    """Beziehung zwischen zwei bestehenden Entities erstellen.

    Beispiele für relation: NUTZT | ARBEITET_AN | GELÖST_DURCH | HÄNGT_AB_VON |
                             LÄUFT_AUF | INTEGRIERT_MIT | GETROFFEN_VON | BEVORZUGT
    Beide Entities müssen zuvor via memory_add angelegt sein — sonst Fehler,
    damit Tippfehler den Graphen nicht mit Stub-Einträgen verschmutzen.
    """
    from_id = _id(from_name)
    to_id = _id(to_name)
    extra_json = json.dumps(extra or {}, ensure_ascii=False)
    ts = _now()

    found = {
        r[0] for r in _rows(
            db_exec(
                "MATCH (e:Entity) WHERE e.id IN [$fid, $tid] RETURN e.id",
                {"fid": from_id, "tid": to_id},
            )
        )
    }
    missing = []
    if from_id not in found:
        missing.append(from_name)
    if to_id not in found:
        missing.append(to_name)
    if missing:
        return (
            f"Entity nicht gefunden: {', '.join(missing)}. "
            f"Lege sie zuerst via memory_add an."
        )

    existing = _rows(
        db_exec(
            """MATCH (a:Entity {id: $fid})-[r:Rel {name: $rel}]->(b:Entity {id: $tid})
               RETURN r.name""",
            {"fid": from_id, "tid": to_id, "rel": relation},
        )
    )
    if existing:
        return f"Relation existiert bereits: {from_name} -[{relation}]-> {to_name}"

    db_exec(
        """MATCH (a:Entity {id: $fid}), (b:Entity {id: $tid})
           CREATE (a)-[:Rel {name: $rel, extra: $extra, created_at: $ts}]->(b)""",
        {"fid": from_id, "tid": to_id, "rel": relation, "extra": extra_json, "ts": ts},
    )
    return f"Erstellt: {from_name} -[{relation}]-> {to_name}"


def _smart_truncate(text: str, threshold: int = 400) -> str:
    if not text or len(text) <= threshold:
        return text
    sentences = re.split(r'(?<=\.)\s+', text.strip())
    if len(sentences) <= 2:
        return text[:threshold] + "…"
    first = sentences[0]
    last = sentences[-1]
    middle_budget = threshold - len(first) - len(last) - 10
    if middle_budget > 40:
        middle = " ".join(sentences[1:-1])
        return f"{first} {middle[:middle_budget]}… {last}"
    return f"{first} … {last}"


@mcp.tool()
def memory_search(query: str, limit: int = 15, context: str = "") -> str:
    """Entities nach Name oder Beschreibung durchsuchen.

    context: "work" | "private" | "" (kein Filter, default)
    """
    q = query.lower()
    params: dict = {"q": q, "lim": limit}
    if context:
        params["ctx"] = context
    rows = _rows(
        db_exec(
            f"""MATCH (e:Entity)
               WHERE (lower(e.name) CONTAINS $q OR lower(e.descr) CONTAINS $q)
                 {_ctx_clause('e', context)}
               RETURN e.type, e.name, e.descr, e.updated_at, e.context
               ORDER BY e.updated_at DESC
               LIMIT $lim""",
            params,
        )
    )
    if not rows:
        return "Keine Ergebnisse."
    lines = []
    for r in rows:
        ctx_tag = r[4] or ""
        ctx_str = f" `[{ctx_tag}]`" if ctx_tag else ""
        lines.append(f"[{r[0]}] **{r[1]}**{ctx_str}: {_smart_truncate(r[2])}  _(aktualisiert {r[3][:10]})_")
    return "\n".join(lines)


@mcp.tool()
def memory_get_context(topic: str = "", context: str = "") -> str:
    """Relevanten Kontext aus dem Knowledge Graph laden.

    Ohne topic: offene Tasks + aktive Projekte + letzte Einträge.
    Mit topic: direkt relevanter Subgraph zu diesem Thema.
    context: "work" | "private" | "" (alles, default)
             Ungetaggte (globale) Entities erscheinen immer.
    """
    ctx_label = f" [{context}]" if context else ""
    sections: list[str] = []
    ctx_param: dict = {"ctx": context} if context else {}

    if topic:
        q = topic.lower()
        rows = _rows(
            db_exec(
                f"""MATCH (e:Entity)
                   WHERE (lower(e.name) CONTAINS $q OR lower(e.descr) CONTAINS $q)
                     {_ctx_clause('e', context)}
                   RETURN e.type, e.name, e.descr, e.updated_at
                   ORDER BY e.updated_at DESC
                   LIMIT 20""",
                {"q": q, **ctx_param},
            )
        )
        if rows:
            lines = [f"[{r[0]}] {r[1]}: {r[2][:300]}" for r in rows]
            sections.append(f"## Kontext: {topic}{ctx_label}\n" + "\n".join(lines))

        rel_rows = _rows(
            db_exec(
                f"""MATCH (a:Entity)-[r:Rel]->(b:Entity)
                   WHERE (lower(a.name) CONTAINS $q OR lower(b.name) CONTAINS $q)
                     {_ctx_clause('a', context)}
                     {_ctx_clause('b', context)}
                   RETURN a.name, r.name, b.name
                   LIMIT 15""",
                {"q": q, **ctx_param},
            )
        )
        if rel_rows:
            lines = [f"{r[0]} -[{r[1]}]-> {r[2]}" for r in rel_rows]
            sections.append("### Relationen\n" + "\n".join(lines))

    # Routinen & Anweisungen (Preferences) — surface near the top so they are
    # acted on, not just read. Topic-specific block above still wins when set.
    # Sort: pinned first → sort_order (numeric, empty last) → updated_at DESC.
    pref_rows = _rows(
        db_exec(
            f"""MATCH (e:Entity {{type: 'Preference'}})
               {_ctx_clause('e', context, where=True)}
               RETURN e.name, e.descr, e.pinned, e.sort_order, e.updated_at""",
            ctx_param,
        )
    )
    if pref_rows:
        def _pref_sort_key(r):
            pin_key  = 0 if r[2] == "true" else 1
            try:
                ord_key = (0, int(r[3]))
            except (TypeError, ValueError):
                ord_key = (1, 0)
            return (pin_key, ord_key, r[4] or "")

        pref_rows = sorted(pref_rows, key=_pref_sort_key, reverse=False)[:12]
        lines = [
            f"- {'📌 ' if r[2] == 'true' else ''}**{r[0]}**: {r[1][:120]}"
            for r in pref_rows
        ]
        sections.append(f"## Routinen & Anweisungen{ctx_label}\n" + "\n".join(lines))

    # Offene Tasks
    task_rows = _rows(
        db_exec(
            f"""MATCH (e:Entity {{type: 'Task'}})
               {_ctx_clause('e', context, where=True)}
               RETURN e.name, e.descr, e.extra, e.updated_at
               ORDER BY e.updated_at DESC
               LIMIT 10""",
            ctx_param,
        )
    )
    tasks = []
    for r in task_rows:
        try:
            extra = json.loads(r[2] or "{}")
        except json.JSONDecodeError:
            extra = {}
        status = extra.get("status", "offen")
        if status.lower() not in ("erledigt", "done", "closed"):
            tasks.append(f"- [{status}] **{r[0]}**: {r[1][:80]}")
    if tasks:
        sections.append(f"## Offene Tasks{ctx_label}\n" + "\n".join(tasks))

    # Aktive Projekte
    proj_rows = _rows(
        db_exec(
            f"""MATCH (e:Entity {{type: 'Project'}})
               {_ctx_clause('e', context, where=True)}
               RETURN e.name, e.descr, e.extra, e.updated_at
               ORDER BY e.updated_at DESC
               LIMIT 8""",
            ctx_param,
        )
    )
    projects = []
    for r in proj_rows:
        try:
            extra = json.loads(r[2] or "{}")
        except json.JSONDecodeError:
            extra = {}
        status = extra.get("status", "aktiv")
        projects.append(f"- [{status}] **{r[0]}**: {r[1][:80]}")
    if projects:
        sections.append(f"## Projekte{ctx_label}\n" + "\n".join(projects))

    # Letzte Entscheidungen / Lösungen / Probleme
    recent_rows = _rows(
        db_exec(
            f"""MATCH (e:Entity)
               WHERE e.type IN ['Problem', 'Solution', 'Decision']
                 {_ctx_clause('e', context)}
               RETURN e.type, e.name, e.descr, e.updated_at
               ORDER BY e.updated_at DESC
               LIMIT 8""",
            ctx_param,
        )
    )
    if recent_rows:
        lines = [f"- [{r[0]}] **{r[1]}**: {r[2][:80]}  _({r[3][:10]})_" for r in recent_rows]
        sections.append(f"## Letzte Entscheidungen & Lösungen{ctx_label}\n" + "\n".join(lines))

    if not sections:
        return "Knowledge Graph ist leer. Nutze memory_add um Einträge anzulegen."
    return "\n\n".join(sections)


@mcp.tool()
def memory_list(type: str = "", context: str = "") -> str:
    """Alle Entities auflisten, optional nach Typ und/oder Context gefiltert.

    Bekannte Typen: Person, Project, Task, Tool, Problem, Solution, Decision, Preference, Topic
    context: "work" | "private" | "" (alles, default)
    """
    params: dict = {}
    if context:
        params["ctx"] = context
    if type:
        params["type"] = type
        rows = _rows(
            db_exec(
                f"""MATCH (e:Entity {{type: $type}})
                   {_ctx_clause('e', context, where=True)}
                   RETURN e.name, e.descr, e.updated_at, e.context
                   ORDER BY e.name""",
                params,
            )
        )
        if not rows:
            return f"Keine Einträge vom Typ '{type}'" + (f" mit context='{context}'" if context else "") + "."
        lines = []
        for r in rows:
            ctx_tag = r[3] or ""
            ctx_str = f" `[{ctx_tag}]`" if ctx_tag else ""
            lines.append(f"- **{r[0]}**{ctx_str}: {r[1][:80]}  _({r[2][:10]})_")
        return "\n".join(lines)

    rows = _rows(
        db_exec(
            f"""MATCH (e:Entity)
               {_ctx_clause('e', context, where=True)}
               RETURN e.type, e.name, e.descr, e.updated_at, e.context
               ORDER BY e.type, e.name""",
            params,
        )
    )
    if not rows:
        return "Keine Einträge."

    current_type = None
    lines = []
    for r in rows:
        if r[0] != current_type:
            current_type = r[0]
            lines.append(f"\n### {current_type}")
        ctx_tag = r[4] or ""
        ctx_str = f" `[{ctx_tag}]`" if ctx_tag else ""
        lines.append(f"- **{r[1]}**{ctx_str}: {r[2][:80]}")
    return "\n".join(lines).strip()


@mcp.tool()
def memory_get_relations(name: str) -> str:
    """Alle Beziehungen einer Entity anzeigen (ausgehend und eingehend)."""
    eid = _id(name)

    out_rows = _rows(
        db_exec(
            """MATCH (a:Entity {id: $id})-[r:Rel]->(b:Entity)
               RETURN r.name, b.type, b.name""",
            {"id": eid},
        )
    )
    in_rows = _rows(
        db_exec(
            """MATCH (a:Entity)-[r:Rel]->(b:Entity {id: $id})
               RETURN r.name, a.type, a.name""",
            {"id": eid},
        )
    )

    if not out_rows and not in_rows:
        return f"Keine Relationen für: {name}"

    lines = [f"## Relationen: {name}"]
    if out_rows:
        lines.append("**Ausgehend:**")
        lines.extend(f"  → [{r[1]}] {r[2]}  via [{r[0]}]" for r in out_rows)
    if in_rows:
        lines.append("**Eingehend:**")
        lines.extend(f"  ← [{r[1]}] {r[2]}  via [{r[0]}]" for r in in_rows)
    return "\n".join(lines)


@mcp.tool()
def memory_status() -> str:
    """Kurzstatus: Anzahl Entities und Relationen im Knowledge Graph."""
    e_count = _rows(db_exec("MATCH (e:Entity) RETURN count(e)"))[0][0]
    r_count = _rows(db_exec("MATCH ()-[r:Rel]->() RETURN count(r)"))[0][0]
    return f"ai-rem: {e_count} Entities, {r_count} Relationen"


@mcp.tool()
def memory_check_update() -> str:
    """Zeigt die installierte Version und prüft ob auf Docker Hub eine neuere verfügbar ist."""
    import urllib.request, json as _json
    installed = VERSION
    try:
        url = "https://hub.docker.com/v2/repositories/magic3arkus/ai-rem/tags/?page_size=50&ordering=last_updated"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = _json.loads(resp.read())
        tags = [t["name"] for t in data.get("results", [])
                if t["name"] != "latest" and t["name"].startswith("v")]
        if not tags:
            return f"Installiert: v{installed}\nDocker Hub: keine Tags gefunden"

        def _ver(tag):
            try:
                return tuple(int(x) for x in tag.lstrip("v").split("."))
            except ValueError:
                return (0,)

        latest = max(tags, key=_ver)
        status = "✓ aktuell" if latest == f"v{installed}" else "⚠ Update verfügbar"
        return f"Installiert: v{installed}\nDocker Hub: {latest}\nStatus: {status}"
    except Exception as e:
        return f"Installiert: v{installed}\nDocker Hub: nicht erreichbar ({e})"


@mcp.tool()
def memory_delete(name: str) -> str:
    """Entity und alle zugehörigen Relationen löschen."""
    eid = _id(name)
    if not _rows(db_exec("MATCH (e:Entity {id: $id}) RETURN e.id", {"id": eid})):
        return f"Nicht gefunden: {name}"
    db_exec("MATCH (e:Entity {id: $id}) DETACH DELETE e", {"id": eid})
    return f"Gelöscht: {name}"


# ─── entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "3456"))
    log.info("Starting ai-rem MCP server on %s:%d", host, port)
    mcp.run(transport="http", host=host, port=port)
