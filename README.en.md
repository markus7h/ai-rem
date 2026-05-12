# ai-rem — Knowledge Graph Memory for Claude

**ai-rem** is a persistent long-term memory for Claude Code, running as an MCP server on your home server.
Claude has no memory across sessions by default. This project solves that: relevant information — open tasks, decisions made, solved problems, projects, tools used — is stored in a knowledge graph and automatically loaded at the start of each conversation.

---

## What is ai-rem?

**ai-rem** is the MCP server that provides the knowledge graph. It runs as a Docker container on your server (`<SERVER_IP>`, port 3456) and is always available as long as your home server is running.

Technically:
- **[FastMCP](https://gofastmcp.com)** — Python MCP server framework, HTTP transport (Streamable HTTP)
- **[Kuzu](https://kuzudb.com)** — embedded graph database (no separate DB container needed)
- Data is stored persistently in `./data/kg.db` (relative to the Compose directory, configurable via `KG_DATA_PATH`)

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

## Requirements

- Docker on the target server
- Claude Code CLI on the client machine
- Network access to `<SERVER_IP>:3456`

---

## Installation / Deployment

### Server (one-time setup)

```bash
# On your-server: create directory
mkdir -p ~/mydocker/compose-files/ai-rem

# Transfer files
rsync -av server.py requirements.txt Dockerfile docker-compose.yml \
  your-server:~/mydocker/compose-files/ai-rem/

# Start the container
ssh your-server "cd ~/mydocker/compose-files/ai-rem && KG_PUBLIC_URL=http://<SERVER_IP>:3456 docker compose up -d --build"
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
rsync -q server.py your-server:~/mydocker/compose-files/ai-rem/
ssh your-server "cd ~/mydocker/compose-files/ai-rem && docker compose up -d --build"
```

---

## Files

```
ai-rem/
├── server.py          # MCP server (FastMCP + Kuzu + custom routes)
├── requirements.txt   # fastmcp, kuzu
├── Dockerfile
├── docker-compose.yml
└── README.md
```

---

## CLAUDE.md Strategy

CLAUDE.md files are kept minimal — they only contain the reference to ai-rem.
Everything else (preferences, LAN configuration, project info, decisions) lives in the graph.

| File | Content |
|---|---|
| `~/.claude/CLAUDE.md` | Startup: `memory_get_context()`, default context `"private"` |
| `work-repo/CLAUDE.md` | Startup: `memory_get_context(context="work")`, default context `"work"` |
