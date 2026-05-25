# ai-rem — Knowledge Graph Memory für Claude

> Diese Dokumentation bezieht sich auf **[v0.1.0](https://github.com/markus7h/ai-rem/releases/tag/v0.1.0)**.

**ai-rem** ist ein persistentes Langzeit-Gedächtnis für Claude Code, das als MCP-Server auf dem Heimserver läuft.
Claude hat von Haus aus kein Gedächtnis über Sessions hinaus. Dieses Projekt löst das Problem: relevante Informationen – offene Tasks, getroffene Entscheidungen, gelöste Probleme, Projekte, genutzte Tools – werden in einem Knowledge Graph gespeichert und beim nächsten Gespräch automatisch geladen.

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
| `memory_search(query, context)` | Volltextsuche über Name + Beschreibung |
| `memory_get_context(topic, context)` | Relevanten Subgraph laden (Tasks, Projekte, Decisions, Preferences) |
| `memory_list(type, context)` | Alle Entities auflisten |
| `memory_get_relations(name)` | Alle Beziehungen einer Entity |
| `memory_delete(name)` | Entity und Relationen entfernen |
| `memory_status()` | Kurzstatus: Anzahl Entities und Relationen (wird vom SessionStart-Hook genutzt) |

### Entity-Typen

`Person` · `Project` · `Task` · `Tool` · `Problem` · `Solution` · `Decision` · `Preference` · `Topic`

### Kontext-Trennung

Jede Entity kann mit einem `context`-Tag versehen werden:
- `context="work"` — nur in Arbeits-Sessions sichtbar
- `context="private"` — nur in privaten Sessions sichtbar
- kein Tag — global, erscheint in allen Abfragen

Der Kontext kann per CLAUDE.md gesetzt werden: z.B. `context="work"` für Arbeits-Repos und `context="private"` für private Projekte.

---

## Web UI

| URL | Funktion |
|---|---|
| `/ui` | Backup-Verwaltung: manuell, Schedule, Download, Restore |
| `/prefs` | Preferences-Manager: pin, Context, Reihenfolge, löschen |

**`/prefs`** — Vollständiger Preferences-Manager im Browser: pin/unpin, Context-Dropdown, manuelle Reihenfolge (`sort_order`), löschen. Klick auf den Namen klappt die vollständige Beschreibung auf. Aufrufbar über den Slash-Command `/ai-rem:prefedit`.

---

## Voraussetzungen

- Docker auf dem Zielserver
- Claude Code CLI auf dem Client-Rechner
- Netzwerkzugang zu `<SERVER_IP>:<PORT>`

---

## Konfiguration

Umgebungsvariablen werden aus einer `.env`-Datei im Compose-Verzeichnis geladen:

```env
KG_PUBLIC_URL=http://<SERVER_IP>:3456   # Öffentliche URL des Servers
PORT=3456                                # TCP-Port (Standard: 3456)
KUZU_DB_PATH=/data/kg.db                 # Pfad zur Datenbank
BACKUP_DIR=/backups                      # Pfad für Backup-Dateien
MAX_BACKUPS=10                           # Maximale Anzahl aufbewahrter Backups
KUZU_POOL_SIZE=4                         # Connection-Pool-Größe
```

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

# .env anlegen und anpassen
cp .env.example .env
# → KG_PUBLIC_URL in .env auf die echte Server-IP setzen

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
2. `~/.claude/settings-template.json` — Basis-Template für Permissions, Deny-Rules und Hooks anlegen (falls nicht vorhanden)
3. `~/.claude/hooks/system-check.py` — konsolidierter SessionStart-Hook deployen (ai-rem Health, SMB-Mount, MCP-Server-Tests, Settings-Sync, Tools-Anzahl)
4. `~/.claude/settings.json` — Permissions, Deny-Rules und Hook eintragen; alte Hooks entfernen; `autoMemoryEnabled: false`
5. `~/.claude/CLAUDE.md` — minimalen 3-Zeilen-Pointer auf ai-rem anlegen oder aktualisieren
6. Slash-Commands installieren (`/setup-ai-rem`, `/ai-rem:prefedit`)
7. `~/.claude/ai-rem/pref-tui.py` — Terminal-Preferences-Manager installieren
8. Preferences & Tool-Entities direkt via MCP API im Knowledge Graph anlegen

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
├── server.py              # MCP-Server (FastMCP + Kuzu + Web UI + Backup)
├── requirements.txt       # fastmcp, kuzu
├── Dockerfile
├── docker-compose.yml
├── .env.example           # Vorlage für Konfiguration
├── .env                   # Konfiguration (nicht im Repo, aus .env.example ableiten)
├── setup-config.json      # Persönliche Konfiguration (gitignored; Beispiel im Repo)
├── README.md
└── README.en.md
```

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
  "smb": {"mount": "/Volumes/markus", "url": "smb://server/share"},
  "mcp_stdio_servers": {"paperless": "/path/to/index.js"},
  "tools_scripts_dir": "/path/to/tools-mcp/scripts",
  "old_hooks": ["legacy-hook.sh"],
  "entities": [{"name": "...", "type": "Tool", "description": "..."}]
}
```

Im Docker-Image wird die Datei zur Buildzeit kopiert (`COPY setup-config*.json ./`). Ein Dummy `setup-config.json` im Repo dient als öffentliches Beispiel ohne private Daten; die echte persönliche Version ist gitignored.

Der `system-check.py`-Hook liest seine Konfiguration aus `~/.claude/settings-template.json`, das beim ersten Setup angelegt wird und u.a. SMB-Pfad, MCP-stdio-Server-Pfade und tools-Verzeichnis enthält.
