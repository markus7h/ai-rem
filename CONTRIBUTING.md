# Contributing

Contributions to ai-rem are welcome. Please read these guidelines before you open a
pull request. Questions, ideas and bug reports go to
[GitHub Issues](https://github.com/markus7h/ai-rem/issues).

## General Steps and Guidelines

* **Coordinate first:** open or comment on an issue before larger work, so we can agree
  on the approach before you invest time.
* **Use forks and branches:** `main` is protected. Do not push to it directly; fork the
  repo (or use a branch) and open a pull request.
* **Stay in sync:** rebase on or merge `main` regularly while you work on an issue.
* **Include tests:** a PR that adds a feature or fixes a bug needs a test under `tests/`
  that fails without the change.
* **Update the changelog:** add your entry under `## [Unreleased]` in `CHANGELOG.md`
  ([Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format). CI checks this; use
  the label `no-changelog` only when nothing user-visible changes.
* **Keep PRs small:** one topic per PR. Large PRs are hard to review and less likely to
  be merged.
* **No secrets:** never commit tokens, keys or a filled-in `setup-config.json` / `.env`.
  The `detect-secrets` pre-commit hook helps (`pipx install pre-commit && pre-commit install`).
* **Maintainer discretion:** the maintainer has the final say on merges. Following these
  guidelines does not guarantee that a PR is accepted.

## Running the Checks Locally

CI (`.github/workflows/ci.yml`) must be green: `lint-compile`, `tests` and `image-smoke`.

```bash
pip install -r requirements.txt pytest ruff
ruff check --select E9,F63,F7,F82 .
EMBED_ENABLED=0 AI_REM_API_TOKEN=dummy pytest -q tests/
```

The tests need the LadybugDB native library. If it is not available on your platform,
run them in a Linux container:

```bash
docker run --rm -v "$PWD":/src:ro python:3.12-slim sh -c '
  apt-get update -qq && apt-get install -yqq git >/dev/null
  cp -r /src /w && cd /w && rm -f setup-config.json
  pip install -q -r requirements.txt pytest
  EMBED_ENABLED=0 AI_REM_API_TOKEN=dummy pytest -q tests/'
```

## Commit Messages

Use [Conventional Commits](https://www.conventionalcommits.org/) with an optional scope,
e.g. `fix(models): …`, `feat(setup): …`, `chore(deps): …`.

## Releases

Releases are done by the maintainer: bump `VERSION` in `server.py`, the version links in
`README.md` / `README.de.md` and the `CHANGELOG.md` section, merge, then push the tag
`vX.Y.Z`. The tag builds and publishes the Docker image.

Thank you for your time and effort!
