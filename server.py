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

DB_PATH = os.getenv("KUZU_DB_PATH", "/data/kg.db")
BACKUP_DIR = os.getenv("BACKUP_DIR", "/backups")
MAX_BACKUPS = int(os.getenv("MAX_BACKUPS", "10"))
KUZU_POOL_SIZE = max(1, int(os.getenv("KUZU_POOL_SIZE", "4")))
_BACKUP_CONFIG = os.path.join(BACKUP_DIR, ".config.json")

# ─── Setup-Endpunkt Inhalte ──────────────────────────────────────────────────

_KG_URL = os.getenv("KG_PUBLIC_URL", "http://localhost:3456")

SETUP_SCRIPT = r"""#!/usr/bin/env bash
# ai-rem Setup - plattformunabhaengig (macOS + Linux).
# Abhaengigkeiten: bash, curl, python3, claude CLI.
set -e
KG_URL="__KG_URL__"
CLAUDE_HOME="$HOME/.claude"
HOOK_PATH="$CLAUDE_HOME/hooks/ai-rem-bootstrap.py"

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

# SessionStart-Hook: Python, damit kein jq/sed-Dialekt-Problem.
cat > "$HOOK_PATH" << 'PYHOOK'
#!/usr/bin/env python3
# SessionStart hook: prueft ai-rem-Erreichbarkeit via memory_status.
import json
import os
import re
import sys
import urllib.request

ENDPOINT = os.environ.get("AI_REM_ENDPOINT", "__KG_URL__/mcp")
TIMEOUT = 5


def emit(msg):
    print(json.dumps({"systemMessage": msg, "suppressOutput": True}))
    sys.exit(0)


def post(body, sid=None):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if sid:
        headers["mcp-session-id"] = sid
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    return urllib.request.urlopen(req, timeout=TIMEOUT)


try:
    resp = post({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "claude-code-bootstrap", "version": "1.0"}},
    })
    sid = resp.headers.get("mcp-session-id")
    resp.read()
    if not sid:
        emit("ai-rem: nicht erreichbar")

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
    if text:
        emit(text)
except Exception:
    pass

emit("ai-rem: nicht erreichbar")
PYHOOK
# __KG_URL__ wird vom Server bereits beim Rendern des SETUP_SCRIPT ersetzt
# (Python-seitiges .replace), daher steht im Hook bereits die echte URL.
chmod +x "$HOOK_PATH"
echo "✓ SessionStart-Hook: $HOOK_PATH"

# settings.json: Allowlist migrieren, Permissions, SessionStart-Hook, autoMemoryEnabled
HOOK_PATH="$HOOK_PATH" python3 - << 'PYEOF'
import json
import os

path = os.path.expanduser("~/.claude/settings.json")
hook_path = os.environ["HOOK_PATH"]
data = json.load(open(path)) if os.path.exists(path) else {}

# Alte kg-memory Allow-Eintraege migrieren
perms = data.setdefault("permissions", {})
allow = perms.setdefault("allow", [])
allow[:] = [p.replace("mcp__kg-memory__", "mcp__ai-rem__") for p in allow]

# ai-rem Permissions ergaenzen
needed = [
    "mcp__ai-rem__memory_status",
    "mcp__ai-rem__memory_get_context",
    "mcp__ai-rem__memory_search",
    "mcp__ai-rem__memory_add",
    "mcp__ai-rem__memory_list",
    "mcp__ai-rem__memory_get_relations",
    "mcp__ai-rem__memory_relate",
    "mcp__ai-rem__memory_delete",
]
added = [p for p in needed if p not in allow]
allow.extend(added)

# SessionStart-Hook idempotent eintragen
hooks = data.setdefault("hooks", {})
sessions = hooks.setdefault("SessionStart", [])
group = next((g for g in sessions if g.get("matcher") == "*"), None)
if group is None:
    group = {"matcher": "*", "hooks": []}
    sessions.append(group)
group.setdefault("hooks", [])
hook_added = False
if not any(h.get("command") == hook_path for h in group["hooks"]):
    group["hooks"].append({"type": "command", "command": hook_path, "timeout": 10})
    hook_added = True

# Datei-Auto-Memory abschalten (alles laeuft ueber ai-rem)
auto_changed = data.get("autoMemoryEnabled") is not False
data["autoMemoryEnabled"] = False

json.dump(data, open(path, "w"), indent=2)
parts = []
if added:        parts.append(f"+{len(added)} Permissions")
if hook_added:   parts.append("SessionStart-Hook")
if auto_changed: parts.append("autoMemoryEnabled=false")
print("✓ settings.json: " + (", ".join(parts) if parts else "bereits aktuell"))
PYEOF

# CLAUDE.md aktualisieren - bestehenden Block (alt oder neu) ersetzen
python3 - << 'PYEOF'
import os
import re

path = os.path.expanduser("~/.claude/CLAUDE.md")
new_block = '''
## Knowledge Graph Memory (ai-rem)
ai-rem ist die einzige Wissensquelle für persistenten Kontext. Verbindung wird beim Sitzungsstart per SessionStart-Hook geprüft (Statuszeile "ai-rem: N Entities, M Relationen" oder "nicht erreichbar"). Datei-basierte Auto-Memory ist deaktiviert — alle Memory-Verhalten laufen über ai-rem.

### Kontext holen
`memory_get_context` für offene Tasks / aktive Projekte / letzte Einträge, `memory_search` für gezielte Themen. Nur wenn die Aufgabe wirklich früheres Wissen braucht.

### Speichern – proaktiv, ohne Nachfrage
Tool: `memory_add` (+ `memory_relate` für Verknüpfungen). Vor neuem Eintrag immer prüfen, ob es eine bestehende Entity gibt — updaten statt duplizieren.

**Entity-Typen** (ai-rem kennt: Person | Project | Task | Tool | Problem | Solution | Decision | Preference | Topic):
- **Preference** — wer der User ist, Präferenzen, Arbeitsweisen, Feedback. Korrekturen UND bestätigte Ansätze ("genau so weitermachen") zählen — auch quiet confirmations. Feedback-Einträge mit Name-Präfix `Feedback: …`. Body bei Regeln: Regel + **Why:** + **How to apply:**.
- **Project** — laufende Arbeit, Ziele, Deadlines. Relative Daten in absolute umrechnen ("Donnerstag" → "2026-05-21").
- **Topic** — Pointer auf externe Systeme / Referenzen (Linear-Projekt, Slack-Channel, Grafana-Dashboard, Doku-Repo, …).
- **Person** — Stakeholder, Team-Mitglieder, Kontakte.
- **Task / Decision / Problem / Solution / Tool** — Strukturen für offene Aufgaben, Architekturentscheidungen, Bugs/Vorfälle, Lösungen, Tools.

### Nicht speichern
Code-Patterns / Architektur / Pfade (aus Code ableitbar), git-Historie (git log/blame ist autoritativ), Fix-Rezepte (Code + Commit haben Kontext), ephemere Sitzungsdetails. Gilt **auch wenn der User darum bittet** — bei "Speicher die PR-Liste" rückfragen, was *überraschend / nicht-offensichtlich* war.

### Vor Empfehlung aus Memory
Wenn eine Erinnerung Datei-Pfade, Funktions- oder Flag-Namen nennt: verifizieren (existiert noch?). Memory ist Behauptung über damals, nicht über jetzt. Bei Konflikt: dem aktuellen Code-Stand vertrauen, Memory updaten.

### Konventionen
- `context="private"` für private Inhalte; globale Entities ohne context-Tag.
- Verwandte Entities verlinken via `memory_relate`.
'''

os.makedirs(os.path.dirname(path), exist_ok=True)
text = open(path).read() if os.path.exists(path) else ""

# Bestehenden ai-rem-Block (alt oder neu) entfernen
pattern = re.compile(r"\n## Knowledge Graph Memory \(ai-rem\)[\s\S]*?(?=\n## |\Z)")
text, n = pattern.subn("", text)
if not text.endswith("\n"):
    text += "\n"
text += new_block

open(path, "w").write(text)
print("✓ CLAUDE.md " + ("Block ersetzt" if n else "Block angelegt"))
PYEOF

# Legacy-Slash-Command entfernen
LEGACY="$CLAUDE_HOME/commands/setup-kg-memory.md"
[ -f "$LEGACY" ] && rm "$LEGACY" && echo "✓ Alter /setup-kg-memory Command entfernt"

# Slash-Commands installieren
curl -sf "$KG_URL/cmd" > "$CLAUDE_HOME/commands/setup-ai-rem.md"
echo "✓ /setup-ai-rem Command angelegt"

mkdir -p "$CLAUDE_HOME/commands/ai-rem"
curl -sf "$KG_URL/cmd/prefedit" > "$CLAUDE_HOME/commands/ai-rem/prefedit.md"
echo "✓ /ai-rem:prefedit Command angelegt"

echo ""
echo "Fertig. Claude Code neu starten - dann ist ai-rem aktiv."
echo "Auf jeder neuen Maschine: bash <(curl -s __KG_URL__/setup)"
""".replace("__KG_URL__", _KG_URL)

CMD_MD = """\
# ai-rem einrichten

## Schritt 1 – MCP-Server registrieren

```bash
bash <(curl -s __KG_URL__/setup)
```

Das Skript registriert den ai-rem MCP-Server und konfiguriert CLAUDE.md.
Auf jeder neuen Maschine: `bash <(curl -s __KG_URL__/setup)`

## Schritt 2 – Gepinnte Tool-Übersicht anlegen

Lege folgende Preference in ai-rem an [type="Preference", context="private", pinned=True]:

Name: `session-start-tool-awareness`
Beschreibung:
```
tools-mcp: tool_md_to_pdf (md→PDF, designs: default2/default), tool_pdf_to_text, tool_head_lines, tool_echo, tool_pipeline_run, tool_list_scripts | MCP: paperless (Dok-Mgmt), ai-rem (KG) | Skills: /setup-ai-rem
```

## Schritt 3 – Tool-Entities für semantische Suche anlegen

Lege folgende Entities an [type="Tool", context="private"]:

- `tool_md_to_pdf`: "Markdown → PDF via WeasyPrint. Inputs: md_path (required), output_path (optional), design (default, default: default2). Output: pdf_path, size_bytes."
- `tool_list_scripts`: "Meta-Tool: listet alle registrierten tools-mcp Scripts mit Manifest-Metadaten (name, description, inputs, requires, ai_rem_entity)."
- `skill_example_4`: "Slash-Command /example-hook: Example hook skill."
- `skill_setup_ai_rem`: "Slash-Command /setup-ai-rem: ai-rem MCP-Server auf neuem System einrichten (MCP registrieren, CLAUDE.md konfigurieren, Tool-Entities anlegen)."
- `skill_example_1`: "Skill /example-skill:doc: Example skill."
- `skill_example_2`: "Skill /example-skill:pres: Example skill."
- `skill_example_3`: "Skill /example-skill:ibcs: Example skill."
- `mcp_paperless`: "MCP-Server paperless: Paperless-NGX. Tools: search_documents, get/upload/update/delete_document, create_letter(_from_md), list_correspondents/document_types/tags, create_tag/correspondent/document_type."
- `mcp_example_rag`: "MCP-Server example-rag: RAG system."
- `mcp_playwright`: "MCP-Server playwright: Browser-Automation – navigieren, Screenshots, Formulare ausfüllen, Web-Scraping."
- `mcp_github`: "MCP-Server github: GitHub API – Issues, Pull Requests, Repositories, Commits, Actions verwalten."
""".replace("__KG_URL__", _KG_URL)

PREFEDIT_CMD_MD = """\
# Preferences verwalten

Öffne den interaktiven Preferences-Manager für ai-rem.

## Ablauf

1. Lade alle Preferences mit `memory_list(type="Preference")` und zeige sie als nummerierte Tabelle:
   `Nr | 📌 | #Pos | Name | Context | Datum`
   - 📌 = gepinnt; #Pos = sort_order (leer = automatisch nach Datum)
2. Frage den User per AskUserQuestion was er tun möchte. Optionen:
   - **Pin / Unpin** — wählt eine Nummer, togglet den pinned-Status
   - **Context ändern** — wählt Nummer + neuen Context (work / private / global)
   - **Position setzen** — wählt Nummer + neue Positionsnummer (1 = ganz oben, leer = auto)
   - **Löschen** — wählt eine Nummer, bestätigt, löscht via `memory_delete`
   - **Fertig** — beendet den Manager
3. Führe die Aktion mit `memory_preference_update` (oder `memory_delete`) aus.
4. Zeige die aktualisierte Tabelle und wiederhole ab Schritt 2.

## Regeln
- `memory_preference_update` für alle Änderungen an context / pinned / sort_order verwenden — es überschreibt nur die explizit gesetzten Felder
- Beim Löschen immer kurz bestätigen lassen bevor `memory_delete` aufgerufen wird
- Tabelle nach jeder Aktion neu laden und anzeigen
"""

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
            _do_backup()
        except Exception as e:
            log.error("Scheduled backup failed: %s", e)
    log.info("Scheduler stopped")


threading.Thread(target=_scheduler_loop, daemon=True, name="backup-scheduler").start()
atexit.register(_shutdown.set)


# ─── Web UI ──────────────────────────────────────────────────────────────────

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
<p class="sub">Knowledge Graph Memory &nbsp;·&nbsp; <span id="ec">—</span> entities &nbsp;·&nbsp; <span id="rc">—</span> relations</p>
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
        "Langzeit-Gedächtnis als Knowledge Graph. "
        "Nutze memory_add/memory_relate um Wissen zu speichern, "
        "memory_get_context/memory_search zum Abrufen."
    ),
)


@mcp.custom_route("/setup", methods=["GET"])
async def setup_route(request: Request) -> PlainTextResponse:
    return PlainTextResponse(SETUP_SCRIPT, media_type="text/plain")


@mcp.custom_route("/cmd", methods=["GET"])
async def cmd_route(request: Request) -> PlainTextResponse:
    return PlainTextResponse(CMD_MD, media_type="text/plain")


@mcp.custom_route("/cmd/prefedit", methods=["GET"])
async def cmd_prefedit_route(request: Request) -> PlainTextResponse:
    return PlainTextResponse(PREFEDIT_CMD_MD, media_type="text/plain")


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
        lines.append(f"[{r[0]}] **{r[1]}**{ctx_str}: {r[2][:100]}  _(aktualisiert {r[3][:10]})_")
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
            lines = [f"[{r[0]}] {r[1]}: {r[2][:100]}" for r in rows]
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
