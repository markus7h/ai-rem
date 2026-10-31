# ai-rem als claude.ai-Connector (Handy-App) — zurückgestellt

## Context
ai-rem soll in der Claude-Handy-App nutzbar sein. Die App kann nur Remote-Connectors
(öffentliche HTTPS-URL + OAuth). Ist-Stand (geprüft 2026-10-31):
- `server.py`: FastMCP 4.1.0, Streamable HTTP unter `/mcp` (`mcp.http_app()`, Z. 5398) — passt.
- Auth: nur `AuthMiddleware` (Z. 5317) mit festem Bearer-Token / Loopback / UI-Cookie — **kein OAuth**.
- Erreichbar nur im LAN über Caddy (`https://airem.lan/mcp`, TLS internal).

User-Entscheidungen:
- Erreichbarkeit: **Port-Forwarding + DynDNS** (derselbe Weg wie der WireGuard-Tunnel).
- OAuth: **zurückgestellt**, bis die laufende ai-rem-Entwicklung abgeschlossen ist.
  Ohne OAuth wird ai-rem NICHT öffentlich gemacht — daher jetzt keine Umsetzung.

## Jetzt
1. Nach Freigabe: Task in ai-rem anlegen (`memory_add`, Typ Task, context private,
   status offen, TEIL_VON ai-rem): „ai-rem als claude.ai-Connector (OAuth + öffentlicher Zugang)“
   mit obigem Ist-Stand und den Entscheidungen.

## Später (wenn wieder aufgenommen)
1. ~~OAuth-Variante entscheiden~~ → entschieden 2026-10-31: externer IdP, ai-rem als Resource-Server.
2. ~~OAuth in `server.py` neben dem Bearer-Pfad~~ → umgesetzt (`AI_REM_OAUTH_ISSUER`, siehe docs/authentication.de.md#oauth-optional). Offen: IdP-Client anlegen.
3. Caddy: öffentlicher Site-Block auf DynDNS-Namen, öffentliches Zertifikat (ACME),
   **nur** `/mcp` + OAuth-Pfade (`/.well-known/*`, authorize/token/register/callback) durchlassen —
   `/setup`, `/pair`, `/install`, Web-UI bleiben LAN-only. Caddy setzt X-Forwarded-For,
   damit greift der Loopback-Bypass der AuthMiddleware nicht.
4. Router: Port 443 → Caddy.
5. claude.ai → Settings → Connectors → URL eintragen, Login, am Handy testen.

## Verifikation (später)
- `curl https://<dyndns>/mcp` ohne Login → 401; `/setup` von außen → 403/404.
- Connector in claude.ai verbinden, in der Handy-App `memory_search` aufrufen.
