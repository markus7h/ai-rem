"""Tests für _task_rows_full — Datenbasis der /tasks-Web-UI.

Liefert alle Tasks (offen und erledigt) mit done-Flag, Projekten und
archived-Status. In-process gegen Temp-DB, Embedding aus. Entity-Namen sind mit TR_ geprefixt:
`server` wird pro pytest-Lauf nur einmal importiert, alle Testmodule teilen sich
deshalb die DB des zuerst importierenden Moduls.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMPDIR = tempfile.mkdtemp(prefix="ai-rem-tasks-")
os.environ["LADYBUG_DB_PATH"] = os.path.join(_TMPDIR, "kg.db")
os.environ["EMBED_ENABLED"] = "0"
os.environ.setdefault("AI_REM_API_TOKEN", "test-token")

import server  # noqa: E402


def _setup():
    server.memory_add("TR_ProjektAlpha", "Project", description="Alpha")
    server.memory_add("TR_OffenerTask", "Task", description="noch zu tun")
    server.memory_add("TR_FertigTask", "Task", description="fertig", extra={"status": "erledigt"})
    server.memory_add("TR_DoneTask", "Task", description="done", extra={"status": "done"})
    server.memory_add("TR_ArchivTask", "Task", description="weggeräumt")
    server.memory_relate("TR_OffenerTask", "TEIL_VON", "TR_ProjektAlpha")
    server.memory_archive("TR_ArchivTask")


def _by_name(rows):
    return {r["name"]: r for r in rows}


def test_done_flag_folgt_done_statuses():
    _setup()
    rows = _by_name(server._task_rows_full("", False))
    assert rows["TR_OffenerTask"]["done"] is False
    assert rows["TR_OffenerTask"]["status"] == "offen"  # Default ohne extra.status
    assert rows["TR_FertigTask"]["done"] is True
    assert rows["TR_DoneTask"]["done"] is True


def test_archivierte_nur_mit_include_archived():
    assert "TR_ArchivTask" not in _by_name(server._task_rows_full("", False))
    rows = _by_name(server._task_rows_full("", True))
    assert rows["TR_ArchivTask"]["archived"] is True
    assert rows["TR_OffenerTask"]["archived"] is False


def test_projekt_relation_kommt_mit():
    rows = _by_name(server._task_rows_full("", False))
    assert rows["TR_OffenerTask"]["projects"] == ["TR_ProjektAlpha"]
    assert rows["TR_FertigTask"]["projects"] == []


def test_kein_duplikat_bei_mehreren_projekten():
    server.memory_add("TR_ProjektBeta", "Project", description="Beta")
    server.memory_relate("TR_OffenerTask", "TEIL_VON", "TR_ProjektBeta")
    rows = server._task_rows_full("", False)
    treffer = [r for r in rows if r["name"] == "TR_OffenerTask"]
    assert len(treffer) == 1
    assert sorted(treffer[0]["projects"]) == ["TR_ProjektAlpha", "TR_ProjektBeta"]
