# ai-rem v0.1.5 — Generisches Starter-Template

Onboarding-Release für neue Nutzer. Kein Schema-Change, keine API-Änderung.

## Highlights

- **`setup-config.example.json`** — Ein generisches Starter-Template wird jetzt mit dem
  Image ausgeliefert. Frische Deployments seeden beim `/setup-ai-rem` ein sinnvolles Set an
  Verhaltens-Preferences statt mit leerem Knowledge Graph zu starten.

## Was sich konkret ändert

### Problem

Die persönliche `setup-config.json` ist gitignored und landet daher nie im öffentlichen
Image. Die Route `/setup-config` gab dann `{}` zurück → das Setup-Skript seedete nur zwei
Stub-Entities. Ein neuer Nutzer startete ohne jede Verhaltens-Preference.

### Lösung

- Neue, eingecheckte **`setup-config.example.json`** (gleiches Schema, rein generisch, keine
  personenbezogenen Daten) mit:
  - 6 Verhaltens-Preferences: Plan-first, knapp antworten, ai-rem vor Rückfragen prüfen,
    Recall-Vorgehen, Halluzinationen vermeiden, Wissen proaktiv speichern
    (jeweils im `Regel / Why / How to apply`-Format)
  - 2 ai-rem-Tool-Entities (`skill_setup_ai_rem`, `skill_ai_rem_prefedit`)
  - 11 generische Allow-Permissions + 5 universelle Deny-Regeln (Secret-Schutz)
- **Route-Fallback** in `server.py`: `/setup-config` liefert die persönliche
  `setup-config.json`, wenn vorhanden — sonst das Example, statt `{}`.
- Der bestehende Seeding-Mechanismus (`entities` → `memory_add`) bleibt unverändert; eine
  eigene `setup-config.json` überschreibt das Template komplett.

## Upgrade

```bash
docker compose up -d --pull always
```

## Geänderte Dateien

- `setup-config.example.json` — neu (generisches Starter-Template).
- `server.py` — `VERSION` auf 0.1.5; `/setup-config`-Route fällt auf das Example zurück.
- `README.md` / `README.de.md` — Version-Badge v0.1.5, Abschnitt „Personal Configuration"
  beschreibt den Template-Fallback.
- `release-notes-v0.1.5.md` — neu.
