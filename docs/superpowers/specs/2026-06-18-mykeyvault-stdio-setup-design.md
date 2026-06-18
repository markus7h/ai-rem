---
name: mykeyvault als lokaler stdio-MCP
description: ai-rem-Setup registriert mykeyvault als lokal gebauten stdio-MCP statt HTTP — exec/file-Tools verfügbar, Secrets nie im LLM-Kontext
status: offen
---

# mykeyvault als lokaler stdio-MCP via ai-rem-Setup

## Problem

Das ai-rem-Setup registriert mykeyvault aktuell als **HTTP**-MCP gegen den
zentralen Container (`update_claude_json` Schritt 2). Über HTTP sind nur
`vault_list_items` / `vault_create_item` verfügbar — die drei lokal
ausführenden Tools `vault_write_secret`, `vault_run_with_secret` und
`vault_run_with_secret_file` sind in `mcp/src/index.ts` bewusst **stdio-only**
(brauchen das Client-Dateisystem). Damit kann ai-rem auf Clients keine Secrets
in Subprozesse injizieren, ohne sie vorher in den LLM-Kontext zu holen.

## Ziel

mykeyvault wird auf Claude-Clients als **lokaler stdio-MCP** registriert
(voller Funktionsumfang). Der zentrale HTTP-Container bleibt unangetastet, wird
für Claude-Clients aber nicht mehr registriert (keine Tool-Namens-Kollision).
Kein mykeyvault-Code ändern — nur frisch bauen (das eingecheckte `dist/` ist
veraltet) und registrieren.

## Entscheidungen

| Frage | Entscheidung |
|---|---|
| Transport | stdio bevorzugt, HTTP als Graceful-Degradation-Fallback |
| Build | git clone/pull + `npm install && npm run build` im `mcp/`-Subdir |
| Env | `VAULT_API_URL` + `VAULT_API_TOKEN` (beide in `update_claude_json` vorhanden) |
| Repo-URL | HTTPS (`https://github.com/markus7h/mykeyvault.git`), nicht SSH-Alias |
| Secret-at-rest | `VAULT_API_TOKEN` landet in `~/.claude.json` (bewusst, siehe unten) |

## Umsetzung

### 1. Build — `_build_node_mcp` (server.py)

Clone/Build/Node-Check aus `build_tools_mcp` in generischen Helfer
`_build_node_mcp(repo, install_dir, entry, subdir, label)` herausziehen:
Node≥18-Check, `git clone --depth 1` / `pull --ff-only`, `npm install && npm
run build` mit `cwd=install_dir/subdir`, Entry-Verifikation. `entry` ist
install_dir-relativ; `subdir` ist nur npm-cwd. `build_tools_mcp` (subdir='')
und neu `build_mykeyvault_mcp` (subdir='mcp') werden dünne Wrapper. → entfernt
Duplikat statt es zu verdoppeln.

### 2. Registrierung — `update_claude_json` (server.py)

Neuer Parameter `vault_entry`. Schritt 2 von HTTP auf stdio kippen:

```python
if vault_entry and vault_token:
    servers['mykeyvault'] = {'type': 'stdio', 'command': 'node',
        'args': [vault_entry],
        'env': {'VAULT_API_URL': vault_url, 'VAULT_API_TOKEN': vault_token}}
else:
    # bisheriger HTTP-Pfad (Reachability-Check + Kandidaten) als Fallback
    ...
```

`vault_url` (Z. 1241) und `vault_token` (Param) liegen bereits vor — kein neuer
Bootstrap. Fehlt node/git oder schlägt der Build fehl (`vault_entry == ''`),
bleibt's beim HTTP-MCP (list/create funktioniert weiter, nur ohne exec/file).

Nebeneffekt: `_vault_coords` (server.py:152) liest `mcpServers.mykeyvault.env`
— mit der stdio-Registrierung stehen `VAULT_API_URL/TOKEN` dort direkt, der
Token-Refresh-Hook läuft ohne die `ai-rem-vault.env`-Fallback-Datei.

### 3. Config + Verdrahtung

`setup-config.json` → `mcp_register.mykeyvault` bekommt einen `stdio`-Block
(`repo`, `install_dir: ~/Code/mykeyvault`, `subdir: mcp`, `entry:
mcp/dist/index.js`). Der bisherige `http`-Block bleibt als Fallback. Im
Hauptlauf wird `build_mykeyvault_mcp` aufgerufen und `vault_entry` an
`update_claude_json` durchgereicht.

## Secret-at-rest

Die stdio-Variante schreibt `VAULT_API_TOKEN` nach `~/.claude.json` (nicht
0600, anders als `ai-rem-vault.env`). Bisher stand dort nur der ai-rem-Bearer.
Bewusste Änderung, konsistent damit, dass `.claude.json` ohnehin Bearer-Tokens
hält.

## Check (ponytail, eine Prüfung)

`tests/test_mykeyvault_stdio.py`: gebautes `index.js` im stdio-Modus starten,
`initialize` + `tools/list` per MCP-Handshake schicken, asserten dass die drei
exec/file-Tools gelistet sind. Kein Netz/Secret nötig — prüft genau das Ziel
(Tools auf stdio verfügbar). Skippt, wenn node oder das gebaute `dist/` fehlt.

## Docs

Kurzer Hinweis in `docs/installation*.md` + mykeyvault-README: Vault-MCP läuft
lokal per stdio, Secrets landen nie im LLM-Kontext.

## Bewusst weggelassen

npm-Publish, sparse-checkout, Entfernen des HTTP-Containers (separate Op).
