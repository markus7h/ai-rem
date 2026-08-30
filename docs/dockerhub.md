# ai-rem — Knowledge Graph Memory for Claude

**Persistent knowledge-graph memory for Claude Code — self-hosted MCP server.**

ai-rem gives Claude Code a long-term memory that lives on **your** home server: open tasks,
decisions, solved problems, projects and tools are stored in a knowledge graph and loaded
*selectively* per session, instead of stuffing everything into `CLAUDE.md` on every machine.
Available from any machine, independent of where you work.

```bash
docker pull magic3arkus/ai-rem
```

- **Source & full docs:** https://github.com/markus7h/ai-rem
- **Supported tags:** `latest`, `vX.Y.Z` — full image, embeddings run in-process, no external service needed.
  `latest-slim`, `vX.Y.Z-slim` — same code without the bundled embedding model (~250 MB smaller, 413 → 162 MB); needs `EMBED_URL` pointing at an OpenAI-compatible `/v1/embeddings` endpoint, otherwise search stays purely lexical. One pair per release — see [GitHub Releases](https://github.com/markus7h/ai-rem/releases)
- **Platforms:** `linux/amd64`, `linux/arm64`

---

## Why ai-rem?

Static memory files like `CLAUDE.md` sit in context in full and are tied to individual
projects and machines. ai-rem **lazy-loads** only the relevant subgraph on demand, so the
per-session footprint stays roughly constant (~1–3k tokens) no matter how large the graph
grows — working out to **~0.7 million tokens/month saved** at ~4.3 sessions/day, with the
savings growing as the graph grows.
([Methodology](https://github.com/markus7h/ai-rem/blob/main/docs/token-savings.md))

**Built on:** [FastMCP](https://gofastmcp.com) (HTTP MCP server) + [Kuzu](https://kuzudb.com)
(embedded graph DB — no separate DB container). Data persists in `/data`, backups in `/backups`.

---

## Quick start

### 1. Server (one-time setup)

```bash
mkdir -p ~/mydocker/compose-files/ai-rem && cd ~/mydocker/compose-files/ai-rem
curl -O https://raw.githubusercontent.com/markus7h/ai-rem/main/docker-compose.yml
curl -O https://raw.githubusercontent.com/markus7h/ai-rem/main/.env.example
cp .env.example .env
# → set KG_PUBLIC_URL to your server IP
# → set AI_REM_API_TOKEN (required, fail-closed) — e.g. `openssl rand -hex 32`
docker compose pull && docker compose up -d
```

The server is **fail-closed**: without `AI_REM_API_TOKEN` it refuses to start. MCP clients
authenticate with `Authorization: Bearer <token>`; the browser Web UI uses a derived,
HttpOnly cookie set at `/login`.
([Auth model](https://github.com/markus7h/ai-rem/blob/main/docs/authentication.md))

### 2. Client (each machine) — say this to Claude Code

```
Run: bash <(curl -s http://<SERVER_IP>:3456/setup)
```

On native Windows (PowerShell, no WSL): `irm http://<SERVER_IP>:3456/setup.ps1 | iex`.
The idempotent setup registers the MCP server, deploys the hooks, writes a minimal
`CLAUDE.md` pointer and installs the slash commands.
([Setup details](https://github.com/markus7h/ai-rem/blob/main/docs/installation.md))

---

## Key features

- **Knowledge-graph memory** — typed entities (`Project · Task · Decision · Preference · …`) and relations; hybrid lexical + semantic search. ([Tool reference](https://github.com/markus7h/ai-rem/blob/main/docs/mcp-tools.md))
- **Lazy-loaded context** — only the relevant subgraph is loaded per session; cost stays flat as the graph grows.
- **Cross-machine** — one server, available from every client; no per-repo `CLAUDE.md` ballast.
- **Web UI** — `/ui` backups (manual/scheduled/restore), `/browse` interactive content browser, `/graph` node-link visualization, `/prefs` preferences manager, `/cleanup` maintenance + archive purge, `/install` onboarding.
- **OKF interop** — export/import the whole graph as an [Open Knowledge Format](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing/) v0.1 bundle (`/export/okf`, `/api/import/okf`); imported entries are indexed for semantic search.
- **Auto-Memory** — a session-end hook extracts structured entities/relations from each transcript via llama-server (with an offline md-fallback + catch-up).
- **Nightly cleanup** — non-destructive dedup/archive (never deletes; preferences & pinned untouched), ambiguous cases go to a review queue.
- **Plan saving** — finalized plans become open `Task`s, a central cross-machine to-do list.

([Hooks & automation](https://github.com/markus7h/ai-rem/blob/main/docs/hooks-and-automation.md))

---

## Configuration (env)

Set in the Compose `.env`:

| Variable | Default | Purpose |
|---|---|---|
| `AI_REM_API_TOKEN` | — (**required**) | API token; server is fail-closed without it |
| `KG_PUBLIC_URL` | — | Public URL of the server |
| `PORT` | `3456` | TCP port |
| `HOST` | `::` | Bind address. The socket is dual-stack (`IPV6_V6ONLY=0`), so the container is reachable over IPv6 *and* the published IPv4 port. `0.0.0.0` for IPv4 only |
| `KUZU_DB_PATH` | `/data/kg.db` | Database path |
| `BACKUP_DIR` | `/backups` | Backup files |
| `MAX_BACKUPS` | `10` | Backups to keep |
| `AI_REM_OLLAMA_URL` | `http://myai:11436` | llama-server (OpenAI-compatible) for nightly cleanup / extraction |
| `EMBED_URL` | — | Embedding backend. Empty = in-process (fastembed/MiniLM, bundled in `latest`). Set to an OpenAI-compatible `/v1/embeddings` URL to use an external service — required for `-slim` images. Switching backends re-computes all vectors on the next start; if the endpoint is down, entries are stored without a vector and the backfill catches up later |
| `EMBED_HTTP_MODEL` | `bge-m3` | Model name sent to `EMBED_URL` |
| `EMBED_THRESHOLD` | `0.45` / `0.50` | Cosine cut-off for semantic hits. Default depends on the backend (in-process / `EMBED_URL`) |
| `EMBED_MAX_CHARS` | `2000` | Input is truncated to this length before embedding. fastembed truncates silently at the model limit; llama.cpp rejects oversized input with HTTP 500 instead |
| `KUZU_BUFFER_POOL_SIZE_MB` | `256` | Kuzu buffer pool. **Raise to 768 when using `EMBED_URL`** — 1024-dim vectors make the backfill's WAL checkpoint fail at 256 MB, and the affected vectors are then lost on restart (the failure is retried once and logged as `ERROR`; `embed_pending` in `/api/status` shows what is missing) |
| `EMBED_ENABLED` | `1` | `0` disables semantic search entirely (lexical only) |

---

## What's new

The three most recent releases; the block is regenerated from
[CHANGELOG.md](https://github.com/markus7h/ai-rem/blob/main/CHANGELOG.md) on every
tagged build. Full history: [GitHub Releases](https://github.com/markus7h/ai-rem/releases).

<!-- CHANGELOG:START -->
<!-- CHANGELOG:END -->

---

## Documentation

Full documentation lives on GitHub:

- **[README](https://github.com/markus7h/ai-rem/blob/main/README.md)** (English) · **[README.de](https://github.com/markus7h/ai-rem/blob/main/README.de.md)** (Deutsch)
- [Architecture](https://github.com/markus7h/ai-rem/blob/main/docs/architecture.md) · [MCP tool reference](https://github.com/markus7h/ai-rem/blob/main/docs/mcp-tools.md)
- [Token savings](https://github.com/markus7h/ai-rem/blob/main/docs/token-savings.md) · [Authentication](https://github.com/markus7h/ai-rem/blob/main/docs/authentication.md)
- [Hooks & automation](https://github.com/markus7h/ai-rem/blob/main/docs/hooks-and-automation.md) · [Configuration](https://github.com/markus7h/ai-rem/blob/main/docs/configuration.md) · [Installation details](https://github.com/markus7h/ai-rem/blob/main/docs/installation.md)

## Related projects

- [tools-registry](https://github.com/markus7h/tools-registry) — MCP server exposing small scripts as tools via a central registry.
- [mykeyvault](https://github.com/markus7h/mykeyvault) — self-hosted secrets vault. ai-rem deliberately stores **no secrets**; credentials live in mykeyvault instead.
