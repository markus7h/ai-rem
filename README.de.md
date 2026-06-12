# ai-rem — Knowledge Graph Memory für Claude

> Diese Dokumentation bezieht sich auf **[v0.4.20](https://github.com/markus7h/ai-rem/releases/tag/v0.4.20)**.
> Die englische [README.md](README.md) ist die kanonische, ausführlichste Referenz.
> Release-Notes stehen in den [GitHub Releases](https://github.com/markus7h/ai-rem/releases); frühe Versionen (≤ v0.1.5) sind in [docs/release-history.md](docs/release-history.md) archiviert.

**ai-rem** ist ein persistentes Langzeit-Gedächtnis für Claude Code, das als MCP-Server auf dem Heimserver läuft.
Statische Memory-Dateien wie `CLAUDE.md` liegen vollständig im Kontext und sind an einzelne Projekte und Rechner gebunden. ai-rem geht effizienter vor: relevante Informationen – offene Tasks, getroffene Entscheidungen, gelöste Probleme, Projekte, genutzte Tools – liegen in einem Knowledge Graph auf dem Heimserver, werden gezielt statt komplett geladen und sind rechnerunabhängig von jeder Maschine aus verfügbar.

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

Claude lädt beim Sitzungsstart via `memory_get_context()` den relevanten Kontext aus dem Graph und nutzt ihn als Arbeitsgrundlage. Neue Erkenntnisse speichert Claude proaktiv mit `memory_add` oder `memory_relate`.

### Verfügbare MCP-Tools

| Tool | Beschreibung |
|---|---|
| `memory_add(name, type, description, extra, context, pinned)` | Entity anlegen oder aktualisieren. `pinned=True` → Preference erscheint immer ganz oben in `get_context` |
| `memory_preference_update(name, context, pinned, sort_order)` | Felder einer Preference gezielt ändern ohne `description` zu überschreiben |
| `memory_relate(from, relation, to, extra)` | Beziehung zwischen zwei Entities erstellen |
| `memory_search(query, context)` | Hybridsuche über Name + Beschreibung: pro-Token-Lexik plus semantischer Vektor-Recall (findet Mehrwort-Queries auch, wenn die Wörter nicht zusammenhängend stehen) |
| `memory_search_full(query, context)` | Wie `memory_search`, aber ungekürzte Beschreibung (keine 400-Zeichen-Kürzung) |
| `memory_get_context(topic, context)` | Relevanten Subgraph laden (Tasks, Projekte, Decisions, Preferences) |
| `memory_list(type, context)` | Alle Entities auflisten |
| `memory_get_relations(name)` | Alle Beziehungen einer Entity (zeigt auch archivierte → für Historie) |
| `memory_archive(name, compressed_description, superseded_by)` | Eintrag archivieren statt löschen (Original in `extra.original_descr` gesichert), verlinkt via `VERALTET_DURCH` |
| `memory_merge(canonical_name, duplicate_name)` | Dublette in canonical falten, Relationen umhängen, Dublette archivieren + `DUPLIKAT_VON` |
| `memory_delete(name)` | Entity und Relationen hart entfernen |
| `memory_status()` | Kurzstatus: Anzahl Entities und Relationen (wird vom SessionStart-Hook genutzt) |
| `memory_check_update()` | Installierte Version vs. neuester Docker-Hub-Tag |

`memory_get_context`, `memory_search` und `memory_list` blenden archivierte Einträge standardmäßig aus — Opt-in über `include_archived=true`.

### Entity-Typen

`Person` · `Project` · `Task` · `Tool` · `Problem` · `Solution` · `Decision` · `Preference` · `Topic`

### Kontext-Trennung

Jede Entity kann mit einem `context`-Tag versehen werden:
- `context="work"` — nur in Arbeits-Sessions sichtbar
- `context="private"` — nur in privaten Sessions sichtbar
- kein Tag — global, erscheint in allen Abfragen

Der Kontext kann per CLAUDE.md gesetzt werden: z.B. `context="work"` für Arbeits-Repos und `context="private"` für private Projekte.

---

## Token-Ersparnis

ai-rem hängt nicht jedem Prompt Wissen an — es **lädt bedarfsweise** nur den relevanten Subgraph, statt alles über die ganze Session in der `CLAUDE.md` mitzuschleppen. Die Last pro Session bleibt nahezu konstant (~1–3k Token), egal wie stark der Graph wächst, während die Alternative — alles Wissen in die `CLAUDE.md` packen — ~20k Token in *jede* Session lädt.

**Beispielrechnung — auf Basis gemessener Nutzung (~4,3 Sessions/Tag):**

| Parameter | Wert | Quelle |
|---|---|---|
| Sessions / Monat | ~4,3 × 30 = **~130** | gemessen (141 Sessions über 33 Tage) |
| Sessions mit echtem Recall | ~59 % → **~76** | gemessen (83/141 Sessions nutzten ai-rem) |
| Triviale Sessions | ~54 | abgeleitet |
| Ersparnis pro Recall-Session | ~12k Token | modelliert (vermiedenes Re-Discovery / kein dauerhafter `CLAUDE.md`-Ballast) |
| Retrieval-Payload pro Recall-Session | ~2,8k Token | gemessen (~7,8 ai-rem-Aufrufe/Session, ~360 Token/Aufruf) |
| Hook-Overhead (jede Session) | ~300 Token | modelliert |

```
Gewinn:     76 Recall-Sessions × 12.000 =  912.000
Retrieval:  76 Recall-Sessions ×  2.800 =  212.800
Hook:      130 Sessions        ×    300 =   39.000
───────────────────────────────────────────────────
Netto ≈ 660.000 Token / Monat gespart
```

**Ergebnis: ~0,7 Mio Token/Monat** bei ~4,3 Sessions/Tag — grob **3 volle 200k-Kontextfenster**, die nicht für Re-Erklären von Kontext, Re-Discovery von Infrastruktur oder dauerhaften `CLAUDE.md`-Ballast draufgehen. Pro Tag ~22k Token, pro Jahr ~8 Mio.

**Bandbreite** (je nachdem, wie wissensintensiv die Sessions sind):

| Szenario | Recall-Sessions | Token/Session | Netto / Monat |
|---|---|---|---|
| Konservativ | 65 (50 %) | 8k | **~0,3 Mio** |
| Typisch | 76 (59 %) | 12k | **~0,7 Mio** |
| Intensiv | 91 (70 %) | 16k | **~1,2 Mio** |

**Die Ersparnis steigt, je größer der Graph wird.** Das ist die entscheidende Langzeit-Eigenschaft: Die Last pro Session bleibt nahezu konstant (~1–3k Token), unabhängig von der Graph-Größe, weil immer nur der *relevante* Subgraph bedarfsweise geladen wird. Die naive Alternative — Wissen in der `CLAUDE.md` halten — skaliert dagegen **linear**: Jeder neue Fakt wird in *jeder* Session aufs Neue bezahlt, für immer. Mit den Monaten sammeln sich Hunderte Entities an, und die Schere öffnet sich: Der `CLAUDE.md`-Ansatz wird stetig teurer, während ai-rems Kosten flach bleiben. Die Zahlen oben (262 Entities) sind eine Momentaufnahme der Frühphase; bei 500+ Entities spart dasselbe Nutzungsmuster deutlich mehr, weil der vermiedene Dauer-Ballast viel größer ist.

> Session-Zahl, Recall-Rate und Retrieval-Payload sind aus echter Nutzung **gemessen** (141 Sessions über 33 Tage, 11.05.–12.06.2026, nachgemessen aus den Claude-Code-Transcripts via `bin/measure-savings.py`). Die Ersparnis pro Session (8–16k) ist ein Modell, keine Messung — das „was es ohne ai-rem gekostet hätte" lässt sich nicht direkt beobachten. Die Summen sind also eine fundierte Schätzung, kein Benchmark.

---

## Web UI

| URL | Funktion |
|---|---|
| `/ui` | Backup-Verwaltung: manuell, Schedule, Download, Restore (Export v2 erhält `pinned`/`sort_order`/`archived`) |
| `/prefs` | Preferences-Manager: pin, Context, Reihenfolge, löschen; archivierte Preferences sind gedimmt, gebadged und stehen unter einer Trennzeile (laden nie in den Session-Kontext) |
| `/cleanup` | Nightly-Cleanup: Konfiguration, manueller Lauf, Pending-Reviews, Lauf-Log |
| `/install` | Client-Setup-Befehle pro Plattform (bash / PowerShell) mit Kopier-Buttons, inkl. Schritt-für-Schritt-SSH-Key-Anleitung (keygen, `~/.ssh/config`-Host-Block mit User aus der `setup-config`, ssh-copy-id / PowerShell-Variante) — public, fürs Onboarding neuer Maschinen |

**`/prefs`** — Vollständiger Preferences-Manager im Browser: pin/unpin, Context-Dropdown, manuelle Reihenfolge (`sort_order`), löschen. Klick auf den Namen klappt die vollständige Beschreibung auf. Aufrufbar über den Slash-Command `/ai-rem:prefedit`.

---

## Auto-Memory & Nightly-Cleanup

**Auto-Memory** ersetzt das eingebaute Markdown-Auto-Memory durch einen Transcript-Extraktor:
`PreCompact`/`SessionEnd`-Hook → `ai-rem ingest` → Ollama extrahiert strukturierte Entities/
Relations → ai-rem.

- **md-Fallback:** Ist Ollama nicht erreichbar, geht nichts verloren — eine heuristische Extraktion
  landet in `~/.claude/auto-memory/fallback.md` (via `@`-Import in `CLAUDE.md` weiter im Kontext)
  und die Session wird vorgemerkt.
- **Catch-up:** Sobald Ollama zurück ist, zieht `ai-rem catchup` die verpassten Sessions sauber nach
  ai-rem nach und leert das md wieder.

**Nightly-Cleanup** (Daemon im Container, default 03:00, konfigurierbar unter `/cleanup`) räumt
Dubletten & überholte Einträge auf — **nicht-destruktiv: archivieren statt löschen**, mit Backup vor
jeder Mutation. `Preference`, gepinnte und bereits archivierte Einträge werden nie angefasst.
Mehrdeutiges (und alles bei Ollama-Ausfall) landet in einer Review-Queue, die der Slash-Command
**`/memory-cleanup`** (still beim Session-Start ausgelöst) mit Urteil abarbeitet.

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

Der Hook liest das Frontmatter der zuletzt geänderten Plan-Datei und upsertet via `memory_add` (`type: Task`, `extra.kind=plan`, `extra.plan_file`). Upsert über `name` → keine Dubletten. Erledigte Pläne werden archiviert (`memory_archive`); der Status liegt zentral in ai-rem (cross-machine). Fail-silent: blockiert nie `ExitPlanMode`.

**Installation:** `hooks/save-plan.py` nach `~/.claude/hooks/` kopieren, `chmod +x`, und den `PostToolUse: ExitPlanMode`-Hook in `~/.claude/settings.json` registrieren (siehe Datei-Header).

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
KUZU_DB_PATH=/data/kg.db                 # Pfad zur Datenbank
BACKUP_DIR=/backups                      # Pfad für Backup-Dateien
MAX_BACKUPS=10                           # Maximale Anzahl aufbewahrter Backups
KUZU_POOL_SIZE=4                         # Connection-Pool-Größe
```

---

## Authentifizierung

Alle sensiblen Routen (`/mcp`, `/api/*`, `/export`, `/import`, `/ui`) verlangen
Authentifizierung. Der Server ist **fail-closed**: ohne `AI_REM_API_TOKEN` startet
er nicht. Ein Request ist autorisiert, wenn **eine** Bedingung gilt:

1. Der Pfad ist public — `/health`, `/setup`, `/setup.py`, `/setup.ps1`, `/install`, `/setup-config`, `/hooks/*`, `/cmd*`, `/login` (nur Onboarding/Login, keine privaten Daten);
2. die Herkunft ist **Loopback** *und der Request ist nicht proxied* (kein `X-Forwarded-For`). Im Bridge-Netz deckt das faktisch nur containerinternen Verkehr ab (z. B. den Healthcheck): getunnelte/proxied Requests kommen als Docker-Gateway-IP an, nicht als Loopback. Hinter einem Same-Host-Reverse-Proxy (z. B. Caddy) ist die Peer-IP zwar `127.0.0.1`, aber `X-Forwarded-For` ist gesetzt → der Token wird trotzdem verlangt;
3. er trägt `Authorization: Bearer <AI_REM_API_TOKEN>` (konstant-zeitlicher Vergleich) — von MCP-Clients (Claudes `/mcp`-Kanal);
4. er trägt ein gültiges `ai_rem_session`-Cookie — von der Browser-Web-UI (siehe unten).

### Web-UI-Login

Ein Browser kann beim Navigieren keinen `Authorization`-Header setzen, daher nutzt
die Web-UI ein Cookie. `/login` öffnen, den API-Token einmal eingeben — der Server
setzt ein **HttpOnly-, Secure-, SameSite=Strict-**Cookie, das `/ui` und dessen
`/api/*`-Calls autorisiert; `/logout` löscht es. Der Cookie-Wert ist **nicht** der
rohe Token, sondern ein abgeleiteter, UI-gescopeter Wert
(`HMAC-SHA256(token, "ai-rem-ui-session")`) — so liegt der `/mcp`-Bearer nie im
Browser, und bei Token-Rotation wird die Session automatisch ungültig. Da das
Cookie `Secure` ist, muss die UI über HTTPS erreicht werden (z. B. ein Caddy-`tls
internal`-vHost). Lebensdauer standardmäßig 30 Tage (`AI_REM_UI_SESSION_TTL`, in
Sekunden).

**Token-Quelle — [mykeyvault](https://github.com/markus7h/mykeyvault):** der Token
liegt einmalig im Vault als Item `ai-rem-api-token` (Single Source of Truth).
- **Server:** `deploy.sh` zieht ihn beim Deploy aus dem Vault und schreibt ihn in die Remote-`.env` — der Serverstart bleibt unabhängig vom Laufzeitzustand des Vaults.
- **Clients:** der SessionStart-Hook `system-check.py` nutzt für die laufende Session den bereits in `~/.claude.json` → `mcpServers."ai-rem".headers.Authorization` gespeicherten Bearer-Token (schnell, kein Vault-Roundtrip — darüber trägt auch Claudes `/mcp`-Kanal den Token) und frischt ihn in einem **detached Hintergrundprozess** für die nächste Session aus dem Vault auf (vault-api-Koordinaten in `~/.claude.json` → `mcpServers.mykeyvault.env`; das `bw`-Backend ist ~8 s, zu langsam für den synchronen Startpfad). Nur der allererste Lauf ohne gespeicherten Header liest synchron aus dem Vault. Liefert weder Header noch Vault einen Token, antwortet ai-rem mit `401`.

Token manuell erzeugen (ohne Vault): `openssl rand -hex 32`.

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

Auf **nativem Windows** (PowerShell, kein WSL nötig):

```
irm http://<SERVER_IP>:3456/setup.ps1 | iex
```

Beide Wrapper laden dieselbe plattformneutrale Logik (`/setup.py`,
benötigt Python 3) — das Verhalten ist auf macOS, Linux, WSL und
Windows identisch. Auf Windows werden die Hooks als
`python -X utf8 <hook>`-Commands registriert und der Secret-Pull
nutzt den mitgelieferten OpenSSH-Client (alternativ
`$env:AI_REM_TOKEN` setzen).

Das Skript erledigt automatisch:
1. `claude mcp add` — ai-rem als user-scoped HTTP MCP-Server registrieren
2. `~/.claude/settings-template.json` — Basis-Template für Permissions, Deny-Rules und Hooks aus der Live-Setup-Config (neu) schreiben
3. `~/.claude/hooks/system-check.py` — konsolidierter SessionStart-Hook deployen (ai-rem Health, SMB-Mount, MCP-Server-Tests, Settings-Sync, Tools-Anzahl, offene Tasks/Pläne)
4. `~/.claude/hooks/auto-memory.py` — PreCompact + SessionEnd Hook deployen (Transcript → `ai-rem ingest` → Ollama-Extraktor → strukturierte Entities)
5. `~/.claude/hooks/claude-md-guard.py` — PreToolUse-Hook deployen, der (non-blocking) warnt, wenn `~/.claude/CLAUDE.md` editiert wird
6. `~/.claude/settings.json` — Permissions, Deny-Rules und alle Hooks eintragen; alte Hooks entfernen; `autoMemoryEnabled: false`
7. `~/.claude/CLAUDE.md` — minimalen Pointer auf ai-rem anlegen oder aktualisieren
8. Slash-Commands installieren (`/setup-ai-rem`, `/ai-rem:prefedit`, `/memory-cleanup`)
9. Preferences & Tool-Entities direkt via MCP API im Knowledge Graph anlegen

**Das einzige, was man sich merken muss:** die URL `<SERVER_IP>:3456/setup`.

Das Skript ist idempotent — mehrfaches Ausführen auf derselben Maschine ist sicher.

### Update auf neue Version

```bash
ssh your-server "cd ~/mydocker/compose-files/ai-rem && docker compose pull && docker compose up -d"
```

---

## Dateien

```
ai-rem/
├── server.py                   # MCP-Server (FastMCP + Kuzu + Web UI + Backup + Cleanup
│                               #   + eingebettete setup.py/bash/PS1-Scripts und Hooks)
├── bin/ai-rem                  # CLI (status/search/ingest/catchup, eigene .venv)
├── lib/                        # Extraktor (+ md-Fallback/Catch-up), Heuristik, mcp_client
├── hooks/save-plan.py          # PostToolUse-Hook: ExitPlanMode → offener Task in ai-rem
├── docs/                       # Architektur (md + Mermaid + PDF), MCP-Funktionsdoku,
│                               #   release-history.md (archivierte Notes ≤ v0.1.5)
├── deploy.sh                   # Deploy auf den Heimserver (scp + Remote-Build + Recreate)
├── .github/workflows/          # Docker-Hub-Publish bei v*-Tags
├── requirements.txt            # fastmcp, kuzu, fastembed
├── Dockerfile
├── docker-compose.yml
├── .env.example                # Vorlage für Konfiguration
├── .env                        # Konfiguration (nicht im Repo, aus .env.example ableiten)
├── setup-config.json           # Persönliche Konfiguration (gitignored)
├── setup-config.example.json   # Generisches Starter-Template (Fallback ohne persönliche Config)
├── .claude/settings.json.example  # Beispiel für repo-lokale Claude-Permissions
├── .claude/settings.json       # Lokale Claude-Permissions (gitignored; aus .example kopieren)
├── README.md                   # Englische Doku (kanonisch)
└── README.de.md                # Diese deutsche Doku
```

> `.claude/settings.json` ist **gitignored**, damit lokale Permission-Anpassungen nie im Repo
> landen. Zum Start: `cp .claude/settings.json.example .claude/settings.json`.

---

## CLAUDE.md Strategie

Das Setup-Skript schreibt in `~/.claude/CLAUDE.md` nur einen **minimalen Pointer**:

```markdown
## ai-rem
ai-rem ist die einzige Wissensquelle für persistenten Kontext. Auto-Memory ist deaktiviert.
Nutzungsregeln kommen über die MCP Server Instructions, Verhaltensregeln aus den ai-rem Preferences.

<!-- Auto-Memory md-Fallback: bei Ollama-Ausfall befüllt, vom catchup geleert -->
@~/.claude/auto-memory/fallback.md
```

Die eigentlichen Regeln kommen aus zwei Quellen, die automatisch beim Sitzungsstart geladen werden:
- **MCP Server Instructions** — was zu speichern ist, was nicht, wie Entities zu verknüpfen sind (fest im Server)
- **ai-rem Preferences** (`memory_get_context`) — persönliche Verhaltensregeln, Feedback, Arbeitsweisen (dynamisch, im Graph)

Projekt-spezifische CLAUDE.md-Dateien setzen den Standard-Context:

| Datei | Zweck |
|---|---|
| `~/.claude/CLAUDE.md` | Minimaler ai-rem-Pointer (verwaltet vom Setup-Skript) |
| `work-repo/CLAUDE.md` | `context="work"` als Standard für Arbeits-Repos |

---

## Persönliche Konfiguration (setup-config.json)

Der Setup-Endpunkt lädt optional eine `setup-config.json` vom Server (`/setup-config`). Diese Datei ist **nicht im Repo** — sie enthält persönliche Einstellungen:

```json
{
  "ssh_host": "your-server",
  "ssh_user": "your-user",
  "ssh_hostname": "your-server.lan",
  "permissions_allow_portable": ["Bash", "mcp__tools__*", ...],
  "permissions_deny": ["Bash(bw get *)", ...],
  "smb": {"mount": "/path/to/mount", "url": "smb://server/share"},
  "mcp_stdio_servers": {"paperless": "/path/to/index.js"},
  "tools_scripts_dir": "/path/to/tools-mcp/scripts",
  "old_hooks": ["legacy-hook.sh"],
  "entities": [{"name": "...", "type": "Tool", "description": "..."}]
}
```

Im Docker-Image wird die Datei zur Buildzeit kopiert (`COPY setup-config*.json ./`). Die persönliche `setup-config.json` ist gitignored und landet daher nie im öffentlichen Image. Stattdessen liegt eine generische **`setup-config.example.json`** im Repo: Fehlt eine persönliche Config, fällt `/setup-config` darauf zurück — ein frisches Deployment seedet so ein sinnvolles Starter-Set an Verhaltens-Preferences (Plan-first, knapp antworten, ai-rem vor Rückfragen prüfen, Halluzinationen vermeiden, Wissen proaktiv speichern) plus generische Permission-/Deny-Regeln. Eine eigene `setup-config.json` überschreibt das Template komplett.

Der `system-check.py`-Hook liest seine Konfiguration aus `~/.claude/settings-template.json`, das beim ersten Setup angelegt wird und u.a. SMB-Pfad, MCP-stdio-Server-Pfade und tools-Verzeichnis enthält.
