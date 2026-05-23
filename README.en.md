# ai-rem — Knowledge Graph Memory for Claude

> This documentation describes **[v0.0.9](https://github.com/markus7h/ai-rem/releases/tag/v0.0.9)** ([release notes](release-notes-v0.0.9.md)).

**ai-rem** is a persistent long-term memory for Claude Code, running as an MCP server on your home server.
Claude has no memory across sessions by default. This project solves that: relevant information — open tasks, decisions made, solved problems, projects, tools used — is stored in a knowledge graph and automatically loaded at the start of each conversation.

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
| `memory_add(name, type, description, context)` | Create or update an entity |
| `memory_relate(from, relation, to)` | Create a relationship between two entities |
| `memory_search(query, context)` | Full-text search across name and description |
| `memory_get_context(topic, context)` | Load relevant subgraph (tasks, projects, decisions) |
| `memory_list(type, context)` | List all entities |
| `memory_get_relations(name)` | Show all relationships of an entity |
| `memory_delete(name)` | Remove an entity and its relationships |

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

The built-in web interface is available at `http://<SERVER_IP>:3456/ui`.

Features:
- **Manual backup** — create a DB snapshot with one click
- **Automatic backup schedule** — hourly / daily / weekly, configurable in the UI
- **Backup management** — list of all backups with download and delete
- **Restore** — upload a JSON backup, mode `merge` or `replace`

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
KG_DATA_PATH=./data                      # Path to the database
KG_BACKUP_PATH=./backups                 # Path for backup files
MAX_BACKUPS=10                           # Maximum number of backups to keep
```

---

## Installation / Deployment

### Server (one-time setup)

```bash
# Create directory
mkdir -p ~/mydocker/compose-files/ai-rem

# Create .env
cat > ~/mydocker/compose-files/ai-rem/.env <<EOF
KG_PUBLIC_URL=http://<SERVER_IP>:3456
EOF

# Transfer files
rsync -av server.py requirements.txt Dockerfile docker-compose.yml \
  your-server:~/mydocker/compose-files/ai-rem/

# Start the container
ssh your-server "cd ~/mydocker/compose-files/ai-rem && docker compose up -d --build"
```

### Client — setting up a new machine

**On every new machine** — say this to Claude:

```
Run: bash <(curl -s http://<SERVER_IP>:3456/setup)
```

The script automatically handles:
1. `claude mcp add` — register ai-rem as a user-scoped HTTP MCP server
2. `~/.claude/CLAUDE.md` — create the startup instruction
3. `~/.claude/commands/setup-ai-rem.md` — create a local slash command

**The only thing to remember:** the URL `<SERVER_IP>:3456/setup`.

The slash command `/setup-ai-rem` is a **local shortcut** — it only exists on machines where setup has already been run. Users who sync `~/.claude/commands/` via Dotfiles or Syncthing can use `/setup-ai-rem` on additional machines — everyone else simply uses the curl command.

### Updating after code changes

```bash
rsync -av server.py requirements.txt Dockerfile docker-compose.yml \
  your-server:~/mydocker/compose-files/ai-rem/
ssh your-server "cd ~/mydocker/compose-files/ai-rem && docker compose up -d --build"
```

---

## Files

```
ai-rem/
├── server.py          # MCP server (FastMCP + Kuzu + web UI + backup)
├── requirements.txt   # fastmcp, kuzu
├── Dockerfile
├── docker-compose.yml
├── .env               # Configuration (not in repo)
├── README.md
└── README.en.md
```

---

## CLAUDE.md Strategy

CLAUDE.md files are kept minimal — they only contain the reference to ai-rem.
Everything else (preferences, LAN configuration, project info, decisions) lives in the graph.

| File | Content |
|---|---|
| `~/.claude/CLAUDE.md` | Startup: `memory_get_context()`, default context `"private"` |
| `work-repo/CLAUDE.md` | Startup: `memory_get_context(context="work")`, default context `"work"` |
