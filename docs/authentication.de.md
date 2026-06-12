# Authentifizierung

[← Zurück zur README](../README.de.md)

Alle sensiblen Routen (`/mcp`, `/api/*`, `/export`, `/import`, `/ui`) verlangen
Authentifizierung. Der Server ist **fail-closed**: ohne `AI_REM_API_TOKEN` startet
er nicht. Ein Request ist autorisiert, wenn **eine** Bedingung gilt:

1. Der Pfad ist public — `/health`, `/setup`, `/setup.py`, `/setup.ps1`, `/install`, `/setup-config`, `/hooks/*`, `/cmd*`, `/login` (nur Onboarding/Login, keine privaten Daten);
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

## Token-Quelle — [mykeyvault](https://github.com/markus7h/mykeyvault)

Der Token liegt einmalig im Vault als Item `ai-rem-api-token` (Single Source of Truth).
- **Server:** `deploy.sh` zieht ihn beim Deploy aus dem Vault und schreibt ihn in die Remote-`.env` — der Serverstart bleibt unabhängig vom Laufzeitzustand des Vaults.
- **Clients:** der SessionStart-Hook `system-check.py` nutzt für die laufende Session den bereits in `~/.claude.json` → `mcpServers."ai-rem".headers.Authorization` gespeicherten Bearer-Token (schnell, kein Vault-Roundtrip — darüber trägt auch Claudes `/mcp`-Kanal den Token) und frischt ihn in einem **detached Hintergrundprozess** für die nächste Session aus dem Vault auf (vault-api-Koordinaten in `~/.claude.json` → `mcpServers.mykeyvault.env`; das `bw`-Backend ist ~8 s, zu langsam für den synchronen Startpfad). Nur der allererste Lauf ohne gespeicherten Header liest synchron aus dem Vault. Liefert weder Header noch Vault einen Token, antwortet ai-rem mit `401`.

Token manuell erzeugen (ohne Vault): `openssl rand -hex 32`.
