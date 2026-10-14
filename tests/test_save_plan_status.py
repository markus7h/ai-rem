"""save-plan.py: Status und created nur beim Anlegen des Plan-Tasks setzen.

ExitPlanMode feuert bei jeder Plan-Revision erneut; der Hook schrieb jedes Mal
status=offen und created=heute — ein laengst erledigter Plan-Task war danach wieder
offen. Geladen wird der Hook ohne main() (und mit Env-Token, damit auth_header()
nicht den echten Keychain fragt).
"""
import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_src = open(os.path.join(ROOT, "hooks", "save-plan.py")).read()
_cut = _src.index("\ntry:\n    main()")
_spec = importlib.util.spec_from_loader("save_plan_status", loader=None)
sp = importlib.util.module_from_spec(_spec)
_prev = os.environ.get("AI_REM_TOKEN")
os.environ["AI_REM_TOKEN"] = "load-dummy"  # pragma: allowlist secret
try:
    exec(compile(_src[:_cut], "save-plan.py", "exec"), sp.__dict__)
finally:
    if _prev is None:
        del os.environ["AI_REM_TOKEN"]
    else:
        os.environ["AI_REM_TOKEN"] = _prev

FM = {"name": "Plan: Herkunft", "description": "kurz"}


def test_neuer_plan_bekommt_status_und_created():
    args = sp.build_args(FM, "/x/plan.md", "")
    assert args["extra"]["status"] == "offen" and args["extra"]["created"]
    assert args["extra"]["plan_file"] == "/x/plan.md"


def test_bestehender_plan_behaelt_status_und_created():
    args = sp.build_args(FM, "/x/plan.md", "Task")
    assert "status" not in args["extra"] and "created" not in args["extra"]
    assert args["extra"]["kind"] == "plan"


def test_expliziter_status_im_frontmatter_gilt():
    args = sp.build_args({**FM, "status": "erledigt"}, "/x/plan.md", "Task")
    assert args["extra"]["status"] == "erledigt"


def test_existing_type_exakt():
    text = ("[Task] **Plan: Herkunft v2** : …\n"
            "[Task] **plan:  herkunft** `[private]`: … _(aktualisiert 2026-10-14)_")
    assert sp.existing_type(text, "Plan: Herkunft") == "Task"
    assert sp.existing_type("[Task] **Plan: Herkunft v2** : …", "Plan: Herkunft") == ""
    assert sp.existing_type("Keine Ergebnisse.", "Plan: Herkunft") == ""
