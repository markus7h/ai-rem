# MCP-Funktionsreferenz & Workflows

Funktionsbeschreibung der drei MCP-Server (ai-rem, mykeyvault, tools-registry) und der
typischen End-to-End-Abläufe. Topologie siehe [`architecture.md`](./architecture.md).

> Die Tool-Listen beschreiben den **aktuell registrierten** Stand (was Claude in der
> Session tatsächlich aufrufen kann). Wo der Repo-/README-Stand zusätzliche Helfer
> definiert, ist das vermerkt.

---

## 1. ai-rem — Langzeit-Gedächtnis (Knowledge Graph)

Persistenter Kontext als Graph aus **Entities** (typisiert) und **Relations**. Einzige
Quelle für dauerhaftes Wissen. Befüllt wird er **proaktiv** (`memory_add`/`memory_relate`
während der Arbeit, Workflow B) und über den **ai-rem Ingest-Hook** am Session-Ende
(Workflow C). Nicht zu verwechseln mit Claude Codes **nativem** datei-basiertem Auto-Memory
(Setting `autoMemoryEnabled`) — das ist bewusst **deaktiviert**, damit ai-rem die einzige
Memory-Senke bleibt.

**Entity-Typen:** `Person · Project · Task · Tool · Problem · Solution · Decision · Preference · Topic`

### Funktionen

| Tool | Zweck |
|---|---|
| `memory_get_context` | Relevanten Subgraph laden. Ohne Topic: offene Tasks + aktive Projekte + letzte Einträge + gepinnte Preferences. Mit Topic: direkt passender Ausschnitt. **Session-Start-Tool.** |
| `memory_search` | Hybride Suche (lexikalisch + semantisch) über Name/Beschreibung; kürzt lange Bodies (~120 Zeichen). |
| `memory_search_full` | Wie `search`, aber mit vollständiger Beschreibung (keine Kürzung). |
| `memory_add` | Entity anlegen **oder aktualisieren** (Upsert nach Name). Felder: name, type, description, context, pinned, extra (JSON). |
| `memory_relate` | Gerichtete Beziehung zwischen zwei Entities anlegen (z. B. NUTZT, ARBEITET_AN, GELÖST_DURCH, HÄNGT_AB_VON). |
| `memory_get_relations` | Alle Beziehungen einer Entity anzeigen. |
| `memory_list` | Entities nach Typ auflisten. |
| `memory_preference_update` | Einzelne Felder einer Preference ändern (context, pinned, sort_order) ohne die Beschreibung zu überschreiben. |
| `memory_set_project_context` | Projektkontext als `Project`-Entity anlegen/aktualisieren (dev_dir, repo, deploy_dir, deploy_host, deploy_cmd, skills, rules) — **feldweises Merge**, nicht übergebene Felder bleiben erhalten. |
| `memory_project_context` | Vollen Projektkontext in einem Aufruf laden: ungekürzter Record inkl. `extra` **plus** alle verknüpften Entities. Exakt oder per Fuzzy-Namenstreffer. |
| `memory_merge` | Zwei Duplikate nicht-destruktiv zusammenführen. |
| `memory_archive` | Entity archivieren statt löschen (Historie bleibt erhalten). |
| `memory_delete` | Entity + alle Kanten löschen. |
| `memory_status` | Kurzstatus: Anzahl Entities und Relationen. |
| `memory_check_update` | Installierte Version vs. Docker Hub prüfen. |

### Workflows

**A) Session-Start → Kontext laden**
1. SessionStart-Hook (`system-check.py`) ruft `memory_get_context` auf.
2. Liefert offene Tasks/Pläne, aktive Projekte, letzte Einträge, gepinnte Preferences.
3. Claude arbeitet mit diesem Kontext; Pfade/Funktionsnamen aus Memory vor Empfehlung gegen den Code verifizieren (Memory = Behauptung über damals).

**B) Proaktiv speichern (während der Arbeit)**
1. Etwas Überraschendes/Nicht-Offensichtliches gelernt → erst `memory_search` (Dublette?).
2. Existiert die Entity: `memory_add` updaten; sonst neu anlegen.
3. Mit `memory_relate` an verwandte Entities hängen (Project↔Task, Problem↔Solution …).

**C) ai-rem Ingest-Hook (Session-Ende)** — *nicht* Claude Codes natives Auto-Memory (das ist aus)
1. `auto-memory.py` (PreCompact/SessionEnd) reicht das Transcript an `ai-rem ingest`.
2. Der Extractor schickt es an **llama-server** (`myubuntu:11434`, OpenAI-kompatibel `/v1/chat/completions`, festes Modell, Default `mistral-small3.2:24b`, JSON-Antwort).
3. Strukturierte Entities/Relations → Bulk-Upsert über `/mcp`.
4. llama-server langsam/kalt/nicht erreichbar → Transcript wandert in die Queue (`pending.jsonl`); `ai-rem catchup` (läuft zu Beginn jedes Hook-Laufs) zieht es nach, sobald llama-server warm/erreichbar ist.
5. Voraussetzung in der Hook-Umgebung: `AI_REM_CLI` (CLI-Pfad) und `AI_REM_ENDPOINT` (z. B. `https://airem.lan/mcp`) — sonst kein Ingest.

**D) Nightly-Cleanup**
1. Daemon-Thread im Container sucht Dubletten/veraltete Einträge.
2. llama-server fällt Merge-/Archiv-Urteile; klare Fälle werden angewandt, unklare landen in der Review-Queue der Web-UI (`/cleanup`).

---

## 2. mykeyvault — Secrets ohne Leak in den Kontext

`mykeyvault-mcp` ist das MCP-Frontend vor `vault-api` (REST) → `bw serve` → Vaultwarden.
Designprinzip: Secrets gelangen **nie als Klartext** in den Chat/Prompt.

### Funktionen (aktuell registriert)

| Tool | Zweck |
|---|---|
| `vault_list_items` | Vault-Einträge auflisten (nur Name + Username, keine Secrets). |
| `vault_create_item` | Login-Eintrag anlegen (Passwort als Parameter, wird nicht zurückgegeben). |
| `vault_create_ssh_key` | SSH-Key-Eintrag anlegen (Private Key wird aus lokaler Datei gelesen, nie als Parameter übergeben). |
| `vault_get_ssh_public_key` | Öffentlichen SSH-Key + Fingerprint holen (kein Private Key). |

> **Repo-/README-Stand** definiert zusätzlich secret-injizierende Helfer
> (`vault_write_secret`, `vault_run_with_secret`, `vault_run_with_secret_file`), die ein
> Secret in eine Temp-Datei / Env-Variable geben und nur den Pfad bzw. das Kommando-Ergebnis
> zurückliefern. Diese sind in der aktuellen Session nicht registriert — bei Bedarf prüfen,
> ob der Container sie ausspielt.

**vault-api REST (hinter dem MCP):** `/health`, `/items`, `/item/{name}`, `/secret/{name}`,
`/ssh-key/{name}`, `POST /items`, `POST /ssh-keys`, `PUT /items/{name}`, `DELETE /items/{name}`
— alle außer `/health` mit Bearer-Token.

### Workflows

**A) Secret hinterlegen**
1. `vault_create_item` mit Name + Passwort (+ optional Username/Notes).
2. Eintrag landet in Vaultwarden; das Passwort taucht nirgends im Antwort-Payload auf.

**B) Token-Verteilung an ai-rem (zentral)**
1. Der gemeinsame Bearer-Token liegt als Vault-Item `ai-rem-api-token`.
2. `vault-api` liefert ihn (Bearer-geschützt) an Clients; ai-rem holt ihn pro Session im Hintergrund und schreibt den Header nach `~/.claude.json`.
3. Fällt mykeyvault aus → Clients nutzen den zuletzt gecachten Header (fail-soft).

**C) SSH-Key anlegen & nutzen**
1. `vault_create_ssh_key` liest den Private Key aus einer lokalen Datei und speichert ihn als Vault-Item (Typ 5).
2. `vault_get_ssh_public_key` gibt Public Key + Fingerprint zur Weitergabe (z. B. `authorized_keys`) — der Private Key verlässt den Vault nie über das MCP.

---

## 3. tools-registry — Scripts als Tools (lokal, live nachgeladen)

Lokaler stdio-MCP-Server am Mac. Registriert Scripts aus `scripts/<name>/` als Tools und
synchronisiert sie alle 5 s von der zentralen `tools-registry` (HTTP `:3457`) — **neue/geänderte
Scripts ohne MCP-Neustart**.

### Funktionen (aktuell registriert)

| Tool | Zweck |
|---|---|
| `list_scripts` | Meta-Tool: alle registrierten Scripts + Manifest-Metadaten (inputs/requires) auflisten. |
| `pipeline_run` | Meta-Tool: mehrere Script-Aufrufe verketten, mit Variablen-Interpolation zwischen den Schritten. |
| `ai_rem_token` | Liest den ai-rem-Bearer-Token aus `~/.claude.json` (`mcpServers.<server>.headers.Authorization`) → `{token, authorization}`. |
| `settings_sync` | Synchronisiert Claude-Code `settings.json` mit dem Template. |
| `subagent_models` | Wertet die Subagent-Modellnutzung aus den `subagent-*.jsonl`-Transcripts aus. |
| `md_to_pdf` | Markdown → PDF (rendert auch Mermaid-Blöcke). |
| `pdf_to_text` | Text aus PDF extrahieren. |
| `head_lines` | Erste N Zeilen einer Datei lesen. |
| `echo` | Echo (Smoke-Test). |
| `magic3_design_install` | Installiert den magic3-Design-Skill nach `~/.claude/skills/magic3-design/`. |
| `dotclaude_install` | Verteilt hauseigene Agents (bulk-worker, scan-worker) + Skills nach `~/.claude/`. |

**Script-Konvention:** je Script ein Verzeichnis mit `manifest.yaml` (name, description,
exec, inputs, requires, optional `ai_rem_entity: tool_<name>`) + ausführbarem `run.sh`/`run.py`.
Inputs kommen als `INPUT_<NAME>` + `TOOLS_INPUTS_JSON`; Outputs schreibt das Script nach
`<run_dir>/outputs.json`.

### Workflows

**A) Script-Distribution / Live-Reload**
1. Script wird im Repo unter `scripts/<name>/` ergänzt/geändert; `tools-registry` mountet den Ordner read-only.
2. `tools-registry` (lokal) pollt `/registry`; ändert sich der SHA256-Versionshash, lädt es geänderte Dateien via `/registry/file` und cached nach `~/.cache/tools-registry/scripts`.
3. Tool wird registriert/aktualisiert/entfernt; SDK meldet `tools/list_changed` → sofort nutzbar.

**B) Script ausführen**
1. Claude ruft das Tool mit den Manifest-Inputs auf.
2. `tools-registry` legt ein Run-Dir (`/tmp/tools-runs/<uuid>`) an, setzt `INPUT_*`/`TOOLS_*` und führt `exec` via `execFile` aus.
3. `outputs.json` wird geparst und als Ergebnis zurückgegeben.

**C) Pipeline**
1. `pipeline_run` verkettet Schritte; Outputs eines Schritts werden per `${var}` in die Inputs des nächsten interpoliert. Gut für „extrahieren → transformieren → rendern".

**D) Dieses Dokument als PDF**
- `md_to_pdf` auf `docs/mcp-functions.md` bzw. `docs/architecture.md` rendert inkl. Mermaid-Diagramm.

---

## Zusammenspiel der drei (Gesamt-Workflow)

1. **Session-Start:** `system-check.py` → ai-rem `memory_get_context`; Bearer-Token (aus mykeyvault) ist im `~/.claude.json`-Header; `tools-registry` hat seine Scripts vom Registry frisch.
2. **Arbeit:** Claude nutzt ai-rem-Tools für Kontext/Speichern, mykeyvault-Tools für Secrets (ohne Leak), tools-registry-Scripts für wiederkehrende Aktionen (PDF, settings-sync, Token-Lookup …).
3. **Session-Ende:** der ai-rem Ingest-Hook `auto-memory.py` → llama-server-Extraktion → ai-rem-Upsert (bei kaltem/langsamem llama-server via `pending.jsonl` + `catchup` nachgezogen). Nachts räumt ai-rem den Graphen auf (llama-server-Urteile + Review-Queue).
