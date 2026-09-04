# Persönliche Konfiguration (setup-config.json)

[← Zurück zur README](../README.de.md)

Die Laufzeit-Umgebungsvariablen (`AI_REM_API_TOKEN`, `KG_PUBLIC_URL`, `PORT`, …) sind im
Abschnitt **Konfiguration** der README dokumentiert. Diese Seite behandelt die optionale
**`setup-config.json`**, die das Client-Onboarding steuert.

Der Setup-Endpunkt lädt optional eine `setup-config.json` vom Server (`/setup-config`). Diese Datei ist **nicht im Repo** — sie enthält persönliche Einstellungen:

```json
{
  "ssh_host": "your-server",
  "ssh_user": "your-user",
  "ssh_hostname": "your-server.lan",
  "permissions_allow_portable": ["Bash", "mcp__tools__*", ...],
  "permissions_deny": ["Bash(bw get *)", ...],
  "permissions_default_mode": "plan",
  "smb": {"mount": "/path/to/mount", "url": "smb://server/share"},
  "mcp_register": {
    "mykeyvault": {"http": {"url": "http://server:3458/mcp", "https_url": "https://keyvault.example/mcp"}, "vault_url": "http://server:8223"},
    "tools": {"stdio": {"repo": "https://github.com/markus7h/tools-registry.git", "install_dir": "~/Code/tools-registry", "entry": "dist/index.js", "registry_url": "http://server:3457"}}
  },
  "old_hooks": ["legacy-hook.sh"],
  "entities": [{"name": "...", "type": "Tool", "description": "..."}]
}
```

Die persönliche `setup-config.json` ist gitignored und landet daher nie im öffentlichen Image. Sie kommt stattdessen per Bind-Mount aus dem Deployment-Verzeichnis in den Container (`./setup-config.json:/app/setup-config.json:ro` in der `docker-compose.yml`) — der `COPY setup-config*.json ./` im Dockerfile greift nur beim lokalen Build. **Ohne den Mount liefert ein aus dem Docker-Hub-Image gestarteter Container die Platzhalter des Examples** (u. a. `ollama_url: http://your-server:11434`), und jede Neuinstallation erbt eine tote llama-URL in ihrem `settings-template.json`. Stattdessen liegt eine generische **`setup-config.example.json`** im Repo: Fehlt eine persönliche Config, fällt `/setup-config` darauf zurück — ein frisches Deployment seedet so ein sinnvolles Starter-Set an Verhaltens-Preferences plus generische Permission-/Deny-Regeln. Eine eigene `setup-config.json` überschreibt das Template komplett.

**`permissions_default_mode`** seedet `permissions.defaultMode` (Default `plan`). Das Template setzt zusätzlich `skipAutoPermissionPrompt` und `useAutoModeDuringPlan`: Im Plan Mode laufen Shell-Kommandos dann über den Auto-Mode-Klassifizierer statt über Einzel-Prompts — Schreibzugriffe bleiben blockiert, und der Plan selbst bleibt bestätigungspflichtig.

**`mcp_register`** lässt das Setup Begleit-MCP-Server einrichten, mit Tokens, die es über SSH von `ssh_host` zieht:
- **mykeyvault** wird als HTTP-MCP aus `http.url` registriert (oder `https_url`, wenn ai-rem selbst über einen vertrauenswürdigen HTTPS-Endpunkt läuft).
- **tools** ([tools-registry](https://github.com/markus7h/tools-registry)) wird aus `stdio.repo` geklont, mit `npm` gebaut und als stdio-MCP mit `TOOLS_REGISTRY_URL=stdio.registry_url` registriert. Das benötigt **node, npm und git** auf dem Client — fehlt etwas, gibt das Setup einen Installationshinweis aus und überspringt `tools` (alles andere läuft trotzdem durch).

> Der `system-check.py`-Hook liest seine Konfiguration aus `~/.claude/settings-template.json`, das beim ersten Setup angelegt wird und u. a. SMB-Pfad, MCP-Server-Koordinaten und das tools-Verzeichnis enthält.
