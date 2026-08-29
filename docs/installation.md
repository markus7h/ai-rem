# Installation details

[← Back to README](../README.md)

The README covers the quick start (server one-time setup + client one-liner). This page
documents what the client setup script actually does, the repo layout, and the CLAUDE.md
strategy it manages.

## What the client setup script does

`bash <(curl -s http://<SERVER_IP>:3456/setup)` (or `irm http://<SERVER_IP>:3456/setup.ps1 | iex`
on native Windows) fetches the same platform-neutral logic (`/setup.py`, requires Python 3) —
behaviour is identical on macOS, Linux, WSL and Windows. On Windows the hooks are registered
as `python -X utf8 <hook>` commands and the secret pull uses the built-in OpenSSH client
(or set `$env:AI_REM_TOKEN` instead).

The script automatically handles:
1. `claude mcp add` — register ai-rem as a user-scoped HTTP MCP server
2. `~/.claude/settings-template.json` — (re)generate base template for permissions, deny rules and hooks from the live setup config
3. `~/.claude/hooks/system-check.py` — deploy consolidated SessionStart hook (ai-rem health, SMB mount, MCP server tests, settings sync, tool count, vector coverage, open tasks/plans)
4. `~/.claude/hooks/auto-memory.py` — deploy PreCompact + SessionEnd hook (transcript → `ai-rem ingest` → llama-server extractor → structured entities)
5. `~/.local/share/ai-rem/bin/ai-rem` (plus `../lib/`, the modules the CLI imports) — install the CLI itself locally and point `AI_REM_CLI` in `~/.claude/settings.json` at it. A clone path already configured there is replaced: if the clone sits on a network share, the CLI is gone at session end as soon as the mount stalls, and the hook aborts silently (visible only in `~/.claude/auto-memory/errors.log`). A manually set `AI_REM_CLI` pointing at anything other than a clone is left alone. If the hook does not find the CLI there, it also looks under `~/myCode/github/ai-rem/bin/ai-rem`, `~/*/myCode/github/ai-rem/bin/ai-rem`, `/Volumes/*/myCode/…` and `PATH`.
6. `~/.claude/hooks/claude-md-guard.py` — deploy PreToolUse hook that warns (non-blocking) when `~/.claude/CLAUDE.md` is edited, so rules/knowledge go into ai-rem instead of silently accumulating in CLAUDE.md
7. `~/.claude/settings.json` — add permissions, deny rules, SessionStart hook, PreCompact + SessionEnd hooks, PreToolUse guard hook; remove old hooks; set `autoMemoryEnabled: false`
8. `~/.claude/CLAUDE.md` — create or update minimal 3-line pointer to ai-rem
9. Install slash commands (`/setup-ai-rem`, `/memory-cleanup`, `/migrate-claude-md`)
10. Create preferences & tool entities directly in the knowledge graph via MCP API
11. **mykeyvault** — build and register locally as a **stdio** MCP (`git clone` + `npm run build` in the `mcp/` folder). Local stdio mode unlocks the exec/file tools (`vault_write_secret`, `vault_run_with_secret`, `vault_run_with_secret_file`), so secrets **never** enter the LLM context — only the locally spawned subprocess. Without Node/Git or on build failure the setup falls back to the HTTP MCP (only `vault_list_items`/`vault_create_item`).

**The only thing to remember:** the URL `<SERVER_IP>:3456/setup`. The script is idempotent — running it multiple times on the same machine is safe.

## Files

```
ai-rem/
├── server.py                   # MCP server (FastMCP + Kuzu + web UI + backup + cleanup
│                               #   + embedded setup.py/bash/PS1 scripts and hooks)
├── bin/ai-rem                  # CLI (status/search/ingest/catchup, pure stdlib, no venv)
├── lib/                        # extractor (+ md-fallback/catchup), heuristic, mcp_client
├── hooks/save-plan.py          # PostToolUse hook: ExitPlanMode → open Task in ai-rem
├── docs/                       # architecture (md + Mermaid + PDF), MCP function docs,
│                               #   release-history.md (archived notes ≤ v0.1.5)
├── deploy.sh                   # Deploy to the home server (scp + remote build + recreate)
├── .github/workflows/          # Docker Hub publish on v* tags
├── requirements.txt            # fastmcp, kuzu, numpy, cryptography
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
