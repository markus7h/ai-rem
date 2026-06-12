# Hooks & automation

[← Back to README](../README.md)

ai-rem ships three Claude Code hooks that keep the graph fed and tidy without manual work:
**Auto-Memory** (session → graph), **Nightly cleanup** (dedup/archive), and **Plan saving**
(plans → open tasks). All three are deployed by the client setup script.

---

## Auto-Memory (PreCompact + SessionEnd → ai-rem)

The built-in Claude Code auto-memory (markdown file) is replaced by a transcript extractor that writes **structured entities and relations** into ai-rem.

**Flow:** `PreCompact` / `SessionEnd` hook → `ai-rem ingest --transcript <path>` → Ollama (qwen3:14b on `AI_REM_OLLAMA_URL`, default `http://localhost:11434`) extracts JSON → bulk-upsert via MCP → log to `~/.claude/auto-memory/<timestamp>.json`.

**CLI** (`bin/ai-rem`, own `.venv`):

```bash
ai-rem status
ai-rem search "auto-memory"
ai-rem show "<name>"   # full, untruncated description + extra + relations (via /export)
ai-rem list --type Decision
ai-rem ingest --transcript <session.jsonl> [--dry-run] [--model qwen3:14b]
```

**Anti-recursion:** transcripts under 500 chars are skipped, `/tmp/ai-rem-ingest.lock` prevents nested runs.

**Failure-mode (md-fallback + catch-up):** if Ollama is unreachable, the session is not lost — a heuristic extraction is appended to `~/.claude/auto-memory/fallback.md` (imported into `CLAUDE.md` via `@`-import, so it stays in context) and the transcript is queued in `pending.jsonl`. As soon as Ollama is reachable again, `ai-rem catchup` (run by the SessionStart and PreCompact/SessionEnd hooks) re-ingests the queued sessions properly into ai-rem and **empties the md**. The hook never breaks `/compact` or session end; hard errors go to `~/.claude/auto-memory/errors.log`.

**Visibility:** each successful run writes `~/.claude/auto-memory/last-run.json`; the SessionStart check surfaces a line like `🧠 N Entities, M Rel` (with `(md-Fallback)` when Ollama was down).

**Configuration env:**
- `AI_REM_ENDPOINT` — MCP URL (default `http://localhost:3456/mcp`)
- `AI_REM_OLLAMA_URL` — Ollama base URL (default `http://localhost:11434`)
- `AI_REM_CLI` — explicit CLI path override (otherwise discovery via known mount paths and `$PATH`)

---

## Nightly cleanup (non-destructive: archive, don't delete)

A daemon thread in the container runs a daily maintenance pass (default 03:00, configurable in the `/cleanup` web UI). It detects duplicate and outdated entries (heuristics + Ollama when reachable) and **archives** them instead of deleting: the entry is tagged `archived`, optionally compressed (with the original preserved in `extra.original_descr`), and linked via `DUPLIKAT_VON` / `VERALTET_DURCH`. Archived entries are hidden from `memory_get_context`/`search`/`list` by default (opt in with `include_archived=true`) but remain reachable for history via `memory_get_relations`. **Preferences, pinned and already-archived entries are never touched.** Every run backs up first; the log is viewable in the `/cleanup` web UI.

Ambiguous cases (and everything when Ollama was down at night) land in a review queue. A non-empty queue is surfaced at session start as an informational hint only (no auto-execution). You can resolve it two ways: **(a)** in the `/cleanup` web UI, where each pending item shows both descriptions with **Mergen/Archivieren** (apply) and **Verwerfen** (keep both) buttons (`POST /api/cleanup/resolve`); or **(b)** the `/memory-cleanup` slash command, which has Claude resolve the entries with judgment. Both use the same non-destructive `memory_merge` / `memory_archive` operations — nothing is deleted.

> **Ollama reachability:** the nightly judge needs `AI_REM_OLLAMA_URL` to point at a reachable Ollama. In the bundled `docker-compose.yml` it defaults to `http://myubuntu:11434` (override per deployment via `.env`). If unset/unreachable, the cleanup still runs but every ambiguous pair is pushed to the review queue instead of being auto-judged (`ollama_used=false` in the run log).

---

## Plan saving (ExitPlanMode → ai-rem)

A `PostToolUse` hook on `ExitPlanMode` (`hooks/save-plan.py`) stores every finalized plan as an **open `Task`** in ai-rem, so plans become a central, cross-machine list instead of just slug files under `~/.claude/plans/`. The `system-check.py` SessionStart hook surfaces these open `Task`s (plans included) automatically, so a new session opens with the list right there — or ask *"any open plans?"* to pick one.

**Fields** come from a small frontmatter block Claude writes at the top of each plan file (no prose guessing):

```
---
name: "Plan: <title>"
description: "<one short sentence>"
status: offen
---
```

The hook reads the newest plan file's frontmatter and upserts via `memory_add` (`type: Task`, `extra.kind=plan`, `extra.plan_file`, `extra.status`). Upsert is keyed by `name`, so re-finalizing a plan never duplicates. Completed plans are archived (`memory_archive`) — the status lives in ai-rem, so it stays consistent across machines. Fail-silent: never blocks `ExitPlanMode`.

**Install:** copy `hooks/save-plan.py` to `~/.claude/hooks/`, `chmod +x`, and register the `PostToolUse: ExitPlanMode` hook in `~/.claude/settings.json` (see the file header).
