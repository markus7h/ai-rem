# ai-rem — Knowledge Graph Memory for Claude

> This documentation describes **[v0.4.23](https://github.com/markus7h/ai-rem/releases/tag/v0.4.23)**.
> Release notes live in the [GitHub Releases](https://github.com/markus7h/ai-rem/releases); notes for early versions (≤ v0.1.5) are archived in [docs/release-history.md](docs/release-history.md).

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

At the start of each session, Claude loads the relevant context from the graph via `memory_get_context()` and saves new insights proactively with `memory_add` / `memory_relate`. The graph holds typed **entities** — `Person · Project · Task · Tool · Problem · Solution · Decision · Preference · Topic` — and **relations** between them. Each entity can carry a `context` tag (`work` / `private` / global) so work and private knowledge stay separated per repo.

**Project context.** A project's working context — local dev dir, deployment dir/host, relevant skills and project-specific rules — lives in a `Project` entity's `extra`. Write it with `memory_set_project_context(...)` (field-wise merge, so you can add `skills` later without wiping `dev_dir`/`rules`) and load it whole in one call with `memory_project_context(name)` — the untruncated record plus every related entity. Use it to start a session "in the context of project X".

```jsonc
// extra schema (all fields optional)
{ "status": "aktiv", "dev_dir": "...", "repo": "...", "deploy_dir": "...",
  "deploy_host": "...", "deploy_cmd": "...", "skills": [...], "rules": [...] }
```

→ **[MCP tool reference](docs/mcp-tools.md)** — the full `memory_*` tool set (15 tools) with signatures, entity types and context separation.

---

## Token savings

ai-rem **lazy-loads** only the relevant subgraph on demand instead of carrying everything in `CLAUDE.md` all session long. The per-session footprint stays roughly constant (~1–3k tokens) no matter how large the graph grows. At ~4.3 sessions/day this works out to **~0.7 million tokens/month saved** — roughly **3 full 200k context windows** — and the savings *grow* as the graph grows, because ai-rem's cost stays flat while a `CLAUDE.md`-everything approach scales linearly.

→ **[Full methodology, measurements and ranges](docs/token-savings.md)**

---

## Web UI

| URL | Function |
|---|---|
| `/ui` | Backup management: manual, schedule, download, restore (export v2 round-trips `pinned`/`sort_order`/`archived`); header shows the server version |
| `/prefs` | Preferences manager: pin, context, sort order, delete; archived preferences are dimmed, badged and listed below a separator (they never load into session context). Accessible via `/ai-rem:prefedit` |
| `/cleanup` | Nightly cleanup: config, manual run, pending reviews, run log |
| `/install` | Client setup commands per platform (bash / PowerShell) with copy buttons, incl. step-by-step SSH key guide — public, for onboarding new machines |

---

## Automation (hooks)

Three Claude Code hooks — all deployed by the client setup — keep the graph fed and tidy:

- **Auto-Memory** — a `PreCompact`/`SessionEnd` hook extracts structured entities/relations from each transcript via Ollama, with an md-fallback + catch-up when Ollama is down.
- **Nightly cleanup** — a daemon dedups/archives outdated entries **non-destructively** (archive, never delete; preferences/pinned untouched), pushing ambiguous cases to a review queue.
- **Plan saving** — an `ExitPlanMode` hook stores every finalized plan as an open `Task`, so plans become a central, cross-machine list.

→ **[Hooks & automation in detail](docs/hooks-and-automation.md)**

---

## Requirements

- Docker on the target server
- Claude Code CLI and **Python 3** on the client machine (the setup and the hooks run on Python)
- Network access to `<SERVER_IP>:<PORT>`
- Optional (only for the `tools` companion MCP): git, Node.js ≥ 18 incl. npm

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
AI_REM_BACKUP_KEY=...                     # Optional — encrypt backups (AES-256-GCM); empty = plaintext
KUZU_POOL_SIZE=4                         # Connection pool size
```

When `AI_REM_BACKUP_KEY` is set, backups are written encrypted with AES-256-GCM
(`backup_<ts>.json.enc`) and downloads return the encrypted blob, so the data
never leaves the server in clear text. Restore auto-detects encrypted and
plaintext backups. The key is kept in [mykeyvault](https://github.com/markus7h/mykeyvault)
(`ai-rem-backup-key`) and pulled in by `deploy.sh`. **Losing the passphrase makes
encrypted backups unrecoverable.**

Client onboarding can additionally be customised via a `setup-config.json` → **[Personal configuration](docs/configuration.md)**.

---

## Authentication

All sensitive routes (`/mcp`, `/api/*`, `/export`, `/import`, `/ui`) require a token. The server is **fail-closed** — without `AI_REM_API_TOKEN` it refuses to start. MCP clients authenticate with `Authorization: Bearer <token>`; the browser Web UI uses a derived, HttpOnly cookie set at `/login`. The token is kept once in [mykeyvault](https://github.com/markus7h/mykeyvault) (`ai-rem-api-token`) and pulled in by `deploy.sh` / the client hook.

→ **[Authentication model, Web UI login & token source](docs/authentication.md)**

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

On **native Windows** (PowerShell, no WSL needed): `irm http://<SERVER_IP>:3456/setup.ps1 | iex`.

The script is idempotent and registers the MCP server, deploys the three hooks, writes the minimal `CLAUDE.md` pointer and installs the slash commands.

→ **[What the setup does, repo layout & CLAUDE.md strategy](docs/installation.md)**

### Update to a new version

```bash
ssh your-server "cd ~/mydocker/compose-files/ai-rem && docker compose pull && docker compose up -d"
```

---

## Related Projects

- [tools-mcp](https://github.com/markus7h/tools-mcp) — MCP server exposing small scripts as tools via a central registry. ai-rem tracks a `Tool` entity per script (the `ai_rem_entity` convention) so the catalog stays discoverable.
- [mykeyvault](https://github.com/markus7h/mykeyvault) — self-hosted secrets vault (Vaultwarden + REST/MCP). ai-rem deliberately stores **no secrets**; credentials live in mykeyvault instead.
