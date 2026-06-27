# Design: Plattformneutrales Setup-Script (Windows-Support)

Datum: 2026-06-11 · Ziel-Version: v0.4.16

## Problem

Das Setup (`bash <(curl -s <url>/setup)`) ist ein ~650-Zeilen-Bash-Script, eingebettet
als `SETUP_SCRIPT` in `server.py`. Windows wird nur über WSL unterstützt. Natives
Windows (PowerShell, claude CLI nativ) kann das Setup nicht ausführen.

~80 % der Logik sind bereits Python (vier Heredocs: `~/.claude.json`,
`settings.json`/`settings-template.json`, `CLAUDE.md`, Entities via MCP-API).
Bash liefert nur Glue: Plattform-Erkennung, Dependency-Checks, curl-Downloads,
SSH-Secret-Pull, git/npm-Orchestrierung.

## Entscheidung

**Python-Core + dünne Wrapper.** Die gesamte Setup-Logik wandert in EIN
plattformneutrales Python-Script (`setup.py`, ausgeliefert unter `/setup.py`).
Bash- und PowerShell-Wrapper machen nur Preflight (Python vorhanden?) und starten es.
Eine Quelle der Wahrheit, kein Sync-Drift zwischen zwei Script-Welten.

Verworfene Alternativen:
- **Voller PS1-Port:** zwei ~500-Zeilen-Scripts dauerhaft synchron halten.
- **PS1-Bootstrap nur für Windows:** Drift zwischen Bash-Pfad und Python-Pfad bleibt.

## Komponenten

### 1. `setup.py` — Single Source of Truth (neuer Endpoint `/setup.py`)

Ersetzt die Bash-Logik vollständig; die bestehenden Python-Heredocs werden fast 1:1
übernommen. Funktionsumfang = volle Parität zum heutigen Bash-Script:

- **Plattform-Erkennung:** `windows` / `macos` / `linux` / `wsl`
  (`sys.platform`, `/proc/version` für WSL).
- **Preflight:** claude CLI via `shutil.which` (findet auf Windows auch
  `claude.cmd`/`claude.exe`). Install-Hinweise je Plattform — Windows:
  `irm https://claude.ai/install.ps1 | iex` oder `npm install -g @anthropic-ai/claude-code`.
  curl entfällt komplett (urllib), ssh ist optional (Fallback: Env-Tokens).
- **MCP-Registrierung:** `claude mcp add --transport http --scope user ai-rem <url>/mcp`
  via subprocess; Entfernen der Legacy-`kg-memory`-Registrierung.
- **setup-config laden, TLS-Endpoint-Wahl:** urllib statt curl; Logik unverändert
  (https bevorzugt, nur wenn erreichbar UND vertraut, sonst http-Fallback).
  Python nutzt auf Windows den System-Zertifikatsspeicher automatisch.
- **SSH-Secret-Pull:** wie bisher (`ssh -o BatchMode=yes <host> grep …`); Windows 10+
  hat OpenSSH-Client an Bord. Fehlt ssh → Hinweis + Env-Fallback (`AI_REM_TOKEN=… `).
- **tools-registry clone+build:** git/node/npm via `shutil.which`, Node>=18-Check,
  npm-Fehlerdiagnose (Cert/EACCES) wie bisher. `npm`-Aufruf auf Windows via
  `npm.cmd` (which löst das auf).
- **Dateien schreiben:** `~/.claude.json`, `settings-template.json`, `settings.json`,
  `CLAUDE.md`, `auto-memory/fallback.md`, Slash-Commands, Hooks — Logik unverändert,
  `os.path.expanduser`/`os.replace` sind portabel.
- **Hook-Registrierung, Windows-Unterschied:** Auf Unix bleibt der Command der nackte
  Hook-Pfad (Shebang + chmod). Auf Windows wird der Command als
  `python "C:\…\system-check.py"` registriert (kein Shebang-Exec); chmod entfällt.
  Gleiches Muster für `AI_REM_CLI`-Discovery: auf Windows kein `/Volumes`-Glob.
- **Entities via MCP-API:** bestehender Python-Block unverändert.
- **Fehlerbehandlung:** wie bisher klare ✗-Meldungen mit plattformspezifischen
  Install-Hinweisen (Windows: winget / claude-install.ps1) und der jeweils richtigen
  „erneut ausführen"-Zeile (bash- bzw. irm-Variante).

### 2. Wrapper

- **`/setup` (bash, Aufruf unverändert `bash <(curl -s <url>/setup)`):**
  prüft python3 (apt/brew-Hinweis), lädt `<url>/setup.py` in Temp-Datei,
  führt `python3 setup.py` aus. ~20 Zeilen.
- **`/setup.ps1` (neu, Aufruf `irm <url>/setup.ps1 | iex`):**
  prüft `python` (inkl. `py`-Launcher; Hinweis `winget install Python.Python.3.12`),
  lädt setup.py, führt es aus. ~25 Zeilen. Kein TLS-Sonderfall: Bootstrap läuft wie
  bei bash über den http-Endpoint.

### 3. Hook-Patches für Windows-Parität

- `system-check.py` + `auto-memory.py`: CLI-Aufrufe (`bin/ai-rem` ist Shebang-Script)
  auf Windows als `[sys.executable, cli, …]` statt `[cli, …]`.
- SMB-Mount-Logik (`mount`-Kommando, macOS/Linux) auf Windows überspringen —
  UNC-Pfade funktionieren nativ.
- Desktop-Notifications: Windows-Zweig entfällt (nur macOS `osascript` /
  Linux `notify-send` wie bisher).
- Cert-Trust-Hinweis im Setup für Windows: `certutil -addstore Root root.crt`
  (+ `NODE_EXTRA_CA_CERTS` für Node, wie auf den anderen Plattformen).

### 4. Doku

- `CMD_MD` (/setup-ai-rem Slash-Command): Windows-Zeile `irm <url>/setup.ps1 | iex`
  ergänzen. Auto-Mode-Regel gilt analog: der Agent führt auch `irm | iex` NICHT
  selbst aus, sondern legt dem User die Zeile hin.
- `README.md` / `README.de.md`: Windows-Abschnitt (Voraussetzungen, Aufruf).

## Nicht-Ziele

- Keine automatische Installation fehlender Dependencies (nur Hinweise, wie bisher).
- Kein Windows-Zweig für SMB-Mount oder Desktop-Notifications in den Hooks.
- Bash-Wrapper-Aufruf und alle bestehenden Endpoints bleiben kompatibel.

## Test

- **Regression Unix:** Setup-Re-Run auf dieser Maschine (Linux) — identisches
  Endergebnis in `~/.claude.json` / `settings.json` wie vor dem Umbau.
- **Windows:** manueller Test auf einer Windows-Maschine (PowerShell, claude CLI nativ).
- **Syntax-Smoke:** `python3 -m py_compile` für setup.py-Inhalt, `bash -n` für den
  Wrapper im Build.
