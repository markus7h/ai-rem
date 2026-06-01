# ai-rem v0.0.7 — Preferences Pinning & erweitertes Context-Limit

Additiver Release. Schema-Migration läuft automatisch beim Serverstart (neue Spalte `pinned`). Kein manueller Migrationsschritt, bestehende Daten bleiben unangetastet.

## Highlights

- **Preferences Pinning** — Einzelne Preferences können mit `pinned=True` markiert werden und erscheinen beim `memory_get_context`-Aufruf immer ganz oben, unabhängig von `updated_at`. Für dauerhaft verhaltensrelevante Regeln (z. B. „kurz antworten", „session-start health check") sichert das, dass sie nie aus dem Kontext fallen.
- **Context-Limit für Preferences: 8 → 12** — `memory_get_context` lädt jetzt bis zu 12 statt 8 Preferences. Gepinnte Einträge belegen garantiert Plätze oben; die restlichen Slots füllen sich nach Aktualität.

## Was sich konkret ändert

### Schema-Migration: `pinned`-Spalte

Beim ersten Start nach dem Update führt `init_schema()` automatisch aus:

```sql
ALTER TABLE Entity ADD pinned STRING DEFAULT ''
```

Log-Eintrag zur Bestätigung: `Schema migration: pinned column added`. Keine Datenmigration nötig — alle bestehenden Entities erhalten `pinned = ''` (nicht gepinnt) als Default.

### `memory_add`: neuer Parameter `pinned`

```python
memory_add(
    name="Feedback: kurz antworten",
    type="Preference",
    description="...",
    context="private",
    pinned=True          # neu
)
```

- `pinned=True` → Wert `'true'` in der Spalte, Return-String enthält `📌`
- `pinned=False` (Default) → unverändertes Verhalten

Bestehende Preferences können nachträglich gepinnt werden, indem `memory_add` mit demselben `name` erneut aufgerufen wird (MERGE-Semantik — nur `pinned` ändert sich).

### `memory_get_context`: Sortierung und Limit

```cypher
ORDER BY e.pinned DESC, e.updated_at DESC
LIMIT 12
```

Gepinnte Preferences (`pinned = 'true'`) sortieren vor ungepinnten; innerhalb beider Gruppen gilt `updated_at DESC`. Im Output werden gepinnte Einträge mit `📌` markiert.

### Interne Refaktorierung: `_entity_has_column`

Die bisherige Hilfsfunktion `_entity_has_context_column` wurde auf eine generische `_entity_has_column(column)` umgestellt, die beide Migrationen (`context`, `pinned`) nutzen.

## Upgrade

Server-Seite:

```bash
cd /path/to/ai-rem
docker compose up -d --build
```

Client-Seite: kein Setup-Lauf nötig, keine CLAUDE.md-Änderung.

### Rollback

Server: `git checkout v0.0.6 && docker compose up -d --build`. Die `pinned`-Spalte bleibt in der DB (Kuzu unterstützt kein DROP COLUMN), schadet aber nicht — v0.0.6 liest sie einfach nicht.

## Geänderte Dateien

- `server.py` — `_entity_has_column`, `_migrate_pinned_column`, `memory_add(pinned=)`, `memory_get_context` Preferences-Query
- `README.md`, `README.en.md` — Version-Badge auf v0.0.7
- `release-notes-v0.0.7.md` — neu
