"""Zeitfenster des Nightly-Cleanups.

_cleanup_due() entscheidet in Lokalzeit (AI_REM_TZ), ob der Scheduler laufen soll;
_cleanup_llm_ready() verschiebt den Lauf, solange das LLM schläft. Läuft im SUBPROZESS
mit eigener Temp-DB, weil der pytest-Modulcache server.py sonst zwischen Tests teilt.
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _scenario() -> None:
    tmp = tempfile.mkdtemp(prefix="ai-rem-window-")
    os.environ["LADYBUG_DB_PATH"] = os.path.join(tmp, "kg.db")
    os.environ["BACKUP_DIR"] = os.path.join(tmp, "backups")
    os.environ["EMBED_ENABLED"] = "0"
    os.environ["AI_REM_TZ"] = "Europe/Berlin"
    os.environ.setdefault("AI_REM_API_TOKEN", "test-token")
    sys.path.insert(0, ROOT)

    from datetime import datetime, timezone
    import server

    tz = server.CLEANUP_TZ
    assert str(tz) == "Europe/Berlin"

    def at(hhmm: str, day: int = 7) -> datetime:
        h, m = hhmm.split(":")
        return datetime(2026, 10, day, int(h), int(m), tzinfo=tz)

    # Default-Fenster, Altbestand "hour" wird verworfen.
    cfg = server._normalize_cleanup_cfg({"enabled": True, "hour": 7, "last_run": None})
    assert "hour" not in cfg
    assert (cfg["window_start"], cfg["window_end"]) == server.CLEANUP_WINDOW_DEFAULT

    # Ungültiges Fenster → Default.
    bad = server._normalize_cleanup_cfg({"window_start": "23:00", "window_end": "06:00"})
    assert (bad["window_start"], bad["window_end"]) == server.CLEANUP_WINDOW_DEFAULT

    assert not server._cleanup_due(at("06:14"), cfg), "vor Fensterbeginn gelaufen"
    assert server._cleanup_due(at("06:15"), cfg), "Fensterbeginn nicht erkannt"
    assert server._cleanup_due(at("14:00"), cfg), "verpasster Lauf wird nicht nachgeholt"
    assert not server._cleanup_due(at("22:30"), cfg), "nach Fensterende gelaufen"
    assert not server._cleanup_due(at("03:00"), cfg), "in myais Nachtruhe gelaufen"
    assert not server._cleanup_due(at("08:00"), {**cfg, "enabled": False})

    # Heute schon gelaufen (last_run naiv in Systemzeit, hier UTC) → nicht nochmal.
    ran = {**cfg, "last_run": datetime(2026, 10, 7, 5, 0, tzinfo=timezone.utc)
           .astimezone().replace(tzinfo=None).isoformat()}
    assert not server._cleanup_due(at("12:00"), ran), "zweiter Lauf am selben Tag"
    assert server._cleanup_due(at("06:30", day=8), ran), "Folgetag nicht fällig"

    # Tagesgrenze: 22:30 UTC am 06.10. ist 00:30 Berlin am 07.10. → zählt als heute.
    late = {**cfg, "last_run": datetime(2026, 10, 6, 22, 30, tzinfo=timezone.utc)
            .astimezone().replace(tzinfo=None).isoformat()}
    assert not server._cleanup_due(at("07:00"), late), "UTC-Datum statt Lokaldatum verglichen"

    # LLM-Gate: schläft das LLM, wird gewartet, bis kurz vor Fensterende.
    server._ollama_up = lambda: False
    assert not server._cleanup_llm_ready(at("07:00"), cfg), "trotz totem LLM gestartet"
    assert server._cleanup_llm_ready(at("22:05"), cfg), "kurz vor Fensterende nicht gestartet"
    server._ollama_up = lambda: True
    assert server._cleanup_llm_ready(at("07:00"), cfg)

    # Persistenz: tz wird nicht gespeichert, hour nicht wieder eingeführt.
    server._save_cleanup_cfg({**cfg, "window_start": "07:00"})
    loaded = server._load_cleanup_cfg()
    assert loaded["window_start"] == "07:00" and loaded["tz"] == "Europe/Berlin"
    import json
    with open(server._CLEANUP_CONFIG) as f:
        assert "tz" not in json.load(f)

    print("OK")


def test_cleanup_window():
    r = subprocess.run(
        [sys.executable, __file__],
        capture_output=True, text=True,
        env={**os.environ, "EMBED_ENABLED": "0", "AI_REM_API_TOKEN": "test-token", "TZ": "UTC"},
    )
    assert r.returncode == 0, f"Szenario fehlgeschlagen:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    assert "OK" in r.stdout


if __name__ == "__main__":
    _scenario()
