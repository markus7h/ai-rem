#!/usr/bin/env python3
"""Claude Code Hook: PreCompact + SessionEnd → ai-rem ingest."""
import glob
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

_CC = os.environ.get("CLAUDE_CONFIG_DIR", "").split(os.pathsep)[0].strip()
CLAUDE_DIR = _CC or os.path.expanduser("~/.claude")
AUTO_MEM_DIR = Path(CLAUDE_DIR) / "auto-memory"
PROCESSED = AUTO_MEM_DIR / ".processed"
ERRORS = AUTO_MEM_DIR / "errors.log"
# Das 24b-Modell braucht für ein volles Transcript real ~5 min. Der Hook laeuft
# darum detached (siehe _detach) — dieses Budget ist nur die Reissleine.
TIMEOUT_S = 1200
CATCHUP_TIMEOUT_S = 60  # darf das Ingest-Budget nicht auffressen

CANDIDATE_CLI_PATHS = [
    os.environ.get("AI_REM_CLI", ""),
    os.path.expanduser("~/myCode/github/ai-rem/bin/ai-rem"),
    os.path.expanduser("~/.local/share/ai-rem/bin/ai-rem"),
]
# Zusätzliche Suchmuster für nicht-Standard-Layouts (SMB-Mount /Volumes/<x>/myCode,
# untergeschobenes Zwischenverzeichnis wie ~/mystorage/myCode).
CLI_GLOBS = [
    "/Volumes/*/myCode/github/ai-rem/bin/ai-rem",
    os.path.expanduser("~/*/myCode/github/ai-rem/bin/ai-rem"),
]


def _find_cli():
    # X_OK ist auf Windows bedeutungslos; dort wird die CLI eh via python gestartet.
    def _usable(p):
        return bool(p) and Path(p).is_file() and (sys.platform == "win32" or os.access(p, os.X_OK))

    for p in CANDIDATE_CLI_PATHS:
        if _usable(p):
            return p
    for pat in CLI_GLOBS:
        for p in sorted(glob.glob(pat)):
            if _usable(p):
                return p
    return shutil.which("ai-rem") or ""


def _cli_cmd(cli, *args):
    # bin/ai-rem ist ein Shebang-Script — Windows kann das nicht direkt starten.
    # -X utf8: die CLI liest UTF-8-Transcripts/JSON ohne explizites encoding=.
    if sys.platform == "win32":
        return [sys.executable, "-X", "utf8", cli, *args]
    return [cli, *args]


def _notify(text):
    """Desktop-Notification, plattformübergreifend: macOS (osascript) /
    Linux-GNOME (notify-send). Schlägt still fehl (SSH/headless ohne DBUS)."""
    title = "ai-rem gespeichert"
    try:
        if sys.platform == "darwin":
            safe = text.replace("\\", " ").replace('"', "'")
            subprocess.run(
                ["osascript", "-e", f'display notification "{safe}" with title "{title}"'],
                capture_output=True, timeout=5)
        elif sys.platform.startswith("linux") and shutil.which("notify-send"):
            subprocess.run(["notify-send", "-a", "ai-rem", title, text],
                           capture_output=True, timeout=5)
    except Exception:
        pass


def _notify_last_run(sid):
    """Zeigt nach erfolgreichem Ingest, was gespeichert wurde (aus last-run.json)."""
    try:
        d = json.loads((AUTO_MEM_DIR / "last-run.json").read_text(encoding="utf-8"))
    except Exception:
        return
    if sid and d.get("session") and d["session"] != sid:
        return  # last-run gehört zu anderer Session (Race) → nichts zeigen
    n = d.get("entity_count", 0)
    applied = d.get("applied", 0)
    if not n and not applied:
        return  # nichts gespeichert → keine Notification-Noise
    ents = d.get("entities") or []
    shown = ", ".join(ents[:4]) + ("…" if len(ents) > 4 else "")
    msg = f"{n} Entities, {d.get('relations', 0)} Rel, {applied} applied"
    if shown:
        msg += f"\n{shown}"
    _notify(msg)


def _log_error(msg):
    try:
        AUTO_MEM_DIR.mkdir(parents=True, exist_ok=True)
        with ERRORS.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{msg}\n")
    except Exception:
        pass


def _run_key(sid, transcript):
    """Key pro Ingest-Lauf, nicht pro Session.

    Nur die sid zu merken hiess: nach PreCompact galt die Session als erledigt und
    das anschliessende SessionEnd lief nie — bei langen Sessions wurde alles nach
    der ersten Compaction verworfen. Die Transcript-Groesse unterscheidet die
    Laeufe; echtes Doppelfeuern desselben Zustands bleibt geblockt.
    """
    try:
        size = Path(transcript).stat().st_size
    except OSError:
        size = 0
    return f"{sid}:{size}"


def _already_processed(key):
    if not key or not PROCESSED.exists():
        return False
    try:
        return key in PROCESSED.read_text(encoding="utf-8").splitlines()
    except Exception:
        return False


def _mark_processed(key):
    if not key:
        return
    try:
        AUTO_MEM_DIR.mkdir(parents=True, exist_ok=True)
        with PROCESSED.open("a", encoding="utf-8") as f:
            f.write(key + "\n")
    except Exception:
        pass


def _detach(raw):
    """Sich selbst als eigenstaendigen Prozess neu starten und sofort zurueckkehren.

    Die Extraktion dauert auf dem lokalen 24b-Modell mehrere Minuten — laenger als
    jedes vernuenftige Hook-Timeout. Ohne Detach blockiert das Session-Ende oder der
    Ingest wird mittendrin abgeschossen. start_new_session=True (setsid) haelt den
    Kindprozess am Leben, wenn Claude Code seine Prozessgruppe abraeumt.
    """
    try:
        p = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**os.environ, "AUTO_MEMORY_DETACHED": "1"},
            start_new_session=True,
        )
        p.stdin.write(raw.encode("utf-8"))
        p.stdin.close()
        return True
    except Exception as e:
        _log_error(f"detach failed, laufe inline weiter: {e}")
        return False


def main():
    if os.environ.get("AUTO_MEMORY_EXTRACTING"):
        return
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        ctx = json.loads(raw)
    except Exception as e:
        _log_error(f"stdin parse: {e}")
        return

    # Windows kennt kein start_new_session → dort inline, das Timeout greift dann.
    if not os.environ.get("AUTO_MEMORY_DETACHED") and sys.platform != "win32":
        if _detach(raw):
            return

    transcript = ctx.get("transcript_path") or ""
    session_id = ctx.get("session_id") or ""
    hook_event = ctx.get("hook_event_name") or ctx.get("event") or "?"

    if not transcript:
        _log_error(f"{hook_event}: kein transcript_path im Hook-Input")
        return
    if not Path(transcript).exists():
        # ponytail: leere/abgebrochene Session schreibt kein Transcript — kein Fehler,
        # sonst meldet der SessionStart-Check dauernd "Auto-Memory gestört".
        return
    run_key = _run_key(session_id, transcript)
    if _already_processed(run_key):
        return

    cli = _find_cli()
    if not cli:
        _log_error(f"{hook_event} session={session_id}: ai-rem CLI not found (set $AI_REM_CLI)")
        return

    # Erst die md-Fallback-Queue nachziehen (no-op wenn Ollama down / Queue leer).
    try:
        subprocess.run(_cli_cmd(cli, "catchup"), capture_output=True, text=True,
                       timeout=CATCHUP_TIMEOUT_S)
    except Exception:
        pass

    try:
        proc = subprocess.run(
            _cli_cmd(cli, "ingest", "--transcript", transcript),
            capture_output=True, text=True, timeout=TIMEOUT_S,
        )
        if proc.returncode != 0:
            _log_error(
                f"{hook_event} session={session_id} rc={proc.returncode} "
                f"stderr={proc.stderr.strip()[:500]}"
            )
            return
    except subprocess.TimeoutExpired:
        _log_error(f"{hook_event} session={session_id} TIMEOUT after {TIMEOUT_S}s")
        return
    except Exception as e:
        _log_error(f"{hook_event} session={session_id} exception: {e}")
        return

    _mark_processed(run_key)
    _notify_last_run(session_id)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        _log_error(f"unhandled: {e}")
    sys.exit(0)
