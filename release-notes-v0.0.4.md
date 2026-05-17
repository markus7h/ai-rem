# ai-rem v0.0.4 — Speed & Robustness Refactor

Schwerpunkt dieses Releases: echte Bugs raus, N+1-Queries weg, Event-Loop unblockieren, Schema sauberer. Keine Breaking Changes für bestehende Backups (JSON-Format bleibt kompatibel). Beim ersten Start mit dieser Version läuft eine automatische Schema-Migration mit Vor-Backup.

## Highlights

- **Connection-Pool statt globalem Lock** — gleichzeitige Requests laufen jetzt parallel statt seriell.
- **Async-Routen blockieren den Event-Loop nicht mehr** — DB-Calls werden via `asyncio.to_thread` ausgelagert.
- **N+1-Queries in Import/Restore beseitigt** — Bulk-Imports sind bei ~1000 Entities deutlich schneller (Erwartung: ~10×).
- **`context` als first-class Spalte** — `LIMIT N` mit Context-Filter liefert jetzt korrekt N Treffer (vorher: konnten weniger sein, weil in Python nachgefiltert wurde).
- **Mehrere Correctness-Bugs gefixt** — kein stilles Überschreiben mehr bei langen Entity-Namen, keine Stub-Entities mehr durch Tippfehler in `memory_relate`, keine korrupte Backup-Config bei gleichzeitigen Writes.

---

## Robustness-Fixes

### `_id()` — keine stillen Kollisionen mehr bei langen Namen
Namen ≤ 64 Zeichen verhalten sich unverändert (bestehende IDs bleiben bit-identisch). Bei längeren Namen wird ein 8-Zeichen-Hash des vollen Namens angehängt — so kollidieren zwei verschiedene Entities mit identischem 64-Zeichen-Präfix nicht mehr still.

### `memory_relate` legt keine `Unknown`-Stub-Entities mehr an
Bisher hat ein Tippfehler in `from_name` oder `to_name` eine leere Entity mit `type='Unknown'` erzeugt — der Graph wurde stillschweigend verschmutzt. Jetzt: klare Fehlermeldung mit dem unbekannten Namen, keine Schreiboperation. **Breaking** für Callsites, die auf das Auto-Create-Verhalten bauen — wenn nötig, vorher explizit via `memory_add` anlegen.

### `memory_add` ist atomar
Wurde umgestellt auf ein einzelnes `MERGE ... ON CREATE ... ON MATCH ...`. Race zwischen Check und Insert ist nicht mehr möglich.

### Backup-Config: kein Schreib-Race mehr
`fcntl.flock` (shared für Read, exklusiv für Write) plus atomic-rename via Temp-Datei. Scheduler-Thread und HTTP-Routes können `.config.json` nicht mehr gleichzeitig korruptieren.

### Backup-Files: Path-Confinement gegen Traversal
Neue `_safe_backup_path()`-Hilfe — verifiziert via `realpath`, dass der Pfad wirklich unter `BACKUP_DIR` liegt. Greift bei Download und Delete.

### Backup-Delete idempotent
TOCTOU-Race zwischen `os.path.exists` und `os.remove` aufgelöst (`try/except FileNotFoundError`).

### Backup-Dateien werden atomar geschrieben
`*.tmp` schreiben, dann `os.replace` — kein abgeschnittenes Backup mehr, wenn der Container während des Schreibens stoppt.

---

## Performance

### Connection-Pool ersetzt globalen Lock
Kuzu-Connection-Objekte sind nicht thread-safe, die Database hingegen schon. Es gibt jetzt einen kleinen Pool (Default 4 Connections) — Requests bekommen je eine Connection aus dem Pool und laufen parallel. Konfigurierbar via neuer Umgebungsvariable:

```
KUZU_POOL_SIZE=4
```

### Event-Loop wird nicht mehr blockiert
Alle `async`-Routen (`/export`, `/import`, `/api/restore`, `/api/status`, `/api/backup/now`) führen ihre DB- und Filesystem-Arbeit jetzt via `asyncio.to_thread` aus. Vorher: ein laufender `/export` hat alle anderen Requests blockiert, bis er fertig war.

### N+1-Queries in Import/Restore beseitigt
`/import` und `/api/restore` haben pro Entity und pro Relation eine separate Existenz-Query abgesetzt — bei 1000 Entities + 1000 Relations waren das ~4000 unnötige Roundtrips. Jetzt wird einmal vorab das komplette Set existierender IDs und Relation-Tupel geladen, danach reines O(1)-Lookup in Python.

### `_dump_graph()` shared
`/export` und der Backup-Scheduler benutzen denselben Helper — kein duplizierter Dump-Code mehr.

---

## Schema-Migration: `context` als first-class Spalte

Bisher lag `context` in der `extra`-JSON-Spalte. Das hatte zwei Probleme:

1. **Correctness-Bug:** Queries machten `LIMIT N` _vor_ dem Python-seitigen Context-Filter — bei `LIMIT 10` und `context="private"` kamen oft weniger als 10 Treffer zurück, obwohl mehr existierten.
2. **Speed:** Pro Zeile ein JSON-Parse plus Python-Loop.

**Was passiert beim ersten Start mit v0.0.4:**

1. Automatisches Vor-Backup nach `/backups/backup_pre_context_<timestamp>.json` (am alten Schema, ohne `context`-Spalte).
2. `ALTER TABLE Entity ADD context STRING DEFAULT ''`.
3. Backfill: für jede Entity mit `extra.context` Wert wird die neue Spalte gesetzt.
4. Log-Zeile: `Schema migration: context column added, N entities backfilled`.

Die Migration ist idempotent — bei späteren Starts wird per `CALL TABLE_INFO('Entity')` geprüft und übersprungen.

### Backup-JSON-Format
Entity-Objekte haben jetzt ein zusätzliches Top-Level-Feld `context`. Restore-Pfad ist rückwärtskompatibel: liest bevorzugt das neue Feld, fällt sonst auf `extra.context` zurück. Alte Backups können also weiter restored werden.

### Neues Response-Feld
`/import` und `/api/restore` geben jetzt zusätzlich `relations_skipped` zurück (Relations, deren Endpoints nicht existieren — wurden bisher still verschluckt).

---

## Sonstiges

- Scheduler-Thread reagiert auf `atexit`, beendet die Schleife sauber statt mitten in der `sleep` abgewürgt zu werden.
- Reduziertes Log-Rauschen bei korrupten Timestamps (statt stilles `pass`).
- Spezifischere Exception-Catches in Routen (`json.JSONDecodeError` statt `except Exception`).

---

## Upgrade

```bash
docker compose pull   # falls Image aus Registry
# oder lokal:
docker compose build && docker compose up -d

# Migration verifizieren:
docker compose logs ai-rem | grep -E "Pre-migration backup|context column"
```

Erwartete Log-Zeilen beim ersten Start nach Upgrade:

```
Pre-migration backup written: backup_pre_context_YYYY-MM-DD_HH-MM-SS.json
Schema migration: context column added, N entities backfilled
Schema ready — DB at /data/kg.db
```

### Rollback

Das Vor-Backup liegt in `./backups/backup_pre_context_*.json`. Falls etwas schiefläuft:

```bash
docker compose down
rm -rf ./data/kg.db   # Vorsicht — löscht die Datenbank
docker compose up -d  # leeres Schema (Stand v0.0.3) wird angelegt
curl -X POST -F "file=@backups/backup_pre_context_<timestamp>.json" -F "mode=replace" http://<server>:3456/api/restore
```

---

## Neue / geänderte Env-Variablen

| Variable | Default | Wirkung |
|---|---|---|
| `KUZU_POOL_SIZE` | `4` | Größe des Kuzu-Connection-Pools. Höher = mehr Parallelität, mehr RAM. |

---

## Geänderte Dateien

- `server.py` (+397 / -215)
- `.env.example` (+4)
- `docker-compose.yml` (+1)
