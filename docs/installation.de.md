# Installations-Details

[← Zurück zur README](../README.de.md)

Die README deckt den Quick-Start ab (Server-Einmalsetup + Client-One-Liner). Diese Seite
dokumentiert, was das Client-Setup-Skript tatsächlich tut, das Repo-Layout und die
CLAUDE.md-Strategie, die es verwaltet.

## Was das Client-Setup-Skript tut

`bash <(curl -s http://<SERVER_IP>:3456/setup)` (oder `irm http://<SERVER_IP>:3456/setup.ps1 | iex`
auf nativem Windows) lädt dieselbe plattformneutrale Logik (`/setup.py`, benötigt Python 3) —
das Verhalten ist auf macOS, Linux, WSL und Windows identisch. Auf Windows werden die Hooks als
`python -X utf8 <hook>`-Commands registriert und der Secret-Pull nutzt den mitgelieferten
OpenSSH-Client (alternativ `$env:AI_REM_TOKEN` setzen).

Das Skript erledigt automatisch:
1. `claude mcp add` — ai-rem als user-scoped HTTP MCP-Server registrieren
2. `~/.claude/settings-template.json` — Basis-Template für Permissions, Deny-Rules und Hooks aus der Live-Setup-Config schreiben
3. `~/.claude/hooks/system-check.py` — konsolidierter SessionStart-Hook deployen (ai-rem Health, SMB-Mount, MCP-Server-Tests, Settings-Sync, Tools-Anzahl, Vektor-Abdeckung, offene Tasks/Pläne)
4. `~/.claude/hooks/auto-memory.py` — PreCompact + SessionEnd Hook deployen (Transcript → `ai-rem ingest` → llama-server-Extraktor → strukturierte Entities)
5. `~/.local/share/ai-rem/bin/ai-rem` (+ `../lib/`, die von der CLI importierten Module) — die CLI selbst lokal ablegen und `AI_REM_CLI` in `~/.claude/settings.json` darauf zeigen lassen. Ein dort eingetragener Clone-Pfad wird ersetzt: liegt der Clone auf einem Netzlaufwerk, ist die CLI beim Session-Ende weg, sobald der Mount hängt, und der Hook bricht still ab (nur sichtbar in `~/.claude/auto-memory/errors.log`). Ein manuell gesetztes `AI_REM_CLI`, das auf keinen Clone zeigt, bleibt unangetastet. Findet der Hook die CLI dort nicht, sucht er zusätzlich unter `~/myCode/github/ai-rem/bin/ai-rem`, `~/*/myCode/github/ai-rem/bin/ai-rem`, `/Volumes/*/myCode/…` und im `PATH`.
6. `~/.claude/hooks/claude-md-guard.py` — PreToolUse-Hook deployen, der (non-blocking) warnt, wenn `~/.claude/CLAUDE.md` editiert wird
7. `~/.claude/settings.json` — Permissions, Deny-Rules und alle Hooks eintragen; alte Hooks entfernen; `autoMemoryEnabled: false`
8. `~/.claude/CLAUDE.md` — minimalen Pointer auf ai-rem anlegen oder aktualisieren
9. Slash-Commands installieren (`/setup-ai-rem`, `/memory-cleanup`, `/migrate-claude-md`)
10. Preferences & Tool-Entities direkt via MCP API im Knowledge Graph anlegen
11. **mykeyvault** lokal als **stdio**-MCP bauen und registrieren (git clone + `npm run build` im `mcp/`-Ordner). Lokaler stdio-Betrieb schaltet die exec/file-Tools frei (`vault_write_secret`, `vault_run_with_secret`, `vault_run_with_secret_file`) — Secrets landen damit **nie** im LLM-Kontext, sondern nur im lokal gestarteten Subprozess. Ohne Node/Git oder bei Build-Fehler fällt das Setup auf den HTTP-MCP zurück (nur `vault_list_items`/`vault_create_item`).

**Das einzige, was man sich merken muss:** die URL `<SERVER_IP>:3456/setup`. Das Skript ist idempotent — mehrfaches Ausführen auf derselben Maschine ist sicher.

## Dateien

```
ai-rem/
├── server.py                   # MCP-Server (FastMCP + Kuzu + Web UI + Backup + Cleanup
│                               #   + eingebettete setup.py/bash/PS1-Scripts und Hooks)
├── bin/ai-rem                  # CLI (status/search/ingest/catchup, reine stdlib, kein venv)
├── lib/                        # Extraktor (+ md-Fallback/Catch-up), Heuristik, mcp_client
├── hooks/save-plan.py          # PostToolUse-Hook: ExitPlanMode → offener Task in ai-rem
├── hooks/vault-secret-reminder.py  # PostToolUse-Hook: Bash → Vault-Secret-Erinnerung bei Auth-Fehler
├── docs/                       # Architektur (md + Mermaid + PDF), MCP-Funktionsdoku,
│                               #   release-history.md (archivierte Notes ≤ v0.1.5)
├── deploy.sh                   # Deploy auf den Heimserver (scp + Remote-Build + Recreate)
├── .github/workflows/          # Docker-Hub-Publish bei v*-Tags
├── requirements.txt            # fastmcp, kuzu, numpy, cryptography
├── requirements-embed.txt      # fastembed — nur im vollen Image, nicht in :slim
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

## CLAUDE.md-Strategie

Das Setup-Skript schreibt in `~/.claude/CLAUDE.md` nur einen **minimalen Pointer**:

```markdown
## ai-rem
ai-rem ist die einzige Wissensquelle für persistenten Kontext. Claude Codes natives Markdown-Auto-Memory ist deaktiviert.
Nutzungsregeln kommen über die MCP Server Instructions, Verhaltensregeln aus den ai-rem Preferences.

<!-- Auto-Memory md-Fallback: bei llama-server-Ausfall befüllt, vom catchup geleert -->
@~/.claude/auto-memory/fallback.md
```

Die eigentlichen Regeln kommen aus zwei Quellen, die automatisch beim Sitzungsstart geladen werden:
- **MCP Server Instructions** — was zu speichern ist, was nicht, wie Entities zu verknüpfen sind (fest im Server)
- **ai-rem Preferences** (`memory_get_context`) — persönliche Verhaltensregeln, Feedback, Arbeitsweisen (dynamisch, im Graph)

Ein **PreToolUse-Guard-Hook** (`claude-md-guard.py`, vom Setup-Skript deployt) verstärkt diese Invariante: Sobald `~/.claude/CLAUDE.md` editiert wird, injiziert er einen non-blocking Hinweis, Regeln/Wissen lieber in ai-rem zu legen, statt sie still in der CLAUDE.md anzusammeln.

Projekt-spezifische CLAUDE.md-Dateien setzen den Standard-Context:

| Datei | Zweck |
|---|---|
| `~/.claude/CLAUDE.md` | Minimaler ai-rem-Pointer (verwaltet vom Setup-Skript) |
| `work-repo/CLAUDE.md` | `context="work"` als Standard für Arbeits-Repos |
