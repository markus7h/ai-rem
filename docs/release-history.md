# Release-Historie (Archiv)

Konsolidierte Release-Notes der frühen Versionen (v0.0.4 – v0.1.5), ursprünglich als
einzelne `release-notes-v*.md` im Repo-Root. **Ab v0.2.0 stehen Release-Notes in den
[GitHub Releases](https://github.com/markus7h/ai-rem/releases).**

---

# ai-rem v0.1.5 — Generisches Starter-Template

Onboarding-Release für neue Nutzer. Kein Schema-Change, keine API-Änderung.

## Highlights

- **`setup-config.example.json`** — Ein generisches Starter-Template wird jetzt mit dem
  Image ausgeliefert. Frische Deployments seeden beim `/setup-ai-rem` ein sinnvolles Set an
  Verhaltens-Preferences statt mit leerem Knowledge Graph zu starten.

## Was sich konkret ändert

### Problem

Die persönliche `setup-config.json` ist gitignored und landet daher nie im öffentlichen
Image. Die Route `/setup-config` gab dann `{}` zurück → das Setup-Skript seedete nur zwei
Stub-Entities. Ein neuer Nutzer startete ohne jede Verhaltens-Preference.

### Lösung

- Neue, eingecheckte **`setup-config.example.json`** (gleiches Schema, rein generisch, keine
  personenbezogenen Daten) mit:
  - 6 Verhaltens-Preferences: Plan-first, knapp antworten, ai-rem vor Rückfragen prüfen,
    Recall-Vorgehen, Halluzinationen vermeiden, Wissen proaktiv speichern
    (jeweils im `Regel / Why / How to apply`-Format)
  - 2 ai-rem-Tool-Entities (`skill_setup_ai_rem`, `skill_ai_rem_prefedit`)
  - 11 generische Allow-Permissions + 5 universelle Deny-Regeln (Secret-Schutz)
- **Route-Fallback** in `server.py`: `/setup-config` liefert die persönliche
  `setup-config.json`, wenn vorhanden — sonst das Example, statt `{}`.
- Der bestehende Seeding-Mechanismus (`entities` → `memory_add`) bleibt unverändert; eine
  eigene `setup-config.json` überschreibt das Template komplett.

## Upgrade

```bash
docker compose up -d --pull always
```

## Geänderte Dateien

- `setup-config.example.json` — neu (generisches Starter-Template).
- `server.py` — `VERSION` auf 0.1.5; `/setup-config`-Route fällt auf das Example zurück.
- `README.md` / `README.de.md` — Version-Badge v0.1.5, Abschnitt „Personal Configuration"
  beschreibt den Template-Fallback.
- `release-notes-v0.1.5.md` — neu.

---

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

---

# ai-rem v0.1.2 — Update-Check

Kleiner Feature-Release. Kein Schema-Change, keine API-Änderung.

## Highlights

- **`memory_check_update`** — Neues MCP-Tool das die installierte Version anzeigt und mit dem neuesten Tag auf Docker Hub vergleicht. Gibt `✓ aktuell` oder `⚠ Update verfügbar` aus.

## Was sich konkret ändert

### VERSION-Konstante

`VERSION = "0.1.2"` als Konstante in `server.py` — wird von `memory_check_update` gelesen und dient künftig als zentrale Versionsreferenz.

### `memory_check_update()`

Neues Tool ohne Parameter. Ablauf:

1. Liest `VERSION` (installierte Version)
2. Fragt Docker Hub API ab: `hub.docker.com/v2/repositories/magic3arkus/ai-rem/tags/`
3. Filtert `latest`-Tag heraus, findet höchsten Semver-Tag
4. Vergleicht und gibt Status aus

Timeout: 5 Sekunden. Bei Netzwerkfehler: installierte Version wird trotzdem angezeigt, Docker Hub als "nicht erreichbar" markiert.

`memory_status` bleibt unverändert schlank (kein Netzwerkzugriff).

## Upgrade

```bash
docker compose up -d --pull always
```

## Geänderte Dateien

- `server.py` — `VERSION`-Konstante, `memory_check_update` Tool neu.
- `README.md` — Version-Badge auf v0.1.2.
- `release-notes-v0.1.2.md` — neu.

---

# ai-rem v0.1.1 — Smart Truncation für memory_search

Qualitäts-Release. Kein Schema-Change, keine API-Änderung.

## Highlights

- **Satzbasierte Kürzung in `memory_search`** — Statt harter Zeichenlimits werden lange Beschreibungen jetzt intelligent gekürzt: erster und letzter Satz bleiben vollständig erhalten, der Mittelteil wird bei Bedarf mit `…` komprimiert. Einträge unter 400 Zeichen erscheinen ungekürzt.
- **Erweitertes Limit in `memory_get_context` (topic)** — Topic-Suche zeigt jetzt bis zu 300 Zeichen (vorher 100), damit Einträge auch bei gezielter Suche vollständiger erscheinen.

## Was sich konkret ändert

### `_smart_truncate(text, threshold=400)`

Neue Hilfsfunktion direkt vor `memory_search`. Funktionsweise:

1. Text ≤ 400 Zeichen → unverändert zurückgeben
2. Text in Sätze splitten (`re.split` auf `. `)
3. ≤ 2 Sätze → harter Cut bei 400 Zeichen + `…`
4. ≥ 3 Sätze → erster Satz + gekürzter Mittelteil + `…` + letzter Satz

Damit bleiben Einträge nach dem Schema `Regel: X. Why: Y. How to apply: Z.` auch bei langen Texten vollständig verwendbar — `X` und `Z` sind immer sichtbar.

### `memory_search`

Ausgabe-Zeile nutzt jetzt `_smart_truncate(r[2])` statt `r[2][:100]`.

### `memory_get_context` (topic-Zweig)

Limit von `[:100]` auf `[:300]` erhöht. `memory_get_context` ohne topic (Überblicks-Modus) und `memory_list` sind unverändert — dort sind kompakte Snippets weiterhin sinnvoll.

## Upgrade

```bash
docker compose up -d --pull always
```

Oder lokal neu bauen:

```bash
docker build -t magic3arkus/ai-rem:latest .
docker compose up -d --force-recreate
```

Kein Client-Update nötig.

## Geänderte Dateien

- `server.py` — `_smart_truncate()` neu, `memory_search` und `memory_get_context` (topic) angepasst.
- `README.md` — Version-Badge auf v0.1.1.
- `release-notes-v0.1.1.md` — neu.

---

# ai-rem v0.0.9 — Backup-Skip & Preferences-Link

Kleiner Pflege-Release. Kein Schema-Change, keine API-Änderung.

## Highlights

- **Automatisches Backup läuft nur noch bei Änderungen** — Wenn der Graph seit dem letzten geplanten Backup unverändert ist, wird der Scheduler-Tick übersprungen. Spart Backup-Slots und I/O auf ruhigen Tagen.
- **Preferences-Link auf der Hauptseite** — `/ui` verlinkt im Header direkt auf `/prefs`, damit der Preferences-Manager ohne Umweg erreichbar ist.

## Was sich konkret ändert

### Backup-Signature

Neue Helferfunktion `_graph_signature()` bildet einen billigen Fingerprint aus
`(entity_count, relation_count, max(entity.updated_at), max(relation.created_at))`.
Bei jedem `_do_backup()` wird die Signature in `.config.json` (`last_backup_signature`)
mitgeschrieben. Der Scheduler vergleicht vor dem nächsten geplanten Backup die aktuelle
Signature mit der gespeicherten — bei Gleichheit wird übersprungen (Log:
`Scheduled backup skipped: no graph changes since last backup`).

Das manuelle "Backup now" in der Web-UI läuft weiterhin immer durch — explizite User-Aktion
schlägt Skip-Logik.

### UI-Header

`/ui` zeigt jetzt im Sub-Header neben Entity-/Relations-Count einen
`Preferences →`-Link auf `/prefs`.

## Upgrade

Server:

```bash
cd ~/mydocker/compose-files/ai-rem
docker compose up -d --build
```

Oder per Deploy-Script aus dem Repo:

```bash
./deploy.sh
```

Kein Client-Update nötig (keine Tool-Signatur-Änderung).

### Rollback

`git checkout v0.0.8 && docker compose up -d --build`. Das zusätzliche Feld
`last_backup_signature` in `.config.json` schadet älteren Versionen nicht
(unbekannte Keys werden ignoriert).

## Geänderte Dateien

- `server.py` — `_graph_signature()`, `_do_backup()` schreibt Signature mit,
  `_scheduler_loop()` überspringt bei Gleichheit, `_UI_HTML` Sub-Header um
  `Preferences →`-Link erweitert.
- `README.md` — Version-Badge auf v0.0.9.
- `release-notes-v0.0.9.md` — neu.

---

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

---

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

---

# ai-rem v0.0.6 — Setup-Script plattformunabhängig, SessionStart-Hook & Auto-Memory-Konsolidierung

Additiver Release mit Fokus auf das Bootstrap-Erlebnis. Kein Schema-Change, kein Daten-Migrationsschritt. Bestehende Maschinen profitieren beim nächsten `bash <(curl -s …/setup)` — Datenbestand im Knowledge Graph bleibt unangetastet.

## Highlights

- **`SETUP_SCRIPT` ist jetzt plattformunabhängig** — läuft identisch auf macOS und Linux. Keine BSD-vs-GNU-`sed`-Falle mehr, kein `jq` als Hard-Dependency. Abhängigkeiten reduziert auf: `bash`, `curl`, `python3`, `claude` CLI.
- **Neuer SessionStart-Hook `ai-rem-bootstrap.py`** — verifiziert die ai-rem-Verbindung beim Sitzungsstart automatisch (Statuszeile `"ai-rem: N Entities, M Relationen"` oder `"nicht erreichbar"`). Ersetzt die "PFLICHT memory_status() beim Start"-Anweisung in CLAUDE.md, spart den ersten Modell-Turn und erkennt Verbindungsprobleme sofort.
- **Datei-basierte Auto-Memory wird deaktiviert (`autoMemoryEnabled: false`)** — alle Auto-Memory-Verhalten (user / feedback / project / reference) laufen jetzt über ai-rem als einzige Wissensquelle. Mapping der Memory-Typen auf ai-rem-Entity-Typen ist im neuen CLAUDE.md-Block dokumentiert.
- **CLAUDE.md-Block deutlich erweitert** — explizite Regeln für *was* gespeichert wird (Entity-Typ pro Memory-Kategorie), *was nicht* (Code-Patterns, git-Historie, Fix-Rezepte), und *Verifikation vor Empfehlung* aus Memory.
- **Setup-Skript ist jetzt re-run-idempotent** — der CLAUDE.md-Block wird beim erneuten Setup ersetzt (vorher: übersprungen wenn vorhanden), `settings.json`-Einträge werden ohne Duplikate gemerged.

## Motivation

v0.0.5 hat das *Wo* (Routinen im KG-Kontext sichtbar) gelöst. v0.0.6 löst das *Wann* und *Wie zuverlässig*:

- Die "PFLICHT beim Start memory_status + memory_get_context"-Anweisung war fragil: ging ein Modell-Turn drauf, und bei nicht erreichbarem Server gab es Verzögerung statt klarer Fehlermeldung.
- Datei-basierte Auto-Memory (Claude Codes eingebauter `~/.claude/projects/<x>/memory/`-Mechanismus) lief parallel zum KG — zwei Memory-Systeme nebeneinander. ai-rem soll *die* Wissensquelle sein.
- Das Setup-Skript hatte ein BSD/GNU-`sed`-Problem (`sed -i` ohne Argument bricht auf macOS) und nutzte `jq`, das auf vielen Linux-Distros nicht out-of-the-box vorhanden ist.

## Was sich konkret ändert

### Setup-Script: plattformunabhängig, Python statt jq

`SETUP_SCRIPT` in `server.py` wurde komplett umgeschrieben:

- Pre-Flight-Check: `python3` und `curl` müssen vorhanden sein (sauberer Fehlerabbruch, wenn nicht).
- Alle JSON-Manipulationen an `settings.json` laufen über `python3 - << 'PYEOF'`-Heredocs (statt `sed -i` / `jq`).
- Der neue Hook wird als Python-Datei (`ai-rem-bootstrap.py`) abgelegt, nicht als Bash — Hook nutzt nur `urllib.request` aus der Standardbibliothek, keine externen Tools im Hot-Path.

### SessionStart-Hook: `~/.claude/hooks/ai-rem-bootstrap.py`

Macht den vollen MCP-Streamable-HTTP-Handshake:

1. `POST initialize` → Session-ID aus `mcp-session-id`-Response-Header ziehen
2. `POST notifications/initialized`
3. `POST tools/call memory_status` → SSE-Frame oder JSON-Body parsen

Gibt JSON mit `systemMessage` aus, das von Claude Code als Statuszeile im UI angezeigt wird. Bei Fehler (Timeout, Server down, Parse-Fehler) immer Fallback auf `"ai-rem: nicht erreichbar"` — nie stiller Crash. Endpoint überschreibbar via `AI_REM_ENDPOINT`-Umgebungsvariable.

In `settings.json` wird der Hook idempotent unter `hooks.SessionStart[matcher="*"]` einsortiert (Timeout 10 s). Bestehende SessionStart-Hooks (z. B. eigener SMB-Mount-Hook) bleiben erhalten.

### `autoMemoryEnabled: false`

Der Setup-Step setzt `autoMemoryEnabled: false` in `~/.claude/settings.json`. Damit verschwindet das `# auto memory`-System-Prompt-Snippet von Claude Code, und ai-rem ist die einzige Memory-Senke.

Bestehende Datei-Memory unter `~/.claude/projects/<sanitized-cwd>/memory/` wird **nicht** automatisch migriert. Manuelles Migrieren (per `memory_add` als Entity) wird empfohlen, falls dort wertvolle Einträge liegen. Die Dateien selbst bleiben liegen — werden nur nicht mehr gelesen.

### Erweiterter CLAUDE.md-Block

Der `## Knowledge Graph Memory (ai-rem)`-Block enthält jetzt:

- **Entity-Typ-Mapping** für die Auto-Memory-Kategorien: `Preference` (UserFacts + Feedback, Feedback-Einträge mit Name-Präfix `Feedback:`), `Project`, `Topic` (Pointer auf externe Systeme), `Person`, plus die bestehenden Typen.
- **"Nicht speichern"-Liste** (Code-Patterns, git-Historie, Fix-Rezepte, ephemere Sitzungsdetails) — auch wenn der User explizit darum bittet.
- **"Vor Empfehlung verifizieren"-Regel** — Erinnerungen sind Behauptungen über damals, nicht über jetzt.
- **Body-Struktur für Regel-Einträge**: Regel + `**Why:**` + `**How to apply:**`.

### Re-Run-Idempotenz

Der Setup-Step:
- Erkennt bestehenden `## Knowledge Graph Memory (ai-rem)`-Block (alte ODER neue Version) und **ersetzt** ihn (vorher: skip, wodurch alte Wording auf bestehenden Maschinen hängen blieb).
- Mergt `permissions.allow` ohne Duplikate.
- Trägt den SessionStart-Hook nur ein, wenn er noch nicht da ist.
- Setzt `autoMemoryEnabled` nur, wenn es noch nicht `false` ist (Output zeigt entsprechende Hinweise).

## Upgrade

Server-Seite:

```bash
cd /path/to/ai-rem
docker compose up -d --build
```

Client-Seite (jede Maschine):

```bash
bash <(curl -s http://<KG_HOST>:3456/setup)
```

Beim ersten Run nach Upgrade werden auf bestehenden Maschinen:
- Der alte CLAUDE.md-Block durch den neuen ersetzt.
- `autoMemoryEnabled: false` neu in `settings.json` eingetragen — **Achtung:** Datei-basierte Auto-Memory wird damit abgeschaltet.
- Der SessionStart-Hook neu angelegt.

Keine Migration nötig auf Server-Seite. Knowledge-Graph-Daten bleiben unangetastet.

### Rollback

Server: `git checkout v0.0.5 && docker compose up -d --build`.

Client: Manuell aus `settings.json` den SessionStart-Hook-Entry und `autoMemoryEnabled: false` entfernen. Den alten CLAUDE.md-Block aus v0.0.5 wieder einsetzen (steht in `release-notes-v0.0.5.md`).

## Breaking-Change-Hinweis

Wer Datei-basierte Auto-Memory aktiv nutzt (`~/.claude/projects/<x>/memory/MEMORY.md` mit Einträgen), sollte vor dem Setup-Run die wichtigen Einträge als ai-rem-Entities (`Preference`-Typ für Feedback / Arbeitsweisen) anlegen. Sonst sind sie nach dem Setup zwar noch auf der Platte, werden aber nicht mehr von Claude gelesen.

## Geänderte Dateien

- `server.py` — `SETUP_SCRIPT` komplett umgeschrieben (+167 / -39)
- `README.md`, `README.en.md` — Version-Badge auf v0.0.6
- `release-notes-v0.0.6.md` — neu

---

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

---

# ai-rem v0.0.4 — Speed & Robustness Refactor

Schwerpunkt dieses Releases: echte Bugs raus, N+1-Queries weg, Event-Loop unblockieren, Schema sauberer. Keine Breaking Changes für bestehende Backups (JSON-Format bleibt kompatibel). Beim ersten Start mit dieser Version läuft eine automatische Schema-Migration mit Vor-Backup.

## Highlights

- **Connection-Pool statt globalem Lock** — gleichzeitige Requests laufen jetzt parallel statt seriell.
- **Async-Routen blockieren den Event-Loop nicht mehr** — DB-Calls werden via `asyncio.to_thread` ausgelagert.
- **N+1-Queries in Import/Restore beseitigt** — Bulk-Imports sind bei ~1000 Entities deutlich schneller (Erwartung: ~10×).
- **`context` als first-class Spalte** — `LIMIT N` mit Context-Filter liefert jetzt korrekt N Treffer (vorher: konnten weniger sein, weil in Python nachgefiltert wurde).
- **Mehrere Correctness-Bugs gefixt** — kein stilles Überschreiben mehr bei langen Entity-Namen, keine Stub-Entities mehr durch Tippfehler in `memory_relate`, keine korrupte Backup-Config bei gleichzeitigen Writes.

---

## Robustness-Fixes

### `_id()` — keine stillen Kollisionen mehr bei langen Namen
Namen ≤ 64 Zeichen verhalten sich unverändert (bestehende IDs bleiben bit-identisch). Bei längeren Namen wird ein 8-Zeichen-Hash des vollen Namens angehängt — so kollidieren zwei verschiedene Entities mit identischem 64-Zeichen-Präfix nicht mehr still.

### `memory_relate` legt keine `Unknown`-Stub-Entities mehr an
Bisher hat ein Tippfehler in `from_name` oder `to_name` eine leere Entity mit `type='Unknown'` erzeugt — der Graph wurde stillschweigend verschmutzt. Jetzt: klare Fehlermeldung mit dem unbekannten Namen, keine Schreiboperation. **Breaking** für Callsites, die auf das Auto-Create-Verhalten bauen — wenn nötig, vorher explizit via `memory_add` anlegen.

### `memory_add` ist atomar
Wurde umgestellt auf ein einzelnes `MERGE ... ON CREATE ... ON MATCH ...`. Race zwischen Check und Insert ist nicht mehr möglich.

### Backup-Config: kein Schreib-Race mehr
`fcntl.flock` (shared für Read, exklusiv für Write) plus atomic-rename via Temp-Datei. Scheduler-Thread und HTTP-Routes können `.config.json` nicht mehr gleichzeitig korruptieren.

### Backup-Files: Path-Confinement gegen Traversal
Neue `_safe_backup_path()`-Hilfe — verifiziert via `realpath`, dass der Pfad wirklich unter `BACKUP_DIR` liegt. Greift bei Download und Delete.

### Backup-Delete idempotent
TOCTOU-Race zwischen `os.path.exists` und `os.remove` aufgelöst (`try/except FileNotFoundError`).

### Backup-Dateien werden atomar geschrieben
`*.tmp` schreiben, dann `os.replace` — kein abgeschnittenes Backup mehr, wenn der Container während des Schreibens stoppt.

---

## Performance

### Connection-Pool ersetzt globalen Lock
Kuzu-Connection-Objekte sind nicht thread-safe, die Database hingegen schon. Es gibt jetzt einen kleinen Pool (Default 4 Connections) — Requests bekommen je eine Connection aus dem Pool und laufen parallel. Konfigurierbar via neuer Umgebungsvariable:

```
KUZU_POOL_SIZE=4
```

### Event-Loop wird nicht mehr blockiert
Alle `async`-Routen (`/export`, `/import`, `/api/restore`, `/api/status`, `/api/backup/now`) führen ihre DB- und Filesystem-Arbeit jetzt via `asyncio.to_thread` aus. Vorher: ein laufender `/export` hat alle anderen Requests blockiert, bis er fertig war.

### N+1-Queries in Import/Restore beseitigt
`/import` und `/api/restore` haben pro Entity und pro Relation eine separate Existenz-Query abgesetzt — bei 1000 Entities + 1000 Relations waren das ~4000 unnötige Roundtrips. Jetzt wird einmal vorab das komplette Set existierender IDs und Relation-Tupel geladen, danach reines O(1)-Lookup in Python.

### `_dump_graph()` shared
`/export` und der Backup-Scheduler benutzen denselben Helper — kein duplizierter Dump-Code mehr.

---

## Schema-Migration: `context` als first-class Spalte

Bisher lag `context` in der `extra`-JSON-Spalte. Das hatte zwei Probleme:

1. **Correctness-Bug:** Queries machten `LIMIT N` _vor_ dem Python-seitigen Context-Filter — bei `LIMIT 10` und `context="private"` kamen oft weniger als 10 Treffer zurück, obwohl mehr existierten.
2. **Speed:** Pro Zeile ein JSON-Parse plus Python-Loop.

**Was passiert beim ersten Start mit v0.0.4:**

1. Automatisches Vor-Backup nach `/backups/backup_pre_context_<timestamp>.json` (am alten Schema, ohne `context`-Spalte).
2. `ALTER TABLE Entity ADD context STRING DEFAULT ''`.
3. Backfill: für jede Entity mit `extra.context` Wert wird die neue Spalte gesetzt.
4. Log-Zeile: `Schema migration: context column added, N entities backfilled`.

Die Migration ist idempotent — bei späteren Starts wird per `CALL TABLE_INFO('Entity')` geprüft und übersprungen.

### Backup-JSON-Format
Entity-Objekte haben jetzt ein zusätzliches Top-Level-Feld `context`. Restore-Pfad ist rückwärtskompatibel: liest bevorzugt das neue Feld, fällt sonst auf `extra.context` zurück. Alte Backups können also weiter restored werden.

### Neues Response-Feld
`/import` und `/api/restore` geben jetzt zusätzlich `relations_skipped` zurück (Relations, deren Endpoints nicht existieren — wurden bisher still verschluckt).

---

## Sonstiges

- Scheduler-Thread reagiert auf `atexit`, beendet die Schleife sauber statt mitten in der `sleep` abgewürgt zu werden.
- Reduziertes Log-Rauschen bei korrupten Timestamps (statt stilles `pass`).
- Spezifischere Exception-Catches in Routen (`json.JSONDecodeError` statt `except Exception`).

---

## Upgrade

```bash
docker compose pull   # falls Image aus Registry
# oder lokal:
docker compose build && docker compose up -d

# Migration verifizieren:
docker compose logs ai-rem | grep -E "Pre-migration backup|context column"
```

Erwartete Log-Zeilen beim ersten Start nach Upgrade:

```
Pre-migration backup written: backup_pre_context_YYYY-MM-DD_HH-MM-SS.json
Schema migration: context column added, N entities backfilled
Schema ready — DB at /data/kg.db
```

### Rollback

Das Vor-Backup liegt in `./backups/backup_pre_context_*.json`. Falls etwas schiefläuft:

```bash
docker compose down
rm -rf ./data/kg.db   # Vorsicht — löscht die Datenbank
docker compose up -d  # leeres Schema (Stand v0.0.3) wird angelegt
curl -X POST -F "file=@backups/backup_pre_context_<timestamp>.json" -F "mode=replace" http://<server>:3456/api/restore
```

---

## Neue / geänderte Env-Variablen

| Variable | Default | Wirkung |
|---|---|---|
| `KUZU_POOL_SIZE` | `4` | Größe des Kuzu-Connection-Pools. Höher = mehr Parallelität, mehr RAM. |

---

## Geänderte Dateien

- `server.py` (+397 / -215)
- `.env.example` (+4)
- `docker-compose.yml` (+1)

---
