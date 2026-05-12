"""
Knowledge Graph Memory MCP Server
Langzeit-Gedächtnis für Claude via Kuzu embedded graph database.
"""

import json
import logging
import os
import re
import threading
from datetime import datetime
from typing import Optional

import kuzu
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DB_PATH = os.getenv("KUZU_DB_PATH", "/data/kg.db")

# ─── Setup-Endpunkt Inhalte ──────────────────────────────────────────────────

_KG_URL = os.getenv("KG_PUBLIC_URL", "http://localhost:3456")

SETUP_SCRIPT = r"""#!/usr/bin/env bash
set -e
KG_URL="__KG_URL__"
CLAUDE_MD="$HOME/.claude/CLAUDE.md"

echo "=== ai-rem Setup ==="

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

# settings.json Allowlist updaten
SETTINGS="$HOME/.claude/settings.json"
if [ -f "$SETTINGS" ] && grep -q "mcp__kg-memory__" "$SETTINGS"; then
    sed -i 's/mcp__kg-memory__/mcp__ai-rem__/g' "$SETTINGS"
    echo "✓ settings.json Allowlist aktualisiert"
fi

# CLAUDE.md
mkdir -p "$HOME/.claude"
if grep -q "Knowledge Graph Memory" "$CLAUDE_MD" 2>/dev/null; then
    echo "✓ CLAUDE.md bereits konfiguriert"
else
    cat >> "$CLAUDE_MD" << 'CMEOF'

## Knowledge Graph Memory
Beim Sitzungsstart memory_get_context() aufrufen und den Kontext nutzen.
Beim Speichern: context="private". Globale Entities ohne Tag.
Proaktiv speichern: Tasks, Entscheidungen, Probleme, Projekte.
CMEOF
    echo "✓ CLAUDE.md aktualisiert"
fi

# Alten Slash-Command entfernen
OLD_CMD="$HOME/.claude/commands/setup-ai-rem.md"
[ -f "$OLD_CMD" ] && rm "$OLD_CMD"
OLD_CMD_LEGACY="$HOME/.claude/commands/setup-kg-memory.md"
[ -f "$OLD_CMD_LEGACY" ] && rm "$OLD_CMD_LEGACY" && echo "✓ Alter /setup-kg-memory Command entfernt"

# Neuen Slash-Command anlegen
mkdir -p "$HOME/.claude/commands"
curl -sf "$KG_URL/cmd" > "$HOME/.claude/commands/setup-ai-rem.md"
echo "✓ /setup-ai-rem Command angelegt"

echo ""
echo "Fertig. Claude Code neu starten — dann ist ai-rem aktiv."
echo "Auf jeder neuen Maschine: bash <(curl -s __KG_URL__/setup)"
""".replace("__KG_URL__", _KG_URL)

CMD_MD = """\
# ai-rem einrichten

Führe dieses Setup-Skript aus:

```bash
bash <(curl -s __KG_URL__/setup)
```

Das Skript registriert den ai-rem MCP-Server und konfiguriert CLAUDE.md.
Auf jeder neuen Maschine: `bash <(curl -s __KG_URL__/setup)`
""".replace("__KG_URL__", _KG_URL)

db = kuzu.Database(DB_PATH)
conn = kuzu.Connection(db)
_lock = threading.Lock()


def db_exec(query: str, params: dict | None = None) -> kuzu.QueryResult:
    with _lock:
        return conn.execute(query, params or {})


def init_schema() -> None:
    stmts = [
        """CREATE NODE TABLE IF NOT EXISTS Entity(
               id     STRING PRIMARY KEY,
               name   STRING,
               type   STRING,
               descr  STRING,
               extra  STRING,
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
    log.info("Schema ready — DB at %s", DB_PATH)


init_schema()

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


@mcp.custom_route("/export", methods=["GET"])
async def export_route(request: Request) -> JSONResponse:
    entities = _rows(db_exec(
        "MATCH (e:Entity) RETURN e.id, e.name, e.type, e.descr, e.extra, e.created_at, e.updated_at"
    ))
    relations = _rows(db_exec(
        "MATCH (a:Entity)-[r:Rel]->(b:Entity) RETURN a.id, r.name, b.id, r.extra, r.created_at"
    ))
    return JSONResponse({
        "version": 1,
        "exported_at": _now(),
        "entities": [
            {
                "id": r[0], "name": r[1], "type": r[2], "description": r[3],
                "extra": json.loads(r[4] or "{}"), "created_at": r[5], "updated_at": r[6],
            }
            for r in entities
        ],
        "relations": [
            {
                "from_id": r[0], "relation": r[1], "to_id": r[2],
                "extra": json.loads(r[3] or "{}"), "created_at": r[4],
            }
            for r in relations
        ],
    })


@mcp.custom_route("/import", methods=["POST"])
async def import_route(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    mode = request.query_params.get("mode", "merge")
    if mode not in ("merge", "replace"):
        return JSONResponse({"error": "mode must be 'merge' or 'replace'"}, status_code=400)

    if mode == "replace":
        db_exec("MATCH (e:Entity) DETACH DELETE e")

    ts = _now()
    entities_created = entities_skipped = relations_created = 0

    for entity in body.get("entities", []):
        eid = entity.get("id") or _id(entity["name"])
        extra_json = json.dumps(entity.get("extra", {}), ensure_ascii=False)
        if _rows(db_exec("MATCH (e:Entity {id: $id}) RETURN e.id", {"id": eid})):
            entities_skipped += 1
            continue
        db_exec(
            """CREATE (:Entity {id: $id, name: $name, type: $type,
                                descr: $descr, extra: $extra,
                                created_at: $ts, updated_at: $ts})""",
            {
                "id": eid, "name": entity["name"], "type": entity.get("type", "Unknown"),
                "descr": entity.get("description", ""), "extra": extra_json,
                "ts": entity.get("created_at", ts),
            },
        )
        entities_created += 1

    for rel in body.get("relations", []):
        from_id, to_id, relation = rel["from_id"], rel["to_id"], rel["relation"]
        extra_json = json.dumps(rel.get("extra", {}), ensure_ascii=False)
        if not _rows(db_exec(
            "MATCH (a:Entity {id: $fid})-[r:Rel {name: $rel}]->(b:Entity {id: $tid}) RETURN r.name",
            {"fid": from_id, "tid": to_id, "rel": relation},
        )):
            db_exec(
                """MATCH (a:Entity {id: $fid}), (b:Entity {id: $tid})
                   CREATE (a)-[:Rel {name: $rel, extra: $extra, created_at: $ts}]->(b)""",
                {"fid": from_id, "tid": to_id, "rel": relation, "extra": extra_json,
                 "ts": rel.get("created_at", ts)},
            )
            relations_created += 1

    return JSONResponse({
        "status": "ok", "mode": mode,
        "entities_created": entities_created, "entities_skipped": entities_skipped,
        "relations_created": relations_created,
    })


# ─── helpers ────────────────────────────────────────────────────────────────


def _id(name: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", name.lower().strip())[:64]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _rows(result: kuzu.QueryResult) -> list[list]:
    rows = []
    while result.has_next():
        rows.append(result.get_next())
    return rows


def _ctx_match(extra_json: str, context: str) -> bool:
    """True wenn Entity zum gesuchten Context passt. Ungetaggte Entities (global) passen immer."""
    if not context:
        return True
    return json.loads(extra_json or "{}").get("context", "") in ("", context)


# ─── tools ──────────────────────────────────────────────────────────────────


@mcp.tool()
def memory_add(
    name: str,
    type: str,
    description: str = "",
    extra: Optional[dict] = None,
    context: str = "",
) -> str:
    """Entity im Knowledge Graph anlegen oder aktualisieren.

    type-Werte: Person | Project | Task | Tool | Problem | Solution | Decision | Preference | Topic
    extra: beliebige JSON-Properties (z.B. {"status": "offen", "priority": "hoch"})
    context: "work" | "private" | "" (global, default — erscheint in allen Context-Abfragen)
    """
    eid = _id(name)
    merged = dict(extra or {})
    if context:
        merged["context"] = context
    extra_json = json.dumps(merged, ensure_ascii=False)
    ts = _now()

    existing = _rows(db_exec("MATCH (e:Entity {id: $id}) RETURN e.id", {"id": eid}))
    if existing:
        db_exec(
            """MATCH (e:Entity {id: $id})
               SET e.name = $name, e.type = $type,
                   e.descr = $descr, e.extra = $extra, e.updated_at = $ts""",
            {"id": eid, "name": name, "type": type, "descr": description, "extra": extra_json, "ts": ts},
        )
        return f"Aktualisiert: [{type}] {name}"

    db_exec(
        """CREATE (:Entity {id: $id, name: $name, type: $type,
                            descr: $descr, extra: $extra,
                            created_at: $ts, updated_at: $ts})""",
        {"id": eid, "name": name, "type": type, "descr": description, "extra": extra_json, "ts": ts},
    )
    return f"Angelegt: [{type}] {name}"


@mcp.tool()
def memory_relate(
    from_name: str,
    relation: str,
    to_name: str,
    extra: Optional[dict] = None,
) -> str:
    """Beziehung zwischen zwei Entities erstellen.

    Beispiele für relation: NUTZT | ARBEITET_AN | GELÖST_DURCH | HÄNGT_AB_VON |
                             LÄUFT_AUF | INTEGRIERT_MIT | GETROFFEN_VON | BEVORZUGT
    Entities werden automatisch angelegt falls noch nicht vorhanden.
    """
    from_id = _id(from_name)
    to_id = _id(to_name)
    extra_json = json.dumps(extra or {}, ensure_ascii=False)
    ts = _now()

    for eid, ename in [(from_id, from_name), (to_id, to_name)]:
        if not _rows(db_exec("MATCH (e:Entity {id: $id}) RETURN e.id", {"id": eid})):
            db_exec(
                """CREATE (:Entity {id: $id, name: $name, type: 'Unknown',
                                   descr: '', extra: '{}',
                                   created_at: $ts, updated_at: $ts})""",
                {"id": eid, "name": ename, "ts": ts},
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
    rows = _rows(
        db_exec(
            """MATCH (e:Entity)
               WHERE lower(e.name) CONTAINS $q OR lower(e.descr) CONTAINS $q
               RETURN e.type, e.name, e.descr, e.updated_at, e.extra
               ORDER BY e.updated_at DESC
               LIMIT $lim""",
            {"q": q, "lim": limit},
        )
    )
    rows = [r for r in rows if _ctx_match(r[4], context)]
    if not rows:
        return "Keine Ergebnisse."
    lines = []
    for r in rows:
        ctx_tag = json.loads(r[4] or "{}").get("context", "")
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

    if topic:
        q = topic.lower()
        rows = _rows(
            db_exec(
                """MATCH (e:Entity)
                   WHERE lower(e.name) CONTAINS $q OR lower(e.descr) CONTAINS $q
                   RETURN e.type, e.name, e.descr, e.updated_at, e.extra
                   ORDER BY e.updated_at DESC
                   LIMIT 20""",
                {"q": q},
            )
        )
        rows = [r for r in rows if _ctx_match(r[4], context)]
        if rows:
            lines = [f"[{r[0]}] {r[1]}: {r[2][:100]}" for r in rows]
            sections.append(f"## Kontext: {topic}{ctx_label}\n" + "\n".join(lines))

        rel_rows = _rows(
            db_exec(
                """MATCH (a:Entity)-[r:Rel]->(b:Entity)
                   WHERE lower(a.name) CONTAINS $q OR lower(b.name) CONTAINS $q
                   RETURN a.name, r.name, b.name, a.extra, b.extra
                   LIMIT 15""",
                {"q": q},
            )
        )
        rel_rows = [r for r in rel_rows if _ctx_match(r[3], context) and _ctx_match(r[4], context)]
        if rel_rows:
            lines = [f"{r[0]} -[{r[1]}]-> {r[2]}" for r in rel_rows]
            sections.append("### Relationen\n" + "\n".join(lines))

    # Offene Tasks
    task_rows = _rows(
        db_exec(
            """MATCH (e:Entity {type: 'Task'})
               RETURN e.name, e.descr, e.extra, e.updated_at
               ORDER BY e.updated_at DESC
               LIMIT 10""",
            {},
        )
    )
    tasks = []
    for r in task_rows:
        if not _ctx_match(r[2], context):
            continue
        extra = json.loads(r[2] or "{}")
        status = extra.get("status", "offen")
        if status.lower() not in ("erledigt", "done", "closed"):
            tasks.append(f"- [{status}] **{r[0]}**: {r[1][:80]}")
    if tasks:
        sections.append(f"## Offene Tasks{ctx_label}\n" + "\n".join(tasks))

    # Aktive Projekte
    proj_rows = _rows(
        db_exec(
            """MATCH (e:Entity {type: 'Project'})
               RETURN e.name, e.descr, e.extra, e.updated_at
               ORDER BY e.updated_at DESC
               LIMIT 8""",
            {},
        )
    )
    projects = []
    for r in proj_rows:
        if not _ctx_match(r[2], context):
            continue
        extra = json.loads(r[2] or "{}")
        status = extra.get("status", "aktiv")
        projects.append(f"- [{status}] **{r[0]}**: {r[1][:80]}")
    if projects:
        sections.append(f"## Projekte{ctx_label}\n" + "\n".join(projects))

    # Letzte Entscheidungen / Lösungen / Probleme
    recent_rows = _rows(
        db_exec(
            """MATCH (e:Entity)
               WHERE e.type IN ['Problem', 'Solution', 'Decision']
               RETURN e.type, e.name, e.descr, e.updated_at, e.extra
               ORDER BY e.updated_at DESC
               LIMIT 8""",
            {},
        )
    )
    recent_rows = [r for r in recent_rows if _ctx_match(r[4], context)]
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
    if type:
        rows = _rows(
            db_exec(
                """MATCH (e:Entity {type: $type})
                   RETURN e.name, e.descr, e.updated_at, e.extra
                   ORDER BY e.name""",
                {"type": type},
            )
        )
        rows = [r for r in rows if _ctx_match(r[3], context)]
        if not rows:
            return f"Keine Einträge vom Typ '{type}'" + (f" mit context='{context}'" if context else "") + "."
        lines = []
        for r in rows:
            ctx_tag = json.loads(r[3] or "{}").get("context", "")
            ctx_str = f" `[{ctx_tag}]`" if ctx_tag else ""
            lines.append(f"- **{r[0]}**{ctx_str}: {r[1][:80]}  _({r[2][:10]})_")
        return "\n".join(lines)

    rows = _rows(
        db_exec(
            "MATCH (e:Entity) RETURN e.type, e.name, e.descr, e.updated_at, e.extra ORDER BY e.type, e.name",
            {},
        )
    )
    rows = [r for r in rows if _ctx_match(r[4], context)]
    if not rows:
        return "Keine Einträge."

    current_type = None
    lines = []
    for r in rows:
        if r[0] != current_type:
            current_type = r[0]
            lines.append(f"\n### {current_type}")
        ctx_tag = json.loads(r[4] or "{}").get("context", "")
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
