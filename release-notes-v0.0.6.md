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
cd /home/markus/mydocker/compose-files/ai-rem
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
