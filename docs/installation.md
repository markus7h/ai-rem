# Installation details

[← Back to README](../README.md)

The README covers the quick start (server one-time setup + client one-liner). This page
documents what the client setup script actually does, the repo layout, and the CLAUDE.md
strategy it manages.

## What the client setup script does

`bash <(curl -s http://<SERVER_IP>:3456/setup)` (or `irm http://<SERVER_IP>:3456/setup.ps1 | iex`
on native Windows) fetches the same platform-neutral logic (`/setup.py`, requires Python 3) —
behaviour is identical on macOS, Linux, WSL and Windows. On Windows the hooks are registered
as `python -X utf8 <hook>` commands and the optional SSH token pull uses the built-in OpenSSH
client (or pair the device in the browser, or set `$env:AI_REM_TOKEN` instead).

The script automatically handles:
1. `~/.claude.json` → `mcpServers.ai-rem` — register ai-rem as a user-scoped **stdio** MCP server: `{"type": "stdio", "command": "~/.local/share/ai-rem/bin/ai-rem", "args": ["mcp-proxy"]}`. The proxy reads the device token from the OS keychain and bridges to the HTTP endpoint, so the config holds no `headers.Authorization` ([Authentication](authentication.md#one-secret-per-device--the-os-keychain))
2. `~/.claude/settings-template.json` — (re)generate base template for permissions, deny rules and hooks from the live setup config
3. `~/.claude/hooks/system-check.py` — deploy consolidated SessionStart hook (ai-rem health, SMB mount, MCP server tests — only for the `mcp_stdio_servers` that are registered in `~/.claude.json` or come from an enabled plugin —, settings sync, tool count, vector coverage, open tasks/plans)
4. `~/.claude/hooks/auto-memory.py` — deploy PreCompact + SessionEnd hook (transcript → `ai-rem ingest` → llama-server extractor → structured entities)
5. `~/.local/share/ai-rem/bin/ai-rem` (plus `../lib/`, the modules the CLI imports) — install the CLI itself locally and point `AI_REM_CLI` in `~/.claude/settings.json` at it. A clone path already configured there is replaced: if the clone sits on a network share, the CLI is gone at session end as soon as the mount stalls, and the hook aborts silently (visible only in `~/.claude/auto-memory/errors.log`). A manually set `AI_REM_CLI` pointing at anything other than a clone is left alone. If the hook does not find the CLI there, it also looks under `~/myCode/github/ai-rem/bin/ai-rem`, `~/*/myCode/github/ai-rem/bin/ai-rem`, `/Volumes/*/myCode/…` and `PATH`. The setup also links `~/.local/bin/ai-rem` to that copy, so `ai-rem` works as a plain command in the shell; if `~/.local/bin` had to be created, the `PATH` on some systems only picks it up at the next login, and the setup prints the line to add if it is missing entirely.
6. `~/.claude/hooks/claude-md-guard.py` — deploy PreToolUse hook that warns (non-blocking) when `~/.claude/CLAUDE.md` is edited, so rules/knowledge go into ai-rem instead of silently accumulating in CLAUDE.md
7. `~/.claude/settings.json` — add permissions, deny rules, SessionStart hook, PreCompact + SessionEnd hooks, PreToolUse guard hook, PostToolUse vault-secret-reminder hook (only when mykeyvault is registered or enabled as a plugin — otherwise an existing entry is removed); remove old hooks; set `autoMemoryEnabled: false`
8. `~/.claude/CLAUDE.md` — create or update minimal 3-line pointer to ai-rem
9. Install slash commands (`/setup-ai-rem`, `/memory-cleanup`, `/migrate-claude-md`, `/ai-rem-update`)
10. Create preferences & tool entities directly in the knowledge graph via MCP API
11. **mykeyvault** — build locally (`git clone` + `npm run build` in the `mcp/` folder) and register it as a **stdio** MCP through the wrapper `ai-rem vault-mcp`, which fetches the vault URL and token from `/api/client-config` at start and `exec`s `node <mcp entry>` with them in its environment — no `env` block with a vault token in `~/.claude.json`, no `ai-rem-vault.env`. Local stdio mode unlocks the exec/file tools (`vault_write_secret`, `vault_run_with_secret`, `vault_run_with_secret_file`), so secrets **never** enter the LLM context — only the locally spawned subprocess. Without Node/Git or on build failure the setup falls back to the HTTP MCP via `ai-rem mcp-proxy --endpoint <vault mcp url>` (only `vault_list_items`/`vault_create_item`).

**ai-rem only:** without `mcp_register` stdio blocks (`mykeyvault.stdio`, `tools.stdio.registry_url`) the setup builds nothing and therefore neither checks for nor installs node/npm/git; without mykeyvault the `vault-secret-reminder.py` hook is skipped (and unregistered if a previous run added it), and the SessionStart check only tests stdio servers that are actually registered.

**Plugin mode:** if an `ai-rem@…` / `mykeyvault@…` plugin is enabled in `~/.claude/settings.json` → `enabledPlugins` (any marketplace, e.g. `ai-rem@tools-registry`), the plugin ships the MCP server itself (`ai-rem mcp-proxy` / `ai-rem vault-mcp`). The setup then does **not** create the same-named `mcpServers` entry in `~/.claude.json` and removes an existing one with a notice (`ai-rem update` too), so there are never two servers of the same name. Hooks and settings are installed as usual; an enabled `mykeyvault` plugin counts as registered.

**The only thing to remember:** the URL `<SERVER_IP>:3456/setup`. The script is idempotent — running it multiple times on the same machine is safe.

## Frontends (targets)

The steps above are the **`claude`** target. The setup picks targets with `--client`
(`bash <(curl -s …/setup) --client opencode`, or later `ai-rem install --client …`); the
default `auto` takes every frontend whose binary is on `PATH` and falls back to `generic`.
Installed targets are recorded in `~/.config/ai-rem/client.json` (`endpoint`, `targets`,
`vault_url`, `vault_entry` = path of the built mykeyvault MCP, `keychain` = the backend
holding the token). The file contains no secret: the device token lives in the OS keychain,
LLM-router key and vault token are fetched per run from `/api/client-config`.

**`opencode`** — the differences to Claude Code:

| | Claude Code | opencode |
|---|---|---|
| Config | `~/.claude.json` → `mcpServers` | `~/.config/opencode/opencode.json` → `mcp` |
| stdio server | `"type": "stdio"`, `command` + `args` | `"type": "local"`, `command` as **one array** |
| http server (not used for ai-rem since 1.7) | `"type": "http"`, `url` + `headers` | `"type": "remote"`, `url` + `headers` |
| Env per server | `env` | `environment` (`AI_REM_CLIENT=opencode` for attribution) |
| Instructions | `~/.claude/CLAUDE.md` | `~/.config/opencode/AGENTS.md` + `instructions[]` |
| Secrets | none in the config — `ai-rem mcp-proxy` / `ai-rem vault-mcp` resolve them at start (OS keychain, `/api/client-config`) | same wrappers, no `{file:…}` reference |
| Session hooks | SessionStart / PreCompact / SessionEnd | plugin events `session.created` / `session.idle` / `session.compacted` |

What the target writes:
1. `opencode.json`: merges `mcp.ai-rem` as a `local` server (`command: [ai-rem, mcp-proxy]`,
   `environment: {AI_REM_CLIENT: opencode}`) and — if node and the builds are available —
   `mykeyvault` (`[ai-rem, vault-mcp]`) and `tools` as `local` servers, with `node`
   resolved from `PATH` (Homebrew: `/opt/homebrew/bin/node`). Providers, models and
   other servers stay untouched; a one-time `opencode.json.pre-airem.bak` is kept. A JSONC
   file with comments is **not** rewritten — the block lands in
   `~/.config/ai-rem/snippets/opencode-mcp.json` instead. The auto-memory `fallback.md`
   goes into `instructions[]` (opencode has no `@` import).
2. `AGENTS.md`: a marked `<!-- ai-rem:begin/end -->` block telling the agent to call
   `memory_get_context()` at the start of every session — opencode has no session-start hook.
3. `plugin/ai-rem.ts`: exports the session via the SDK and runs `ai-rem ingest` detached,
   10 minutes after the last `session.idle` (only if new messages arrived) and immediately
   on `session.compacted`; on `session.created` it runs `ai-rem catchup` and, once per
   process, `ai-rem update --check` (toast on drift).
4. `command/*.md`: `/setup-ai-rem`, `/memory-cleanup`, `/ai-rem-update`.

**`generic`** — writes nothing outside `~/.config/ai-rem/snippets/`: ready-to-paste blocks
for Codex (`config.toml`, HTTP with `bearer_token_env_var`, token via
`export AI_REM_TOKEN="$(ai-rem token)"`), Gemini CLI (`settings.json`) and Cursor
(`mcp.json`) — both stdio via `{"command": "ai-rem", "args": ["mcp-proxy"]}` — plus an
`AGENTS.md` pointer. These frontends get the tools and the server instructions, but no
auto-memory.

`ai-rem uninstall --client <target>` removes what the target added (for opencode the
`ai-rem` server, the `instructions` entry, the `AGENTS.md` block, plugin and commands;
`mykeyvault`/`tools` stay).

## Keeping an existing installation current

Steps 3–9 above ship files that change with almost every other release. Re-running the
full setup for that is out of proportion — it redoes the SSH secret pull, the `git clone`
and the `npm` builds — so an installed client uses the CLI instead:

```bash
ai-rem update --check   # report only, exit 1 if anything is behind
ai-rem update           # pull the new files for every installed target, then restart the frontends
```

`GET /manifest` lists a SHA-256 for every file the server ships; the CLI hashes the local
counterparts and pulls only if they differ. Under the hood it runs the very same
`setup.py --update`, which does steps 2–9 and skips everything that needs SSH, git or npm.
The `system-check` SessionStart hook runs the same comparison and reports a lag on its own,
so in practice you are told before you have to ask.

`settings.json` is only ever added to — new permissions and hook groups are merged in,
nothing is removed. The `settings-template.json` it merges from belongs to the server and
**is** rewritten wholesale, so keep your own changes in `settings.json`.

Hooks are loaded at startup only: restart Claude Code after an update.

**Always run it through the server URL, not from a clone.** `scripts/setup.py` carries `KG_URL` as a placeholder that `server.py` substitutes when serving the file. Started straight out of a checkout the placeholder survives, and step 1 registers a literal `__KG_URL__/mcp` in `~/.claude.json` — the MCP server then never connects (`INVALID_CONFIG: 'url' is not a valid URL`). The script now refuses to run in that state (exit 2). To test it from a clone anyway, set `KG_URL` in the environment.

## Files

```
ai-rem/
├── server.py                   # MCP server (FastMCP + LadybugDB + web UI + backup + cleanup
│                               #   + embedded setup.py/bash/PS1 scripts and hooks)
├── bin/ai-rem                  # CLI (status/search/ingest/catchup, pure stdlib, no venv)
├── lib/                        # extractor (+ md-fallback/catchup), heuristic, mcp_client
├── hooks/save-plan.py          # PostToolUse hook: ExitPlanMode → open Task in ai-rem
├── hooks/vault-secret-reminder.py  # PostToolUse hook: Bash → vault-secret reminder on auth failure
├── docs/                       # architecture (md + Mermaid + PDF), MCP function docs,
│                               #   release-history.md (archived notes ≤ v0.1.5)
├── deploy.sh                   # Deploy to the home server (scp + remote build + recreate)
├── .github/workflows/          # Docker Hub publish on v* tags
├── requirements.txt            # fastmcp, ladybug, numpy, cryptography
├── requirements-embed.txt      # fastembed — only installed in the full image, not in :slim
├── Dockerfile
├── docker-compose.yml
├── .env.example                # Configuration template
├── .env                        # Configuration (not in repo, derived from .env.example)
├── setup-config.json           # Personal configuration (gitignored)
├── setup-config.example.json   # Generic starter template (served when no personal config)
├── .claude/settings.json.example  # Example repo-local Claude permissions
├── .claude/settings.json       # Your local Claude permissions (gitignored; copy from .example)
├── README.md                   # English documentation
└── README.de.md                # German documentation
```

> `.claude/settings.json` is **gitignored** so personal/local permission tweaks never land in
> the repo. Copy the template to get started: `cp .claude/settings.json.example .claude/settings.json`.

## CLAUDE.md strategy

The setup script writes only a **minimal pointer** to `~/.claude/CLAUDE.md`:

```markdown
## ai-rem
ai-rem is the only knowledge source for persistent context. Claude Code's native markdown auto-memory is disabled.
Usage rules come via MCP Server Instructions, behavioural rules from ai-rem Preferences.

<!-- Auto-memory md-fallback: filled when llama-server is down, emptied by catchup -->
@~/.claude/auto-memory/fallback.md
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
