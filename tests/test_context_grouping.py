"""Tests für die nach Projekt gruppierte Offene-Tasks-Anzeige in memory_get_context (Issue #49).

Default (ohne topic): pro Projekt nur Zähler, keine Task-Bodies. Drill-down via topic=<Projekt>
klappt die offenen Tasks der Gruppe mit Body aus. In-process gegen Temp-DB, Embedding aus.
"""
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMPDIR = tempfile.mkdtemp(prefix="ai-rem-grp-")
os.environ["KUZU_DB_PATH"] = os.path.join(_TMPDIR, "kg.db")
os.environ["EMBED_ENABLED"] = "0"
os.environ.setdefault("AI_REM_API_TOKEN", "test-token")

import server  # noqa: E402


def _setup():
    server.memory_add("ProjektAlpha", "Project", description="Alpha")
    server.memory_add("ProjektBeta", "Project", description="Beta")
    server.memory_add("AlphaTask1", "Task", description="erste Alpha-Aufgabe")
    server.memory_add("AlphaTask2", "Task", description="zweite Alpha-Aufgabe")
    server.memory_add("BetaTask1", "Task", description="Beta-Aufgabe")
    server.memory_add("WaiseTask", "Task", description="ohne Projekt")
    server.memory_add("FertigTask", "Task", description="schon fertig", extra={"status": "erledigt"})
    server.memory_relate("AlphaTask1", "TEIL_VON", "ProjektAlpha")
    server.memory_relate("AlphaTask2", "TEIL_VON", "ProjektAlpha")
    server.memory_relate("BetaTask1", "TEIL_VON", "ProjektBeta")
    server.memory_relate("FertigTask", "TEIL_VON", "ProjektAlpha")  # zählt nicht (erledigt)


def test_default_shows_counts_not_bodies():
    _setup()
    out = server.memory_get_context()
    assert "ProjektAlpha** — 2 offen" in out
    assert "ProjektBeta** — 1 offen" in out
    assert "_ohne Projekt_** — 1 offen" in out
    # Bodies tauchen im Default NICHT auf
    assert "erste Alpha-Aufgabe" not in out
    # erledigter Task wird nicht gezählt
    assert "FertigTask" not in out
    # Gesamtzähler: 4 offene (2 Alpha + 1 Beta + 1 Waise)
    assert "## Offene Tasks (4)" in out


def test_header_matches_hook_regex():
    """Der SessionStart-Hook zieht die Task-Anzahl aus diesem Header. Ein Format-Drift
    hat den Task-Block schon einmal still getoetet (Issue #52) — hier faellt er auf."""
    out = server.memory_get_context()
    # identisch zu OPEN_TASKS_RE in hooks/system-check.py
    m = re.search(r"^## Offene Tasks[^(\n]*\((\d+)\)", out, re.M)
    assert m and m.group(1) == "4"


def test_drilldown_by_topic_expands_group():
    out = server.memory_get_context(topic="ProjektAlpha")
    assert "## Offene Tasks: ProjektAlpha" in out
    assert "AlphaTask1" in out
    assert "erste Alpha-Aufgabe" in out
    assert "AlphaTask2" in out
    # Beta-Task gehört nicht in die Alpha-Gruppe
    assert "BetaTask1" not in out.split("## Offene Tasks: ProjektAlpha")[1]


if __name__ == "__main__":
    test_default_shows_counts_not_bodies()
    test_header_matches_hook_regex()
    test_drilldown_by_topic_expands_group()
    print("OK")
