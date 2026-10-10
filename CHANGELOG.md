# Changelog

All notable changes per release. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versioning follows
[Semantic Versioning](https://semver.org/).

This file is the source of truth: on a tag push `docker-publish.yml` creates the
GitHub release from the matching section and renders the most recent entries into
the Docker Hub description. A release without a section here falls back to notes
generated from PR titles.

Older versions: [GitHub Releases](https://github.com/markus7h/ai-rem/releases)
(from v0.2.0) and [docs/release-history.md](docs/release-history.md) (v0.0.4–v0.1.5,
German).

## [1.8.0] – 2026-10-10

### Added
- **Automatic task → project linking.** Nothing ever set a task's project edge, so
  `get_context` listed most open tasks under "_ohne Projekt_". `memory_add` now links a task
  without a project edge via `TEIL_VON` when exactly one active project matches by keyword
  (`extra.keywords` on the project; word boundaries, case-insensitive). Projects without
  keywords never match — a name fallback mislinked to extractor pseudo-projects such as
  "mystorage". Several matches link nothing.
- `ai-rem project-keywords <project> "kw1,kw2"` sets a project's keywords;
  `ai-rem link-projects [--dry-run]` (server tool `memory_link_projects`) back-fills
  existing open tasks.
- The extractor prompt now offers `TEIL_VON` and always lists the active projects.

### Changed
- **Completion reports no longer pile up as open tasks.** The extractor skips new tasks whose
  description only reports completion ("PR #225 gemergt", "wurde geprüft") and closes a known
  task when the session reports it done.
- The nightly cleanup also proposes (review queue, never automatic) tasks whose completion
  marker sits anywhere in the text, and open tasks untouched for `CLEANUP_TASK_STALE_DAYS`
  (default 30). Dismissing either keeps it out of the queue.

## [1.7.1] – 2026-10-09

### Fixed
- **Keychain hint on headless servers.** On a server without a desktop login (SSH) the
  login keyring stays locked, so `secret-tool store` fails and the token goes to the 0600
  file fallback — which is the intended behavior there. Setup still told the user to
  "install libsecret, then `ai-rem pair`", although `secret-tool` was already installed and
  re-pairing could not help. The libsecret advice now only appears when `secret-tool` is
  actually missing; with a locked keyring setup prints a short note naming the reason
  ("Schlüsselbund gesperrt (SSH/headless)").
- **`ai-rem doctor` shows where the token really is.** A fresh process that found the token
  only in the file fallback still reported `Keychain (libsecret)`; it now reports the file.

## [1.7.0] – 2026-10-09

### Added
- **One secret per device: the ai-rem token lives in the OS keychain** (`lib/keychain.py`,
  shipped via `/lib/`). macOS Keychain (`security`), Linux libsecret (`secret-tool`;
  without a session or without `secret-tool` a 0600 file under
  `$XDG_CONFIG_HOME/ai-rem/keyring/<account>`, shown as such in the status), Windows
  Credential Manager. Service `ai-rem`, account `device-token` (`AI_REM_KEYCHAIN_ACCOUNT`).
  The lookup order is the same in the CLI, the hooks and the setup: env `AI_REM_TOKEN` →
  keychain → legacy plain text (read-only, with the hint that `ai-rem update` migrates it).
- **`GET /api/client-config`** (bearer-only) → `{version, llm_url, llm_api_key, vault_url,
  vault_token}`. The CLI (`ingest`/`catchup`), the SessionStart hook and `ai-rem vault-mcp`
  fetch LLM-router and vault access per run and never persist it. Server-side the LLM key
  comes from the env `AI_REM_LLM_API_KEY` (preferred) or the setup-config field
  `llm_api_key`, so rotation happens on the server without touching a workstation.
- **`ai-rem mcp-proxy`** — stdio MCP server that bridges to the Streamable HTTP endpoint
  (session id, SSE events, 401 → JSON-RPC error). Claude Code and opencode start this
  instead of talking HTTP with a stored header; `--endpoint` overrides `client.json`.
- **`ai-rem vault-mcp`** — starts the locally built mykeyvault MCP (`node`) with
  `VAULT_API_URL`/`VAULT_API_TOKEN` taken from `/api/client-config` and `exec`s it, so the
  vault token is on disk nowhere.
- `ai-rem token --store` (token on stdin, never argv) and `ai-rem token --forget` maintain
  the keychain entry; `ai-rem doctor` shows the token source and the `/api/client-config`
  status.
- SessionStart status line `token ✓ (Keychain)` / `token ✓ (Env)` /
  `token ⚠ Klartext-Legacy → ai-rem update` / `token ❌ fehlt → ai-rem pair`. Against a
  server < 1.7 the hook says "Server < 1.7" instead of blaming the router for a 401.

### Changed
- **Claude Code talks to ai-rem over stdio.** `~/.claude.json` → `mcpServers.ai-rem` is
  `{"type": "stdio", "command": "~/.local/share/ai-rem/bin/ai-rem", "args": ["mcp-proxy"]}`
  instead of `http` + `headers.Authorization`; `mykeyvault` becomes `"args": ["vault-mcp"]`
  without an `env` block (HTTP fallback without node: `mcp-proxy --endpoint <url>`).
- **opencode:** `mcp.ai-rem = {type: local, command: [ai-rem, mcp-proxy], environment:
  {AI_REM_CLIENT: opencode}}`, `mykeyvault = [ai-rem, vault-mcp]` — no `{file:…}`
  references any more.
- `GET /setup-config` stays public but drops every top-level field ending in `_key`,
  `_token`, `_secret` or `_password` (`_comment_*` keys stay).
- `~/.config/ai-rem/client.json` holds `endpoint`, `targets`, `vault_url`, `vault_entry`
  and `keychain` (backend name); `llm_api_key`, `llm_url` and `token_file` are gone.
- The hooks no longer sync the bearer header into `~/.claude.json` at SessionStart and no
  longer read the vault. `AI_REM_LLAMA_URL`/`AI_REM_LLM_API_KEY` on a client are an optional
  override only — the source is `/api/client-config`.
- Generic snippets (Gemini CLI, Cursor) use the stdio proxy
  `{"command": "ai-rem", "args": ["mcp-proxy"]}`; Codex keeps `$(ai-rem token)`.
- **Plugins take precedence over `~/.claude.json`.** With an `ai-rem@…` / `mykeyvault@…`
  plugin enabled in `settings.json` → `enabledPlugins` (any marketplace, e.g. the
  tools-registry plugins that start `ai-rem mcp-proxy` / `ai-rem vault-mcp`), the setup
  does not create the same-named `mcpServers` entry and removes an existing one with a
  notice (`ai-rem update` as well) — never two servers of the same name. A missing
  `ai-rem` entry no longer aborts `update_claude_json`; hooks and settings are installed
  either way.
- `node`/`npm`/`git` are only checked for and installed when `mcp_register` actually asks
  for a build (`mykeyvault.stdio` or `tools.stdio.registry_url`), not for every
  `mcp_register`.

### Removed
- Plain-text token files `~/.config/ai-rem/token` and `~/.config/ai-rem/vault.token`,
  `~/.claude/ai-rem-vault.env`, and `AI_REM_LLM_API_KEY` in `~/.claude/settings.json` →
  `env` (the 1.2.0 note below describes that write; it no longer happens).
- `--refresh` in `system-check.py` — there is no header left to refresh.

### Fixed
- **ai-rem-only setups (no mykeyvault/tools):** `vault-secret-reminder.py` is only deployed
  and registered as a `PostToolUse` hook when mykeyvault is registered (in
  `~/.claude.json` or as a plugin); otherwise an entry from an earlier run is removed —
  its advice pointed at a vault that does not exist.
- The SessionStart check (`system-check.py`) only tests the `mcp_stdio_servers` that are
  registered in `~/.claude.json` or come from an enabled plugin, instead of reporting
  `❌ mykeyvault, tools` for servers that were never set up.

### Security
- `/setup-config` is public (onboarding runs before the first token) and handed out the
  complete setup-config **including `llm_api_key`** without auth. **Rotate the LLM router
  key after the upgrade** — treat every key that ever sat in a setup-config as exposed —
  and set the new one on the server as `AI_REM_LLM_API_KEY`; no client needs it any more.
  Per-device token rotation is `ai-rem pair`.
- `/api/pair/poll` still returns `vault_token`/`vault_url` for installers < 1.7
  (deprecated); the current installer stores only the ai-rem token.

### Migration
1. Deploy server 1.7.0 — old clients keep working, the header auth is unchanged.
2. `ai-rem update` on every workstation (any setup run does the same): moves an existing
   plain-text token into the keychain, deletes the token files and `ai-rem-vault.env`,
   strips header/env secrets from `~/.claude.json`, `settings.json`, `client.json` and
   `opencode.json`, switches the MCP entries to the stdio wrappers and prints
   `✓ Klartext entfernt: …` per location. Restart Claude Code / opencode afterwards.
3. Then rotate the LLM router key and set it on the server as env.

## [1.6.1] – 2026-10-09

### Fixed
- **opencode 2.x: the plugin did not load.** opencode 2 only accepts a default export
  with an `id` and `setup(ctx)` ("Plugin must export a default definition…"); the
  1.x-style named export was rejected, so nothing was ingested. The plugin now exports
  `{ id, setup, server }` — `setup` for 2.x, `server` for 1.18.29+ — on one shared core,
  still a single file without `node_modules`.
- v2 events and messages: a finished turn is `session.execution.succeeded|failed|interrupted`
  (the `session.idle` of 1.x is kept as well), the session ID comes from `event.data`, and
  `{type:"user"|"assistant"}` messages are normalised to the existing export format, so
  server and extractor are unchanged.
- **Compaction no longer loses history:** `ctx.session.context()` only returns messages
  since the last compaction, so the plugin exports on `session.compaction.started` and
  continues in a new file (`opencode-<session>-c<N>.json`) instead of overwriting.
- The v2 event stream is volatile (slow consumers get dropped); the plugin reconnects
  with backoff.

### Changed
- The "client outdated" toast is gone on opencode 2.x (plugins have no toast API there);
  the hint goes to the log. 1.x keeps the toast.
- Sample files (`.env.example`, `setup-config.example.json`, compose comments) no longer name
  a specific host; they use `<ai-rem-host>` as the placeholder for the machine ai-rem runs on.

## [1.6.0] – 2026-10-07

### Added
- **Device pairing — set up a machine without SSH.** The installer starts a pairing,
  shows a code and opens `/pair` in the browser; approving it in the logged-in web UI
  (phone works too) hands the ai-rem token — and `AI_REM_PAIR_VAULT_TOKEN` plus the
  vault URL, if set — to the installer exactly once. Pairings expire after 10 minutes,
  starts are rate-limited per IP, approval needs the UI login, every decision is logged
  (`/pair`, `backups/paired-devices.json`). `/login` now returns to `?next=`.
- **The installer installs missing packages itself.** node ≥ 18, npm and git (for
  mykeyvault and tools) come via brew, apt/NodeSource or winget after one prompt
  (`--yes` skips it); the build continues in the same run instead of "install X, then
  run again".
- `ai-rem pair` (renew just the token), `ai-rem doctor --fix` (re-runs install),
  `ai-rem install --yes/--pair`. The setup ends with a ✓/✗ summary and `ai-rem doctor`.

### Fixed
- Commands pasted after the setup line were swallowed: `ssh` inherited the terminal's
  stdin. Subprocesses now get `stdin=DEVNULL`.
- No more SSH warning when SSH is simply not set up — pairing takes over.

## [1.5.0] – 2026-10-07

### Added
- **opencode as a first-class client** (#121). `ai-rem install --client opencode`
  merges `ai-rem` (plus `mykeyvault`/`tools`, if built) into the `mcp` block of
  `~/.config/opencode/opencode.json` without touching providers or other servers,
  adds an `AGENTS.md` pointer (call `memory_get_context()` at session start), puts the
  auto-memory `fallback.md` into `instructions[]` and installs the commands and a
  plugin. The plugin exports the session after 10 minutes of `session.idle` (and on
  `session.compacted`) and runs `ai-rem ingest`; the extractor reads the opencode
  export next to Claude's JSONL. Tokens are referenced as `{file:…}`, never written
  into the config; a JSONC file with comments is left alone and a snippet is written.
- **Generic target** for other MCP frontends: `--client generic` writes ready-to-paste
  snippets for Codex, Gemini CLI and Cursor plus an `AGENTS.md` pointer to
  `~/.config/ai-rem/snippets/`.
- **The CLI installs and maintains clients:** `ai-rem install [--client …]`,
  `ai-rem uninstall --client …`, `ai-rem doctor` (server version, targets, drift, token
  source). `ai-rem update` covers every installed target. The setup one-liner passes
  arguments through (`bash <(curl -s …/setup) --client opencode`) and defaults to every
  frontend found on `PATH`; the `claude` CLI is no longer a hard requirement.
- **`extra.client`** records which frontend created an entry — from MCP
  `clientInfo.name` or the `X-AI-REM-Client` header the CLI sends (`claude-code`,
  `opencode`, `ai-rem-cli`). Set on create only, never overwritten.
- Client-neutral config `~/.config/ai-rem/client.json` with the token file
  `~/.config/ai-rem/token` (0600). The CLI looks there before `~/.claude.json`; the
  SessionStart hook keeps the file in step with a vault token rotation.

### Changed
- MCP server instructions no longer assume Claude Code; they ask every client to call
  `memory_get_context()` once per session.
- `/manifest` also lists the opencode artifacts (`opencode/…`); the SessionStart hook and
  `ai-rem update` only check targets that are actually installed.
- `lib/mcp_client.py` honours `CLAUDE_CONFIG_DIR` when reading `~/.claude.json`.
- Without `~/.claude`, the extractor keeps its state in `~/.local/share/ai-rem/state`.

## [1.4.0] – 2026-10-07

### Changed
- **Nightly cleanup runs in a time window instead of a fixed hour.** The scheduler
  compared `datetime.now().hour` against the configured hour — in the container's UTC,
  so "7" meant 09:00 CEST, and a run missed in that hour was skipped for the day. It
  now runs once per day inside `window_start`–`window_end` (default 06:15–22:30) in
  the new `AI_REM_TZ` (default `Europe/Berlin`), matching the LLM host's waking hours
  (myai sleeps 23:00–06:00). A run missed by a restart is caught up later in the window.
- **The run waits for the LLM.** If `AI_REM_OLLAMA_URL` is unreachable at the start of
  the window it retries every 15 min and only runs without the judge in the last 30
  minutes of the window.
- `/cleanup` web UI and `POST /api/cleanup/config` take `window_start` / `window_end`
  (`HH:MM`). The old `hour` setting is dropped on load; the default window applies.

### Added
- `tzdata` dependency (the slim image has no zoneinfo database).

## [1.3.2] – 2026-10-06

### Fixed
- **A crash no longer discards days of writes.** `_checkpoint_wal` only merged the WAL
  above `LADYBUG_WAL_CHECKPOINT_MB` (2 MB). Everyday writes stayed below that for days,
  so everything since the last restart lived in the WAL alone. On 2026-10-05 the server
  died mid-checkpoint, the WAL guard quarantined the 1.7 MB checkpoint file and ten days
  of writes were gone — closed tasks came back as open, the nightly cleanup's
  archivings were undone. The WAL is now also checkpointed once it has been idle for
  `AI_REM_WAL_CHECKPOINT_IDLE_S` (default 300 s), so a crash costs minutes, not days.
- **`ai-rem close --note` kept the note.** `memory_add` dropped an explicitly passed
  `extra.status_note` whenever the status mapped cleanly (e.g. `erledigt`).

### Added
- **Import mode `upsert`** (`POST /import?mode=upsert`, `/api/restore` with
  `mode=upsert`). Like `merge`, but existing entities are overwritten when the
  imported `updated_at` is newer — this brings back changed entries (task status,
  archive flag) from a backup without clobbering younger writes. Deletions are not
  undone by any mode.
- **Automatic recovery after a WAL quarantine.** If the start-up guard had to move
  WAL leftovers aside, the newest backup from before the crash is replayed with
  `upsert`. `memory_status` reports the loss and the recovery result.

### Changed
- Dependency bump: `numpy` 2.5.2 → 2.5.3 (#138).

## [1.3.1] – 2026-10-05

### Changed
- **LadybugDB 0.20.4 → 0.21.2.** 0.21 brings, among others, the fix for the HNSW
  SET-on-NULL segfault (LadybugDB/ladybug#900). Dependabot's 0.21.1 bump (#150) was red:
  0.21.x has a regression where `MATCH (t:Entity {type: 'Task'}) OPTIONAL MATCH …`
  nulls the *whole* row — including `t` — when the optional part finds nothing, as
  soon as the outer `MATCH` filters with an inline property map. With `WHERE t.type =
  'Task'` the result is correct. Hit: tasks without a project came back as `None`, so
  `memory_get_context` died on `" · ".join(...)`, and the `/tasks` UI lost every task
  without a project relation. Both task queries (`_open_task_rows`,
  `_task_rows_full`) now filter with `WHERE`; on 0.20.4 the result is identical.
- Dependency bumps: `cryptography` 50.0.1 → 50.0.2 (#149), `fastembed` 0.8.0 → 0.8.1
  (#147), `fastmcp` 3.4.7 → 4.0.10 (#151).

## [1.3.0] – 2026-09-19

### Changed
- **`memory_add` merges `extra` instead of replacing it.** Closing a task by hand
  (`--extra '{"status":"erledigt"}'`) used to wipe `plan_file`, `kind` and
  `created` off the entry, because the dict was overwritten wholesale. Keys you do
  not pass now survive; same-named keys still win. Removing a key is no longer
  possible through `memory_add` — that path is `POST /api/tool`.
- **`CLEANUP_TASK_RETENTION_DAYS` default 30 → 14.**

### Added
- **`extra.status` on a `Task` is normalised to `offen | laufend | blockiert |
  erledigt`.** Live data had 17 distinct status values across 111 tasks. The old
  read path compared the raw string against a six-value whitelist without
  `strip()`, so a truthy-but-unknown value (`"abgeschlosen"`, `"erledigt "`,
  `"completed"`) fell through *both* branches of the nightly cleanup: not in the
  done-set, and the description-text fallback only fires on an *empty* status. Such
  a task was permanently "open" and immune to self-cleaning — a typo was worse than
  no status at all. Writes never fail: known spellings map to the enum, free text is
  preserved in `extra.status_note` and the task stays visible as `laufend`. Both the
  write path and the read paths normalise, so entries restored by `_apply_import`
  (which writes past `memory_add`) are covered too. `Project` entities are
  explicitly exempt — `memory_set_project_context` writes `status="aktiv"`.
- **A task closed via the status is archived after a grace period, anchored on
  `extra.done_at`.** `updated_at` is rewritten by every `memory_add`, so the clock
  would restart on each stray edit. Leaving `erledigt` deletes the anchor again.
- **A completion marker that only exists in the description text no longer archives
  by itself.** Prose is too unreliable a reason to hide an entry; it now produces a
  pending item of kind `archive` in the review queue. Dismissing one sets
  `extra.done_marker_dismissed`, so the same tasks do not queue up again every
  night.
- **The comfortable path.** A done button in the `/tasks` UI, `ai-rem close <name>
  [--note ...]` on the CLI, and a line in the MCP `instructions` telling the model
  that `memory_add(name, "Task", extra={"status": "erledigt"})` is all a closure
  takes. This matters because the MCP surface is four tools — `memory_archive` sits
  in the admin set and the model cannot call it at all.
- **`memory_normalize_task_status(dry_run=True)`** (admin, via `POST /api/tool`) to
  migrate existing entries once. It writes `extra` directly without touching
  `updated_at`: going through `memory_add` would date all tasks to today, destroy
  the recency ordering in `get_context` and restart every grace period.

### Fixed
- **`memory_archive` on a task now sets `status="erledigt"` as well.** The two axes
  were decoupled, so an archived task reappeared as open under
  `include_archived=1`.
- A non-string `extra.status` no longer raises an `AttributeError` that took out the
  whole open-tasks section of `memory_get_context`.

## [1.2.7] – 2026-09-18

### Fixed
- **A vector lost to a failed embedding call is now picked up within the hour.**
  `_embed_backfill` only ran at container start and at the end of the nightly
  cleanup, so a single failed call left an entity unsearchable semantically until
  the next morning — with the `system-check` hook reporting `embed ❌ N ohne Vektor`
  at every session start in between. Seen on 2026-09-18: the LiteLLM router sent
  three ai-rem calls (09:08, 12:12, 15:47) to a `bge-m3` deployment whose host was
  down. The router knows the host is unhealthy and `enable_health_check_routing`
  normally filters it out — 40 of 40 test calls went to the healthy host — but once
  a deployment's 120 s cooldown expires it is back in the pool, and the gateway log
  shows `Cooldown Deployments=[]` right next to the connection error. ai-rem cannot
  fix the router, so it stops depending on every single call succeeding: the cleanup
  scheduler thread, which ticks once a minute anyway, now also reconciles missing
  vectors every `EMBED_RECONCILE_SEC` (default 3600, `0` disables). It runs before
  the cleanup `enabled` check — backfilling vectors has nothing to do with whether
  nightly cleanup is switched on — and is a single counting query when no vectors
  are open.

## [1.2.6] – 2026-09-18

### Fixed
- **The WAL guard now catches the death it was built for.** v1.2.5 hung on
  `except RuntimeError` and cleaned up when LadybugDB *reported* a damaged WAL. On
  2026-09-18 it reported nothing: the `ladybug.Database()` constructor itself died by
  SIGSEGV, and no `except` ever sees a signal. Same picture as before — segfault in
  `libstdc++` mid self-checkpoint, `kg.db.wal.checkpoint`, `kg.db.shadow` and both
  checkpoint locks left behind — but this time 22 restarts produced not a single log
  line, because the process was gone before the first one. Reproduced against the
  production image on a copy of the crash state: `ladybug.Database()` exits 139, no
  Python exception. The guard now decides where that death is observable at all: when
  WAL or checkpoint leftovers are present, kg.db is opened in a throwaway subprocess
  first, and an exit code other than 0 moves `kg.db.wal*`, `kg.db.shadow` and the
  checkpoint locks aside as `*.corrupt-<timestamp>`. Moved, not deleted. A probe killed
  by SIGKILL counts as survived — that is the OOM killer, and memory pressure must not
  cost healthy transactions. A probe still running after 300s counts as survived too: a
  long recovery is slow, not broken. The probe costs a full second open, so it only runs
  when leftovers are actually there, never after a clean stop. (#143)

### Changed
- **LadybugDB 0.20.2 → 0.20.4.** 0.20.4 fixes, among others,
  [#924](https://github.com/LadybugDB/ladybug/issues/924) — every checkpoint orphaned its
  shadow `FileHandle`, leaking ~4 MiB of the `max_db_size` budget per fold, and the first
  checkpoint past the cap segfaulted mid-fold leaving exactly the artifacts we keep
  finding (stale `.shadow`, orphaned `.wal.checkpoint`). Whether that was our trigger is
  unproven: no `Maximum database size` error appears in the logs of any of the four
  incidents. 0.20.3/0.20.4 carry four further segfault candidates (use-after-free on a
  `ResultSet` outliving its `Database` and on Arrow registry entries, a data race in
  `OptimisticAllocator`, a MERGE crash on corrupted HASH PK entries). Verified before
  rollout: 0.20.4 reads the 0.20.2 storage unchanged (1735 entities), and it still
  segfaults on the already-broken state from 2026-09-18 — a damaged database stays
  damaged, which is precisely why the guard above stays. (#143)

## [1.2.5] – 2026-09-16

### Fixed
- **A damaged WAL no longer leaves the server in a crash loop.** On 2026-09-16 LadybugDB
  segfaulted in the middle of a WAL checkpoint (exit 139, in `libstdc++`). A segfault does
  not ask for SIGTERM, so `stop_grace_period` — which covers an orderly `docker stop` —
  did not apply: the leftover `kg.db.wal.checkpoint` was unusable, every subsequent start
  died on `Checksum verification failed, the WAL file is corrupted`, and
  `restart: unless-stopped` retried 20 times without ever coming up. 15 minutes of
  downtime, ended by moving the file aside by hand. The server now does that itself: on a
  WAL error while opening kg.db, `kg.db.wal*`, `kg.db.shadow` and the checkpoint locks are
  renamed to `*.corrupt-<timestamp>` and the open is retried once. Moved, not deleted.
  LadybugDB words the damage differently depending on which file it hit, so both variants
  are matched; any other error is passed through untouched, so a misconfiguration cannot
  clear a healthy WAL. An **intact** WAL left behind by a hard kill is the normal case and
  is still recovered as before. If the retry fails as well, the error stands — then only a
  restore from `/backups` helps. (#142)

## [1.2.4] – 2026-09-14

### Added
- **`ai-rem` is on the `PATH` after a setup.** The installer put the CLI in
  `~/.local/share/ai-rem/bin/ai-rem` and pointed `AI_REM_CLI` at it — enough for the
  hooks, useless for a human: typing `ai-rem update` got "command not found", so the one
  command that keeps a client current was the one nobody could run. The setup now links
  `~/.local/bin/ai-rem` (a `.cmd` shim on Windows, where symlinks need admin rights) to
  that copy, and prints the line to add if `~/.local/bin` is not on the `PATH`. Shell
  configs are left alone. An existing symlink there is replaced; a real file is not
  touched. Because the link is not a shipped file, it carries no manifest hash, so
  `ai-rem update --check` tests for it separately and reports it missing. (#141)
- **`ai-rem update` brings an installed client up to date.** Hooks, CLI, `lib/` and the
  slash commands are shipped by the server and changed in 14 commits since August — and
  were never refreshed, because nothing carried a version, a hash or an mtime to compare.
  The only update path was re-running the full setup, which also redoes the SSH secret
  pull, the `git clone` and the `npm` builds. Now `GET /manifest` publishes a SHA-256 per
  shipped file, `ai-rem update --check` reports what is behind (exit 1), and `ai-rem
  update` pulls it via `setup.py --update` — the file half of the setup, without
  anything that needs SSH, git or npm. The SessionStart hook runs the same comparison and
  reports a lag on its own, so the lag surfaces before anyone asks. New slash command
  `/ai-rem-update`. `settings.json` keeps being merged additively; only the
  server-owned `settings-template.json` is rewritten wholesale. (#139)

### Fixed
- **A deploy no longer corrupts the WAL.** `docker stop` — and with it every `compose up
  -d` — sends SIGKILL 10 seconds after SIGTERM. The SIGTERM handler merges the WAL into
  kg.db in that window, which a ~180 MB database does not finish in ten seconds. On 13 Sep
  the kill landed mid-checkpoint: `kg.db.wal.checkpoint` plus the lock files stayed behind
  and the server crash-looped on `Checksum verification failed, the WAL file is corrupted`,
  taking the whole instance down until the leftovers were moved aside — the writes in that
  WAL were lost. `docker-compose.yml` now sets `stop_grace_period: 180s`, and
  `tests/test_compose_stop_grace.py` keeps it there. (#140)
- **The CI build cache no longer freezes Debian security patches.** `ci.yml` built the
  smoke image with a fixed `cache-from: type=gha`, which pinned the `apt-get update &&
  apt-get upgrade` layer — the one whose entire job is to pick up those patches. As soon
  as new CVEs landed, the Trivy gate in the same job blocked on vulnerabilities a real
  build would already have fixed, and a re-run did nothing because it pulled the same
  cache (13 Sep: twelve fixable perl CVEs, image on `5.40.1-6`, fix `5.40.1-6+deb13u1`
  sitting in the mirror). The cache scope now rotates daily: one full build a day, and
  the gate is never more than that far behind. Only `ci.yml` was affected — the published
  images build without this cache. (#139)

## [1.2.3] – 2026-09-12

### Added
- **The dashboard shows how large kg.db was at startup.** `/api/status` gained
  `db_start_mb`, measured once the service is ready — after a rebuild has compacted and
  the embedding backfill has written its vectors, both of which still belong to the
  start. The dashboard prints that figure, and as soon as the current size drifts from
  it, both (`40 → 41.2 MB`). A single size says nothing about whether the DB grows *while
  running*, which is the symptom that went unnoticed for days in the Kuzu era.

### Fixed
- **`:latest-slim` now actually contains the slim image.** `docker-compose.yml` declares
  both `image:` and `build:`, so `docker compose up --build` overwrites the pulled image
  with the local build and keeps the tag name. Without a matching build arg that build
  fell back to the Dockerfile default (`EMBED_BACKEND=local`), so every deploy baked
  fastembed and its model into the image and published it under `latest-slim` — 382 MB
  that a container with `EMBED_URL` set never touches. Nothing broke, which is why it
  went unnoticed since the tag was introduced. `docker-compose.yml` now passes
  `EMBED_BACKEND` through to the build and `deploy.sh` derives it from `AI_REM_TAG`, so
  the two cannot drift apart. The Docker Hub images were never affected — both workflows
  always passed the arg. Rebuilt on the server: 696 MB → 314 MB.
- **`/` reaches the dashboard again instead of 404.** The logo in the navigation links to
  `/` on every page, and `https://airem.lan` is the natural entry point in a browser, but
  the dashboard only ever existed at `/ui` and no root route was ever defined. From any
  subpage the logo therefore led nowhere — and with it the only route back to backup and
  restore, which live on the dashboard. `/` now redirects to `/ui`, behind the same auth.

### Changed
- **The container restarts with `unless-stopped` instead of `on-failure:5`.** A reboot
  stops the container gracefully, so it exits 0 — which is not a failure, so Docker never
  brought it back. After a host restart ai-rem was the only service still down while every
  neighbouring container came back up, and the reverse proxy answered 502 into the void.
  The old policy dated from the Kuzu era, when a segfault in the WAL checkpoint caused 264
  restarts and grew kg.db to 27 GB; that trigger is gone since the move to LadybugDB,
  while the cost of the policy had become concrete. The remaining guards from that era —
  `KG_MAX_MB`, `KG_MIN_FREE_MB`, `KG_REBUILD_MB` and `MEM_LIMIT` — are unchanged.

## [1.2.2] – 2026-09-10

### Changed
- **Every Web UI page shows the running server version.** It used to sit only in the
  dashboard subtitle, so on `/browse`, `/tasks`, `/graph`, `/prefs`, `/cleanup`, `/logs`
  and `/install` there was no way to tell which build was answering — the question
  "did the client pick up the latest hooks?" needed a shell and a diff instead of a
  glance at the browser. The version now sits at the right end of the shared header
  navigation on all eight pages and is gone from the dashboard subtitle (it would
  otherwise appear twice there).
- **`_pkg_text()` substitutes `__VERSION__` while loading a template**, instead of each
  render route doing its own `.replace()`. One place to change, and a new page picks the
  version up by writing the placeholder. Hooks and setup scripts carry no such
  placeholder, so the substitution only ever touches HTML.

## [1.2.1] – 2026-09-10

### Fixed
- **`Auto-Memory ❌ gestört` no longer sticks after a single transient failure**
  (`hooks/system-check.py`). The check compared mtimes only: one error newer than
  `last-run.json` raised the alarm and kept it up until some session happened to end
  successfully. Two failed ingests during a container rebuild were enough to leave the
  warning standing for a day with nothing actually broken — the third false alarm of this
  kind. `_auto_memory_fault()` now counts the timestamped `errors.log` lines written after
  the last success (new `_errors_since()` helper) and only reports from the second one on.
  Traceback continuation lines carry no timestamp and are not counted.
- **`scripts/setup.py` refuses to run with an unsubstituted `KG_URL`.** The placeholder is
  replaced by `server.py` when the file is served; started from a local checkout it survived
  and `claude mcp add` registered a literal `__KG_URL__/mcp` in `~/.claude.json`, so the MCP
  server never connected (`INVALID_CONFIG: 'url' is not a valid URL`). The hook did not
  notice because it uses `$AI_REM_ENDPOINT`. Now exits 2 with a pointer to the server URL;
  set `KG_URL` in the environment to run it from a clone anyway.

### Changed
- **Failed ingests log 2000 instead of 500 characters of stderr** (`hooks/auto-memory.py`).
  The cut fell exactly where the traceback names the error, which made the failures above
  undiagnosable after the fact.

## [1.2.0] – 2026-09-09

### Changed
- **LLM calls go through a router instead of a single GPU host.** `AI_REM_OLLAMA_URL` /
  `AI_REM_LLAMA_URL` now default to `http://mystorage.lan:11437` (LiteLLM) instead of
  `http://myai:11436`, and the model defaults (`AI_REM_LLM_MODEL`, `CLEANUP_LLM_MODEL`)
  to the model group `qwen` instead of the long-gone `mistral-small3.2:24b`. That GPU host
  sleeps 23:00–06:00 — exactly when the nightly cleanup and most session-end extractions
  run — so both silently degraded (review queue / markdown fallback). The router covers
  the same window with a cloud fallback. Pointing the variables straight at a llama-server
  still works.
- **Reachability probes hit `/v1/models`, not `/health`** (`server.py`, `lib/extractor.py`,
  `hooks/system-check.py`). On the router `/health` is the *admin* endpoint: every call
  fires real test requests against every configured model, paid cloud model included — on
  every SessionStart and every cleanup run.

### Added
- `AI_REM_LLM_API_KEY` and `EMBED_API_KEY` — bearer tokens for the LLM and embedding
  endpoints. Empty means no `Authorization` header at all, so direct llama-server setups
  are unaffected. `deploy.sh` pulls the LLM key from mykeyvault (item `litellm ai-rem key`)
  into the remote `.env`; `scripts/setup.py` writes the setup-config field `llm_api_key`
  into `~/.claude/settings.json` → `env` for the workstation hooks. Use a virtual key with
  a budget, never a master key — that file exists on every workstation.

### Fixed
- `deploy.sh` resolved the vault coordinates twice in two copies of the same 30-line
  inline Python. One `vault_secret()` helper now, plus a `put_env()` for the `.env` writes.

### Security
- **~460 fewer CVEs in the image.** The Dockerfile installed `gcc`, which no build ever
  used — every dependency (`ladybug`, `numpy`, `cryptography`, `pyyaml`, `fastembed`)
  ships manylinux wheels for amd64 and arm64. Its layer dragged in binutils & friends and
  accounted for 456 of the 629 OS CVEs a Trivy scan reported on `:latest` and
  `:latest-slim`. The install step is now a plain `apt-get update && apt-get upgrade`.
- **`pip` is removed after the install** (`python -m ensurepip` brings it back if needed).
  Nothing installs packages at runtime, and `pip` was the only Python component with open
  CVEs — 6 of them, all with a fix available.
- **Weekly rebuild** (`.github/workflows/rebuild.yml`, Mondays 04:00 UTC + manual
  dispatch): rebuilds the newest release tag against the current base image and pushes
  `latest` / `latest-slim` only — version tags keep their content.
- **CI blocks fixable CRITICAL/HIGH findings.** The `image-smoke` job now runs Trivy with
  `ignore-unfixed`, so the Debian base CVEs without an upstream patch (currently 3
  CRITICAL and 51 HIGH in `perl-base` / `util-linux`) don't block PRs, while anything
  repairable we pull in does.

## [1.1.0] – 2026-09-07

### Added
- **`/tasks` web UI** — a task list next to `/browse`: status and project per row,
  "open only" filter (on by default), toggle for archived tasks, and search across name,
  description and project. Each open task has an **Archive** button that runs
  `memory_archive` through the existing `/api/tool` dispatch — the task leaves the session
  context but stays in the database and remains reachable via `include_archived`.
  Backed by the new `GET /api/tasks` route (`include_archived=1` optional), which reads
  `_task_rows_full()`: like the counter query behind `memory_get_context`, but with the
  done flag, archive state and the project relations of every task, open or closed.

### Changed
- **`/browse` paginates.** The list rendered every filtered entry at once, which at ~1500
  entities meant a very long page. It now shows 20 at a time with a "load more" button;
  changing search, type filter or the archived toggle starts over at 20. Same behaviour
  on `/tasks`. Purely client-side — `/export` still delivers the whole graph in one go.

## [1.0.0] – 2026-09-06

The database underneath ai-rem is no longer [Kuzu](https://github.com/kuzudb/kuzu) but
[LadybugDB](https://github.com/LadybugDB/ladybug). Kuzu was archived on 2025-10-10 with
v0.11.3 as its last release; LadybugDB is the maintained community fork with the same
Python API. The swap itself landed in v0.9.0 and has been running in production since —
this release makes it the headline it deserves, because **upgrading is not automatic**.

### Breaking
- **The database file formats are not compatible.** LadybugDB refuses a Kuzu `kg.db` with
  `The file is not a valid Lbug database file!`, so an existing installation cannot simply
  pull the new image. Move the graph with `scripts/migrate.py` (ships inside the image):
  dump the running v0.8.x instance, move the old `kg.db` aside, start v1.0.0, import the
  dump. The full sequence is in the README under "Upgrade from v0.8.x (Kuzu)".
  A fresh install needs none of this.
- **`KUZU_*` environment variables are now `LADYBUG_*`.** The old names still work as a
  fallback, so an existing `.env` keeps running — but they are no longer documented.

### Why it was worth a major release
Kuzu rewrote the entire column on every checkpoint: the file grew with the *number* of
checkpoints, and a checkpoint that outgrew the buffer pool discarded what earlier ones had
persisted. That defect caused two outages, one of which filled the disk (kg.db grew from
~680 MB to 27 GB across 264 restarts on 2026-09-03) and took neighbouring containers with
it. The same reproduction — 1342 entities with 1024-dimensional vectors, a checkpoint per
chunk of 32, buffer pool 256 MB:

| | Kuzu 0.11.3 | LadybugDB 0.20.2 |
|---|---|---|
| vectors surviving | **0 / 1342** (`buffer pool is full`) | **1342 / 1342** |
| file size | 771 MB | **40 MB** |

The first real migration confirms it: 1498 entities and 1601 relations moved over, all
1498 vectors recomputed without a single failed checkpoint, and `kg.db` went from
**1290 MB to 50 MB**.

### Note
The guards built for the Kuzu era — portioned embedding backfill, `KG_REBUILD_MB`,
`KG_MAX_MB`, the startup rebuild — are still in place and still never trigger. Removing
them is a separate change, deliberately not bundled into this release.

## [0.9.2] – 2026-09-06

### Added
- **`scripts/migrate.py` now ships inside the image** and carries the graph from a v0.8.x
  instance to a v0.9.x one (#125): `export` writes a verified JSON dump, `import` waits for
  the new container's `/health`, replays the dump and checks the entity count afterwards.
  Stdlib only, so it runs on any host with Python 3.10+ and in the slim image. It was
  merged after v0.9.1 was tagged, so `magic3arkus/ai-rem:latest` carries it from this
  release onwards — the extraction command in the 0.9.0 migration note needs v0.9.2 or newer.

## [0.9.1] – 2026-09-05

### Changed
- **A task whose description starts with a done marker is now archived by the nightly
  cleanup**, even when nobody set `extra.status`. Closing a task in prose
  ("ERLEDIGT 2026-09-01: …") was the common case, so the open-task counter only ever
  grew: 156 tasks counted as open, 123 of which were finished or had never been
  standalone work. The regex `_DONE_BODY` (ERLEDIGT/GELÖST/GEGENSTANDSLOS/OBSOLET/…)
  is the second axis next to `_DONE_STATUSES`; the existing retention window
  (`CLEANUP_TASK_RETENTION_DAYS`, 30 days) and the "never destructive" rule apply
  unchanged. An explicit `status` still wins — `status: offen` with a done marker in the
  body stays open, and only `Task` entities are affected.
- **The extractor no longer stores work steps as tasks.** Names like `PR #262`,
  `task_556`, `T1: …`, `Phase 0`, `Implementierer A0` or a bare `status` are steps
  inside a finished session, not standing work — 97 such orphans had accumulated.
  `is_step_task()` in `lib/extractor.py` drops them before the upsert (tasks only;
  a `Decision` of the same name is unaffected), and the system prompt now says
  explicitly that `Task` is for work that outlives the session.

## [0.9.0] – 2026-09-05

> Never tagged: these changes reached Docker Hub with v0.9.1. The link above
> therefore compares against the merge commit, not against a tag.

### Changed
- **The graph database is now [LadybugDB](https://github.com/LadybugDB/ladybug) 0.20.2
  instead of Kuzu 0.11.3.** Kuzu was archived on 2025-10-10 and v0.11.3 is its last
  release; LadybugDB is the maintained community fork with the same Python API — all 86
  queries in `server.py` are unchanged. The switch was made because of the defect behind
  the two outages of the 0.8.2x line: in Kuzu every checkpoint rewrote the whole column,
  so the file grew with the *number* of checkpoints and a checkpoint that outgrew the
  buffer pool discarded what earlier ones had persisted. The same reproduction — 1342
  entities with 1024-dimensional vectors, a checkpoint per chunk of 32, buffer pool 256 MB:

  | | Kuzu 0.11.3 | LadybugDB 0.20.2 |
  |---|---|---|
  | vectors surviving | **0 / 1342** (`buffer pool is full`) | **1342 / 1342** |
  | file size | 771 MB | **40 MB** |

  The workarounds from 0.8.29–0.8.32 (portioned backfill, size guard, startup rebuild)
  stay in place for now — they simply never trigger. A later release will simplify that path.
- **`KUZU_*` environment variables are now `LADYBUG_*`** (`LADYBUG_DB_PATH`,
  `LADYBUG_POOL_SIZE`, `LADYBUG_BUFFER_POOL_SIZE_MB`, `LADYBUG_WAL_CHECKPOINT_MB`). The
  old names keep working as a fallback, so existing `.env` files need no change.
- **The advice to raise the buffer pool to 768 MB for `EMBED_URL` is gone.** It was a
  consequence of the Kuzu defect; measured against LadybugDB, 256 MB carries the same
  1024-dimensional vectors.

### Migration
The file format is not compatible — LadybugDB refuses a Kuzu `kg.db`. Pull the script out
of the image (`docker run --rm --entrypoint cat magic3arkus/ai-rem:latest /app/scripts/migrate.py`), dump
the running 0.8.x instance, move the old `kg.db` aside, start the new image and import the
dump. That also sheds the accumulated Kuzu bloat. Embeddings are not part of the dump —
the new instance recomputes them.

## [0.8.32] – 2026-09-04

### Changed
- **A fresh install no longer has to click through a permission prompt for every
  `grep`.** Plan mode and the permission allowlist are two separate gates: with
  `defaultMode: plan`, shell commands are prompted for one by one no matter how many
  `Bash(...)` allow rules are configured. `settings-template.json` now carries
  `skipAutoPermissionPrompt` and `useAutoModeDuringPlan`, which route shell commands in
  plan mode through Claude Code's auto-mode classifier instead — writes stay blocked and
  the plan itself still needs the user's approval.
- **`permissions_default_mode`** is a new `setup-config.json` key (default `plan`) and
  seeds `permissions.defaultMode`; previously the template could not express it at all.

## [0.8.31] – 2026-09-04

### Fixed
- **The embedding backfill no longer loses everything it writes.** Every `CHECKPOINT`
  rewrites the entire column in Kuzu 0.11.3, so the file grows with the *number of
  checkpoints* rather than with the data — and once it outgrows the buffer pool, the
  next checkpoint fails and discards what earlier ones had already persisted. The
  backfill checkpointed after every 32-vector chunk: on a fresh database, 1342 vectors
  written that way grew the file from 3 MB to 771 MB and left **0** behind. In
  production this showed up as `Embedding-Backfill fertig (1251)` in the log with
  `embed_pending` still at 1210. (#118)
  The backfill now writes `EMBED_BACKFILL_PORTION` (default 300) vectors per Kuzu session
  and rebinds the session in between — all 1342 survive, and the file grows to 151 MB
  instead of 771 MB. It also samples one entity after each portion and stops with an
  `ERROR` if the checkpoint threw the portion away.
- **The startup backfill runs before uvicorn.** Rebinding the Kuzu session tolerates no
  concurrent access. A restore therefore costs about a minute of startup time, covered by
  a `start_period` of 300s in the compose healthcheck; with nothing to backfill (the
  normal case) startup is unchanged. At runtime one portion is written per run and the
  next run continues with the rest. (#118)

## [0.8.30] – 2026-09-03

### Fixed
- **kg.db cannot silently eat the disk any more.** Kuzu never returns space when a
  property is overwritten — a checkpoint rewrites the column and leaves the old
  version in the file, and there is no `VACUUM`. On 2026-09-03 the container
  segfaulted in the 60s WAL checkpoint; `restart: unless-stopped` restarted it 264
  times, and every start rewrote all 1291 embedding vectors (bge-m3, 1024 dim).
  kg.db grew from ~680 MB to 27 GB, filled the 30 GB partition and took the
  neighbouring containers with it. Three guards now bound this: Compose uses
  `restart: on-failure:5` so a crash loop ends; the backfill refuses to write above
  `KG_MAX_MB` or below `KG_MIN_FREE_MB` free disk; and a start with kg.db above
  `KG_REBUILD_MB` compacts it via dump → fresh DB → import (backed up to
  `BACKUP_DIR` beforehand, old DB kept if that fails). (#116)
- **Read tools are annotated `readOnlyHint`, so plan mode stops asking.** Claude
  Code gates MCP calls in plan mode on the server-supplied `readOnlyHint`
  annotation, independently of the permission allowlist: without the annotation
  it defaults to "not read-only" and prompts on every call, even for tools the
  user has explicitly allowed. `memory_search` and `memory_get_context` now
  declare `annotations={"readOnlyHint": True}`. The writing tools (`memory_add`,
  `memory_relate`) keep prompting, which is correct. Takes effect after the
  client reconnects, since `tools/list` is fetched on connect. (#115)

### Added
- `/api/status` reports `db_mb` plus both thresholds, so the bloat is visible before
  it becomes an outage. (#116)

## [0.8.29] – 2026-09-01

### Fixed
- **Auto-memory log directory now follows `CLAUDE_CONFIG_DIR`.** The hook and the
  SessionStart check derive it from the config dir, but `lib/extractor.py` was pinned to
  `~/.claude`. In a session with its own config dir the write and read paths diverged:
  `errors.log` in the profile dir, `last-run.json` in the global one. The check never saw
  a success there and reported `Auto-Memory gestört` on every start while the ingest was
  in fact running fine. The fault message also names the real `errors.log` path now
  instead of always the global one. (#113)
- **Documented that the cleanup hour must sit outside the LLM host's sleep schedule.**
  The nightly run also backfills missing embedding vectors, so if `AI_REM_OLLAMA_URL` or
  `EMBED_URL` point at a machine that sleeps at night, both go nowhere: the judge stays
  silent (`ollama_used=false`) and the backfill dies at the dimension probe before writing
  a single chunk — leaving `embed_pending` stuck, since the nightly run is its only trigger
  besides container start. Noted in `docker-compose.yml` and both language versions of
  `docs/hooks-and-automation`. (#112)
- **`vault-secret-reminder` no longer fires on `gh … view/diff/list`.** Those print
  foreign text — PR descriptions, issue bodies — which can quote `gh auth login` or a
  401. Observed on a `gh pr view` of the very PR that introduced the hook. Added to the
  display-command exemption; `gh api`, `gh pr merge` and friends still report, since
  there a 401 is the call's own. (#111)

## [0.8.28] – 2026-08-30

### Changed
- **Web UI: navigation moved into the logo row.** The page links used to hang off the
  end of the metadata line, in a different order and selection on every page. They are
  now a proper header nav to the right of the logo — same seven entries everywhere,
  the current page underlined in the accent colour. The line below the logo keeps only
  what belongs to the page itself (counts, hints). (#109)

## [0.8.27] – 2026-08-30

### Added
- **`vault-secret-reminder` hook.** A `PostToolUse` hook on `Bash` scans command output
  for auth/credential failures and injects a reminder to pull the secret from mykeyvault
  (`vault_list_items` → `vault_run_with_secret`) instead of asking the user for a token
  or an interactive login. Deterministic rather than a pinned preference: costs no
  routine slot and no tokens per turn, fires exactly at the failure. Shipped via
  `/hooks/vault-secret-reminder.py` and registered by `scripts/setup.py`, so every newly
  set up device gets it. Display commands (`git diff/log/show`, `grep`, `cat`, …) are
  exempt — there an auth pattern is almost always quoted text. (#102)
- Two regression tests in `tests/test_changelog.py` guard the release bookkeeping that
  slipped through four releases: every version heading must have its compare-link
  definition, the Unreleased link must diff against the newest section, and the version
  anchor at the top of `README.md` / `README.de.md` must match `server.py:VERSION`.
  They run in the existing `tests` CI job, so a release PR that forgets either fails
  before the tag is pushed. (#107)

### Changed
- Dependency bumps: `cryptography` 50.0.0 → 50.0.1 (#103), `numpy` 2.5.1 → 2.5.2 (#94).

## [0.8.26] – 2026-08-30

### Fixed
- **Patch history caught up.** The compare-link definitions at the end of this file
  stopped at `[0.8.22]`, so `[0.8.23]`–`[0.8.25]` rendered as plain text and
  `[Unreleased]` still diffed against v0.8.22. Links for the three missing releases
  added, `[Unreleased]` moved to v0.8.26.
- **Version anchor in both READMEs** was still pointing at v0.8.21 while the code
  had shipped v0.8.25 — updated in `README.md` and `README.de.md`.

## [0.8.25] – 2026-08-29

### Fixed
- **A failed WAL checkpoint no longer passes as success.** `_checkpoint_wal` logged
  every error as a warning and returned normally ("failures are uncritical"). During
  the backfill on 2026-08-29 a chunk checkpoint failed with `buffer pool is full`
  and the run still reported `Embedding-Backfill fertig (993)`. It only got away
  with it because the *next* chunk's checkpoint merged the same writes 1.4 s later —
  the last chunk has no such successor. The checkpoint is now retried once (the
  failure is typically transient), returns a bool, and the backfill forces a final
  checkpoint after the loop. Success is reported only when every checkpoint went
  through; otherwise an `ERROR` names the pool size. The run is deliberately not
  aborted midway: written chunks are valid and the backfill is idempotent.
- **`force=True` was a no-op when no WAL file existed** — a bare `return` in the
  `except OSError` branch. Dirty pages hang off the buffer pool, not the WAL size,
  so the forced checkpoint is exactly the one that must still run. The quietest of
  the data-loss paths.
- **SIGTERM handler alongside `atexit`.** `docker stop` sends SIGTERM, where
  `atexit` does not run — so the final checkpoint was skipped on *every* container
  restart. If the process is killed mid-rewrite, a half-written `kg.db` remains:
  `count()` still answers, every column access segfaults. Verified in operation —
  before the fix a restart left 917 of 993 entities without a vector, after it none.

### Added
- `embed_pending` and `embed_enabled` in `/api/status`, surfaced in the web UI and
  in the SessionStart status line. If the number stays put across restarts, the
  vectors did not survive the checkpoint — previously visible only as a warning in
  the volatile log ring.
- Failed checks in the SessionStart status line are now marked `❌` instead of `✗`,
  which was near-indistinguishable from `✓`. `scripts/setup.py` keeps `✗`: it writes
  straight to the TTY and is guarded against cp850 Windows consoles.

### Changed
- Buffer pool recommendation for `EMBED_URL` setups raised from 512 to 768 MB
  (`MEM_LIMIT` 1536m); ~1000 entities at 1024 dimensions make for a ~280 MB `kg.db`.

## [0.8.24] – 2026-08-23

### Added
- The nightly cleanup now also hunts for **stale content**, not just duplicates:
  entries asserting perishable infrastructure facts (IPs, `host:port`, container
  and port lists, versions) that have not been checked against reality for
  `CLEANUP_VERIFY_AFTER_DAYS` (default 90) are proposed as a new `verify` pending
  item. A regex prefilter and a llama-server judgment keep conceptual knowledge
  out. Nothing is ever archived automatically — suspects only ever reach the
  review queue, resolvable in the `/cleanup` UI ("Passt noch" / "Verwerfen") or
  via `/memory-cleanup`, which is told to verify live rather than guess. The
  verification age counts from `extra.verify_checked` (set by the check on every
  outcome, acting as the cooldown) or the existing `geprueft_am`/`verifiziert_am`/
  `korrigiert_am`/`erhoben_am`/`gemessen_am` markers, and only falls back to
  `updated_at` — which every `memory_add` resets and which therefore says nothing
  about when a fact was last confirmed. Candidates per run are capped by
  `CLEANUP_VERIFY_MAX_PER_RUN` (default 5). (#100)

### Fixed
- `deploy.sh` ships `requirements-embed.txt`. The Dockerfile has copied it since
  the embedding split, but the file was missing from the `FILES` list, so the
  remote build failed at `COPY` (`"/requirements-embed.txt": not found`) and
  compose silently kept the previously pulled Docker Hub image. A deployment
  therefore ran days-old code while the deploy looked like it had only printed a
  build error. (#99)

## [0.8.23] – 2026-08-20

### Fixed
- The client setup no longer aborts halfway through installing the CLI. `fetch_to`
  treated an empty response body as a failed download, but `lib/__init__.py` is
  legitimately 0 bytes — so `install_cli()` bailed out right after writing
  `bin/ai-rem` and before making it executable. The result was a half-installed
  CLI with an empty `lib/`, and the auto-memory hook silently logged
  "CLI not found (set $AI_REM_CLI)" at every session end. Only a transport error
  now counts as a failure; the guard against truncating an existing file is kept.
  Affects every platform, Windows included, since bash and PowerShell load the
  same `setup.py`. (#97)
- `setup-config.json` reaches the container via bind mount
  (`./setup-config.json:/app/setup-config.json:ro`) instead of relying on the
  Dockerfile `COPY`, which only runs on a local build. A deployment running the
  public Docker Hub image served the example placeholders from `/setup-config`,
  so every fresh client install inherited `ollama_url: http://your-server:11434`
  and reported `llm ✗`. `_load_setup_cfg` uses `isfile` so a missing host file
  (which Docker materialises as a directory) falls back instead of raising. (#97)
- `ai-rem ingest` no longer reports `{"skipped": "llm_down"}` while the session-start
  report shows `llm ✓`. The hook read the llama URL from `settings-template.json`,
  but the CLI only ever reads the environment, so the setup now also writes
  `AI_REM_LLAMA_URL` (from the setup-config `ollama_url`) into `settings.json`. (#97)
- A model reply with trailing text after the JSON object is parsed instead of
  discarded. `response_format=json_object` is not a hard grammar in llama.cpp, and
  the occasional trailing prose made `json.loads` raise `Extra data`, pushing an
  otherwise usable extraction into the fallback queue. Parsing now stops after the
  first complete object; a reply that does not start with JSON is still an error. (#97)

### Changed
- Default llama-server URL is `http://myai:11436` instead of `http://myubuntu:11434`,
  which has been permanently stopped since 2026-08-04. Applies to the extraction
  hook, the session-start check, the nightly cleanup judge and the compose default. (#97)

## [0.8.22] – 2026-08-16

### Added
- `CHANGELOG.md` as the source of truth for release notes, with entries for every
  release back to v0.8.14. On a tag push the publish workflow now creates the
  GitHub release from the matching section and renders the three most recent
  entries into the Docker Hub description. Both had drifted: releases were written
  by hand (and forgotten from v0.8.14 on), and Docker Hub never showed what
  changed between versions at all. (#95)
- Two CI gates keep every pull request in the changelog: one rejects a PR that
  does not touch `CHANGELOG.md` (Dependabot and the `no-changelog` label are
  exempt), the other checks on release PRs that every PR merged since the last tag
  is referenced in the new section — that one also catches the exempted ones.
  `scripts/changelog.py` provides `section`, `latest` and `verify` for both the
  workflow and the tests. (#95)

## [0.8.21] – 2026-08-15

### Changed
- `/discover` selects injected knowledge precision-first: fusion of full-text,
  name and semantic hits, token matching restricted to words in entity *names*
  instead of substrings anywhere, and semantic recall only from two keywords on.
  When no source contributes anything, nothing is injected instead of the three
  least unsuitable entries. Measured against 40 real session prompts, the share of
  relevant injections rose from ~25% to ~75%. (#92)

## [0.8.20] – 2026-08-15

### Changed
- Hybrid search merges lexical and semantic hits via reciprocal-rank fusion
  instead of listing every substring hit (ordered by modification date) ahead of
  the first semantic one. Name matches beat description matches. On the live
  graph: MRR 0.38 → 0.82, top-1 5/13 → 10/13, no relevant hit left outside the
  top 15. (#90)

### Documentation
- Switching to an external embedding backend requires raising
  `KUZU_BUFFER_POOL_SIZE_MB` (512 instead of 256): the 1024-dimensional vectors
  otherwise make the backfill's WAL checkpoint fail, the vectors never reach the
  database and are recomputed on every start. (#89)

## [0.8.19] – 2026-08-15

### Fixed
- Cosine threshold for the external embedding backend lowered from 0.55 to 0.50.
  The old calibration used entity names as the query — those appear verbatim in
  the embedded passage and flatter the separation. With real paraphrases, correct
  hits land at 0.43–0.66; 0.55 cut off three of eight test cases. (#87)

## [0.8.18] – 2026-08-15

### Fixed
- Input is truncated to `EMBED_MAX_CHARS` (default 2000) before embedding.
  fastembed truncates silently at the model limit while llama.cpp rejects
  oversized input with HTTP 500 — a single long entry took down the whole backfill
  chunk. Error messages now carry the server's response instead of a bare
  "HTTP Error 500". (#85)

## [0.8.17] – 2026-08-15

### Added
- The embedding backend is switchable: the default stays in-process (fastembed, no
  external service required), `EMBED_URL` points at an OpenAI-compatible
  `/v1/embeddings` endpoint instead. If that endpoint is down, entries are stored
  without a vector and search keeps working lexically; the backfill catches up
  later. A backend switch is detected via the vector dimension and recomputes all
  vectors — in both directions. (#83, closes #33)
- Additional image variant `vX.Y.Z-slim` / `latest-slim` without fastembed and the
  bundled model: 413 MB → 162 MB. It requires `EMBED_URL`, otherwise search stays
  purely lexical. (#83)

## [0.8.16] – 2026-08-15

### Changed
- Dependency updates: fastmcp 3.4.4 → 3.4.7 (#73), cryptography 49.0.0 → 50.0.0
  (#74), actions/setup-python 6 → 7 (#59).

## [0.8.15] – 2026-08-14

### Changed
- The session start line no longer prints counters (`ai-rem: N Entities, M
  Relationen` and the 🧠 line with auto-memory details), just `ai-rem ✓` and
  `Auto-Memory ✓` or the respective failure state. The full diagnosis of a fault
  still reaches the assistant as context. When the auto-memory hook is not
  registered at all, the feature counts as deliberately disabled and is no longer
  reported as broken. (#80)

## [0.8.14] – 2026-08-14

### Fixed
- The open-tasks display at session start was broken twice over: the server
  filtered completed tasks against a literal list that happened to omit
  "abgeschlossen" — the status set by convention — and the hook compared the
  section header exactly while the server had long been appending a counter and
  context label. A "Zuletzt:" line with the five most recently updated open tasks
  was added on top. (#78)
- `system-check.py` accepts `AI_REM_LLAMA_URL`; it previously checked against the
  template default and falsely reported `llm ✗`. (#77)

### Added
- Compose network moved to IPv6 (`fd00:24:9:68::/64`, routed) (#76) and dual-stack
  bind instead of `uvicorn(host=…)`, with `HOST` now defaulting to `::` (#75).

[Unreleased]: https://github.com/markus7h/ai-rem/compare/v1.8.0...HEAD
[1.8.0]: https://github.com/markus7h/ai-rem/compare/v1.7.1...v1.8.0
[1.7.1]: https://github.com/markus7h/ai-rem/compare/v1.7.0...v1.7.1
[1.7.0]: https://github.com/markus7h/ai-rem/compare/v1.6.1...v1.7.0
[1.6.1]: https://github.com/markus7h/ai-rem/compare/v1.6.0...v1.6.1
[1.6.0]: https://github.com/markus7h/ai-rem/compare/v1.5.0...v1.6.0
[1.5.0]: https://github.com/markus7h/ai-rem/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/markus7h/ai-rem/compare/v1.3.2...v1.4.0
[1.3.2]: https://github.com/markus7h/ai-rem/compare/v1.3.1...v1.3.2
[1.3.1]: https://github.com/markus7h/ai-rem/compare/v1.3.0...v1.3.1
[1.3.0]: https://github.com/markus7h/ai-rem/compare/v1.2.7...v1.3.0
[1.2.7]: https://github.com/markus7h/ai-rem/compare/v1.2.6...v1.2.7
[1.2.6]: https://github.com/markus7h/ai-rem/compare/v1.2.5...v1.2.6
[1.2.5]: https://github.com/markus7h/ai-rem/compare/v1.2.4...v1.2.5
[1.2.4]: https://github.com/markus7h/ai-rem/compare/v1.2.3...v1.2.4
[1.2.3]: https://github.com/markus7h/ai-rem/compare/v1.2.2...v1.2.3
[1.2.2]: https://github.com/markus7h/ai-rem/compare/v1.2.1...v1.2.2
[1.2.1]: https://github.com/markus7h/ai-rem/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/markus7h/ai-rem/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/markus7h/ai-rem/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/markus7h/ai-rem/compare/v0.9.2...v1.0.0
[0.9.2]: https://github.com/markus7h/ai-rem/compare/v0.9.1...v0.9.2
[0.9.1]: https://github.com/markus7h/ai-rem/compare/v0.8.32...v0.9.1
[0.9.0]: https://github.com/markus7h/ai-rem/compare/v0.8.32...26efcb9
[0.8.32]: https://github.com/markus7h/ai-rem/compare/v0.8.31...v0.8.32
[0.8.31]: https://github.com/markus7h/ai-rem/compare/v0.8.30...v0.8.31
[0.8.30]: https://github.com/markus7h/ai-rem/compare/v0.8.29...v0.8.30
[0.8.29]: https://github.com/markus7h/ai-rem/compare/v0.8.28...v0.8.29
[0.8.28]: https://github.com/markus7h/ai-rem/compare/v0.8.27...v0.8.28
[0.8.27]: https://github.com/markus7h/ai-rem/compare/v0.8.26...v0.8.27
[0.8.26]: https://github.com/markus7h/ai-rem/compare/v0.8.25...v0.8.26
[0.8.25]: https://github.com/markus7h/ai-rem/compare/v0.8.24...v0.8.25
[0.8.24]: https://github.com/markus7h/ai-rem/compare/v0.8.23...v0.8.24
[0.8.23]: https://github.com/markus7h/ai-rem/compare/v0.8.22...v0.8.23
[0.8.22]: https://github.com/markus7h/ai-rem/compare/v0.8.21...v0.8.22
[0.8.21]: https://github.com/markus7h/ai-rem/compare/v0.8.20...v0.8.21
[0.8.20]: https://github.com/markus7h/ai-rem/compare/v0.8.19...v0.8.20
[0.8.19]: https://github.com/markus7h/ai-rem/compare/v0.8.18...v0.8.19
[0.8.18]: https://github.com/markus7h/ai-rem/compare/v0.8.17...v0.8.18
[0.8.17]: https://github.com/markus7h/ai-rem/compare/v0.8.16...v0.8.17
[0.8.16]: https://github.com/markus7h/ai-rem/compare/v0.8.15...v0.8.16
[0.8.15]: https://github.com/markus7h/ai-rem/compare/v0.8.14...v0.8.15
[0.8.14]: https://github.com/markus7h/ai-rem/compare/v0.8.13...v0.8.14
