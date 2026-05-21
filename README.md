# ai-rem — Knowledge Graph Memory für Claude

> Diese Dokumentation bezieht sich auf **[v0.0.7](https://github.com/markus7h/ai-rem/releases/tag/v0.0.7)** ([Release Notes](release-notes-v0.0.7.md)).

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
| `memory_add(name, type, description, context)` | Entity anlegen oder aktualisieren |
| `memory_relate(from, relation, to)` | Beziehung zwischen zwei Entities erstellen |
| `memory_search(query, context)` | Volltextsuche über Name + Beschreibung |
| `memory_get_context(topic, context)` | Relevanten Subgraph laden (Tasks, Projekte, Decisions) |
| `memory_list(type, context)` | Alle Entities auflisten |
| `memory_get_relations(name)` | Alle Beziehungen einer Entity |
| `memory_delete(name)` | Entity und Relationen entfernen |

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

Die eingebaute Web-Oberfläche ist erreichbar unter `http://<SERVER_IP>:3456/ui`.

Funktionen:
- **Manueller Backup** — DB-Snapshot per Klick erstellen
- **Automatischer Backup-Schedule** — stündlich / täglich / wöchentlich, konfigurierbar in der UI
- **Backup-Verwaltung** — Liste aller Backups mit Download und Löschen
- **Restore** — JSON-Backup hochladen, Modus `merge` oder `replace`

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
KG_DATA_PATH=./data                      # Pfad zur Datenbank
KG_BACKUP_PATH=./backups                 # Pfad für Backup-Dateien
MAX_BACKUPS=10                           # Maximale Anzahl aufbewahrter Backups
```

---

## Installation / Deployment

### Server (einmalig)

```bash
# Verzeichnis anlegen
mkdir -p ~/mydocker/compose-files/ai-rem

# Dateien übertragen
rsync -av server.py requirements.txt Dockerfile docker-compose.yml .env.example \
  your-server:~/mydocker/compose-files/ai-rem/

# .env aus Vorlage anlegen und anpassen
ssh your-server "cp ~/mydocker/compose-files/ai-rem/.env.example ~/mydocker/compose-files/ai-rem/.env"
# → KG_PUBLIC_URL in .env auf die echte Server-IP setzen

# Container starten
ssh your-server "cd ~/mydocker/compose-files/ai-rem && docker compose up -d --build"
```

### Client — neuer Rechner einrichten

**Auf jeder neuen Maschine** — einen Satz zu Claude:

```
Führe aus: bash <(curl -s http://<SERVER_IP>:3456/setup)
```

Das Skript erledigt automatisch:
1. `claude mcp add` — ai-rem als user-scoped HTTP MCP-Server registrieren
2. `~/.claude/CLAUDE.md` — Startup-Instruction anlegen
3. `~/.claude/commands/setup-ai-rem.md` — lokalen Slash-Command anlegen

**Das einzige, was man sich merken muss:** die URL `<SERVER_IP>:3456/setup`.

Der Slash-Command `/setup-ai-rem` ist ein **lokaler Shortcut** — er existiert nur auf Maschinen,
auf denen das Setup bereits gelaufen ist. Wer `~/.claude/commands/` per Dotfiles/Syncthing
synchronisiert, kann auf weiteren Maschinen `/setup-ai-rem` nutzen — alle anderen verwenden
einfach den curl-Befehl.

### Update nach Code-Änderungen

```bash
rsync -av server.py requirements.txt Dockerfile docker-compose.yml \
  your-server:~/mydocker/compose-files/ai-rem/
ssh your-server "cd ~/mydocker/compose-files/ai-rem && docker compose up -d --build"
```

---

## Dateien

```
ai-rem/
├── server.py          # MCP-Server (FastMCP + Kuzu + Web UI + Backup)
├── requirements.txt   # fastmcp, kuzu
├── Dockerfile
├── docker-compose.yml
├── .env.example       # Vorlage für Konfiguration
├── .env               # Konfiguration (nicht im Repo, aus .env.example ableiten)
├── README.md
└── README.en.md
```

---

## CLAUDE.md Strategie

Die CLAUDE.md-Dateien sind minimal gehalten — sie enthalten nur den Verweis auf ai-rem.
Alles andere (Präferenzen, LAN-Konfiguration, Projektinfos, Entscheidungen) lebt im Graph.

| Datei | Inhalt |
|---|---|
| `~/.claude/CLAUDE.md` | Startup: `memory_get_context()`, Standard-Context `"private"` |
| `work-repo/CLAUDE.md` | Startup: `memory_get_context(context="work")`, Standard-Context `"work"` |
