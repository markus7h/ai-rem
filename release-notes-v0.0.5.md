# ai-rem v0.0.5 — Routinen & Anweisungen im Session-Kontext

Kleiner, additiver Release. Kein Schema-Change, keine Breaking Changes, kein Migrations-Schritt — ein Upgrade ist ein reiner `docker compose up -d --build`.

## Highlights

- **Neue Section "Routinen & Anweisungen" in `memory_get_context()`** — `Preference`-Entities erscheinen jetzt prominent oben im Context-Output, damit Anweisungen, die im Knowledge Graph leben, beim Sitzungsstart zuverlässig befolgt werden.
- **CLAUDE.md-Template im Setup-Script geschärft** — die generierte Startup-Anweisung sagt jetzt explizit "Routinen & Anweisungen befolgen", statt nur "als Arbeitsgrundlage nutzen". Greift auf neuen Maschinen via `bash <(curl -s …/setup)`.

## Motivation

Bisher war es nicht möglich, *durchsetzbare* Konventionen oder Routinen über den Knowledge Graph zu transportieren: `memory_get_context()` lieferte nur Tasks, Projekte und letzte Decisions/Solutions/Probleme. `Preference`-Entities existierten zwar als Typ, blieben aber unsichtbar im Default-Kontext.

Mit v0.0.5 lassen sich Anweisungen wie "beim Sitzungsstart auch dies-und-jenes prüfen" als Preference-Entity ablegen — sie tauchen automatisch oben im Kontext auf und werden befolgt. Damit kann der KG als zentrale Quelle für sitzungsübergreifende Routinen dienen, ohne dass projektspezifische Logik in den ai-rem-Code wandert.

## Was sich konkret ändert

### `memory_get_context()`: neue Section "Routinen & Anweisungen"

Direkt nach dem optionalen Topic-Block (wenn `topic` gesetzt) und vor "Offene Tasks" wird jetzt — sofern Preferences existieren — eine eigene Section gerendert:

```
## Routinen & Anweisungen
- **<name>**: <description (max 120 Zeichen)>
- …
```

Limit: 8 Einträge, sortiert nach `updated_at DESC`. Context-Filter (`work`/`private`/leer) wirkt wie bei allen anderen Sections.

### Setup-Script: stärkere Wording

Schritt 2 in der CLAUDE.md-Vorlage lautet jetzt:

> `memory_get_context()` aufrufen → Kontext laden, dort aufgeführte Routinen & Anweisungen befolgen, Kontext als Arbeitsgrundlage nutzen.

Bestehende CLAUDE.md-Dateien werden nicht überschrieben (Setup-Script ist weiterhin idempotent via `grep -q "Knowledge Graph Memory"`). Auf existierenden Maschinen kann die Zeile bei Bedarf manuell angepasst werden.

## Upgrade

```bash
docker compose up -d --build
```

Keine Migration, keine Backup-Notwendigkeit. Bestehende Preferences im KG erscheinen sofort nach dem Restart in `memory_get_context()`.

### Rollback

Falls nötig: `git checkout v0.0.4 && docker compose up -d --build`. Daten bleiben unangetastet.

## Geänderte Dateien

- `server.py` — neue Section in `memory_get_context()` (+18); CLAUDE.md-Wording im SETUP_SCRIPT (~1 Zeile)
- `README.md`, `README.en.md` — Version-Badge auf v0.0.5
- `release-notes-v0.0.5.md` — neu
