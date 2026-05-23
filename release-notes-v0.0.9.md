# ai-rem v0.0.9 — Backup-Skip & Preferences-Link

Kleiner Pflege-Release. Kein Schema-Change, keine API-Änderung.

## Highlights

- **Automatisches Backup läuft nur noch bei Änderungen** — Wenn der Graph seit dem letzten geplanten Backup unverändert ist, wird der Scheduler-Tick übersprungen. Spart Backup-Slots und I/O auf ruhigen Tagen.
- **Preferences-Link auf der Hauptseite** — `/ui` verlinkt im Header direkt auf `/prefs`, damit der Preferences-Manager ohne Umweg erreichbar ist.

## Was sich konkret ändert

### Backup-Signature

Neue Helferfunktion `_graph_signature()` bildet einen billigen Fingerprint aus
`(entity_count, relation_count, max(entity.updated_at), max(relation.created_at))`.
Bei jedem `_do_backup()` wird die Signature in `.config.json` (`last_backup_signature`)
mitgeschrieben. Der Scheduler vergleicht vor dem nächsten geplanten Backup die aktuelle
Signature mit der gespeicherten — bei Gleichheit wird übersprungen (Log:
`Scheduled backup skipped: no graph changes since last backup`).

Das manuelle "Backup now" in der Web-UI läuft weiterhin immer durch — explizite User-Aktion
schlägt Skip-Logik.

### UI-Header

`/ui` zeigt jetzt im Sub-Header neben Entity-/Relations-Count einen
`Preferences →`-Link auf `/prefs`.

## Upgrade

Server:

```bash
cd ~/mydocker/compose-files/ai-rem
docker compose up -d --build
```

Oder per Deploy-Script aus dem Repo:

```bash
./deploy.sh
```

Kein Client-Update nötig (keine Tool-Signatur-Änderung).

### Rollback

`git checkout v0.0.8 && docker compose up -d --build`. Das zusätzliche Feld
`last_backup_signature` in `.config.json` schadet älteren Versionen nicht
(unbekannte Keys werden ignoriert).

## Geänderte Dateien

- `server.py` — `_graph_signature()`, `_do_backup()` schreibt Signature mit,
  `_scheduler_loop()` überspringt bei Gleichheit, `_UI_HTML` Sub-Header um
  `Preferences →`-Link erweitert.
- `README.md` — Version-Badge auf v0.0.9.
- `release-notes-v0.0.9.md` — neu.
