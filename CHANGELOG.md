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

### Added
- `CHANGELOG.md` as the source of truth for release notes, with entries for every
  release back to v0.8.14. On a tag push the publish workflow now creates the
  GitHub release from the matching section and renders the three most recent
  entries into the Docker Hub description. Both had drifted: releases were written
  by hand (and forgotten from v0.8.14 on), and Docker Hub never showed what
  changed between versions at all. (#94)
- Two CI gates keep every pull request in the changelog: one rejects a PR that
  does not touch `CHANGELOG.md` (Dependabot and the `no-changelog` label are
  exempt), the other checks on release PRs that every PR merged since the last tag
  is referenced in the new section — that one also catches the exempted ones.
  `scripts/changelog.py` provides `section`, `latest` and `verify` for both the
  workflow and the tests. (#94)

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

[Unreleased]: https://github.com/markus7h/ai-rem/compare/v0.8.21...HEAD
[0.8.21]: https://github.com/markus7h/ai-rem/compare/v0.8.20...v0.8.21
[0.8.20]: https://github.com/markus7h/ai-rem/compare/v0.8.19...v0.8.20
[0.8.19]: https://github.com/markus7h/ai-rem/compare/v0.8.18...v0.8.19
[0.8.18]: https://github.com/markus7h/ai-rem/compare/v0.8.17...v0.8.18
[0.8.17]: https://github.com/markus7h/ai-rem/compare/v0.8.16...v0.8.17
[0.8.16]: https://github.com/markus7h/ai-rem/compare/v0.8.15...v0.8.16
[0.8.15]: https://github.com/markus7h/ai-rem/compare/v0.8.14...v0.8.15
[0.8.14]: https://github.com/markus7h/ai-rem/compare/v0.8.13...v0.8.14
