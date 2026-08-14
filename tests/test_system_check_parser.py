"""Der SessionStart-Hook muss die Offene-Tasks-Sektion aus memory_get_context ziehen.

Regression: der Parser verglich den Header exakt mit '## Offene Tasks' und fand ihn
nicht mehr, seit der Server Zaehler und Kontext-Label anhaengt — der Block blieb leer.
"""
import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Hook laden ohne ihn auszufuehren: die Datei startet ihre Checks auf Modulebene,
# darum nur den Quelltext bis zur ersten Top-Level-Ausfuehrung importieren.
_src = open(os.path.join(ROOT, "hooks", "system-check.py")).read()
_cut = _src.index("\n_sync_ai_rem_header(")
_spec = importlib.util.spec_from_loader("system_check_parser", loader=None)
sc = importlib.util.module_from_spec(_spec)
exec(compile(_src[:_cut], "system-check.py", "exec"), sc.__dict__)

CTX = """## Routinen & Anweisungen
- 📌 **Irgendeine Regel**: bla

## Offene Tasks (12)
- **_ohne Projekt_** — 6 offen
- **ProjektAlpha** — 3 offen
Zuletzt: TaskEins · TaskZwei
→ Details: `memory_get_context(topic="<Projekt>")`

## Projekte
- [aktiv] **ProjektAlpha**: irgendwas
"""


def test_header_mit_zaehler_wird_erkannt():
    out = sc.offene_tasks_section(CTX)
    assert out.startswith("## Offene Tasks (12)")
    assert "- **ProjektAlpha** — 3 offen" in out
    assert "Zuletzt: TaskEins · TaskZwei" in out
    # naechste Sektion gehoert nicht mehr dazu
    assert "## Projekte" not in out
    assert "[aktiv]" not in out


def test_ohne_sektion_leer():
    assert sc.offene_tasks_section("## Projekte\n- [aktiv] **X**: y") == ""


def test_auto_memory_status_nur_wenn_hook_registriert(tmp_path):
    """Ohne Eintrag in settings.json ist Auto-Memory bewusst aus — dann weder
    Statuszeile noch Stoerungsmeldung."""
    orig, sc.results[:] = sc.SETTINGS, []
    try:
        aus = tmp_path / "aus.json"
        aus.write_text('{"hooks": {}}')
        sc.SETTINGS = str(aus)
        assert sc.check_auto_memory() == ""
        assert sc.results == []

        an = tmp_path / "an.json"
        an.write_text('{"hooks": {"SessionEnd": "~/.claude/hooks/auto-memory.py"}}')
        sc.SETTINGS = str(an)
        sc.check_auto_memory()
        assert sc.results and sc.results[0].startswith("Auto-Memory ")
        assert "🧠" not in sc.results[0]
    finally:
        sc.SETTINGS, sc.results[:] = orig, []


if __name__ == "__main__":
    test_header_mit_zaehler_wird_erkannt()
    test_ohne_sektion_leer()
    print("OK")
