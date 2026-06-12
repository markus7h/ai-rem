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
  "smb": {"mount": "/path/to/mount", "url": "smb://server/share"},
  "mcp_register": {
    "mykeyvault": {"http": {"url": "http://server:3458/mcp", "https_url": "https://keyvault.example/mcp"}, "vault_url": "http://server:8223"},
    "tools": {"stdio": {"repo": "https://github.com/markus7h/tools-mcp.git", "install_dir": "~/Code/tools-mcp", "entry": "dist/index.js", "registry_url": "http://server:3457"}}
  },
  "old_hooks": ["legacy-hook.sh"],
  "entities": [{"name": "...", "type": "Tool", "description": "..."}]
}
```

The Docker image copies this file at build time (`COPY setup-config*.json ./`). The personal `setup-config.json` is gitignored, so it never ships in the public image. Instead the repo includes a generic **`setup-config.example.json`**: when no personal config is present, `/setup-config` falls back to it, so a fresh deployment seeds a useful starter set of behavioural preferences plus generic permission/deny rules. Drop in your own `setup-config.json` to override the template entirely.

**`mcp_register`** lets the setup wire up companion MCP servers using tokens it pulls from `ssh_host` over SSH:
- **mykeyvault** is registered as an HTTP MCP from `http.url` (or `https_url` when ai-rem itself runs over a trusted https endpoint).
- **tools** ([tools-mcp](https://github.com/markus7h/tools-mcp)) is cloned from `stdio.repo`, built with `npm`, and registered as a stdio MCP with `TOOLS_MCP_REGISTRY_URL=stdio.registry_url`. This requires **node, npm and git** on the client — if any are missing the setup prints an install hint and skips `tools` (everything else still completes).
