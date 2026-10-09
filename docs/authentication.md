# Authentication

[← Back to README](../README.md)

All sensitive routes (`/mcp`, `/api/*`, `/export`, `/import`, `/ui`) require
authentication. The server is **fail-closed**: without `AI_REM_API_TOKEN` it
refuses to start. A request is authorized if **any** of these holds:

1. the path is public — `/health`, `/setup`, `/setup.py`, `/setup.ps1`, `/install`, `/setup-config` (secret fields filtered out), `/hooks/*`, `/bin/*`, `/lib/*`, `/cmd*`, `/login` (onboarding/login only, no private data);
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

## Pairing a new machine (no SSH needed)

The installer gets its token the way `gh auth login` does: it starts a pairing
(`POST /api/pair/start`), shows a code like `K7QF-2M9X` and opens
`https://<server>/pair?code=…`. You approve it in the web UI — logged in, from any
device including your phone — and the installer collects the token exactly once
(`POST /api/pair/poll`). A pairing expires after 10 minutes, starts are rate-limited per
IP, approval needs the UI login, and every decision is logged in `/pair` ("recently
paired devices", `backups/paired-devices.json`).

What it hands over: the ai-rem token — and nothing else is stored on the machine. The
installer puts it into the OS keychain; everything else a client needs (LLM-router key,
mykeyvault URL and token) comes per run from `GET /api/client-config`, authenticated with
that token (see [Configuration](configuration.md)). `/api/pair/poll` still returns
`vault_token`/`vault_url` when `AI_REM_PAIR_VAULT_TOKEN` is set — deprecated, only for
installers older than 1.7; the current installer ignores them.

Order the installer tries: `AI_REM_TOKEN` env → SSH pull from `ssh_host` (silent, if an SSH
key is set up) → token already in the keychain → pairing → paste prompt. `ai-rem pair`
skips the keychain step and renews just the token on an installed machine — that is also
how you rotate a device token.

## One secret per device — the OS keychain

Since 1.7 the ai-rem token is the only secret on a workstation, and it lives in the
platform keychain (`lib/keychain.py`, shipped with the CLI): service `ai-rem`, account
`device-token` (`AI_REM_KEYCHAIN_ACCOUNT` overrides the account, e.g. for tests).

| Platform | Backend | Notes |
|---|---|---|
| macOS | Keychain (`security`) | The command goes to `security -i` over stdin, so the token never appears in `ps`. Items created this way are readable later without a GUI prompt; in an SSH session without GUI login run `security unlock-keychain` first |
| Linux | libsecret (`secret-tool`) | GNOME Keyring, KWallet via portal. Without a D-Bus session or an unlocked keyring the store falls back to the file backend and says so |
| Linux fallback | File `$XDG_CONFIG_HOME/ai-rem/keyring/<account>` (0600 in a 0700 dir) | Used when `secret-tool` is missing or has no session. `ai-rem doctor` and `client.json` → `keychain` show `Datei (0600) …` so the degradation stays visible |
| Windows | Credential Manager (`advapi32`) | Target `ai-rem/<account>`, UTF-16LE blob — what `cmdkey`/PowerShell expect |

Every consumer resolves the token in the same order:
1. `AI_REM_TOKEN` in the environment — a deliberate override (CI, one-off scripts);
2. the keychain;
3. legacy plain text, **read only**: `token_file` from an old `client.json`, then
   `mcpServers.ai-rem.headers.Authorization` in `~/.claude.json`. Both print a hint that
   `ai-rem update` migrates them; nothing writes there any more.

`ai-rem token` prints the resolved token (for `/login` or `curl`), `echo <token> | ai-rem
token --store` writes it to the keychain (stdin, never argv), `ai-rem token --forget`
removes it. The SessionStart hook reports the source as `token ✓ (Keychain)`,
`token ✓ (Env)`, `token ⚠ Klartext-Legacy → ai-rem update` or `token ❌ fehlt → ai-rem pair`.

The MCP frontends never see the token either: Claude Code and opencode start `ai-rem
mcp-proxy`, a stdio MCP server that reads the keychain and bridges to the HTTP endpoint,
and `ai-rem vault-mcp`, which fetches the vault access from `/api/client-config` and
`exec`s the mykeyvault MCP with it in its environment. A `401` from the server surfaces as
a JSON-RPC error with the hint `ai-rem pair`.

## Token source on the server — [mykeyvault](https://github.com/markus7h/mykeyvault)

The token is stored once in the vault as item `ai-rem-api-token` (single source of truth).
`deploy.sh` pulls it from the vault at deploy time and writes it into the remote `.env` —
server startup stays independent of the vault's runtime state. Clients no longer talk to
the vault for the ai-rem token at all; they get it through pairing (or SSH) once and keep
it in the keychain.

Generate a token manually (if not using the vault): `openssl rand -hex 32`.
