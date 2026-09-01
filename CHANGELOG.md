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

## [Unreleased]

### Fixed
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
  there a 401 is the call's own.

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

[Unreleased]: https://github.com/markus7h/ai-rem/compare/v0.8.28...HEAD
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
