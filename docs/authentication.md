# Authentication

[← Back to README](../README.md)

All sensitive routes (`/mcp`, `/api/*`, `/export`, `/import`, `/ui`) require
authentication. The server is **fail-closed**: without `AI_REM_API_TOKEN` it
refuses to start. A request is authorized if **any** of these holds:

1. the path is public — `/health`, `/setup`, `/setup.py`, `/setup.ps1`, `/install`, `/setup-config`, `/hooks/*`, `/cmd*`, `/login` (onboarding/login only, no private data);
2. it originates from **loopback** *and the request is not proxied* (no `X-Forwarded-For`). In a bridge-network container this effectively only covers in-container traffic (e.g. the healthcheck): tunneled/proxied requests arrive as the Docker gateway IP, not loopback. Behind a same-host reverse proxy (e.g. Caddy) the peer is `127.0.0.1` but `X-Forwarded-For` is set, so the token is still required;
3. it carries `Authorization: Bearer <AI_REM_API_TOKEN>` (constant-time compared) — used by MCP clients (Claude's `/mcp` channel);
4. it carries a valid `ai_rem_session` cookie — used by the browser Web UI (see below).

## Web UI login

A browser cannot set an `Authorization` header when navigating, so the Web UI
uses a cookie. Open `/login`, enter the API token once, and the server sets an
**HttpOnly, Secure, SameSite=Strict** cookie that authorizes `/ui` and its
`/api/*` calls; `/logout` clears it. The cookie value is **not** the raw token
but a derived, UI-scoped value (`HMAC-SHA256(token, "ai-rem-ui-session")`), so the
`/mcp` Bearer never reaches the browser and the session auto-invalidates when the
token rotates. Because the cookie is `Secure`, the UI must be reached over HTTPS
(e.g. a Caddy `tls internal` vhost). Lifetime defaults to 30 days
(`AI_REM_UI_SESSION_TTL`, in seconds).

## Token source — [mykeyvault](https://github.com/markus7h/mykeyvault)

The token is stored once in the vault as item `ai-rem-api-token` (single source of truth).
- **Server:** `deploy.sh` pulls it from the vault at deploy time and writes it into the remote `.env` — server startup stays independent of the vault's runtime state.
- **Clients:** the `system-check.py` SessionStart hook uses the bearer token already stored in `~/.claude.json` → `mcpServers."ai-rem".headers.Authorization` for the current session (fast, no vault roundtrip — that is also how Claude's `/mcp` channel carries it) and refreshes it from the vault in a **detached background process** for the next session (vault-api coordinates live in `~/.claude.json` → `mcpServers.mykeyvault.env`; the `bw` backend is ~8 s, too slow for the synchronous startup path). Only the first run without a stored header reads the vault synchronously. If neither a header nor the vault yields a token, ai-rem returns `401`.

Generate a token manually (if not using the vault): `openssl rand -hex 32`.
