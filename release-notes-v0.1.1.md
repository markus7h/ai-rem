# ai-rem v0.1.1 — Smart Truncation für memory_search

Qualitäts-Release. Kein Schema-Change, keine API-Änderung.

## Highlights

- **Satzbasierte Kürzung in `memory_search`** — Statt harter Zeichenlimits werden lange Beschreibungen jetzt intelligent gekürzt: erster und letzter Satz bleiben vollständig erhalten, der Mittelteil wird bei Bedarf mit `…` komprimiert. Einträge unter 400 Zeichen erscheinen ungekürzt.
- **Erweitertes Limit in `memory_get_context` (topic)** — Topic-Suche zeigt jetzt bis zu 300 Zeichen (vorher 100), damit Einträge auch bei gezielter Suche vollständiger erscheinen.

## Was sich konkret ändert

### `_smart_truncate(text, threshold=400)`

Neue Hilfsfunktion direkt vor `memory_search`. Funktionsweise:

1. Text ≤ 400 Zeichen → unverändert zurückgeben
2. Text in Sätze splitten (`re.split` auf `. `)
3. ≤ 2 Sätze → harter Cut bei 400 Zeichen + `…`
4. ≥ 3 Sätze → erster Satz + gekürzter Mittelteil + `…` + letzter Satz

Damit bleiben Einträge nach dem Schema `Regel: X. Why: Y. How to apply: Z.` auch bei langen Texten vollständig verwendbar — `X` und `Z` sind immer sichtbar.

### `memory_search`

Ausgabe-Zeile nutzt jetzt `_smart_truncate(r[2])` statt `r[2][:100]`.

### `memory_get_context` (topic-Zweig)

Limit von `[:100]` auf `[:300]` erhöht. `memory_get_context` ohne topic (Überblicks-Modus) und `memory_list` sind unverändert — dort sind kompakte Snippets weiterhin sinnvoll.

## Upgrade

```bash
docker compose up -d --pull always
```

Oder lokal neu bauen:

```bash
docker build -t magic3arkus/ai-rem:latest .
docker compose up -d --force-recreate
```

Kein Client-Update nötig.

## Geänderte Dateien

- `server.py` — `_smart_truncate()` neu, `memory_search` und `memory_get_context` (topic) angepasst.
- `README.md` — Version-Badge auf v0.1.1.
- `release-notes-v0.1.1.md` — neu.
