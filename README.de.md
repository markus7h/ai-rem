# ai-rem — Knowledge Graph Memory für Claude

> Diese Dokumentation bezieht sich auf **[v0.8.27](https://github.com/markus7h/ai-rem/releases/tag/v0.8.27)**.
> Die englische [README.md](README.md) ist die kanonische, ausführlichste Referenz.
> Release-Notes werden im [CHANGELOG.md](CHANGELOG.md) gepflegt und bei jedem Tag in die [GitHub Releases](https://github.com/markus7h/ai-rem/releases) und die Docker-Hub-Beschreibung veröffentlicht; frühe Versionen (≤ v0.1.5) sind in [docs/release-history.md](docs/release-history.md) archiviert.

**ai-rem** ist ein persistentes Langzeit-Gedächtnis für Claude Code, das als MCP-Server auf dem Heimserver läuft.
Statische Memory-Dateien wie `CLAUDE.md` liegen vollständig im Kontext und sind an einzelne Projekte und Rechner gebunden. ai-rem geht effizienter vor: relevante Informationen – offene Tasks, getroffene Entscheidungen, gelöste Probleme, Projekte, genutzte Tools – liegen in einem Knowledge Graph auf dem Heimserver, werden gezielt statt komplett geladen und sind rechnerunabhängig von jeder Maschine aus verfügbar.

Docker Hub: `docker pull magic3arkus/ai-rem`

---

## Was ist ai-rem?

**ai-rem** ist der MCP-Server, der den Knowledge Graph bereitstellt. Er läuft als Docker Container auf dem Heimserver (`<SERVER_IP>`, Port konfigurierbar, Standard `3456`) und ist damit immer verfügbar, solange der Server läuft.

Technisch:
- **[FastMCP](https://gofastmcp.com)** — Python MCP-Server-Framework, HTTP-Transport (Streamable HTTP)
- **[Kuzu](https://kuzudb.com)** — embedded Graph-Datenbank (kein separater DB-Container nötig)
- Daten liegen persistent in `./data/kg.db` (konfigurierbar via `KG_DATA_PATH`)
- Backups werden in `./backups/` gespeichert (konfigurierbar via `KG_BACKUP_PATH`)

---

## Wie es funktioniert

Claude lädt beim Sitzungsstart via `memory_get_context()` den relevanten Kontext aus dem Graph und speichert neue Erkenntnisse proaktiv mit `memory_add` / `memory_relate`. Der Graph hält typisierte **Entities** — `Person · Project · Task · Tool · Problem · Solution · Decision · Preference · Topic` — und **Relations** dazwischen. Jede Entity kann ein `context`-Tag tragen (`work` / `private` / global), sodass Arbeits- und Privat-Wissen pro Repo getrennt bleiben.

**Projektkontext.** Der Arbeitskontext eines Projekts — lokales Dev-Verzeichnis, Deploy-Verzeichnis/-Host, relevante Skills und projektspezifische Regeln — liegt im `extra` einer `Project`-Entity. Schreiben mit `memory_set_project_context(...)` (feldweises Merge, d. h. `skills` später ergänzen ohne `dev_dir`/`rules` zu verlieren), in einem Aufruf vollständig laden mit `memory_project_context(name)` — ungekürzter Record plus alle verknüpften Entities. Damit lässt sich eine Sitzung „im Kontext von Projekt X" starten.

```jsonc
// extra-Schema (alle Felder optional)
{ "status": "aktiv", "dev_dir": "...", "repo": "...", "deploy_dir": "...",
  "deploy_host": "...", "deploy_cmd": "...", "skills": [...], "rules": [...] }
```

→ **[MCP-Funktionsreferenz](docs/mcp-functions.md)** — alle `memory_*`-Tools (und die Begleit-MCPs) mit Signaturen und Workflows.

---

## Token-Ersparnis

ai-rem **lädt bedarfsweise** nur den relevanten Subgraph, statt alles über die ganze Session in der `CLAUDE.md` mitzuschleppen. Die Last pro Session bleibt nahezu konstant (~1–3k Token), egal wie stark der Graph wächst. Bei ~4,3 Sessions/Tag ergibt das **~0,7 Mio Token/Monat gespart** — grob **3 volle 200k-Kontextfenster** —, und die Ersparnis *steigt*, je größer der Graph wird, weil ai-rems Kosten flach bleiben, während ein „alles in der `CLAUDE.md`"-Ansatz linear skaliert.

→ **[Vollständige Methodik, Messungen und Bandbreiten](docs/token-savings.de.md)**

---

## Web UI

| URL | Funktion |
|---|---|
| `/ui` | Backup-Verwaltung: manuell, Schedule, Download, Restore (Export v2 erhält `pinned`/`sort_order`/`archived`); zusätzlich OKF-Bundle-Import; Kopfzeile zeigt die Server-Version |
| `/browse` | Interaktiver Inhalts-Browser: Suche und Typ-Filter, archivierte ein-/ausblenden, Eintrag aufklappen für Beschreibung, Extra und Relationen; importierte Einträge sind gebadged |
| `/graph` | Node-Link-Visualisierung (vis-network): Knoten nach Typ eingefärbt, Kanten mit Relationsnamen; Filter nach Kontext (work / privat / global) und Typ-Toggle über die Legende; Physik- und Archiv-Toggle; „nur Verbundene" fixiert den angeklickten Knoten samt Nachbarn bis zur einstellbaren Distanz (1, 2 … n; Einfachklick zeigt Info, Doppelklick setzt den Anker um) |
| `/prefs` | Preferences-Manager: pin, Context, Reihenfolge, löschen; archivierte Preferences sind gedimmt, gebadged und stehen unter einer Trennzeile (laden nie in den Session-Kontext). |
| `/cleanup` | Nightly-Cleanup: Konfiguration, manueller Lauf, Pending-Reviews, Lauf-Log; plus Archiv-Purge (archivierte Einträge endgültig löschen, optional die letzten *X* Tage behalten) |
| `/logs` | Server-Log ohne Shell-Zugang: Level-Filter, Substring-Suche, optionales Auto-Refresh alle 5s, Download als Text. Gespeist aus einem In-Memory-Ringpuffer (letzte `AI_REM_LOG_RING` Zeilen, Default 500) — reicht also nur bis zum letzten Container-Neustart. Bearer-Tokens werden maskiert. |
| `/install` | Client-Setup-Befehle pro Plattform (bash / PowerShell) mit Kopier-Buttons, inkl. Schritt-für-Schritt-SSH-Key-Anleitung — public, fürs Onboarding neuer Maschinen |

**Interop (OKF).** ai-rem spricht das [Open Knowledge Format](https://cloud.google.com/blog/products/data-analytics/how-the-open-knowledge-format-can-improve-data-sharing/) v0.1: `/export/okf` lädt den ganzen Graph als Markdown+YAML-Bundle (ZIP), `/api/import/okf` liest eines wieder ein. Eigene Exporte tragen `source: ai-rem` (Round-Trip bleibt ungetaggt), Fremd-Einträge werden als `imported` markiert und beim Import für die semantische Suche indexiert.

---

## Automatisierung (Hooks)

Vier Claude-Code-Hooks — alle vom Client-Setup deployt — halten den Graph befüllt und sauber:

- **Auto-Memory** — ein `PreCompact`/`SessionEnd`-Hook extrahiert strukturierte Entities/Relations aus jedem Transcript via llama-server, mit md-Fallback + Catch-up, wenn llama-server down ist. Er läuft detached (die Extraktion dauert Minuten) und meldet beim nächsten Sessionstart, wenn er gestört ist. Das Setup legt die CLI nach `~/.local/share/ai-rem/bin/ai-rem` und richtet `AI_REM_CLI` darauf aus — der Hook hängt damit nicht daran, wohin das Repo geklont wurde.
- **Nightly-Cleanup** — ein Daemon dedupliziert/archiviert überholte Einträge **nicht-destruktiv** (archivieren statt löschen; `Preference`/gepinnt unangetastet) und schiebt Mehrdeutiges in eine Review-Queue. Dazu ein **Veraltungs-Check**, der Einträge mit verderblichen Infrastruktur-Fakten (IPs, Ports, Dienste, Geräte) zur Realitäts-Prüfung vorlegt — nie automatisch.
- **Plan-Speicherung** — ein `ExitPlanMode`-Hook speichert jeden finalisierten Plan als offenen `Task`, sodass Pläne eine zentrale, maschinenübergreifende Liste werden.
- **Vault-Secret-Erinnerung** — ein `PostToolUse`-Hook durchsucht die Bash-Ausgabe nach Auth-/Credential-Fehlern und injiziert eine Erinnerung, das passende Secret via mykeyvault aus dem Vault zu holen, statt den User nach Token oder Passwort zu fragen — fail-silent, blockiert also nie einen Befehl.

→ **[Hooks & Automatisierung im Detail](docs/hooks-and-automation.de.md)**

---

## Voraussetzungen

- Docker auf dem Zielserver
- Claude Code CLI und **Python 3** auf dem Client-Rechner (Setup und Hooks laufen auf Python)
- Netzwerkzugang zu `<SERVER_IP>:<PORT>`
- Optional (nur für den tools-Begleit-MCP): git, Node.js ≥ 18 inkl. npm

---

## Konfiguration

Umgebungsvariablen werden aus einer `.env`-Datei im Compose-Verzeichnis geladen:

```env
AI_REM_API_TOKEN=...                     # PFLICHT — API-Token (fail-closed, siehe Authentifizierung)
KG_PUBLIC_URL=http://<SERVER_IP>:3456   # Öffentliche URL des Servers
PORT=3456                                # TCP-Port (Standard: 3456)
HOST=::                                  # Bind-Adresse (Standard: ::) — dual-stack Socket, IPv6 und der published IPv4-Port funktionieren beide; 0.0.0.0 für reines IPv4
KUZU_DB_PATH=/data/kg.db                 # Pfad zur Datenbank
BACKUP_DIR=/backups                      # Pfad für Backup-Dateien
MAX_BACKUPS=10                           # Maximale Anzahl aufbewahrter Backups
AI_REM_BACKUP_KEY=...                     # Optional — Backups verschlüsseln (AES-256-GCM); leer = Klartext
KUZU_POOL_SIZE=4                         # Connection-Pool-Größe
KUZU_BUFFER_POOL_SIZE_MB=256             # Kuzu Buffer-Pool in MiB (0 = Default: 80% Host-RAM)
KUZU_WAL_CHECKPOINT_MB=2                 # WAL selbst mergen ab dieser Größe (0/leer = aus)
AI_REM_LOG_RING=500                      # Zeilen Server-Log, die für /logs im RAM gehalten werden
EMBED_URL=                               # Leer = Embeddings im Container; gesetzt = OpenAI-kompatible /v1/embeddings-URL eines externen Dienstes
EMBED_HTTP_MODEL=bge-m3                  # Modellname, der an EMBED_URL geschickt wird
EMBED_THRESHOLD=                         # Cosine-Schwelle; leer = Default je Backend (0.45 in-process, 0.50 extern)
EMBED_MAX_CHARS=2000                     # Text vor dem Embedden kappen (llama.cpp lehnt zu lange Eingaben ab, statt zu kürzen)
AI_REM_TAG=latest                        # latest (Modell im Image) oder latest-slim (~250 MB kleiner, braucht EMBED_URL)
MEM_LIMIT=1536m                          # Speicherlimit des Containers; ohne Modell genügen 512m
```

### Embeddings: im Container oder extern

Die semantische Suche braucht Vektoren. Standardmäßig entstehen sie **im Container**
(fastembed/MiniLM, Modell ist ins Image gebacken) — es muss nichts weiter laufen. Wird
`EMBED_URL` auf einen OpenAI-kompatiblen Endpoint gesetzt (z. B. ein llama.cpp-Server
mit `bge-m3`), wandert die Rechenarbeit nach außen und das `-slim`-Image wird nutzbar,
das ohne fastembed und Modell kommt (413 MB → 162 MB).

In beiden Fällen sucht ai-rem **hybrid**: Substring-Treffer (lokal berechnet) und
semantische Treffer werden per Reciprocal-Rank-Fusion verschmolzen — Einträge, die
mehrere Signale bestätigen, stehen vorn, Name-Treffer schlagen Beschreibungs-Treffer.
Ist der externe Endpoint nicht erreichbar, werden Einträge ohne Vektor gespeichert und
die Suche funktioniert lexikalisch weiter — der Backfill beim Start und im Nightly-Lauf
holt die fehlenden Vektoren nach.

Ein Backendwechsel ändert die Vektor-Dimension (384 ↔ 1024) und macht gespeicherte
Vektoren bedeutungslos. Der Server erkennt das beim nächsten Backfill und rechnet
**alle** Vektoren neu — ohne manuelle Migration, in beide Richtungen.

> **Beim Umstellen auf extern `KUZU_BUFFER_POOL_SIZE_MB` erhöhen** (z. B. 768, und
> `MEM_LIMIT` auf 1536m). Die 1024-dimensionalen Vektoren erzeugen beim Backfill mehr
> Schreiblast, als der 256-MB-Default verkraftet: der WAL-Checkpoint scheitert mit
> `buffer pool is full` und die betroffenen Vektoren landen nie dauerhaft in der DB.
> Ein fehlgeschlagener Checkpoint wird einmal wiederholt; konnte der Backfill danach
> nicht alles sichern, meldet er das als `ERROR` statt Erfolg zu behaupten. Im Log
> sichtbar als `WAL-Checkpoint fehlgeschlagen`, in `/api/status` als `embed_pending`,
> das dann über Neustarts hinweg über null bleibt.

> **Hinweis (Speicher):** Ohne `KUZU_BUFFER_POOL_SIZE_MB` dimensioniert kuzu seinen
> Buffer-Pool auf ~80 % des **Host**-RAMs und ignoriert das Container-`mem_limit`.
> Der Normalbetrieb braucht bei dieser DB nur ~32 MB, daher genügen 256 MiB.
> `KUZU_WAL_CHECKPOINT_MB` hält die WAL klein (periodisch + beim Shutdown) — eine
> aufgestaute WAL würde beim Öffnen eine teure Recovery auslösen (mehrere GB → OOM).

Ist `AI_REM_BACKUP_KEY` gesetzt, werden Backups mit AES-256-GCM verschlüsselt
geschrieben (`backup_<ts>.json.enc`) und beim Download als verschlüsselter Blob
ausgegeben — die Daten verlassen den Server nie im Klartext. Restore erkennt
verschlüsselte und Klartext-Backups automatisch. Der Schlüssel liegt in
[mykeyvault](https://github.com/markus7h/mykeyvault) (`ai-rem-backup-key`) und
wird von `deploy.sh` bezogen. **Geht die Passphrase verloren, sind verschlüsselte
Backups nicht mehr wiederherstellbar.**

Das Client-Onboarding lässt sich zusätzlich über eine `setup-config.json` anpassen → **[Persönliche Konfiguration](docs/configuration.de.md)**.

---

## Authentifizierung

Alle sensiblen Routen (`/mcp`, `/api/*`, `/export`, `/import`, `/ui`) verlangen einen Token. Der Server ist **fail-closed** — ohne `AI_REM_API_TOKEN` startet er nicht. MCP-Clients authentifizieren sich mit `Authorization: Bearer <token>`; die Browser-Web-UI nutzt ein abgeleitetes, HttpOnly-Cookie, das `/login` setzt. Der Token liegt einmalig in [mykeyvault](https://github.com/markus7h/mykeyvault) (`ai-rem-api-token`) und wird von `deploy.sh` / dem Client-Hook bezogen.

→ **[Auth-Modell, Web-UI-Login & Token-Quelle](docs/authentication.de.md)**

---

## Installation / Deployment

### Server (einmalig)

```bash
# Verzeichnis anlegen
mkdir -p ~/mydocker/compose-files/ai-rem
cd ~/mydocker/compose-files/ai-rem

# docker-compose.yml und .env.example herunterladen
curl -O https://raw.githubusercontent.com/markus7h/ai-rem/main/docker-compose.yml
curl -O https://raw.githubusercontent.com/markus7h/ai-rem/main/.env.example

# .env anlegen und konfigurieren
cp .env.example .env
# → KG_PUBLIC_URL auf die echte Server-IP setzen
# → AI_REM_API_TOKEN setzen (Pflicht) — z. B. `openssl rand -hex 32`, oder via
#   deploy.sh automatisch aus mykeyvault ziehen (siehe Authentifizierung)

# Image pullen und Container starten
docker compose pull && docker compose up -d
```

### Client — neuer Rechner einrichten

**Auf jeder neuen Maschine** — einen Satz zu Claude:

```
Führe aus: bash <(curl -s http://<SERVER_IP>:3456/setup)
```

Auf **nativem Windows** (PowerShell, kein WSL nötig): `irm http://<SERVER_IP>:3456/setup.ps1 | iex`.

Das Skript ist idempotent und registriert den MCP-Server, deployt die drei Hooks, schreibt den minimalen `CLAUDE.md`-Pointer und installiert die Slash-Commands.

→ **[Was das Setup tut, Repo-Layout & CLAUDE.md-Strategie](docs/installation.de.md)**

### Update auf neue Version

```bash
ssh your-server "cd ~/mydocker/compose-files/ai-rem && docker compose pull && docker compose up -d"
```

---

## Verwandte Projekte

- [tools-registry](https://github.com/markus7h/tools-registry) — MCP-Server, der kleine Scripts via zentrale Registry als Tools bereitstellt. ai-rem führt pro Script eine `Tool`-Entity (Konvention `ai_rem_entity`), damit der Katalog auffindbar bleibt.
- [mykeyvault](https://github.com/markus7h/mykeyvault) — self-hosted Secrets-Vault (Vaultwarden + REST/MCP). ai-rem speichert bewusst **keine Secrets**; Credentials liegen stattdessen in mykeyvault.
