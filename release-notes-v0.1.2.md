# ai-rem v0.1.2 — Update-Check

Kleiner Feature-Release. Kein Schema-Change, keine API-Änderung.

## Highlights

- **`memory_check_update`** — Neues MCP-Tool das die installierte Version anzeigt und mit dem neuesten Tag auf Docker Hub vergleicht. Gibt `✓ aktuell` oder `⚠ Update verfügbar` aus.

## Was sich konkret ändert

### VERSION-Konstante

`VERSION = "0.1.2"` als Konstante in `server.py` — wird von `memory_check_update` gelesen und dient künftig als zentrale Versionsreferenz.

### `memory_check_update()`

Neues Tool ohne Parameter. Ablauf:

1. Liest `VERSION` (installierte Version)
2. Fragt Docker Hub API ab: `hub.docker.com/v2/repositories/magic3arkus/ai-rem/tags/`
3. Filtert `latest`-Tag heraus, findet höchsten Semver-Tag
4. Vergleicht und gibt Status aus

Timeout: 5 Sekunden. Bei Netzwerkfehler: installierte Version wird trotzdem angezeigt, Docker Hub als "nicht erreichbar" markiert.

`memory_status` bleibt unverändert schlank (kein Netzwerkzugriff).

## Upgrade

```bash
docker compose up -d --pull always
```

## Geänderte Dateien

- `server.py` — `VERSION`-Konstante, `memory_check_update` Tool neu.
- `README.md` — Version-Badge auf v0.1.2.
- `release-notes-v0.1.2.md` — neu.
