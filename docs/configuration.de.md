# Persönliche Konfiguration (setup-config.json)

[← Zurück zur README](../README.de.md)

Die Laufzeit-Umgebungsvariablen (`AI_REM_API_TOKEN`, `KG_PUBLIC_URL`, `PORT`, …) sind im
Abschnitt **Konfiguration** der README dokumentiert. Diese Seite behandelt die optionale
**`setup-config.json`**, die das Client-Onboarding steuert.

Der Setup-Endpunkt lädt optional eine `setup-config.json` vom Server (`GET /setup-config`, public, weil das Onboarding vor dem ersten Token läuft — jedes Top-Level-Feld mit Endung `_key`, `_token`, `_secret` oder `_password` wird aus der Antwort herausgefiltert; `_comment_*`-Keys bleiben). Diese Datei ist **nicht im Repo** — sie enthält persönliche Einstellungen:

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

**`llm_api_key`** (optional) — Bearer-Token für den LLM-Router hinter `ollama_url`. Er wird nie auf einen Client geschrieben: Clients holen ihn pro Lauf von `/api/client-config` (unten). Bevorzugt am Server als Env `AI_REM_LLM_API_KEY` setzen, das hat Vorrang vor diesem Feld — dann steht der Key in keiner Datei, die den Server verlässt. Einen Virtual Key mit Budget nehmen, nicht den Master-Key.

**`mcp_register`** lässt das Setup Begleit-MCP-Server einrichten:
- **mykeyvault** wird aus `stdio.repo` gebaut und als stdio-MCP über `ai-rem vault-mcp` registriert, das beim Start `vault_url`/`vault_token` von `/api/client-config` holt (der Server liefert sie aus `AI_REM_PAIR_VAULT_TOKEN` und `vault_url`). Ohne node oder bei Build-Fehler fällt es auf den HTTP-MCP aus `http.url` zurück (oder `https_url`, wenn ai-rem selbst über einen vertrauenswürdigen HTTPS-Endpunkt läuft), via `ai-rem mcp-proxy --endpoint`.
- **tools** ([tools-registry](https://github.com/markus7h/tools-registry)) wird aus `stdio.repo` geklont, mit `npm` gebaut und als stdio-MCP mit `TOOLS_REGISTRY_URL=stdio.registry_url` registriert. Das benötigt **node, npm und git** auf dem Client — fehlt etwas, gibt das Setup einen Installationshinweis aus und überspringt `tools` (alles andere läuft trotzdem durch).

## Laufzeit-Config für Clients — `GET /api/client-config`

Seit 1.7 speichert eine Workstation genau ein Secret, den Geräte-Token im OS-Keychain (siehe
[Authentifizierung](authentication.de.md#ein-secret-pro-gerät--der-os-keychain)). Alles
Weitere wird pro Lauf geholt, authentifiziert mit diesem Token:

```json
{"version": "1.7.0", "llm_url": "http://router:11437", "llm_api_key": "…", "vault_url": "http://server:8223", "vault_token": "…"}
```

- `llm_url` — `AI_REM_OLLAMA_URL`/`AI_REM_LLAMA_URL` am Server, sonst `ollama_url` aus der setup-config.
- `llm_api_key` — Server-Env `AI_REM_LLM_API_KEY` (bevorzugt), sonst `llm_api_key` aus der setup-config.
- `vault_url`, `vault_token` — `AI_REM_PAIR_VAULT_TOKEN` und die Vault-URL aus `mcp_register.mykeyvault`; leer, wenn die Variable nicht gesetzt ist — dann bleibt mykeyvault auf HTTP ohne die exec/file-Tools.

Verbraucher: `ai-rem ingest`/`catchup` (Extraktor), der SessionStart-Hook `system-check.py` (Router-Probe) und `ai-rem vault-mcp`. Nichts davon wird persistiert — LLM-Key oder Vault-Token rotieren heißt: `.env` am Server ändern, Neustart, fertig; keine Workstation muss angefasst werden. `AI_REM_LLAMA_URL`/`AI_REM_LLM_API_KEY` im Env eines Clients überschreiben die gelieferten Werte weiterhin; gegen einen Server < 1.7 fallen die Clients auf `settings-template.json` zurück und sagen das auch.

> Der `system-check.py`-Hook liest seine Konfiguration aus `~/.claude/settings-template.json`, das beim ersten Setup angelegt wird und u. a. SMB-Pfad, MCP-Server-Koordinaten und das tools-Verzeichnis enthält.
