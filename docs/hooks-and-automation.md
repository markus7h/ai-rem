# Hooks & automation

[← Back to README](../README.md)

ai-rem ships three Claude Code hooks that keep the graph fed and tidy without manual work:
**Auto-Memory** (session → graph), **Nightly cleanup** (dedup/archive), and **Plan saving**
(plans → open tasks). All three are deployed by the client setup script.

> **Where the hook sources live:** `hooks/*.py` in this repo — plain, lintable Python files.
> `server.py` reads them at import time and serves them unchanged under `/hooks/<name>.py`;
> the client setup fetches them from there. Edit the files, never a copy in `server.py`.
> The same applies to `scripts/setup.py` (served as `/setup.py`) and the web UI under
> `templates/*.html`.

---

## Auto-Memory (PreCompact + SessionEnd → ai-rem)

The built-in Claude Code auto-memory (markdown file) is replaced by a transcript extractor that writes **structured entities and relations** into ai-rem.

**Flow:** `PreCompact` / `SessionEnd` hook → `ai-rem ingest --transcript <path>` → llama-server (`mistral-small3.2:24b` on `AI_REM_OLLAMA_URL`, OpenAI-compatible `/v1/chat/completions`, default `http://myai:11436`) extracts JSON → bulk-upsert via MCP → log to `~/.claude/auto-memory/<timestamp>.json`.

**CLI** (`bin/ai-rem`, pure stdlib — no venv needed, runs on any `python3 ≥3.8` on Windows/Linux/macOS):

```bash
ai-rem status
ai-rem search "auto-memory"
ai-rem show "<name>"   # full, untruncated description + extra + relations (via /export)
ai-rem list --type Decision
ai-rem ingest --transcript <session.jsonl> [--dry-run] [--model mistral-small3.2:24b]
```

**Anti-recursion:** transcripts under 500 chars are skipped, `/tmp/ai-rem-ingest.lock` prevents nested runs.

**Detached execution:** the hook re-spawns itself with `start_new_session=True` and returns within milliseconds — extraction on a local 24b model takes minutes, far longer than any sane hook timeout. Without this, session end would either block or the ingest would be killed mid-run. Each run is keyed by `<session_id>:<transcript_size>` in `.processed`, so `PreCompact` and the later `SessionEnd` of the same session are both ingested (keying by session id alone meant everything after the first compaction was silently dropped).

**Context budget:** `MAX_TOTAL_CHARS` (45k) must fit the llama-server context (`n_ctx=32768` for the 24b Mistral, ~16k tokens at 45k chars) alongside system prompt and answer. Oversized transcripts drop their **middle**, never the end — decisions and insights live at the end of a session; the first user message (the task) is always kept. Raise this only after checking `/props` on the llama-server.

**Failure-mode (md-fallback + catch-up):** if llama-server is unreachable, the session is not lost — a heuristic extraction is appended to `~/.claude/auto-memory/fallback.md` (imported into `CLAUDE.md` via `@`-import, so it stays in context) and the transcript is queued in `pending.jsonl`. As soon as llama-server is reachable again, `ai-rem catchup` (run by the SessionStart and PreCompact/SessionEnd hooks) re-ingests the queued sessions properly into ai-rem and **empties the md**. The hook never breaks `/compact` or session end; hard errors go to `~/.claude/auto-memory/errors.log`.

**Visibility:** each successful run writes `~/.claude/auto-memory/last-run.json`; the SessionStart check only surfaces the status `Auto-Memory ✓` — what was stored last stays in that file, not in the status line.

**Fault detection:** because the hook deliberately fails silently (rc=0, so it never breaks `/compact` or session end), a broken auto-memory used to stay invisible — it once ran dead for 7 weeks. The SessionStart check now compares the mtime of `last-run.json` against `errors.log` and reports on two channels: `Auto-Memory ✗ gestört` in the status line, plus the full diagnosis (last error, likely cause, log path) as `additionalContext` so the assistant sees it too and can raise it. Three conditions trigger it: errors newer than the last success, no `last-run.json` at all, or nothing stored for more than 7 days.

**Configuration env:**
- `AI_REM_ENDPOINT` — MCP URL (default `http://localhost:3456/mcp`)
- `AI_REM_LLAMA_URL` (alt name: `AI_REM_OLLAMA_URL`) — llama-server base URL; setup writes it from the setup-config `ollama_url` into `~/.claude/settings.json` → `env`, because the CLI (unlike the hook) does not read `settings-template.json` (OpenAI-compatible, `/v1` appended internally; env wins, `AI_REM_LLAMA_URL` taking precedence; otherwise `ollama_url` from setup-config / settings-template; default `http://myai:11436`); model is fixed via `AI_REM_LLM_MODEL` (default `mistral-small3.2:24b`) since llama-server hosts exactly one model
- `AI_REM_CLI` — explicit CLI path override (otherwise discovery via known mount paths and `$PATH`). The setup points this at `~/.local/share/ai-rem/bin/ai-rem`, the locally installed copy. If it points into a clone on a network share instead, the hook aborts silently with `ai-rem CLI not found` on every session end as soon as the mount stalls — rerun `/setup` in that case. Put it in the `env` block of `~/.claude/settings.json` so hooks inherit it.

---

## Nightly cleanup (non-destructive: archive, don't delete)

A daemon thread in the container runs a daily maintenance pass (default 03:00, configurable in the `/cleanup` web UI). It detects duplicate and outdated entries (heuristics + llama-server when reachable) and **archives** them instead of deleting: the entry is tagged `archived`, optionally compressed (with the original preserved in `extra.original_descr`), and linked via `DUPLIKAT_VON` / `VERALTET_DURCH`. Archived entries are hidden from `memory_get_context`/`search`/`list` by default (opt in with `include_archived=true`) but remain reachable for history via `memory_get_relations`. **Preferences, pinned and already-archived entries are never touched.** Every run backs up first; the log is viewable in the `/cleanup` web UI.

Ambiguous cases (and everything when llama-server was down at night) land in a review queue. A non-empty queue is surfaced at session start as an informational hint only (no auto-execution). You can resolve it two ways: **(a)** in the `/cleanup` web UI, where each pending item shows both descriptions with **Mergen/Archivieren** (apply) and **Verwerfen** (keep both) buttons (`POST /api/cleanup/resolve`); or **(b)** the `/memory-cleanup` slash command, which has Claude resolve the entries with judgment. Both use the same non-destructive `memory_merge` / `memory_archive` operations — nothing is deleted.

### Staleness check (infrastructure facts)

The same pass also looks for entries whose **content** has rotted — network setup, services, devices: IP addresses, `host:port`, container and port lists, versions. Three stages, cheap to expensive: a regex prefilter for perishable facts in `descr`, then the verification age, then a llama-server judgment on whether the entry actually asserts facts that can change in reality (conceptual and conventional knowledge is filtered out).

**Never automatic:** suspects only ever land in the review queue as a `verify` pending item — with **Passt noch** (checked, still current) and **Verwerfen** buttons. Via `/memory-cleanup` the rule is: verify live (`ssh`, `docker ps`, port check) and correct on mismatch; anything that cannot be verified stays in the queue for the user.

The verification age deliberately is **not** `updated_at`: every `memory_add` resets it, even for a one-clause addition — "last written" is not "last checked against reality". It counts from the most recent date in `extra`: `verify_checked` (set by the check itself), otherwise the organically grown markers `geprueft_am`, `verifiziert_am`, `korrigiert_am`, `erhoben_am`, `gemessen_am`; only if none exist does `updated_at` count. `verify_checked` is set on every outcome (LLM says "current", user clicks "Passt noch" or dismisses) and acts as a cooldown — without it the same entry would be back in the queue every night. Entries whose `descr` starts with `VERALTET` are skipped.

- `CLEANUP_VERIFY_AFTER_DAYS` — verification age at which an entry is proposed (default `90`)
- `CLEANUP_VERIFY_MAX_PER_RUN` — candidates per night, oldest first (default `5`; keeps queue and LLM load small)

> **llama-server reachability:** the nightly judge needs `AI_REM_OLLAMA_URL` to point at a reachable llama-server; the judged model is fixed via `CLEANUP_LLM_MODEL` (default `mistral-small3.2:24b`). In the bundled `docker-compose.yml` it defaults to `http://myai:11436` (override per deployment via `.env`). If unset/unreachable, the cleanup still runs but every ambiguous pair is pushed to the review queue instead of being auto-judged (`ollama_used=false` in the run log).

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

**Install:** deployed automatically by the client setup — `install_hooks()` fetches `save-plan.py` to `~/.claude/hooks/` (chmod +x) and registers the `PostToolUse: ExitPlanMode` hook in `~/.claude/settings.json`. No manual step (the file header documents the standalone install for reference).
