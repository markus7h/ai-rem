# Personal configuration (setup-config.json)

[← Back to README](../README.md)

Runtime environment variables (`AI_REM_API_TOKEN`, `KG_PUBLIC_URL`, `PORT`, …) are
documented in the README's **Configuration** section. This page covers the optional
**`setup-config.json`** that drives client onboarding.

The setup endpoint optionally loads a `setup-config.json` from the server (`/setup-config`). This file is **not in the repo** — it contains personal settings:

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

**`mcp_register`** lets the setup wire up companion MCP servers using tokens it pulls from `ssh_host` over SSH:
- **mykeyvault** is registered as an HTTP MCP from `http.url` (or `https_url` when ai-rem itself runs over a trusted https endpoint).
- **tools** ([tools-registry](https://github.com/markus7h/tools-registry)) is cloned from `stdio.repo`, built with `npm`, and registered as a stdio MCP with `TOOLS_REGISTRY_URL=stdio.registry_url`. This requires **node, npm and git** on the client — if any are missing the setup prints an install hint and skips `tools` (everything else still completes).
