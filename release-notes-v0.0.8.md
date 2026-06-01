# ai-rem v0.0.8 — Preferences Web UI & Setup-Automatisierung

Additiver Release. Schema-Migration läuft automatisch beim Serverstart (neue Spalte `sort_order`). Kein manueller Migrationsschritt, bestehende Daten bleiben unangetastet.

## Highlights

- **Preferences Web UI (`/prefs`)** — Vollständiger Browser-basierter Preferences-Manager: pin/unpin, Context ändern, Position setzen, löschen — alles per Klick, kein Terminal nötig. Klick auf den Namen klappt die vollständige Beschreibung inline auf. Aufrufbar über `/ai-rem:prefedit`.
- **Manuelle Reihenfolge (`sort_order`)** — Neue Spalte in der Entity-Tabelle. Sortierung: gepinnt → sort_order numerisch → updated_at. Preferences mit expliziter Position verdrängen nie die gepinnten.
- **`memory_preference_update`** — Neues MCP-Tool: ändert `context`, `pinned` und `sort_order` einer Preference gezielt, ohne `description` oder andere Felder zu überschreiben.
- **Setup vollständig script-basiert** — `/setup-ai-rem` ist jetzt ein einziger Bash-Aufruf. Schritt 2+3 (Preferences & Tool-Entities anlegen) laufen direkt im Setup-Script via HTTP API — kein Claude-Token-Verbrauch, keine manuellen MCP-Calls mehr.

## Was sich konkret ändert

### Schema-Migration: `sort_order`-Spalte

```sql
ALTER TABLE Entity ADD sort_order STRING DEFAULT ''
```

Log-Eintrag zur Bestätigung: `Schema migration: sort_order column added`. Alle bestehenden Entities erhalten `sort_order = ''` (automatisch nach Datum) als Default.

### `memory_preference_update(name, context, pinned, sort_order)`

Neues MCP-Tool für gezielte Feld-Updates:

```python
memory_preference_update(name="Feedback: kurz antworten", pinned=True, sort_order=2)
```

Nur übergebene Parameter werden aktualisiert — `description`, `name`, `type` bleiben unverändert (MATCH + SET statt MERGE).

### Web UI `/prefs`

Neue Seite im bestehenden Dark-Theme:
- Tabelle: 📌 · Name (klickbar) · Context-Dropdown · Positions-Feld · Datum · Löschen-Button
- Klick auf Name → vollständige Beschreibung klappt inline auf
- Alle Änderungen werden sofort gespeichert (kein Save-Button)
- Link zurück zur Hauptseite `/ui`

Neue API-Endpoints: `POST /api/preferences/update`, `POST /api/preferences/delete`

### `/ai-rem:prefedit` Slash-Command

Gibt die URL des Web-Managers aus — in VS Code direkt klickbar.

### Setup: ein Schritt statt drei

`/setup-ai-rem` (bzw. `bash <(curl -s .../setup)`) erledigt jetzt alles automatisch:
1. MCP registrieren
2. SessionStart-Hook + settings.json
3. CLAUDE.md aktualisieren
4. Slash-Commands installieren (`/setup-ai-rem`, `/ai-rem:prefedit`)
5. `~/.claude/ai-rem/pref-tui.py` installieren
6. Alle 13 Preferences & Tool-Entities via HTTP API anlegen

### Neues Prinzip: Script-first

Alle Aktionen die als Script ausführbar sind, laufen als Script — kein Token-Verbrauch für wiederholbare Operationen. Als Preference `Feedback: Script-first statt Token-Verbrauch` im KG verankert.

## Upgrade

Server:

```bash
cd /path/to/ai-rem
docker compose up -d --build
```

Client (jede Maschine):

```bash
bash <(curl -s http://<KG_HOST>:3456/setup)
```

### Rollback

Server: `git checkout v0.0.7 && docker compose up -d --build`. Die `sort_order`-Spalte bleibt in der DB (Kuzu unterstützt kein DROP COLUMN), schadet v0.0.7 aber nicht.

## Geänderte Dateien

- `server.py` — `_migrate_sort_order_column`, `memory_preference_update`, `_PREFS_HTML`, `/prefs`-Route, `/api/preferences/update|delete`, `PREFEDIT_CMD_MD`, `CMD_MD`, `SETUP_SCRIPT` (Entities-Sektion), `PREF_TUI_SCRIPT`
- `README.md` — Version-Badge auf v0.0.8
- `release-notes-v0.0.8.md` — neu
