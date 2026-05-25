# ai-rem — Knowledge Graph Memory for Claude

> This documentation describes **[v0.1.0](https://github.com/markus7h/ai-rem/releases/tag/v0.1.0)**.

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
2. `~/.claude/settings-template.json` — create base template for permissions, deny rules and hooks (if not present)
3. `~/.claude/hooks/system-check.py` — deploy consolidated SessionStart hook (ai-rem health, SMB mount, MCP server tests, settings sync, tool count)
4. `~/.claude/settings.json` — add permissions, deny rules and hook; remove old hooks; set `autoMemoryEnabled: false`
5. `~/.claude/CLAUDE.md` — create or update minimal 3-line pointer to ai-rem
6. Install slash commands (`/setup-ai-rem`, `/ai-rem:prefedit`)
7. `~/.claude/ai-rem/pref-tui.py` — install terminal preferences manager
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
├── server.py              # MCP server (FastMCP + Kuzu + web UI + backup)
├── requirements.txt       # fastmcp, kuzu
├── Dockerfile
├── docker-compose.yml
├── .env.example           # Configuration template
├── .env                   # Configuration (not in repo, derived from .env.example)
├── setup-config.json      # Personal configuration (gitignored; example in repo)
├── README.md              # This file (English)
└── README.de.md           # German documentation
```

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

The Docker image copies this file at build time (`COPY setup-config*.json ./`). A dummy `setup-config.json` in the repo serves as a public example without private data; the real personal version is gitignored.
