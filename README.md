# ai-rem — Knowledge Graph Memory for Claude

> This documentation describes **[v0.3.0](https://github.com/markus7h/ai-rem/releases/tag/v0.3.0)**.

**ai-rem** is a persistent long-term memory for Claude Code, running as an MCP server on your home server.
Static memory files like `CLAUDE.md` sit in context in full and are tied to individual projects and machines. ai-rem takes a more efficient approach: relevant information — open tasks, decisions made, solved problems, projects, tools used — lives in a knowledge graph on your home server, is loaded selectively instead of wholesale, and is available from any machine, independent of where you work.

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
| `memory_search(query, context)` | Hybrid search over name + description: per-token lexical matching plus semantic vector recall (finds multi-word queries even when the words aren't contiguous) |
| `memory_search_full(query, context)` | Like `memory_search`, but returns the full description without the 400-char truncation |
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

## Token savings

ai-rem doesn't add knowledge to every prompt — it **lazy-loads** only the relevant subgraph on demand instead of carrying everything in `CLAUDE.md` all session long. The per-session footprint stays roughly constant (~1–3k tokens) no matter how large the graph grows, while the alternative — stuffing all knowledge into `CLAUDE.md` — costs ~20k tokens loaded into *every* session.

**Worked estimate — assuming an average of 5 sessions/day:**

| Parameter | Value | Source |
|---|---|---|
| Sessions / month | 5 × 30 = **150** | assumption |
| Sessions with real recall | ~72 % → **~108** | measured (90/125 sessions used ai-rem) |
| Trivial sessions | ~42 | derived |
| Savings per recall session | ~12k tokens | modelled (avoided re-discovery / no permanent `CLAUDE.md` ballast) |
| Hook + retrieval overhead | ~300 tokens/injection | measured (~2.4 injections/session) |

```
Gain:      108 recall sessions × 12,000  = 1,296,000
Cost:       42 trivial sessions ×    300 =     12,600
Overhead:  ~360 injections     ×    300 =    108,000
────────────────────────────────────────────────────
Net ≈ 1,175,000 tokens / month saved
```

**Result: ~1.2 million tokens/month** at 5 sessions/day — roughly **6 full 200k context windows** you don't burn on re-explaining context, re-discovering infrastructure, or permanent `CLAUDE.md` bloat. Per day that's ~39k tokens; per year ~14M.

**Range** (depending on how knowledge-heavy your sessions are):

| Scenario | Recall sessions | Tokens/session | Net / month |
|---|---|---|---|
| Conservative | 90 (60 %) | 8k | **~0.6M** |
| Typical | 108 (72 %) | 12k | **~1.2M** |
| Intensive | 120 (80 %) | 16k | **~1.8M** |

**The savings grow as the graph grows.** This is the decisive long-term property: the per-session footprint stays roughly constant (~1–3k tokens) regardless of graph size, because only the *relevant* subgraph is loaded on demand. The naive alternative — keeping knowledge in `CLAUDE.md` — scales **linearly**: every new fact is paid for in *every* session forever. So as months pass and the graph accumulates hundreds of entities, the gap widens — the `CLAUDE.md` approach gets steadily more expensive while ai-rem's cost stays flat. The numbers above (146 entities) are an early-stage snapshot; at 500+ entities the same 5-sessions/day pattern saves substantially more, since the avoided always-on ballast is far larger.

> The session count and recall rate are **measured** from real usage (125 sessions over ~28 days). The per-session savings (8–16k) is a model, not a measurement — the "what it would have cost without ai-rem" can't be observed directly. Treat the totals as an informed estimate, not a benchmark.

---

## Web UI

| URL | Function |
|---|---|
| `/ui` | Backup management: manual, schedule, download, restore |
| `/prefs` | Preferences manager: pin, context, sort order, delete |
| `/cleanup` | Nightly cleanup: config, manual run, pending reviews, run log |

**`/prefs`** — Full preferences manager in the browser: pin/unpin, context dropdown, manual sort order, delete. Click on the name to expand the full description inline. A dashed **context-limit line** marks how many preferences `memory_get_context` actually loads into the session (top `CONTEXT_PREF_LIMIT`, default 15 — pinned first, then sort order / recency); rows below it are dimmed. Accessible via the slash command `/ai-rem:prefedit`.

---

## Auto-Memory (PreCompact + SessionEnd → ai-rem)

The built-in Claude Code auto-memory (markdown file) is replaced by a transcript extractor that writes **structured entities and relations** into ai-rem.

**Flow:** `PreCompact` / `SessionEnd` hook → `ai-rem ingest --transcript <path>` → Ollama (qwen3:14b on `AI_REM_OLLAMA_URL`, default `http://localhost:11434`) extracts JSON → bulk-upsert via MCP → log to `~/.claude/auto-memory/<timestamp>.json`.

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
- `AI_REM_ENDPOINT` — MCP URL (default `http://localhost:3456/mcp`)
- `AI_REM_OLLAMA_URL` — Ollama base URL (default `http://localhost:11434`)
- `AI_REM_CLI` — explicit CLI path override (otherwise discovery via known mount paths and `$PATH`)

---

## Nightly cleanup (non-destructive: archive, don't delete)

A daemon thread in the container runs a daily maintenance pass (default 03:00, configurable in the `/cleanup` web UI). It detects duplicate and outdated entries (heuristics + Ollama when reachable) and **archives** them instead of deleting: the entry is tagged `archived`, optionally compressed (with the original preserved in `extra.original_descr`), and linked via `DUPLIKAT_VON` / `VERALTET_DURCH`. Archived entries are hidden from `memory_get_context`/`search`/`list` by default (opt in with `include_archived=true`) but remain reachable for history via `memory_get_relations`. **Preferences, pinned and already-archived entries are never touched.** Every run backs up first; the log is viewable in the `/cleanup` web UI.

Ambiguous cases (and everything when Ollama was down at night) land in a review queue. A non-empty queue is surfaced at session start as an informational hint only (no auto-execution). You can resolve it two ways: **(a)** in the `/cleanup` web UI, where each pending item shows both descriptions with **Mergen/Archivieren** (apply) and **Verwerfen** (keep both) buttons (`POST /api/cleanup/resolve`); or **(b)** the `/memory-cleanup` slash command, which has Claude resolve the entries with judgment. Both use the same non-destructive `memory_merge` / `memory_archive` operations — nothing is deleted.

> **Ollama reachability:** the nightly judge needs `AI_REM_OLLAMA_URL` to point at a reachable Ollama. In the bundled `docker-compose.yml` it defaults to `http://192.168.2.11:11434` (override per deployment via `.env`). If unset/unreachable, the cleanup still runs but every ambiguous pair is pushed to the review queue instead of being auto-judged (`ollama_used=false` in the run log).

---

## Plan saving (ExitPlanMode → ai-rem)

A `PostToolUse` hook on `ExitPlanMode` (`hooks/save-plan.py`) stores every finalized plan as an **open `Task`** in ai-rem, so plans become a central, cross-machine list instead of just slug files under `~/.claude/plans/`. The `system-check.py` SessionStart hook surfaces these open `Task`s (plans included) automatically, so a new session opens with the list right there — or ask *"any open plans?"* to pick one.

**Fields** come from a small frontmatter block Claude writes at the top of each plan file (no prose guessing):

```
---
name: "Plan: <title>"
description: "<one short sentence>"
status: offen
---
```

The hook reads the newest plan file's frontmatter and upserts via `memory_add` (`type: Task`, `extra.kind=plan`, `extra.plan_file`, `extra.status`). Upsert is keyed by `name`, so re-finalizing a plan never duplicates. Completed plans are archived (`memory_archive`) — the status lives in ai-rem, so it stays consistent across machines. Fail-silent: never blocks `ExitPlanMode`.

**Install:** copy `hooks/save-plan.py` to `~/.claude/hooks/`, `chmod +x`, and register the `PostToolUse: ExitPlanMode` hook in `~/.claude/settings.json` (see the file header).

---

## Requirements

- Docker on the target server
- Claude Code CLI on the client machine
- Network access to `<SERVER_IP>:<PORT>`

---

## Configuration

Environment variables are loaded from a `.env` file in the Compose directory:

```env
AI_REM_API_TOKEN=...                     # REQUIRED — API token (fail-closed, see Authentication)
KG_PUBLIC_URL=http://<SERVER_IP>:3456   # Public URL of the server
PORT=3456                                # TCP port (default: 3456)
KUZU_DB_PATH=/data/kg.db                 # Path to the database
BACKUP_DIR=/backups                      # Path for backup files
MAX_BACKUPS=10                           # Maximum number of backups to keep
KUZU_POOL_SIZE=4                         # Connection pool size
```

---

## Authentication

All sensitive routes (`/mcp`, `/api/*`, `/export`, `/import`, `/ui`) require
authentication. The server is **fail-closed**: without `AI_REM_API_TOKEN` it
refuses to start. A request is authorized if **any** of these holds:

1. the path is public — `/health`, `/setup`, `/setup-config`, `/hooks/*`, `/cmd*`, `/login` (onboarding/login only, no private data);
2. it originates from **loopback** *and the request is not proxied* (no `X-Forwarded-For`). In a bridge-network container this effectively only covers in-container traffic (e.g. the healthcheck): tunneled/proxied requests arrive as the Docker gateway IP, not loopback. Behind a same-host reverse proxy (e.g. Caddy) the peer is `127.0.0.1` but `X-Forwarded-For` is set, so the token is still required;
3. it carries `Authorization: Bearer <AI_REM_API_TOKEN>` (constant-time compared) — used by MCP clients (Claude's `/mcp` channel);
4. it carries a valid `ai_rem_session` cookie — used by the browser Web UI (see below).

### Web UI login

A browser cannot set an `Authorization` header when navigating, so the Web UI
uses a cookie. Open `/login`, enter the API token once, and the server sets an
**HttpOnly, Secure, SameSite=Strict** cookie that authorizes `/ui` and its
`/api/*` calls; `/logout` clears it. The cookie value is **not** the raw token
but a derived, UI-scoped value (`HMAC-SHA256(token, "ai-rem-ui-session")`), so the
`/mcp` Bearer never reaches the browser and the session auto-invalidates when the
token rotates. Because the cookie is `Secure`, the UI must be reached over HTTPS
(e.g. a Caddy `tls internal` vhost). Lifetime defaults to 30 days
(`AI_REM_UI_SESSION_TTL`, in seconds).

**Token source — [mykeyvault](https://github.com/markus7h/mykeyvault):** the token
is stored once in the vault as item `ai-rem-api-token` (single source of truth).
- **Server:** `deploy.sh` pulls it from the vault at deploy time and writes it into the remote `.env` — server startup stays independent of the vault's runtime state.
- **Clients:** the `system-check.py` SessionStart hook uses the bearer token already stored in `~/.claude.json` → `mcpServers."ai-rem".headers.Authorization` for the current session (fast, no vault roundtrip — that is also how Claude's `/mcp` channel carries it) and refreshes it from the vault in a **detached background process** for the next session (vault-api coordinates live in `~/.claude.json` → `mcpServers.mykeyvault.env`; the `bw` backend is ~8 s, too slow for the synchronous startup path). Only the first run without a stored header reads the vault synchronously. If neither a header nor the vault yields a token, ai-rem returns `401`.

Generate a token manually (if not using the vault): `openssl rand -hex 32`.

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

# Create .env and configure
cp .env.example .env
# → set KG_PUBLIC_URL to your actual server IP
# → set AI_REM_API_TOKEN (required) — e.g. `openssl rand -hex 32`, or use deploy.sh
#   to pull it from mykeyvault automatically (see Authentication)

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
3. `~/.claude/hooks/system-check.py` — deploy consolidated SessionStart hook (ai-rem health, SMB mount, MCP server tests, settings sync, tool count, open tasks/plans)
4. `~/.claude/hooks/auto-memory.py` — deploy PreCompact + SessionEnd hook (transcript → `ai-rem ingest` → Ollama-Extraktor → structured entities)
5. `~/.claude/hooks/claude-md-guard.py` — deploy PreToolUse hook that warns (non-blocking) when `~/.claude/CLAUDE.md` is edited, so rules/knowledge go into ai-rem instead of silently accumulating in CLAUDE.md
6. `~/.claude/settings.json` — add permissions, deny rules, SessionStart hook, PreCompact + SessionEnd hooks, PreToolUse guard hook; remove old hooks; set `autoMemoryEnabled: false`
7. `~/.claude/CLAUDE.md` — create or update minimal 3-line pointer to ai-rem
8. Install slash commands (`/setup-ai-rem`, `/ai-rem:prefedit`)
9. Create preferences & tool entities directly in the knowledge graph via MCP API

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

A **PreToolUse guard hook** (`claude-md-guard.py`, deployed by the setup script) reinforces this invariant: whenever `~/.claude/CLAUDE.md` is edited, it injects a non-blocking reminder to put rules/knowledge into ai-rem rather than letting them silently accumulate in CLAUDE.md. This replaces relying on a pinned preference for the same purpose.

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
  "smb": {"mount": "/path/to/mount", "url": "smb://server/share"},
  "mcp_stdio_servers": {"paperless": "/path/to/index.js"},
  "tools_scripts_dir": "/path/to/tools-mcp/scripts",
  "old_hooks": ["legacy-hook.sh"],
  "entities": [{"name": "...", "type": "Tool", "description": "..."}]
}
```

The Docker image copies this file at build time (`COPY setup-config*.json ./`). The personal `setup-config.json` is gitignored, so it never ships in the public image. Instead the repo includes a generic **`setup-config.example.json`**: when no personal config is present, `/setup-config` falls back to it, so a fresh deployment seeds a useful starter set of behavioural preferences (plan-first, answer concisely, check ai-rem before asking, avoid hallucinations, store knowledge proactively) plus generic permission/deny rules. Drop in your own `setup-config.json` to override the template entirely.

## Related Projects

- [tools-mcp](https://github.com/markus7h/tools-mcp) — MCP server exposing small scripts as tools via a central registry. ai-rem tracks a `Tool` entity per script (the `ai_rem_entity` convention) so the catalog stays discoverable.
- [mykeyvault](https://github.com/markus7h/mykeyvault) — self-hosted secrets vault (Vaultwarden + REST/MCP). ai-rem deliberately stores **no secrets**; credentials live in mykeyvault instead.
