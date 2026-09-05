"""Tests für die beiden Bremsen gegen wachsende Task-Karteileichen.

1. Cleanup archiviert Tasks, deren Beschreibung mit einem Erledigt-Marker beginnt,
   auch wenn niemand extra.status gesetzt hat.
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
    """updated_at über die Retention-Frist zurückdatieren."""
    alt = (datetime.now() - timedelta(days=server.CLEANUP_TASK_RETENTION_DAYS + 5)
           ).strftime("%Y-%m-%dT%H:%M:%S")
    server.db_exec("MATCH (e:Entity {id: $id}) SET e.updated_at = $ts",
                   {"id": server._id(name), "ts": alt})


def test_body_marker_wird_archiviert():
    server.memory_add("BodyErledigt", "Task",
                      description="ERLEDIGT 2026-09-01: Release deployed und verifiziert.")
    server.memory_add("BodyOffen", "Task",
                      description="OFFEN: Retry für gescheiterte Dokumente fehlt noch.")
    server.memory_add("BodyErledigtFrisch", "Task",
                      description="GELÖST 2026-09-05: PR gemergt.")
    _alt("BodyErledigt")
    _alt("BodyOffen")

    namen = {a["name"] for a in server._cleanup_candidates()["auto_archive"]}
    assert "BodyErledigt" in namen
    assert "BodyOffen" not in namen
    # Retention gilt weiter: frisch Erledigtes wird nicht sofort weggeräumt.
    assert "BodyErledigtFrisch" not in namen


def test_expliziter_status_schlaegt_body():
    server.memory_add("StatusOffenTrotzMarker", "Task",
                      description="ERLEDIGT bis auf Punkt 3.", extra={"status": "offen"})
    _alt("StatusOffenTrotzMarker")
    namen = {a["name"] for a in server._cleanup_candidates()["auto_archive"]}
    assert "StatusOffenTrotzMarker" not in namen


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
