# Installations-Details

[← Zurück zur README](../README.de.md)

Die README deckt den Quick-Start ab (Server-Einmalsetup + Client-One-Liner). Diese Seite
dokumentiert, was das Client-Setup-Skript tatsächlich tut, das Repo-Layout und die
CLAUDE.md-Strategie, die es verwaltet.

## Was das Client-Setup-Skript tut

`bash <(curl -s http://<SERVER_IP>:3456/setup)` (oder `irm http://<SERVER_IP>:3456/setup.ps1 | iex`
auf nativem Windows) lädt dieselbe plattformneutrale Logik (`/setup.py`, benötigt Python 3) —
das Verhalten ist auf macOS, Linux, WSL und Windows identisch. Auf Windows werden die Hooks als
`python -X utf8 <hook>`-Commands registriert und der optionale SSH-Token-Pull nutzt den
mitgelieferten OpenSSH-Client (alternativ das Gerät im Browser koppeln oder `$env:AI_REM_TOKEN` setzen).

Das Skript erledigt automatisch:
1. `~/.claude.json` → `mcpServers.ai-rem` — ai-rem als user-scoped **stdio**-MCP-Server registrieren: `{"type": "stdio", "command": "~/.local/share/ai-rem/bin/ai-rem", "args": ["mcp-proxy"]}`. Der Proxy liest den Geräte-Token aus dem OS-Keychain und brückt zum HTTP-Endpoint; in der Config steht kein `headers.Authorization` mehr ([Authentifizierung](authentication.de.md#ein-secret-pro-gerät--der-os-keychain))
2. `~/.claude/settings-template.json` — Basis-Template für Permissions, Deny-Rules und Hooks aus der Live-Setup-Config schreiben
3. `~/.claude/hooks/system-check.py` — konsolidierter SessionStart-Hook deployen (ai-rem Health, SMB-Mount, MCP-Server-Tests — nur für die `mcp_stdio_servers`, die in `~/.claude.json` registriert sind oder von einem aktiven Plugin kommen —, Settings-Sync, Tools-Anzahl, Vektor-Abdeckung, offene Tasks/Pläne)
4. `~/.claude/hooks/auto-memory.py` — PreCompact + SessionEnd Hook deployen (Transcript → `ai-rem ingest` → llama-server-Extraktor → strukturierte Entities)
5. `~/.local/share/ai-rem/bin/ai-rem` (+ `../lib/`, die von der CLI importierten Module) — die CLI selbst lokal ablegen und `AI_REM_CLI` in `~/.claude/settings.json` darauf zeigen lassen. Ein dort eingetragener Clone-Pfad wird ersetzt: liegt der Clone auf einem Netzlaufwerk, ist die CLI beim Session-Ende weg, sobald der Mount hängt, und der Hook bricht still ab (nur sichtbar in `~/.claude/auto-memory/errors.log`). Ein manuell gesetztes `AI_REM_CLI`, das auf keinen Clone zeigt, bleibt unangetastet. Findet der Hook die CLI dort nicht, sucht er zusätzlich unter `~/myCode/github/ai-rem/bin/ai-rem`, `~/*/myCode/github/ai-rem/bin/ai-rem`, `/Volumes/*/myCode/…` und im `PATH`. Zusätzlich verlinkt das Setup `~/.local/bin/ai-rem` auf diese Kopie, damit `ai-rem` in der Shell als normaler Befehl funktioniert. Musste `~/.local/bin` dafür erst angelegt werden, zieht der `PATH` auf manchen Systemen erst beim nächsten Login nach; fehlt das Verzeichnis ganz im `PATH`, gibt das Setup die passende Zeile aus.
6. `~/.claude/hooks/claude-md-guard.py` — PreToolUse-Hook deployen, der (non-blocking) warnt, wenn `~/.claude/CLAUDE.md` editiert wird
7. `~/.claude/settings.json` — Permissions, Deny-Rules und alle Hooks eintragen (den PostToolUse-Hook `vault-secret-reminder.py` nur, wenn mykeyvault registriert oder als Plugin aktiv ist — sonst wird ein vorhandener Eintrag entfernt); alte Hooks entfernen; `autoMemoryEnabled: false`
8. `~/.claude/CLAUDE.md` — minimalen Pointer auf ai-rem anlegen oder aktualisieren
9. Slash-Commands installieren (`/setup-ai-rem`, `/memory-cleanup`, `/migrate-claude-md`, `/ai-rem-update`)
10. Preferences & Tool-Entities direkt via MCP API im Knowledge Graph anlegen
11. **mykeyvault** lokal bauen (git clone + `npm run build` im `mcp/`-Ordner) und als **stdio**-MCP über den Wrapper `ai-rem vault-mcp` registrieren, der beim Start Vault-URL und -Token von `/api/client-config` holt und `node <mcp entry>` damit im Env per `exec` startet — kein `env`-Block mit Vault-Token in `~/.claude.json`, keine `ai-rem-vault.env`. Lokaler stdio-Betrieb schaltet die exec/file-Tools frei (`vault_write_secret`, `vault_run_with_secret`, `vault_run_with_secret_file`) — Secrets landen damit **nie** im LLM-Kontext, sondern nur im lokal gestarteten Subprozess. Ohne Node/Git oder bei Build-Fehler fällt das Setup auf den HTTP-MCP über `ai-rem mcp-proxy --endpoint <vault-mcp-url>` zurück (nur `vault_list_items`/`vault_create_item`).

**Nur ai-rem:** Ohne Build-Blöcke in `mcp_register` (`mykeyvault.stdio`, `tools.stdio.registry_url`) baut das Setup nichts und prüft bzw. installiert deshalb auch kein node/npm/git; ohne mykeyvault entfällt der Hook `vault-secret-reminder.py` (ein Eintrag aus einem früheren Lauf wird ausgetragen), und der SessionStart-Check testet nur stdio-Server, die tatsächlich registriert sind.

**Plugin-Modus:** Ist in `~/.claude/settings.json` → `enabledPlugins` ein Plugin `ai-rem@…` / `mykeyvault@…` aktiv (beliebiger Marketplace, z. B. `ai-rem@tools-registry`), bringt es den MCP-Server selbst mit (`ai-rem mcp-proxy` / `ai-rem vault-mcp`). Das Setup legt dann den gleichnamigen `mcpServers`-Eintrag in `~/.claude.json` **nicht** an und entfernt einen vorhandenen mit Hinweis (auch `ai-rem update`) — es gibt nie zwei Server gleichen Namens. Hooks und Settings werden wie gewohnt installiert; ein aktives `mykeyvault`-Plugin gilt als registriert.

**Das einzige, was man sich merken muss:** die URL `<SERVER_IP>:3456/setup`. Das Skript ist idempotent — mehrfaches Ausführen auf derselben Maschine ist sicher.

## Frontends (Ziele)

Die Schritte oben sind das Ziel **`claude`**. Das Setup wählt Ziele mit `--client`
(`bash <(curl -s …/setup) --client opencode` oder später `ai-rem install --client …`); der
Default `auto` nimmt jedes Frontend, dessen Binary im `PATH` liegt, und fällt sonst auf
`generic` zurück. Installierte Ziele stehen in `~/.config/ai-rem/client.json` (`endpoint`,
`targets`, `vault_url`, `vault_entry` = Pfad des gebauten mykeyvault-MCP, `keychain` = das
Backend, das den Token hält). Die Datei enthält kein Secret: der Geräte-Token liegt im
OS-Keychain, LLM-Router-Key und Vault-Token kommen pro Lauf von `/api/client-config`.

**`opencode`** — die Unterschiede zu Claude Code:

| | Claude Code | opencode |
|---|---|---|
| Config | `~/.claude.json` → `mcpServers` | `~/.config/opencode/opencode.json` → `mcp` |
| stdio-Server | `"type": "stdio"`, `command` + `args` | `"type": "local"`, `command` als **ein Array** |
| http-Server (für ai-rem seit 1.7 nicht mehr genutzt) | `"type": "http"`, `url` + `headers` | `"type": "remote"`, `url` + `headers` |
| Env pro Server | `env` | `environment` (`AI_REM_CLIENT=opencode` für die Attribution) |
| Instruktionen | `~/.claude/CLAUDE.md` | `~/.config/opencode/AGENTS.md` + `instructions[]` |
| Secrets | keine in der Config — `ai-rem mcp-proxy` / `ai-rem vault-mcp` lösen sie beim Start auf (OS-Keychain, `/api/client-config`) | dieselben Wrapper, keine `{file:…}`-Referenz |
| Session-Hooks | SessionStart / PreCompact / SessionEnd | Plugin-Events `session.created` / `session.idle` / `session.compacted` |

Was das Ziel schreibt:
1. `opencode.json`: mergt `mcp.ai-rem` als `local`-Server (`command: [ai-rem, mcp-proxy]`,
   `environment: {AI_REM_CLIENT: opencode}`) und — wenn node und die Builds da sind —
   `mykeyvault` (`[ai-rem, vault-mcp]`) und `tools` als `local`-Server; `node` kommt
   aus dem `PATH` (Homebrew: `/opt/homebrew/bin/node`). Provider, Modelle und fremde Server
   bleiben unberührt, einmalig wird `opencode.json.pre-airem.bak` angelegt. Eine
   JSONC-Datei mit Kommentaren wird **nicht** umgeschrieben — der Block landet stattdessen
   in `~/.config/ai-rem/snippets/opencode-mcp.json`. Die Auto-Memory-`fallback.md` kommt
   in `instructions[]` (opencode kennt keinen `@`-Import).
2. `AGENTS.md`: ein markierter `<!-- ai-rem:begin/end -->`-Block, der den Agenten zu
   Sitzungsbeginn `memory_get_context()` aufrufen lässt — opencode hat keinen Session-Start-Hook.
3. `plugin/ai-rem.ts`: exportiert die Session über das SDK und startet `ai-rem ingest`
   detached, 10 Minuten nach dem letzten `session.idle` (nur bei neuen Nachrichten) und
   sofort bei `session.compacted`; bei `session.created` laufen `ai-rem catchup` und einmal
   pro Prozess `ai-rem update --check` (Toast bei Drift).
4. `command/*.md`: `/setup-ai-rem`, `/memory-cleanup`, `/ai-rem-update`.

**`generic`** — schreibt nichts außerhalb von `~/.config/ai-rem/snippets/`: fertige Blöcke
für Codex (`config.toml`, HTTP mit `bearer_token_env_var`, Token über
`export AI_REM_TOKEN="$(ai-rem token)"`), Gemini CLI (`settings.json`) und Cursor
(`mcp.json`) — beide stdio über `{"command": "ai-rem", "args": ["mcp-proxy"]}` — plus einen
`AGENTS.md`-Pointer. Diese Frontends bekommen Tools und Server-Instruktionen, aber kein
Auto-Memory.

`ai-rem uninstall --client <ziel>` entfernt, was das Ziel angelegt hat (bei opencode den
`ai-rem`-Server, den `instructions`-Eintrag, den `AGENTS.md`-Block, Plugin und Commands;
`mykeyvault`/`tools` bleiben stehen).

## Bestehende Installation aktuell halten

Die Schritte 3–9 liefern Dateien aus, die sich etwa bei jedem zweiten Release ändern.
Dafür das komplette Setup erneut zu fahren ist unverhältnismäßig — es macht den
SSH-Secret-Pull, `git clone` und die `npm`-Builds mit. Ein installierter Client nimmt
stattdessen die CLI:

```bash
ai-rem update --check   # nur berichten, Exit 1 wenn etwas hinterherhinkt
ai-rem update           # Dateien aller installierten Ziele nachziehen, danach die Frontends neu starten
```

`GET /manifest` listet zu jeder ausgelieferten Datei einen SHA-256; die CLI hasht die
lokalen Gegenstücke und zieht nur nach, was abweicht. Darunter läuft dasselbe
`setup.py --update`, das die Schritte 2–9 macht und alles überspringt, was SSH, git oder
npm braucht. Der `system-check`-SessionStart-Hook vergleicht genauso und meldet den
Rückstand von selbst — man erfährt es also in der Regel, bevor man fragen muss.

Die `settings.json` wird dabei nur ergänzt: neue Permissions und Hook-Gruppen kommen
hinzu, entfernt wird nichts. Die `settings-template.json`, aus der sie mergt, gehört dem
Server und **wird** vollständig neu geschrieben — eigene Änderungen gehören deshalb in
die `settings.json`.

Hooks werden nur beim Start geladen: nach einem Update Claude Code neu starten.

**Immer über die Server-URL starten, nie aus einem Clone.** `scripts/setup.py` trägt `KG_URL` als Platzhalter, den `server.py` beim Ausliefern ersetzt. Direkt aus einem Checkout gestartet bleibt der Platzhalter stehen, und Schritt 1 registriert wörtlich `__KG_URL__/mcp` in `~/.claude.json` — der MCP-Server verbindet sich dann nie (`INVALID_CONFIG: 'url' is not a valid URL`). Das Skript bricht in diesem Zustand jetzt ab (Exit 2). Zum Testen aus einem Clone `KG_URL` in der Umgebung setzen.

## Dateien

```
ai-rem/
├── server.py                   # MCP-Server (FastMCP + LadybugDB + Web UI + Backup + Cleanup
│                               #   + eingebettete setup.py/bash/PS1-Scripts und Hooks)
├── bin/ai-rem                  # CLI (status/search/ingest/catchup, reine stdlib, kein venv)
├── lib/                        # Extraktor (+ md-Fallback/Catch-up), Heuristik, mcp_client
├── hooks/save-plan.py          # PostToolUse-Hook: ExitPlanMode → offener Task in ai-rem
├── hooks/vault-secret-reminder.py  # PostToolUse-Hook: Bash → Vault-Secret-Erinnerung bei Auth-Fehler
├── docs/                       # Architektur (md + Mermaid + PDF), MCP-Funktionsdoku,
│                               #   release-history.md (archivierte Notes ≤ v0.1.5)
├── deploy.sh                   # Deploy auf den Heimserver (scp + Remote-Build + Recreate)
├── .github/workflows/          # Docker-Hub-Publish bei v*-Tags
├── requirements.txt            # fastmcp, ladybug, numpy, cryptography
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
