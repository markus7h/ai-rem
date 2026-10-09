# Personal configuration (setup-config.json)

[← Back to README](../README.md)

Runtime environment variables (`AI_REM_API_TOKEN`, `KG_PUBLIC_URL`, `PORT`, …) are
documented in the README's **Configuration** section. This page covers the optional
**`setup-config.json`** that drives client onboarding.

The setup endpoint optionally loads a `setup-config.json` from the server (`GET /setup-config`, public because onboarding runs before the first token — every top-level field ending in `_key`, `_token`, `_secret` or `_password` is filtered out of that response; `_comment_*` keys stay). This file is **not in the repo** — it contains personal settings:

```json
{
  "ssh_host": "your-server",
  "ssh_user": "your-user",
  "ssh_hostname": "your-server.lan",
  "permissions_allow_portable": ["Bash", "mcp__tools__*", ...],
  "permissions_deny": ["Bash(bw get *)", ...],
  "permissions_default_mode": "plan",
  "smb": {"mount": "/path/to/mount", "url": "smb://server/share"},
  "mcp_register": {
    "mykeyvault": {"http": {"url": "http://server:3458/mcp", "https_url": "https://keyvault.example/mcp"}, "vault_url": "http://server:8223"},
    "tools": {"stdio": {"repo": "https://github.com/markus7h/tools-registry.git", "install_dir": "~/Code/tools-registry", "entry": "dist/index.js", "registry_url": "http://server:3457"}}
  },
  "old_hooks": ["legacy-hook.sh"],
  "entities": [{"name": "...", "type": "Tool", "description": "..."}]
}
```

The personal `setup-config.json` is gitignored, so it never ships in the public image. It is bind-mounted into the container from the deployment directory instead (`./setup-config.json:/app/setup-config.json:ro` in `docker-compose.yml`); the Dockerfile's `COPY setup-config*.json ./` only applies to local builds. **Without that mount a container started from the Docker Hub image serves the example placeholders** (including `ollama_url: http://your-server:11434`), and every fresh client install inherits a dead llama URL in its `settings-template.json`. Instead the repo includes a generic **`setup-config.example.json`**: when no personal config is present, `/setup-config` falls back to it, so a fresh deployment seeds a useful starter set of behavioural preferences plus generic permission/deny rules. Drop in your own `setup-config.json` to override the template entirely.

**`permissions_default_mode`** seeds `permissions.defaultMode` (default `plan`). The template also sets `skipAutoPermissionPrompt` and `useAutoModeDuringPlan`, so plan mode routes shell commands through the auto-mode classifier instead of prompting per command — writes stay blocked and the plan itself still needs the user's approval.

**`llm_api_key`** (optional) — bearer token for the LLM router behind `ollama_url`. It is never written to a client: clients fetch it per run from `/api/client-config` (below). Prefer setting it on the server as the env `AI_REM_LLM_API_KEY`, which takes precedence over this field; then the key is not in any file that leaves the server. Use a virtual key with a budget, not the master key.

**`mcp_register`** lets the setup wire up companion MCP servers:
- **mykeyvault** is built from `stdio.repo` and registered as a stdio MCP through `ai-rem vault-mcp`, which fetches `vault_url`/`vault_token` from `/api/client-config` at start (the server serves them from `AI_REM_PAIR_VAULT_TOKEN` and `vault_url`). Without node or on build failure it falls back to the HTTP MCP from `http.url` (or `https_url` when ai-rem itself runs over a trusted https endpoint) via `ai-rem mcp-proxy --endpoint`.
- **tools** ([tools-registry](https://github.com/markus7h/tools-registry)) is cloned from `stdio.repo`, built with `npm`, and registered as a stdio MCP with `TOOLS_REGISTRY_URL=stdio.registry_url`. This requires **node, npm and git** on the client — if any are missing the setup prints an install hint and skips `tools` (everything else still completes).

## Runtime config for clients — `GET /api/client-config`

Since 1.7 a workstation stores exactly one secret, the device token in the OS keychain (see
[Authentication](authentication.md#one-secret-per-device--the-os-keychain)). Everything else
is fetched per run, authenticated with that token:

```json
{"version": "1.7.0", "llm_url": "http://router:11437", "llm_api_key": "…", "vault_url": "http://server:8223", "vault_token": "…"}
```

- `llm_url` — `AI_REM_OLLAMA_URL`/`AI_REM_LLAMA_URL` on the server, else `ollama_url` from the setup-config.
- `llm_api_key` — server env `AI_REM_LLM_API_KEY` (preferred), else `llm_api_key` from the setup-config.
- `vault_url`, `vault_token` — `AI_REM_PAIR_VAULT_TOKEN` and the vault URL from `mcp_register.mykeyvault`; empty when the variable is not set, then mykeyvault stays on HTTP without the exec/file tools.

Consumers: `ai-rem ingest`/`catchup` (extractor), the `system-check.py` SessionStart hook (router probe) and `ai-rem vault-mcp`. Nothing is persisted, so rotating the LLM key or the vault token is a change in the server's `.env` followed by a restart — no workstation needs touching. `AI_REM_LLAMA_URL`/`AI_REM_LLM_API_KEY` in a client's environment still override the served values; against a server < 1.7 the clients fall back to `settings-template.json` and say so.
