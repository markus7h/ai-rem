# ai-rem v0.1.3 — Resilientes Auto-Memory & nicht-destruktiver Nightly-Cleanup

Größerer Release rund um Robustheit und Wartung des Gedächtnisses. **Schema-Change** (neue
`archived`-Spalte, automatische Migration), neue MCP-Tools, neue Web-UI-Seite.

## Highlights

- **md-Fallback bei Ollama-Ausfall** — Fällt der Extraktor aus, geht die Session nicht verloren:
  eine heuristische Extraktion landet in `~/.claude/auto-memory/fallback.md` (via `@`-Import in
  `CLAUDE.md` weiterhin im Kontext) und wird für später vorgemerkt.
- **Catch-up** — Sobald Ollama wieder erreichbar ist, zieht `ai-rem catchup` die verpassten
  Sessions sauber strukturiert nach ai-rem nach und **leert das md** wieder.
- **Nächtlicher Cleanup im Container, nicht-destruktiv** — Ein Daemon-Thread räumt täglich
  Dubletten & überholte Einträge auf, indem er sie **archiviert statt löscht** (Historie bleibt).
- **`/cleanup` Web-UI** — Konfiguration, manueller Lauf, Pending-Reviews und Lauf-Log im Browser.
- **Sichtbarkeit** — Der SessionStart zeigt, was zuletzt gespeichert wurde (`🧠 N Entities, M Rel`).

## Was sich konkret ändert

### Schema: `archived`-Spalte (Migration)

Neue `Entity.archived`-Spalte (`'true'`/`''`, analog `pinned`), per Migration automatisch
ergänzt. Archivierte Einträge werden in `memory_get_context`, `memory_search` und `memory_list`
**standardmäßig ausgeblendet** — Opt-in über den neuen Parameter `include_archived=true`. Über
`memory_get_relations` bleiben sie für die Historie auffindbar.

### Neue MCP-Tools (beide nicht löschend)

- **`memory_archive(name, compressed_description?, superseded_by?)`** — markiert einen Eintrag als
  `archived`, komprimiert optional die Beschreibung (Original gesichert in `extra.original_descr`)
  und verlinkt via `VERALTET_DURCH`.
- **`memory_merge(canonical_name, duplicate_name)`** — faltet eine Dublette in den kanonischen
  Eintrag (Relationen umhängen, Unique-Info ergänzen), archiviert die Dublette und verlinkt sie
  via `DUPLIKAT_VON`. Kein Delete.

### Auto-Memory: Fallback, Catch-up, Sichtbarkeit

- `lib/extractor.py`: `_ollama_up()`-Probe; bei Ollama-Ausfall md-Fallback (heuristische Extraktion)
  + `pending.jsonl`-Queue; neue `catchup()`-Funktion; `last-run.json` für die SessionStart-Anzeige.
- `bin/ai-rem catchup` — neues Subcommand; die Hooks (SessionStart + PreCompact/SessionEnd) stoßen
  Catch-up an, sobald Ollama wieder da ist.

### Nightly-Cleanup

- Container-Daemon (`_cleanup_scheduler_loop`, default 03:00), konfigurierbar über `/cleanup`.
- Kandidaten heuristisch (Namens-Ähnlichkeit, erledigte/überholte Einträge), Entscheidung via
  Ollama wenn erreichbar. **Backup vor jeder Mutation**, Blast-Radius-Cap, **`Preference`/pinned/
  bereits archivierte Einträge werden nie angefasst.**
- Mehrdeutiges & alles bei Ollama-Ausfall → Review-Queue. Der Slash-Command **`/memory-cleanup`**
  (still beim Session-Start ausgelöst) lässt Claude diese Fälle mit Urteil abarbeiten — Inhalte
  strikt als Daten, Backup vorher, nur `memory_merge`/`memory_archive`.

### Web-UI

- `/cleanup` mit `/api/cleanup/{config,now,log,pending}`. Verlinkt aus `/ui` und `/prefs`.

## Aufräumen

- Entfernt: `bin/hook-auto-memory.sh` (veraltete Hook-Variante), `lib/extractor.py.bak`.

## Upgrade

```bash
docker compose up -d --pull always
```

Danach auf jeder Client-Maschine das Setup neu ausführen (neue Hooks, `/memory-cleanup`-Command,
`fallback.md`-Import in `CLAUDE.md`) und Claude Code neu starten:

```bash
bash <(curl -s http://<SERVER_IP>:3456/setup)
```

## Geänderte Dateien

- `server.py` — `archived`-Spalte+Migration, `_archived_clause` + Default-Ausblendung, Tools
  `memory_archive`/`memory_merge`, Nightly-Cleanup (`_cleanup_run`, Scheduler, Ollama-Helper),
  `/cleanup`-Routen + HTML, `SYSTEM_CHECK_PY`/`AUTO_MEMORY_HOOK_PY` (Ollama-Check, Catch-up,
  last-run, Cleanup-Direktive), SETUP_SCRIPT (`@import`, `/memory-cleanup`), `VERSION = 0.1.3`.
- `lib/extractor.py` — md-Fallback, `catchup`, `_ollama_up`, `last-run.json`.
- `bin/ai-rem` — `catchup`-Subcommand.
- `README.md` — Auto-Memory-Failure-Mode (Fallback/Catch-up) + Nightly-Cleanup-Abschnitt.
- `release-notes-v0.1.3.md` — neu.
