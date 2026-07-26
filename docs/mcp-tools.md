# MCP Tool Reference (ai-rem)

[← Back to README](../README.md) · German workflow reference: [`mcp-functions.md`](./mcp-functions.md)

The ai-rem MCP server keeps a **deliberately small MCP surface** (Issue #32): only the
four always-on tools below are listed in `tools/list`, so they cost very little
per-session context. At the start of each session Claude loads the relevant context via
`memory_get_context()` and saves new insights proactively with `memory_add` / `memory_relate`.

The remaining twelve operations are **admin ops** — they stay full Python functions but are
no longer MCP tools. Reach them over HTTP via the generic dispatch route
`POST /api/tool` (body `{"name": "<tool>", "arguments": {…}}`, same Bearer auth as every
`/api/*` route) or, more conveniently, via the [`ai-rem` CLI](../bin/ai-rem) which wraps that
route. Setting `AI_REM_ADMIN_TOOLS=1` on the server re-registers all twelve as MCP tools
(escape hatch).

## Always-on MCP tools (4)

| Tool | Description |
|---|---|
| `memory_get_context(topic, context, include_archived)` | Load relevant subgraph (tasks, projects, decisions, preferences) |
| `memory_search(query, context, include_archived, limit)` | Hybrid search over name + description: per-token lexical matching plus semantic vector recall (finds multi-word queries even when the words aren't contiguous) |
| `memory_add(name, type, description, extra, context, pinned, supersedes)` | Create or update an entity. `pinned=True` → preference always appears at the top in `get_context`. Updating a changed `description` snapshots the previous state into `extra.history[]` (last 10, newest first). `supersedes="<old name>"` archives that entry and links it via `VERALTET_DURCH` |
| `memory_relate(from, relation, to, extra)` | Create a relationship between two entities |

## Admin ops — via `ai-rem <cmd>` / `POST /api/tool` (12)

| Tool / CLI subcommand | Description |
|---|---|
| `memory_search_full` · `search-full` | Like `memory_search`, but returns the full description without the 400-char truncation |
| `memory_list` · `list` | List all entities |
| `memory_get_relations` · `relations` | Show all relationships of an entity |
| `memory_preference_update` · `preference-update` | Update preference fields without overwriting the description |
| `memory_project_context` · `project-context` | Load a project's full working context in one call: untruncated record incl. `extra` (paths/skills/rules) **plus** all directly related entities |
| `memory_set_project_context` · `set-project-context` | Create/update a project's working context as a `Project` entity — **field-wise merge** (omitted fields are kept; `""`/`[]` clears one) |
| `memory_archive` · `archive` | Archive an entry instead of deleting it — hidden from context/search/list by default, optionally compressed and linked via `VERALTET_DURCH` |
| `memory_merge` · `merge` | Fold a duplicate into the canonical entry: relations repointed, unique info appended, duplicate archived and linked via `DUPLIKAT_VON` |
| `memory_delete` · `delete --yes` | Remove an entity and its relationships |
| `memory_purge_archived` · `purge-archived --yes` | Permanently delete archived entries (destructive); `--keep-days N` spares recent ones |
| `memory_status` · `status` | Quick status: number of entities and relations |
| `memory_check_update` · `check-update` | Show the installed version and check Docker Hub for a newer one |

`memory_get_context`, `memory_search` and `memory_list` hide archived entries by
default — opt in with `include_archived=true`.

## Entity Types

`Person` · `Project` · `Task` · `Tool` · `Problem` · `Solution` · `Decision` · `Preference` · `Topic`

## Context Separation

Each entity can be tagged with a `context`:
- `context="work"` — only visible in work sessions
- `context="private"` — only visible in private sessions
- no tag — global, appears in all queries

The context can be set per CLAUDE.md: e.g. `context="work"` for work repositories and `context="private"` for personal projects.
