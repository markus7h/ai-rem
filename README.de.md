# ai-rem — Knowledge Graph Memory für Claude

> Diese Dokumentation bezieht sich auf **[v0.1.5](https://github.com/markus7h/ai-rem/releases/tag/v0.1.5)**.
> Die englische [README.md](README.md) ist die kanonische, ausführlichste Referenz.

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
| `memory_add(name, type, description, context, pinned)` | Entity anlegen oder aktualisieren. `pinned=True` → Preference erscheint immer ganz oben in `get_context` |
| `memory_preference_update(name, context, pinned, sort_order)` | Felder einer Preference gezielt ändern ohne `description` zu überschreiben |
| `memory_relate(from, relation, to)` | Beziehung zwischen zwei Entities erstellen |
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

**Beispielrechnung — Annahme: durchschnittlich 5 Sessions/Tag:**

| Parameter | Wert | Quelle |
|---|---|---|
| Sessions / Monat | 5 × 30 = **150** | Annahme |
| Sessions mit echtem Recall | ~72 % → **~108** | gemessen (90/125 Sessions nutzten ai-rem) |
| Triviale Sessions | ~42 | abgeleitet |
| Ersparnis pro Recall-Session | ~12k Token | modelliert (vermiedenes Re-Discovery / kein dauerhafter `CLAUDE.md`-Ballast) |
| Hook- + Retrieval-Overhead | ~300 Token/Injektion | gemessen (~2,4 Injektionen/Session) |

```
Gewinn:    108 Recall-Sessions × 12.000 = 1.296.000
Kosten:     42 Trivial-Sessions ×    300 =     12.600
Overhead:  ~360 Injektionen     ×    300 =    108.000
────────────────────────────────────────────────────
Netto ≈ 1.175.000 Token / Monat gespart
```

**Ergebnis: ~1,2 Mio Token/Monat** bei 5 Sessions/Tag — grob **6 volle 200k-Kontextfenster**, die nicht für Re-Erklären von Kontext, Re-Discovery von Infrastruktur oder dauerhaften `CLAUDE.md`-Ballast draufgehen. Pro Tag ~39k Token, pro Jahr ~14 Mio.

**Bandbreite** (je nachdem, wie wissensintensiv die Sessions sind):

| Szenario | Recall-Sessions | Token/Session | Netto / Monat |
|---|---|---|---|
| Konservativ | 90 (60 %) | 8k | **~0,6 Mio** |
| Typisch | 108 (72 %) | 12k | **~1,2 Mio** |
| Intensiv | 120 (80 %) | 16k | **~1,8 Mio** |

**Die Ersparnis steigt, je größer der Graph wird.** Das ist die entscheidende Langzeit-Eigenschaft: Die Last pro Session bleibt nahezu konstant (~1–3k Token), unabhängig von der Graph-Größe, weil immer nur der *relevante* Subgraph bedarfsweise geladen wird. Die naive Alternative — Wissen in der `CLAUDE.md` halten — skaliert dagegen **linear**: Jeder neue Fakt wird in *jeder* Session aufs Neue bezahlt, für immer. Mit den Monaten sammeln sich Hunderte Entities an, und die Schere öffnet sich: Der `CLAUDE.md`-Ansatz wird stetig teurer, während ai-rems Kosten flach bleiben. Die Zahlen oben (146 Entities) sind eine Momentaufnahme der Frühphase; bei 500+ Entities spart dasselbe 5-Sessions/Tag-Muster deutlich mehr, weil der vermiedene Dauer-Ballast viel größer ist.

> Session-Zahl und Recall-Rate sind aus echter Nutzung **gemessen** (125 Sessions über ~28 Tage). Die Ersparnis pro Session (8–16k) ist ein Modell, keine Messung — das „was es ohne ai-rem gekostet hätte" lässt sich nicht direkt beobachten. Die Summen sind also eine fundierte Schätzung, kein Benchmark.

---

## Web UI

| URL | Funktion |
|---|---|
| `/ui` | Backup-Verwaltung: manuell, Schedule, Download, Restore |
| `/prefs` | Preferences-Manager: pin, Context, Reihenfolge, löschen |
| `/cleanup` | Nightly-Cleanup: Konfiguration, manueller Lauf, Pending-Reviews, Lauf-Log |

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

## Voraussetzungen

- Docker auf dem Zielserver
- Claude Code CLI auf dem Client-Rechner
- Netzwerkzugang zu `<SERVER_IP>:<PORT>`

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

1. Der Pfad ist public — `/health`, `/setup`, `/setup-config`, `/hooks/*`, `/cmd*`, `/login` (nur Onboarding/Login, keine privaten Daten);
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
- **Clients:** der SessionStart-Hook `system-check.py` holt den Token jede Session frisch aus dem Vault (vault-api-Koordinaten stehen bereits in `~/.claude.json` → `mcpServers.mykeyvault.env`) und schreibt ihn in `~/.claude.json` → `mcpServers."ai-rem".headers.Authorization` — darüber trägt Claudes `/mcp`-Kanal den Token. Ist der Vault down/locked, antwortet ai-rem mit `401`.

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

Das Skript erledigt automatisch:
1. `claude mcp add` — ai-rem als user-scoped HTTP MCP-Server registrieren
2. `~/.claude/settings-template.json` — Basis-Template für Permissions, Deny-Rules und Hooks aus der Live-Setup-Config (neu) schreiben
3. `~/.claude/hooks/system-check.py` — konsolidierter SessionStart-Hook deployen (ai-rem Health, SMB-Mount, MCP-Server-Tests, Settings-Sync, Tools-Anzahl)
4. `~/.claude/settings.json` — Permissions, Deny-Rules und Hook eintragen; alte Hooks entfernen; `autoMemoryEnabled: false`
5. `~/.claude/CLAUDE.md` — minimalen 3-Zeilen-Pointer auf ai-rem anlegen oder aktualisieren
6. Slash-Commands installieren (`/setup-ai-rem`, `/ai-rem:prefedit`)
7. Preferences & Tool-Entities direkt via MCP API im Knowledge Graph anlegen

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
├── server.py                   # MCP-Server (FastMCP + Kuzu + Web UI + Backup + Cleanup)
├── bin/ai-rem                  # CLI (status/search/ingest/catchup, eigene .venv)
├── lib/                        # Extraktor (+ md-Fallback/Catch-up), Heuristik, mcp_client
├── requirements.txt            # fastmcp, kuzu
├── Dockerfile
├── docker-compose.yml
├── .env.example                # Vorlage für Konfiguration
├── .env                        # Konfiguration (nicht im Repo, aus .env.example ableiten)
├── setup-config.json           # Persönliche Konfiguration (gitignored; Beispiel im Repo)
├── .claude/settings.json.example  # Beispiel für repo-lokale Claude-Permissions
├── .claude/settings.json       # Lokale Claude-Permissions (gitignored; aus .example kopieren)
├── README.md                   # Englische Doku (kanonisch)
└── README.de.md                # Diese deutsche Doku
```

> `.claude/settings.json` ist **gitignored**, damit lokale Permission-Anpassungen nie im Repo
> landen. Zum Start: `cp .claude/settings.json.example .claude/settings.json`.

---

## CLAUDE.md Strategie

Das Setup-Skript schreibt in `~/.claude/CLAUDE.md` nur einen **minimalen 3-Zeilen-Pointer**:

```markdown
## ai-rem
ai-rem ist die einzige Wissensquelle für persistenten Kontext. Auto-Memory ist deaktiviert.
Nutzungsregeln kommen über die MCP Server Instructions, Verhaltensregeln aus den ai-rem Preferences.
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
