"""Der SessionStart-Hook muss die Offene-Tasks-Sektion aus memory_get_context ziehen.

Regression: der Parser verglich den Header exakt mit '## Offene Tasks' und fand ihn
nicht mehr, seit der Server Zaehler und Kontext-Label anhaengt — der Block blieb leer.
"""
import importlib.util
import json
import os
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Hook laden ohne ihn auszufuehren: die Datei startet ihre Checks auf Modulebene,
# darum nur den Quelltext bis zur ersten Top-Level-Ausfuehrung (check_ai_rem())
# importieren. AI_REM_TOKEN waehrend des Ladens setzen, sonst fragt die Token-
# Aufloesung auf Modulebene den echten OS-Keychain der Entwickler-Maschine.
_src = open(os.path.join(ROOT, "hooks", "system-check.py")).read()
_cut = _src.index("\ncheck_ai_rem()\n")
_spec = importlib.util.spec_from_loader("system_check_parser", loader=None)
sc = importlib.util.module_from_spec(_spec)
_prev_tok = os.environ.get("AI_REM_TOKEN")
os.environ["AI_REM_TOKEN"] = "load-dummy"  # pragma: allowlist secret
try:
    exec(compile(_src[:_cut], "system-check.py", "exec"), sc.__dict__)
finally:
    if _prev_tok is None:
        del os.environ["AI_REM_TOKEN"]
    else:
        os.environ["AI_REM_TOKEN"] = _prev_tok

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


def test_fehler_marker_ist_rotes_x():
    """Fehlgeschlagene Checks muessen ❌ tragen — ✗ geht in der Statuszeile unter."""
    assert "\u2717" not in _src, "Fehler-Marker in system-check.py: ❌ statt ✗"


if __name__ == "__main__":
    test_header_mit_zaehler_wird_erkannt()
    test_ohne_sektion_leer()
    test_fehler_marker_ist_rotes_x()
    print("OK")


def test_auto_memory_frisch_installiert_kein_fehlalarm(tmp_path, monkeypatch):
    """Frisch angelegtes auto-memory ohne jeden Lauf: noch keine Session geendet,
    also kein Grund zur Warnung. Nach der Gnadenfrist schon."""
    base = tmp_path / "auto-memory"
    base.mkdir()
    assert sc._auto_memory_fault(str(base)) == ""

    real = sc.time.time()
    monkeypatch.setattr(sc.time, "time", lambda: real + 2 * 86400)
    assert "noch nie" in sc._auto_memory_fault(str(base))


def test_auto_memory_frisch_mit_fehlern_meldet_trotzdem(tmp_path):
    """Die Gnadenfrist gilt nur ohne Fehler — Fehlschlaege bleiben sichtbar."""
    base = tmp_path / "auto-memory"
    base.mkdir()
    (base / "errors.log").write_text("2026-10-09T10:00:00\tSessionEnd rc=1\n")
    assert "noch nie" in sc._auto_memory_fault(str(base))


# --------------------------------------------------------------------- Token-Quelle

ENV_TOK = "tok-aus-env"  # pragma: allowlist secret
KC_TOK = "tok-aus-keychain"  # pragma: allowlist secret
LEGACY_TOK = "tok-aus-claude-json"  # pragma: allowlist secret


def _legacy_json(tmp_path, token):
    p = tmp_path / ".claude.json"
    p.write_text(json.dumps({"mcpServers": {"ai-rem": {
        "headers": {"Authorization": "Bearer " + token}}}}))
    return str(p)


def test_token_vorrang_env_keychain_legacy(tmp_path, monkeypatch):
    """Env schlaegt Keychain schlaegt Klartext-Header; die Quelle steht fuer die
    Statuszeile dabei. Keychain ist gefakt — kein Zugriff auf den echten."""
    monkeypatch.setattr(sc, "CLAUDE_JSON", _legacy_json(tmp_path, LEGACY_TOK))
    monkeypatch.setattr(sc, "_keychain_token", lambda: KC_TOK)
    monkeypatch.setenv("AI_REM_TOKEN", ENV_TOK)
    assert sc._resolve_ai_rem_token() == (ENV_TOK, "env")

    monkeypatch.delenv("AI_REM_TOKEN")
    assert sc._resolve_ai_rem_token() == (KC_TOK, "keychain")

    monkeypatch.setattr(sc, "_keychain_token", lambda: "")
    assert sc._resolve_ai_rem_token() == (LEGACY_TOK, "legacy")

    monkeypatch.setattr(sc, "CLAUDE_JSON", str(tmp_path / "gibts-nicht.json"))
    assert sc._resolve_ai_rem_token() == ("", "")


def test_keychain_token_ohne_modul_leer(tmp_path, monkeypatch):
    """Fehlt lib/keychain.py (Hook kopiert, CLI nicht installiert) -> "" statt Traceback."""
    monkeypatch.setenv("AI_REM_CLI", str(tmp_path / "bin" / "ai-rem"))
    monkeypatch.setenv("HOME", str(tmp_path))
    assert sc._keychain_token() == ""


def test_keychain_token_laedt_modul_neben_cli(tmp_path, monkeypatch):
    """lib/keychain.py wird relativ zu $AI_REM_CLI gefunden und get() gerufen."""
    (tmp_path / "bin").mkdir()
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "keychain.py").write_text(
        "def get():\n    return '  %s\\n'\n" % KC_TOK)
    monkeypatch.setenv("AI_REM_CLI", str(tmp_path / "bin" / "ai-rem"))
    assert sc._keychain_token() == KC_TOK

    (tmp_path / "lib" / "keychain.py").write_text(
        "def get():\n    raise RuntimeError('keychain gesperrt')\n")
    assert sc._keychain_token() == ""


def test_token_statuszeile(monkeypatch):
    monkeypatch.setattr(sc, "AI_REM_ENDPOINT", "https://airem.test/mcp")
    for src, want in (("keychain", "token ✓ (Keychain)"),
                      ("env", "token ✓ (Env)"),
                      ("legacy", "token ⚠ Klartext-Legacy → ai-rem update"),
                      ("", "token ❌ fehlt → ai-rem pair")):
        sc.results[:] = []
        monkeypatch.setattr(sc, "AI_REM_TOKEN_SOURCE", src)
        sc.check_token()
        assert sc.results == [want]
    sc.results[:] = []


# ------------------------------------------------------------------------ LLM-Check

LLM_KEY = "router-key-vom-server"  # pragma: allowlist secret


class _FakeResp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(seen, ok=True):
    def urlopen(req, timeout=None):
        seen.append(req)
        if not ok:
            raise OSError("401")
        return _FakeResp()
    return urlopen


def test_llm_check_nutzt_url_und_key_vom_server(monkeypatch):
    """Ohne Env kommen Router-URL und Key aus /api/client-config."""
    monkeypatch.setattr(sc, "AI_REM_LLM_URL_ENV", "")
    monkeypatch.setattr(sc, "AI_REM_LLM_API_KEY", "")
    monkeypatch.setattr(sc, "_api_get", lambda path: {
        "version": "1.7.0", "llm_url": "http://router.test:11437/",
        "llm_api_key": LLM_KEY} if path == "/api/client-config" else None)
    monkeypatch.setattr(sc, "_ai_rem_cli", lambda: "")
    seen = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(seen))
    sc.results[:] = []
    assert sc.check_ollama_and_catchup() == ""
    assert sc.results == []
    assert seen[0].full_url == "http://router.test:11437/v1/models"
    assert seen[0].get_header("Authorization") == "Bearer " + LLM_KEY
    sc.results[:] = []


def test_llm_check_env_schlaegt_server(monkeypatch):
    monkeypatch.setattr(sc, "AI_REM_LLM_URL_ENV", "http://env.test:1")
    monkeypatch.setattr(sc, "AI_REM_LLM_API_KEY", "env-key")  # pragma: allowlist secret
    monkeypatch.setattr(sc, "_api_get", lambda path: {
        "llm_url": "http://router.test:11437", "llm_api_key": LLM_KEY})
    monkeypatch.setattr(sc, "_ai_rem_cli", lambda: "")
    seen = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(seen))
    sc.results[:] = []
    sc.check_ollama_and_catchup()
    assert seen[0].full_url == "http://env.test:1/v1/models"
    assert seen[0].get_header("Authorization") == "Bearer env-key"
    sc.results[:] = []


def test_llm_check_alter_server_hinweis(monkeypatch):
    """Server ohne /api/client-config (< 1.7) und kein Env-Key: llm ❌ wie bisher,
    dazu der Hinweis, dass der Server und nicht der Router das Problem ist."""
    monkeypatch.setattr(sc, "AI_REM_LLM_URL_ENV", "")
    monkeypatch.setattr(sc, "AI_REM_LLM_API_KEY", "")
    monkeypatch.setattr(sc, "AI_REM_LLM_URL_TMPL", "http://tmpl.test:11437")
    monkeypatch.setattr(sc, "_api_get", lambda path: None)
    monkeypatch.setattr(sc, "ai_rem_ok", True)
    seen = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(seen, ok=False))
    sc.results[:] = []
    hint = sc.check_ollama_and_catchup()
    assert sc.results == ["llm ❌"]
    assert "Server < 1.7" in hint and "aktualisieren" in hint
    assert seen[0].full_url == "http://tmpl.test:11437/v1/models"
    assert seen[0].get_header("Authorization") is None

    # Server gar nicht erreichbar (ai-rem ❌): kein Versions-Hinweis, der waere falsch.
    monkeypatch.setattr(sc, "ai_rem_ok", False)
    sc.results[:] = []
    assert sc.check_ollama_and_catchup() == ""
    assert sc.results == ["llm ❌"]
    sc.results[:] = []
