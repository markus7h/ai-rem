---
name: Backup-Verschlüsselung
description: At-Rest-Verschlüsselung der ai-rem Backups (AES-256-GCM, opt-in)
status: offen
---

# Backup-Verschlüsselung (AES-256-GCM, opt-in)

GitHub-Issue: [#31](https://github.com/markus7h/ai-rem/issues/31)

## Problem

Backup-Dateien (`backup_*.json`) liegen im Klartext in `BACKUP_DIR` und werden
beim Download im Klartext ausgegeben. Sie enthalten den kompletten
Knowledge-Graph (private Notizen, Präferenzen, Projekte) — at rest und beim
Transfer ungeschützt.

## Ziel

At-Rest-Verschlüsselung der Backups, **opt-in** und vollständig
rückwärtskompatibel: bestehende Klartext-Backups bleiben restore-bar, Setups
ohne Schlüssel verhalten sich wie bisher.

## Entscheidungen

| Frage | Entscheidung |
|---|---|
| Mechanismus | AES-256-GCM (Lib `cryptography`), Key via `scrypt` aus Passphrase |
| Schlüssel-Quelle | Env `AI_REM_BACKUP_KEY`, Konvention mykeyvault → `deploy.sh` → `.env` |
| Modus | Opt-in: Key gesetzt → verschlüsselt, leer → Klartext wie bisher |
| Download | Liefert verschlüsselten Blob roh — kein Klartext verlässt den Server |
| Restore | Auto-Detection via Magic-Header, serverseitige Entschlüsselung |

## Dateiformat

Binär, Datei `backup_<ts>.json.enc`:

```
magic "AIREMENC1" (9 bytes) | salt (16) | nonce (12) | ciphertext + GCM-tag
```

- `key = scrypt(passphrase, salt, n=2^14, r=8, p=1, dklen=32)`
- Salt pro Datei neu → gleiche Passphrase erzeugt unterschiedliche Blobs.
- GCM-Tag liefert Integrität/Authentizität; falscher Key → Entschlüsselung schlägt sauber fehl.

## Komponenten

### `lib/backup_crypto.py` (isoliert, ohne server.py-Side-Effects)

- `MAGIC: bytes` — Format-Kennung `AIREMENC1`
- `derive_key(passphrase: bytes, salt: bytes) -> bytes`
- `encrypt(plaintext: bytes, passphrase: bytes) -> bytes`
- `decrypt(blob: bytes, passphrase: bytes) -> bytes` — wirft bei falschem Key / kaputtem Blob
- `is_encrypted(data: bytes) -> bool` — Magic-Header-Check

### `server.py` Verdrahtung

- `_backup_key() -> Optional[bytes]` — liest `AI_REM_BACKUP_KEY`, leer → `None`
- `_do_backup()` — bei Key: JSON verschlüsseln, `.enc` atomar schreiben (tmp + replace)
- Retention/Listing-Globs (`_do_backup`, `api_backup_files`) — beide Muster
  (`backup_*.json` + `backup_*.json.enc`)
- `_safe_backup_path()` — `.json` und `.json.enc` zulassen
- Download — Media-Type `application/octet-stream` für `.enc`
- `/api/restore` — Inhalt einlesen, bei Magic-Header mit Key entschlüsseln, dann `json.loads`.
  Fehlerfälle: „verschlüsselt, aber kein Key gesetzt" / „Entschlüsselung fehlgeschlagen (falscher Key?)" → 400
- Pre-Migration-Backup (`_migrate_context_column`) — analog verschlüsseln, wenn Key gesetzt

## Fehlerverhalten

- Falscher/fehlender Key beim Restore → sauberer 400-Fehler, kein Crash.
- Verschlüsseltes Backup ohne Key → klare Fehlermeldung.
- Bestehende Klartext-Backups → unverändert restore-bar (Auto-Detection).

## Tests (TDD, vorab)

- Encrypt → Decrypt Round-Trip ergibt Original.
- Zwei Verschlüsselungen derselben Daten → unterschiedliche Blobs (Salt/Nonce).
- Decrypt mit falscher Passphrase → wirft.
- `is_encrypted` erkennt `.enc`-Blob als True, Klartext-JSON als False.

## Bewusst weggelassen (YAGNI)

Key-Rotation, mehrere Keys, Re-Encrypt bestehender Backups, asymmetrische Keys.
