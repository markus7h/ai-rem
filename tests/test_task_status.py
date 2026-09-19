"""Tests für das Task-Status-Enum, den extra-Merge und die Karenzzeit.

extra.status war lange freier Text, was 17 Schreibweisen für vier Zustände ergab —
und ein Tippfehler machte einen Task gleichzeitig ewig offen und immun gegen die
Selbstreinigung. Geschrieben wird jetzt nur noch kanonisch.

In-process gegen Temp-DB, Embedding aus. Entity-Namen sind mit TS_ geprefixt:
`server` wird pro pytest-Lauf nur einmal importiert, alle Testmodule teilen sich
deshalb die DB des zuerst importierenden Moduls.
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMPDIR = tempfile.mkdtemp(prefix="ai-rem-taskstatus-")
os.environ["LADYBUG_DB_PATH"] = os.path.join(_TMPDIR, "kg.db")
os.environ["EMBED_ENABLED"] = "0"
os.environ.setdefault("AI_REM_API_TOKEN", "test-token")

import server  # noqa: E402


def _extra(name: str) -> dict:
    rows = server._rows(server.db_exec(
        "MATCH (e:Entity {id: $id}) RETURN e.extra", {"id": server._id(name)}))
    return json.loads(rows[0][0] or "{}")


def _vor(tagen: int) -> str:
    return (datetime.now() - timedelta(days=tagen)).strftime("%Y-%m-%dT%H:%M:%S")


def test_canon_status_mappt_synonyme():
    for roh, erwartet in [("gemergt", "erledigt"), ("DONE", "erledigt"),
                          ("erledigt ", "erledigt"), ("abgeschlossen", "erledigt"),
                          ("deployed", "erledigt"), ("in Arbeit", "laufend"),
                          ("WIP", "laufend"), ("blocked", "blockiert"),
                          ("todo", "offen"), ("", "offen"), (None, "offen")]:
        status, note = server._canon_status(roh)
        assert (status, note) == (erwartet, ""), roh


def test_canon_status_ueberlebt_nicht_strings():
    # Ein extra.status vom Typ bool/int hat früher die komplette Offene-Tasks-Sektion
    # mit AttributeError gekillt.
    assert server._canon_status(True)[0] == "laufend"
    assert server._canon_status(3)[0] == "laufend"


def test_freitext_landet_in_status_note():
    status, note = server._canon_status("PR 743 offen, Merge ausstehend")
    assert status == "laufend" and note == "PR 743 offen, Merge ausstehend"

    server.memory_add("TS_Freitext", "Task", description="x",
                      extra={"status": "PR 743 offen, Merge ausstehend"})
    ex = _extra("TS_Freitext")
    assert ex["status"] == "laufend"
    assert ex["status_note"] == "PR 743 offen, Merge ausstehend"
    # Wird der Status kanonisch nachgezogen, verschwindet die Notiz wieder.
    server.memory_add("TS_Freitext", "Task", extra={"status": "erledigt"})
    assert "status_note" not in _extra("TS_Freitext")


def test_projekt_status_bleibt_unberuehrt():
    # memory_set_project_context schreibt status="aktiv" — kein Task-Status.
    server.memory_set_project_context("TS_Projekt", description="p", status="aktiv")
    assert _extra("TS_Projekt")["status"] == "aktiv"


def test_abschluss_erhaelt_plan_metadaten():
    server.memory_add("TS_Plan", "Task", description="Plan",
                      extra={"kind": "plan", "plan_file": "/x/plan.md"})
    server.memory_add("TS_Plan", "Task", extra={"status": "gemergt"})
    ex = _extra("TS_Plan")
    assert ex["plan_file"] == "/x/plan.md" and ex["kind"] == "plan"
    assert ex["status"] == "erledigt" and "done_at" in ex


def test_wiedereroeffnen_loescht_done_at():
    server.memory_add("TS_Reopen", "Task", description="x", extra={"status": "erledigt"})
    assert "done_at" in _extra("TS_Reopen")
    server.memory_add("TS_Reopen", "Task", extra={"status": "offen"})
    assert "done_at" not in _extra("TS_Reopen")


def test_karenzzeit_zaehlt_ab_done_at():
    for name, tage in [("TS_Alt", server.CLEANUP_TASK_RETENTION_DAYS + 6),
                       ("TS_Frisch", 5)]:
        server.memory_add(name, "Task", description="x", extra={"status": "erledigt"})
        server._mark_extra(name, "done_at", _vor(tage))
    namen = {a["name"] for a in server._cleanup_candidates()["auto_archive"]}
    assert "TS_Alt" in namen and "TS_Frisch" not in namen


def test_archivieren_setzt_status_erledigt():
    server.memory_add("TS_Arch", "Task", description="x")
    assert _extra("TS_Arch")["status"] == "offen"
    server.memory_archive("TS_Arch")
    ex = _extra("TS_Arch")
    assert ex["status"] == "erledigt" and "done_at" in ex


def test_migration_ist_idempotent_und_schont_updated_at():
    # Direkt in die DB, wie es _apply_import beim Backup-Restore tut: so entsteht
    # Altbestand, den memory_add nie zu sehen bekommen hat.
    ts = _vor(40)
    server.db_exec(
        "CREATE (e:Entity {id: $id, name: $n, type: 'Task', descr: 'x', "
        "extra: $ex, context: '', pinned: '', created_at: $ts, updated_at: $ts})",
        {"id": server._id("TS_Alt_Import"), "n": "TS_Alt_Import",
         "ex": json.dumps({"status": "abgeschlosen", "kind": "plan"}), "ts": ts})

    trocken = server.memory_normalize_task_status(dry_run=True)
    assert "TS_Alt_Import" in trocken
    assert _extra("TS_Alt_Import")["status"] == "abgeschlosen"  # nichts geschrieben

    server.memory_normalize_task_status(dry_run=False)
    ex = _extra("TS_Alt_Import")
    # "abgeschlosen" ist ein Tippfehler, kein Synonym — er wird zu laufend plus Notiz,
    # damit der Task sichtbar bleibt statt still als erledigt zu verschwinden.
    assert ex["status"] == "laufend" and ex["status_note"] == "abgeschlosen"
    assert ex["kind"] == "plan"

    rows = server._rows(server.db_exec(
        "MATCH (e:Entity {id: $id}) RETURN e.updated_at",
        {"id": server._id("TS_Alt_Import")}))
    assert rows[0][0] == ts  # Migration verschiebt weder Ranking noch Karenzuhr

    assert server.memory_normalize_task_status(dry_run=False).startswith("Normalisiert: 0/")


if __name__ == "__main__":
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
            fn(); print("ok:", fn.__name__)
