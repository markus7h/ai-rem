# Hooks & Automatisierung

[← Zurück zur README](../README.de.md)

ai-rem bringt drei Claude-Code-Hooks mit, die den Graph ohne Handarbeit befüllen und sauber
halten: **Auto-Memory** (Session → Graph), **Nightly-Cleanup** (Dedup/Archivieren) und
**Plan-Speicherung** (Pläne → offene Tasks). Alle drei werden vom Client-Setup-Skript deployt.

> **Wo die Hook-Quellen liegen:** `hooks/*.py` in diesem Repo — normale, lintbare Python-Dateien.
> `server.py` liest sie beim Import und liefert sie unverändert unter `/hooks/<name>.py` aus;
> das Client-Setup holt sie von dort. Immer die Dateien bearbeiten, nie eine Kopie in `server.py`.
> Dasselbe gilt für `scripts/setup.py` (ausgeliefert als `/setup.py`) und die Web-UI unter
> `templates/*.html`.

---

## Auto-Memory (PreCompact + SessionEnd → ai-rem)

Das eingebaute Markdown-Auto-Memory von Claude Code wird durch einen Transcript-Extraktor ersetzt, der **strukturierte Entities und Relations** in ai-rem schreibt.

**Ablauf:** `PreCompact`/`SessionEnd`-Hook → `ai-rem ingest --transcript <pfad>` → llama-server (`mistral-small3.2:24b` auf `AI_REM_OLLAMA_URL`, OpenAI-kompatibel `/v1/chat/completions`, default `http://myubuntu:11434`) extrahiert JSON → Bulk-Upsert via MCP → Log nach `~/.claude/auto-memory/<timestamp>.json`.

**CLI** (`bin/ai-rem`, reine stdlib — kein venv nötig, läuft auf jedem `python3 ≥3.8` unter Windows/Linux/macOS):

```bash
ai-rem status
ai-rem search "auto-memory"
ai-rem show "<name>"   # vollständige, ungekürzte description + extra + relations (via /export)
ai-rem list --type Decision
ai-rem ingest --transcript <session.jsonl> [--dry-run] [--model mistral-small3.2:24b]
```

**Anti-Rekursion:** Transcripts unter 500 Zeichen werden übersprungen, `/tmp/ai-rem-ingest.lock` verhindert verschachtelte Läufe.

**Detached-Ausführung:** Der Hook startet sich selbst per `start_new_session=True` neu und kehrt in Millisekunden zurück — die Extraktion auf einem lokalen 24b-Modell dauert Minuten, länger als jedes vernünftige Hook-Timeout. Ohne das würde entweder das Session-Ende blockieren oder der Ingest mittendrin abgeschossen. Jeder Lauf wird in `.processed` unter `<session_id>:<transcript_size>` vermerkt, damit `PreCompact` und das spätere `SessionEnd` derselben Session beide verarbeitet werden (mit der session_id allein als Schlüssel ging alles nach der ersten Compaction still verloren).

**Context-Budget:** `MAX_TOTAL_CHARS` (45k) muss in den Context des llama-servers passen (`n_ctx=32768` beim 24b-Mistral, 45k Zeichen ≈ 16k Token) — zusammen mit System-Prompt und Antwort. Zu grosse Transcripts verlieren ihre **Mitte**, nie das Ende: Entscheidungen und Erkenntnisse stehen am Session-Ende; die erste USER-Message (die Aufgabenstellung) bleibt immer erhalten. Vor dem Hochsetzen `/props` am llama-server prüfen.

**Failure-Mode (md-Fallback + Catch-up):** Ist llama-server nicht erreichbar, geht die Session nicht verloren — eine heuristische Extraktion wird an `~/.claude/auto-memory/fallback.md` angehängt (via `@`-Import in `CLAUDE.md`, bleibt also im Kontext) und das Transcript in `pending.jsonl` vorgemerkt. Sobald llama-server wieder erreichbar ist, zieht `ai-rem catchup` (von den SessionStart- und PreCompact/SessionEnd-Hooks ausgeführt) die vorgemerkten Sessions sauber nach ai-rem nach und **leert das md**. Der Hook bricht nie `/compact` oder das Session-Ende; harte Fehler gehen nach `~/.claude/auto-memory/errors.log`.

**Sichtbarkeit:** Jeder erfolgreiche Lauf schreibt `~/.claude/auto-memory/last-run.json`; der SessionStart-Check zeigt eine Zeile wie `🧠 N Entities, M Rel` (mit `(md-Fallback)`, wenn llama-server down war).

**Ausfallerkennung:** Weil der Hook bewusst still scheitert (rc=0, damit er weder `/compact` noch das Session-Ende bricht), blieb ein kaputtes Auto-Memory bisher unsichtbar — es lief hier einmal 7 Wochen lang tot. Der SessionStart-Check vergleicht jetzt die mtime von `last-run.json` gegen `errors.log` und meldet auf zwei Kanälen: `🧠 ✗ gestört` in der Statuszeile plus die volle Diagnose (letzter Fehler, wahrscheinliche Ursache, Log-Pfad) als `additionalContext`, damit auch der Assistent es sieht und ansprechen kann. Drei Auslöser: Fehler neuer als der letzte Erfolg, gar kein `last-run.json`, oder seit über 7 Tagen nichts gespeichert.

**Konfigurations-Env:**
- `AI_REM_ENDPOINT` — MCP-URL (default `http://localhost:3456/mcp`)
- `AI_REM_LLAMA_URL` (Alt-Name: `AI_REM_OLLAMA_URL`) — llama-server-Basis-URL (OpenAI-kompatibel, `/v1` wird intern angehängt; Env hat Vorrang, dabei `AI_REM_LLAMA_URL` vor `AI_REM_OLLAMA_URL`; sonst `ollama_url` aus setup-config / settings-template; default `http://myubuntu:11434`); Modell ist fix via `AI_REM_LLM_MODEL` (default `mistral-small3.2:24b`), da llama-server genau ein Modell hostet
- `AI_REM_CLI` — expliziter CLI-Pfad (sonst Discovery über bekannte Mount-Pfade und `$PATH`). Das Setup trägt hier `~/.local/share/ai-rem/bin/ai-rem` ein, die lokal installierte Kopie. Zeigt der Wert stattdessen in einen Clone auf einem Netzlaufwerk, bricht der Hook bei jedem Session-Ende still mit `ai-rem CLI not found` ab, sobald der Mount hängt — dann `/setup` erneut laufen lassen. Gehört in den `env`-Block von `~/.claude/settings.json`, damit Hooks ihn erben.

---

## Nightly-Cleanup (nicht-destruktiv: archivieren statt löschen)

Ein Daemon-Thread im Container fährt täglich einen Wartungslauf (default 03:00, konfigurierbar in der `/cleanup`-Web-UI). Er erkennt Dubletten und überholte Einträge (Heuristiken + llama-server, wenn erreichbar) und **archiviert** sie, statt zu löschen: Der Eintrag wird `archived` getaggt, optional komprimiert (Original in `extra.original_descr` gesichert) und via `DUPLIKAT_VON` / `VERALTET_DURCH` verlinkt. Archivierte Einträge sind aus `memory_get_context`/`search`/`list` standardmäßig ausgeblendet (Opt-in mit `include_archived=true`), bleiben aber für die Historie via `memory_get_relations` erreichbar. **`Preference`, gepinnte und bereits archivierte Einträge werden nie angefasst.** Jeder Lauf sichert zuerst; das Log ist in der `/cleanup`-Web-UI einsehbar.

Mehrdeutige Fälle (und alles, wenn llama-server nachts down war) landen in einer Review-Queue. Eine nicht-leere Queue wird beim Session-Start nur als informativer Hinweis angezeigt (keine Auto-Ausführung). Abarbeiten auf zwei Wegen: **(a)** in der `/cleanup`-Web-UI, wo jedes Pending-Item beide Beschreibungen mit **Mergen/Archivieren** (anwenden) und **Verwerfen** (beide behalten) zeigt (`POST /api/cleanup/resolve`); oder **(b)** der Slash-Command `/memory-cleanup`, der die Einträge von Claude mit Urteil abarbeiten lässt. Beide nutzen dieselben nicht-destruktiven `memory_merge` / `memory_archive`-Operationen — nichts wird gelöscht.

> **llama-server-Erreichbarkeit:** Der nächtliche Judge braucht einen erreichbaren llama-server unter `AI_REM_OLLAMA_URL`; das beurteilende Modell ist fix via `CLEANUP_LLM_MODEL` (default `mistral-small3.2:24b`). In der mitgelieferten `docker-compose.yml` ist der Default `http://myubuntu:11434` (pro Deployment via `.env` überschreibbar). Ist es nicht gesetzt/erreichbar, läuft der Cleanup trotzdem, schiebt aber jedes mehrdeutige Paar in die Review-Queue statt es automatisch zu beurteilen (`ollama_used=false` im Lauf-Log).

---

## Plan-Speicherung (ExitPlanMode → ai-rem)

Ein `PostToolUse`-Hook auf `ExitPlanMode` (`hooks/save-plan.py`) speichert jeden finalisierten Plan als **offenen `Task`** in ai-rem — so werden Pläne eine zentrale, maschinenübergreifende Liste statt nur Slug-Dateien unter `~/.claude/plans/`. Der SessionStart-Hook `system-check.py` zeigt diese offenen `Task`s (inkl. Pläne) automatisch an — eine neue Session startet direkt mit der Liste; alternativ *„gibt es offene Pläne?"* fragen und auswählen.

**Felder** kommen aus einem kleinen Frontmatter-Block, den Claude oben in jede Plan-Datei schreibt (kein Raten aus dem Fließtext):

```
---
name: "Plan: <Titel>"
description: "<ein kurzer Satz>"
status: offen
---
```

Der Hook liest das Frontmatter der zuletzt geänderten Plan-Datei und upsertet via `memory_add` (`type: Task`, `extra.kind=plan`, `extra.plan_file`, `extra.status`). Upsert über `name` → keine Dubletten. Erledigte Pläne werden archiviert (`memory_archive`); der Status liegt zentral in ai-rem (cross-machine). Fail-silent: blockiert nie `ExitPlanMode`.

**Installation:** wird vom Client-Setup automatisch deployt — `install_hooks()` holt `save-plan.py` nach `~/.claude/hooks/` (chmod +x) und registriert den `PostToolUse: ExitPlanMode`-Hook in `~/.claude/settings.json`. Kein manueller Schritt (der Datei-Header dokumentiert die Standalone-Installation als Referenz).
