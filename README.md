# ai-rem — Knowledge Graph Memory for Claude

> This documentation describes **[v0.1.5](https://github.com/markus7h/ai-rem/releases/tag/v0.1.5)**.

**ai-rem** is a persistent long-term memory for Claude Code, running as an MCP server on your home server.
Claude has no memory across sessions by default. This project solves that: relevant information — open tasks, decisions made, solved problems, projects, tools used — is stored in a knowledge graph and automatically loaded at the start of each conversation.

Docker Hub: `docker pull magic3arkus/ai-rem`

---

## What is ai-rem?

**ai-rem** is the MCP server that provides the knowledge graph. It runs as a Docker container on your home server (`<SERVER_IP>`, configurable port, default `3456`) and is always available as long as the server is running.

Technically:
- **[FastMCP](https://gofastmcp.com)** — Python MCP server framework, HTTP transport (Streamable HTTP)
- **[Kuzu](https://kuzudb.com)** — embedded graph database (no separate DB container needed)
- Data is stored persistently in `./data/kg.db` (configurable via `KG_DATA_PATH`)
- Backups are saved to `./backups/` (configurable via `KG_BACKUP_PATH`)

---

## How it works

At the start of each session, Claude loads the relevant context from the graph via `memory_get_context()` and uses it as a working basis. New insights are proactively saved by Claude using `memory_add` or `memory_relate`.

### Available MCP Tools

| Tool | Description |
|---|---|
| `memory_add(name, type, description, context, pinned)` | Create or update an entity. `pinned=True` → preference always appears at the top in `get_context` |
| `memory_preference_update(name, context, pinned, sort_order)` | Update preference fields without overwriting the description |
| `memory_relate(from, relation, to)` | Create a relationship between two entities |
| `memory_search(query, context)` | Full-text search across name and description |
| `memory_get_context(topic, context)` | Load relevant subgraph (tasks, projects, decisions, preferences) |
| `memory_list(type, context)` | List all entities |
| `memory_get_relations(name)` | Show all relationships of an entity |
| `memory_delete(name)` | Remove an entity and its relationships |
| `memory_status()` | Quick status: number of entities and relations (used by the SessionStart hook) |

### Entity Types

`Person` · `Project` · `Task` · `Tool` · `Problem` · `Solution` · `Decision` · `Preference` · `Topic`

### Context Separation

Each entity can be tagged with a `context`:
- `context="work"` — only visible in work sessions
- `context="private"` — only visible in private sessions
- no tag — global, appears in all queries

The context can be set per CLAUDE.md: e.g. `context="work"` for work repositories and `context="private"` for personal projects.

---

## Web UI

| URL | Function |
|---|---|
| `/ui` | Backup management: manual, schedule, download, restore |
| `/prefs` | Preferences manager: pin, context, sort order, delete |

**`/prefs`** — Full preferences manager in the browser: pin/unpin, context dropdown, manual sort order, delete. Click on the name to expand the full description inline. Accessible via the slash command `/ai-rem:prefedit`.

---

## Auto-Memory (PreCompact + SessionEnd → ai-rem)

The built-in Claude Code auto-memory (markdown file) is replaced by a transcript extractor that writes **structured entities and relations** into ai-rem.

**Flow:** `PreCompact` / `SessionEnd` hook → `ai-rem ingest --transcript <path>` → Ollama (qwen3:14b on `AI_REM_OLLAMA_URL`, default `http://192.168.2.11:11434`) extracts JSON → bulk-upsert via MCP → log to `~/.claude/auto-memory/<timestamp>.json`.

**CLI** (`bin/ai-rem`, own `.venv`):

```bash
ai-rem status
ai-rem search "auto-memory"
ai-rem show "<name>"   # full, untruncated description + extra + relations (via /export)
ai-rem list --type Decision
ai-rem ingest --transcript <session.jsonl> [--dry-run] [--model qwen3:14b]
```

**Anti-recursion:** transcripts under 500 chars are skipped, `/tmp/ai-rem-ingest.lock` prevents nested runs.

**Failure-mode (md-fallback + catch-up):** if Ollama is unreachable, the session is not lost — a heuristic extraction is appended to `~/.claude/auto-memory/fallback.md` (imported into `CLAUDE.md` via `@`-import, so it stays in context) and the transcript is queued in `pending.jsonl`. As soon as Ollama is reachable again, `ai-rem catchup` (run by the SessionStart and PreCompact/SessionEnd hooks) re-ingests the queued sessions properly into ai-rem and **empties the md**. The hook never breaks `/compact` or session end; hard errors go to `~/.claude/auto-memory/errors.log`.

**Visibility:** each successful run writes `~/.claude/auto-memory/last-run.json`; the SessionStart check surfaces a line like `🧠 N Entities, M Rel` (with `(md-Fallback)` when Ollama was down).

**Configuration env:**
- `AI_REM_ENDPOINT` — MCP URL (default `http://192.168.2.15:3456/mcp`)
- `AI_REM_OLLAMA_URL` — Ollama base URL (default `http://192.168.2.11:11434`)
- `AI_REM_CLI` — explicit CLI path override (otherwise discovery via known mount paths and `$PATH`)

---

## Nightly cleanup (non-destructive: archive, don't delete)

A daemon thread in the container runs a daily maintenance pass (default 03:00, configurable in the `/cleanup` web UI). It detects duplicate and outdated entries (heuristics + Ollama when reachable) and **archives** them instead of deleting: the entry is tagged `archived`, optionally compressed (with the original preserved in `extra.original_descr`), and linked via `DUPLIKAT_VON` / `VERALTET_DURCH`. Archived entries are hidden from `memory_get_context`/`search`/`list` by default (opt in with `include_archived=true`) but remain reachable for history via `memory_get_relations`. **Preferences, pinned and already-archived entries are never touched.** Every run backs up first; the log is viewable in the `/cleanup` web UI.

Ambiguous cases (and everything when Ollama was down at night) land in a review queue. The `/memory-cleanup` slash command — auto-triggered silently at session start when the queue is non-empty — has Claude resolve them with judgment via the non-destructive `memory_merge` / `memory_archive` MCP tools.

---

## Requirements

- Docker on the target server
- Claude Code CLI on the client machine
- Network access to `<SERVER_IP>:<PORT>`

---

## Configuration

Environment variables are loaded from a `.env` file in the Compose directory:

```env
KG_PUBLIC_URL=http://<SERVER_IP>:3456   # Public URL of the server
PORT=3456                                # TCP port (default: 3456)
KUZU_DB_PATH=/data/kg.db                 # Path to the database
BACKUP_DIR=/backups                      # Path for backup files
MAX_BACKUPS=10                           # Maximum number of backups to keep
KUZU_POOL_SIZE=4                         # Connection pool size
```

---

## Installation / Deployment

### Server (one-time setup)

```bash
# Create directory
mkdir -p ~/mydocker/compose-files/ai-rem
cd ~/mydocker/compose-files/ai-rem

# Download docker-compose.yml and .env.example
curl -O https://raw.githubusercontent.com/markus7h/ai-rem/main/docker-compose.yml
curl -O https://raw.githubusercontent.com/markus7h/ai-rem/main/.env.example

# Create .env and set your server IP
cp .env.example .env
# → set KG_PUBLIC_URL in .env to your actual server IP

# Pull image and start container
docker compose pull && docker compose up -d
```

### Client — setting up a new machine

**On every new machine** — say this to Claude:

```
Run: bash <(curl -s http://<SERVER_IP>:3456/setup)
```

The script automatically handles:
1. `claude mcp add` — register ai-rem as a user-scoped HTTP MCP server
2. `~/.claude/settings-template.json` — (re)generate base template for permissions, deny rules and hooks from the live setup config
3. `~/.claude/hooks/system-check.py` — deploy consolidated SessionStart hook (ai-rem health, SMB mount, MCP server tests, settings sync, tool count)
4. `~/.claude/hooks/auto-memory.py` — deploy PreCompact + SessionEnd hook (transcript → `ai-rem ingest` → Ollama-Extraktor → structured entities)
5. `~/.claude/settings.json` — add permissions, deny rules, SessionStart hook, PreCompact + SessionEnd hooks; remove old hooks; set `autoMemoryEnabled: false`
6. `~/.claude/CLAUDE.md` — create or update minimal 3-line pointer to ai-rem
7. Install slash commands (`/setup-ai-rem`, `/ai-rem:prefedit`)
8. Create preferences & tool entities directly in the knowledge graph via MCP API

**The only thing to remember:** the URL `<SERVER_IP>:3456/setup`.

The script is idempotent — running it multiple times on the same machine is safe.

### Update to a new version

```bash
ssh your-server "cd ~/mydocker/compose-files/ai-rem && docker compose pull && docker compose up -d"
```

---

## Files

```
ai-rem/
├── server.py                   # MCP server (FastMCP + Kuzu + web UI + backup + cleanup)
├── bin/ai-rem                  # CLI (status/search/ingest/catchup, own .venv)
├── lib/                        # extractor (+ md-fallback/catchup), heuristic, mcp_client
├── requirements.txt            # fastmcp, kuzu
├── Dockerfile
├── docker-compose.yml
├── .env.example                # Configuration template
├── .env                        # Configuration (not in repo, derived from .env.example)
├── setup-config.json           # Personal configuration (gitignored; example in repo)
├── .claude/settings.json.example  # Example repo-local Claude permissions
├── .claude/settings.json       # Your local Claude permissions (gitignored; copy from .example)
├── README.md                   # This file (English)
└── README.de.md                # German documentation
```

> `.claude/settings.json` is **gitignored** so personal/local permission tweaks never land in
> the repo. Copy the template to get started: `cp .claude/settings.json.example .claude/settings.json`.

---

## CLAUDE.md Strategy

The setup script writes only a **minimal 3-line pointer** to `~/.claude/CLAUDE.md`:

```markdown
## ai-rem
ai-rem is the only knowledge source for persistent context. Auto-memory is disabled.
Usage rules come via MCP Server Instructions, behavioural rules from ai-rem Preferences.
```

The actual rules come from two sources loaded automatically at session start:
- **MCP Server Instructions** — what to store, what not to, how to link entities (built into the server)
- **ai-rem Preferences** (`memory_get_context`) — personal behaviour rules, feedback, working styles (dynamic, in the graph)

Project-specific CLAUDE.md files set the default context:

| File | Purpose |
|---|---|
| `~/.claude/CLAUDE.md` | Minimal ai-rem pointer (managed by setup script) |
| `work-repo/CLAUDE.md` | `context="work"` as default for work repositories |

---

## Personal Configuration (setup-config.json)

The setup endpoint optionally loads a `setup-config.json` from the server (`/setup-config`). This file is **not in the repo** — it contains personal settings:

```json
{
  "permissions_allow_portable": ["Bash", "mcp__tools__*", ...],
  "permissions_deny": ["Bash(bw get *)", ...],
  "smb": {"mount": "/Volumes/markus", "url": "smb://server/share"},
  "mcp_stdio_servers": {"paperless": "/path/to/index.js"},
  "tools_scripts_dir": "/path/to/tools-mcp/scripts",
  "old_hooks": ["legacy-hook.sh"],
  "entities": [{"name": "...", "type": "Tool", "description": "..."}]
}
```

The Docker image copies this file at build time (`COPY setup-config*.json ./`). The personal `setup-config.json` is gitignored, so it never ships in the public image. Instead the repo includes a generic **`setup-config.example.json`**: when no personal config is present, `/setup-config` falls back to it, so a fresh deployment seeds a useful starter set of behavioural preferences (plan-first, answer concisely, check ai-rem before asking, avoid hallucinations, store knowledge proactively) plus generic permission/deny rules. Drop in your own `setup-config.json` to override the template entirely.
