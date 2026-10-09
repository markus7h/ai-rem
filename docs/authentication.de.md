# Authentifizierung

[← Zurück zur README](../README.de.md)

Alle sensiblen Routen (`/mcp`, `/api/*`, `/export`, `/import`, `/ui`) verlangen
Authentifizierung. Der Server ist **fail-closed**: ohne `AI_REM_API_TOKEN` startet
er nicht. Ein Request ist autorisiert, wenn **eine** Bedingung gilt:

1. Der Pfad ist public — `/health`, `/setup`, `/setup.py`, `/setup.ps1`, `/install`, `/setup-config` (Secret-Felder herausgefiltert), `/hooks/*`, `/bin/*`, `/lib/*`, `/cmd*`, `/login` (nur Onboarding/Login, keine privaten Daten);
2. die Herkunft ist **Loopback** *und der Request ist nicht proxied* (kein `X-Forwarded-For`). Im Bridge-Netz deckt das faktisch nur containerinternen Verkehr ab (z. B. den Healthcheck): getunnelte/proxied Requests kommen als Docker-Gateway-IP an, nicht als Loopback. Hinter einem Same-Host-Reverse-Proxy (z. B. Caddy) ist die Peer-IP zwar `127.0.0.1`, aber `X-Forwarded-For` ist gesetzt → der Token wird trotzdem verlangt;
3. er trägt `Authorization: Bearer <AI_REM_API_TOKEN>` (konstant-zeitlicher Vergleich) — von MCP-Clients (Claudes `/mcp`-Kanal);
4. er trägt ein gültiges `ai_rem_session`-Cookie — von der Browser-Web-UI (siehe unten).

## Web-UI-Login

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

## Neuen Rechner koppeln (ohne SSH)

Der Installer holt seinen Token wie `gh auth login`: Er startet eine Kopplung
(`POST /api/pair/start`), zeigt einen Code wie `K7QF-2M9X` und öffnet
`https://<server>/pair?code=…`. Du gibst sie in der Web-UI frei — eingeloggt, von jedem
Gerät, auch vom Handy — und der Installer holt den Token genau einmal ab
(`POST /api/pair/poll`). Eine Kopplung läuft nach 10 Minuten ab, Starts sind je IP
begrenzt, die Freigabe verlangt den UI-Login, und jede Entscheidung wird protokolliert
(`/pair` „Zuletzt gekoppelte Geräte“, `backups/paired-devices.json`).

Was übergeben wird: der ai-rem-Token — und mehr wird auf dem Rechner nicht gespeichert.
Der Installer legt ihn im OS-Keychain ab; alles Weitere, was ein Client braucht
(LLM-Router-Key, mykeyvault-URL und -Token), kommt pro Lauf von `GET /api/client-config`,
authentifiziert mit genau diesem Token (siehe [Konfiguration](configuration.de.md)).
`/api/pair/poll` liefert bei gesetztem `AI_REM_PAIR_VAULT_TOKEN` weiterhin
`vault_token`/`vault_url` — deprecated, nur für Installer vor 1.7; der aktuelle Installer
ignoriert die Felder.

Reihenfolge im Installer: Env `AI_REM_TOKEN` → SSH-Pull von `ssh_host` (still, wenn ein
SSH-Key eingerichtet ist) → Token bereits im Keychain → Kopplung → Eingabe. `ai-rem pair`
überspringt die Keychain-Stufe und erneuert auf einem eingerichteten Rechner nur den
Token — so rotiert man auch einen Geräte-Token.

## Ein Secret pro Gerät — der OS-Keychain

Seit 1.7 ist der ai-rem-Token das einzige Secret auf einer Workstation, und er liegt im
Keychain der Plattform (`lib/keychain.py`, wird mit der CLI ausgeliefert): Service
`ai-rem`, Account `device-token` (`AI_REM_KEYCHAIN_ACCOUNT` überschreibt den Account,
z. B. für Tests).

| Plattform | Backend | Hinweise |
|---|---|---|
| macOS | Keychain (`security`) | Das Kommando geht per stdin an `security -i`, der Token taucht nie in `ps` auf. So angelegte Items liest `security` später ohne GUI-Prompt; in einer SSH-Session ohne GUI-Login vorher `security unlock-keychain` |
| Linux | libsecret (`secret-tool`) | GNOME Keyring, KWallet via Portal. Ohne D-Bus-Session oder entsperrten Keyring fällt das Speichern auf das Datei-Backend zurück und sagt das auch |
| Linux-Fallback | Datei `$XDG_CONFIG_HOME/ai-rem/keyring/<account>` (0600 in einem 0700-Verzeichnis) | Greift, wenn `secret-tool` fehlt oder keine Session hat. `ai-rem doctor` und `client.json` → `keychain` zeigen `Datei (0600) …`, damit die Degradierung sichtbar bleibt |
| Windows | Credential Manager (`advapi32`) | Target `ai-rem/<account>`, UTF-16LE-Blob — wie `cmdkey`/PowerShell es erwarten |

Jeder Verbraucher löst den Token in derselben Reihenfolge auf:
1. `AI_REM_TOKEN` im Env — bewusster Override (CI, Einmal-Skripte);
2. der Keychain;
3. Klartext-Altlasten, **nur lesend**: `token_file` aus einer alten `client.json`, dann
   `mcpServers.ai-rem.headers.Authorization` in `~/.claude.json`. Beide geben den Hinweis
   aus, dass `ai-rem update` migriert; geschrieben wird dort nichts mehr.

`ai-rem token` gibt den aufgelösten Token aus (für `/login` oder `curl`), `echo <token> |
ai-rem token --store` legt ihn im Keychain ab (stdin, nie argv), `ai-rem token --forget`
entfernt ihn. Der SessionStart-Hook meldet die Quelle als `token ✓ (Keychain)`,
`token ✓ (Env)`, `token ⚠ Klartext-Legacy → ai-rem update` oder `token ❌ fehlt → ai-rem pair`.

Auch die MCP-Frontends sehen den Token nie: Claude Code und opencode starten `ai-rem
mcp-proxy`, einen stdio-MCP-Server, der den Keychain liest und zum HTTP-Endpoint brückt,
und `ai-rem vault-mcp`, das den Vault-Zugang von `/api/client-config` holt und den
mykeyvault-MCP damit im Env per `exec` startet. Ein `401` vom Server kommt als
JSON-RPC-Fehler mit dem Hinweis `ai-rem pair` an.

## Token-Quelle am Server — [mykeyvault](https://github.com/markus7h/mykeyvault)

Der Token liegt einmalig im Vault als Item `ai-rem-api-token` (Single Source of Truth).
`deploy.sh` zieht ihn beim Deploy aus dem Vault und schreibt ihn in die Remote-`.env` — der
Serverstart bleibt unabhängig vom Laufzeitzustand des Vaults. Clients reden für den
ai-rem-Token gar nicht mehr mit dem Vault; sie bekommen ihn einmal per Kopplung (oder SSH)
und halten ihn im Keychain.

Token manuell erzeugen (ohne Vault): `openssl rand -hex 32`.
