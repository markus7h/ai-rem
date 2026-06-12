# MCP Tool Reference (ai-rem)

[← Back to README](../README.md) · German workflow reference: [`mcp-functions.md`](./mcp-functions.md)

The ai-rem MCP server exposes the `memory_*` tool set below. At the start of each
session Claude loads the relevant context via `memory_get_context()` and saves new
insights proactively with `memory_add` / `memory_relate`.

## Available MCP Tools

| Tool | Description |
|---|---|
| `memory_add(name, type, description, extra, context, pinned)` | Create or update an entity. `pinned=True` → preference always appears at the top in `get_context` |
| `memory_preference_update(name, context, pinned, sort_order)` | Update preference fields without overwriting the description |
| `memory_relate(from, relation, to, extra)` | Create a relationship between two entities |
| `memory_search(query, context, include_archived, limit)` | Hybrid search over name + description: per-token lexical matching plus semantic vector recall (finds multi-word queries even when the words aren't contiguous) |
| `memory_search_full(query, context)` | Like `memory_search`, but returns the full description without the 400-char truncation |
| `memory_get_context(topic, context, include_archived)` | Load relevant subgraph (tasks, projects, decisions, preferences) |
| `memory_list(type, context, include_archived)` | List all entities |
| `memory_get_relations(name)` | Show all relationships of an entity |
| `memory_archive(name, compressed_description, superseded_by)` | Archive an entry instead of deleting it — hidden from context/search/list by default, optionally compressed and linked via `VERALTET_DURCH` |
| `memory_merge(canonical_name, duplicate_name)` | Fold a duplicate into the canonical entry: relations are repointed, unique info appended, the duplicate archived and linked via `DUPLIKAT_VON` |
| `memory_delete(name)` | Remove an entity and its relationships |
| `memory_status()` | Quick status: number of entities and relations (used by the SessionStart hook) |
| `memory_check_update()` | Show the installed version and check Docker Hub for a newer one |

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
