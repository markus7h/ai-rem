"""Tests für die beiden Bremsen gegen wachsende Task-Karteileichen.

1. Cleanup schlägt Tasks, deren Beschreibung mit einem Erledigt-Marker beginnt, zur
   Archivierung vor (Review-Queue) — archiviert wird nur, was am Status erledigt ist.
2. Der Extraktor legt für reine Arbeitsschritte ("PR #262", "task_556") keinen Task an.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMPDIR = tempfile.mkdtemp(prefix="ai-rem-selbstreinigung-")
os.environ["LADYBUG_DB_PATH"] = os.path.join(_TMPDIR, "kg.db")
os.environ["EMBED_ENABLED"] = "0"
os.environ.setdefault("AI_REM_API_TOKEN", "test-token")

import server  # noqa: E402
from lib.extractor import is_step_task  # noqa: E402


def _alt(name: str) -> None:
    """updated_at und done_at über die Retention-Frist zurückdatieren.

    done_at muss mit: die Karenzzeit hängt daran, updated_at ist nur der Fallback
    für Bestand, der vor der Einführung des Ankers geschlossen wurde."""
    alt = (datetime.now() - timedelta(days=server.CLEANUP_TASK_RETENTION_DAYS + 5)
           ).strftime("%Y-%m-%dT%H:%M:%S")
    server.db_exec("MATCH (e:Entity {id: $id}) SET e.updated_at = $ts",
                   {"id": server._id(name), "ts": alt})
    server._mark_extra(name, "done_at", alt)


def test_body_marker_wird_vorgeschlagen_nicht_archiviert():
    server.memory_add("BodyErledigt", "Task",
                      description="ERLEDIGT 2026-09-01: Release deployed und verifiziert.")
    server.memory_add("BodyOffen", "Task",
                      description="OFFEN: Retry für gescheiterte Dokumente fehlt noch.")
    _alt("BodyErledigt")
    _alt("BodyOffen")

    cands = server._cleanup_candidates()
    # Fließtext ist als Archivierungsgrund zu unzuverlässig — nur Vorschlag.
    assert "BodyErledigt" not in {a["name"] for a in cands["auto_archive"]}
    ziele = {a["target"] for a in cands["archive_review"]}
    assert "BodyErledigt" in ziele
    assert "BodyOffen" not in ziele


def test_vorschlag_kommt_nach_dismiss_nicht_wieder():
    server.memory_add("BodyDismissed", "Task",
                      description="ERLEDIGT 2026-09-02: nichts mehr zu tun.")
    assert "BodyDismissed" in {a["target"] for a in
                               server._cleanup_candidates()["archive_review"]}
    server._mark_extra("BodyDismissed", "done_marker_dismissed", server._now())
    assert "BodyDismissed" not in {a["target"] for a in
                                   server._cleanup_candidates()["archive_review"]}


def test_expliziter_status_verhindert_auto_archiv():
    server.memory_add("StatusOffenTrotzMarker", "Task",
                      description="ERLEDIGT bis auf Punkt 3.", extra={"status": "offen"})
    _alt("StatusOffenTrotzMarker")
    cands = server._cleanup_candidates()
    assert "StatusOffenTrotzMarker" not in {a["name"] for a in cands["auto_archive"]}
    # Der Widerspruch Status/Text ist trotzdem meldenswert.
    assert "StatusOffenTrotzMarker" in {a["target"] for a in cands["archive_review"]}


def test_nur_tasks_betroffen():
    server.memory_add("ErledigteEntscheidung", "Decision",
                      description="ERLEDIGT 2026-09-01: so entschieden.")
    _alt("ErledigteEntscheidung")
    namen = {a["name"] for a in server._cleanup_candidates()["auto_archive"]}
    assert "ErledigteEntscheidung" not in namen


def test_extractor_filtert_arbeitsschritte():
    for name in ("PR #262", "PR-287", "Review 329", "#607", "task_556", "T1: CLI-Strings",
                 "Task 2 (#80)", "Phase 0 - Rollenwrapper", "Implementierer A0", "status"):
        assert is_step_task(name, "Task"), name


def test_extractor_laesst_echte_tasks_durch():
    for name in ("doc-graph: Failed-Docs sichtbar machen + Retry",
                 "Silbersee #463: Beziehungsschluessel auf int64",
                 "Reisekostenabrechnung erstellen", "Statusbericht für Thomas"):
        assert not is_step_task(name, "Task"), name
    # Andere Typen bleiben unberührt, auch bei Schritt-Namen.
    assert not is_step_task("PR #262", "Decision")


if __name__ == "__main__":
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
            fn(); print("ok:", fn.__name__)
