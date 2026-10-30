"""Der SessionStart-Hook muss die Offene-Tasks-Sektion aus memory_get_context ziehen.

Regression: der Parser verglich den Header exakt mit '## Offene Tasks' und fand ihn
nicht mehr, seit der Server Zaehler und Kontext-Label anhaengt — der Block blieb leer.
"""
import importlib.util
import json
import os
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Hook laden ohne ihn auszufuehren: die Checks starten nur unter
# __name__ == "__main__", exec unter eigenem Modulnamen fuehrt nichts aus. AI_REM_TOKEN waehrend des Ladens setzen, sonst fragt die Token-
# Aufloesung auf Modulebene den echten OS-Keychain der Entwickler-Maschine.
_src = open(os.path.join(ROOT, "hooks", "system-check.py")).read()
_spec = importlib.util.spec_from_loader("system_check_parser", loader=None)
sc = importlib.util.module_from_spec(_spec)
_prev_tok = os.environ.get("AI_REM_TOKEN")
os.environ["AI_REM_TOKEN"] = "load-dummy"  # pragma: allowlist secret
try:
    exec(compile(_src, "system-check.py", "exec"), sc.__dict__)
finally:
    if _prev_tok is None:
        del os.environ["AI_REM_TOKEN"]
    else:
        os.environ["AI_REM_TOKEN"] = _prev_tok

CTX = """## Routinen & Anweisungen
- 📌 **Irgendeine Regel**: bla

## Offene Tasks (12)
Zuletzt: TaskEins · TaskZwei
→ Details: `memory_get_context(topic="<Projekt>")`

## Projekte
- [aktiv] **ProjektAlpha**: irgendwas
"""


def test_header_mit_zaehler_wird_erkannt():
    out = sc.offene_tasks_section(CTX)
    assert out.startswith("## Offene Tasks (12)")
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
    assert sc.check_ollama_and_catchup() is False
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
    hint = sc._llm_hint(sc.check_ollama_and_catchup())
    assert sc.results == ["llm ❌"]
    assert "Server < 1.7" in hint and "aktualisieren" in hint
    assert seen[0].full_url == "http://tmpl.test:11437/v1/models"
    assert seen[0].get_header("Authorization") is None

    # Server gar nicht erreichbar (ai-rem ❌): kein Versions-Hinweis, der waere falsch.
    monkeypatch.setattr(sc, "ai_rem_ok", False)
    sc.results[:] = []
    assert sc._llm_hint(sc.check_ollama_and_catchup()) == ""
    assert sc.results == ["llm ❌"]
    sc.results[:] = []


def test_session_herkunft_abschnitt(monkeypatch):
    import subprocess

    monkeypatch.setattr(sc, "_ai_rem_cli", lambda: "/x/ai-rem")
    monkeypatch.setattr(sc, "_origin_lib", lambda cli: None)  # CLI-Fallback pruefen
    seen = []

    def fake_run(origin):
        def run(cmd, **kw):
            seen.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, json.dumps(origin), "")
        return run

    monkeypatch.setattr(sc.subprocess, "run", fake_run(
        {"project": "ai-rem", "context": "private", "ask": False, "suggestions": []}))
    out = sc.session_origin_section("/w/ai-rem")
    assert seen[0][-4:] == ["origin", "--cwd", "/w/ai-rem", "--json"]
    assert out.startswith("## Session-Herkunft\nProjekt: ai-rem · Kontext: private")
    assert "context=private" in out and "TEIL_VON ai-rem" in out
    assert "AskUserQuestion" not in out

    monkeypatch.setattr(sc.subprocess, "run", fake_run(
        {"project": "", "context": "", "ask": True, "suggestions": ["Luschendorf", "neu: lu"]}))
    out = sc.session_origin_section("/w/lu")
    assert "AskUserQuestion" in out and "Luschendorf, neu: lu" in out
    assert 'ai-rem map-dir --dir "/w/lu"' in out

    # Ad-hoc-Verzeichnis (nichts bekannt, keine Frage) und CLI-Fehler: kein Abschnitt.
    monkeypatch.setattr(sc.subprocess, "run", fake_run({"project": "", "context": "", "ask": False}))
    assert sc.session_origin_section("/Users/x") == ""

    def boom(cmd, **kw):
        raise OSError("weg")
    monkeypatch.setattr(sc.subprocess, "run", boom)
    assert sc.session_origin_section("/w") == ""


def _load_origin_lib():
    spec = importlib.util.spec_from_file_location("origin_for_test", os.path.join(ROOT, "lib", "origin.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_session_herkunft_in_process_ohne_cli(tmp_path, monkeypatch):
    """Mit Endpoint und lib/origin.py: kein CLI-Unterprozess, Server-Daten ueber den
    Hook-Client (Token/Endpoint des Hooks)."""
    import subprocess

    repo = tmp_path / "demo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin",
                    "git@github.com:markus7h/demo.git"], check=True)

    class FakeClient:
        def client_config(self):
            return {"context_rules": [{"owner": "markus7h", "emails": [], "context": "private"}]}

        def call(self, tool, args=None):
            assert tool == "memory_project_index"
            return json.dumps([{"name": "Demo-Projekt", "repos": ["markus7h/demo"]}])

    monkeypatch.setattr(sc, "AI_REM_ENDPOINT", "https://x.test/mcp")
    monkeypatch.setattr(sc, "_ai_rem_cli", lambda: "/x/ai-rem")
    monkeypatch.setattr(sc, "_origin_lib", lambda cli: _load_origin_lib())
    monkeypatch.setattr(sc, "_HookClient", FakeClient)

    def no_cli(*a, **kw):
        raise AssertionError("CLI darf nicht laufen")
    monkeypatch.setattr(sc, "_origin_via_cli", no_cli)
    out = sc.session_origin_section(str(repo))
    assert out.startswith("## Session-Herkunft\nProjekt: Demo-Projekt · Kontext: private")


def test_session_herkunft_in_process_fehler_faellt_auf_cli(monkeypatch):
    class Kaputt:
        def fetch_projects(self, c):
            return []

        def fetch_rules(self, c):
            raise RuntimeError("weg")

    monkeypatch.setattr(sc, "AI_REM_ENDPOINT", "https://x.test/mcp")
    monkeypatch.setattr(sc, "_ai_rem_cli", lambda: "/x/ai-rem")
    monkeypatch.setattr(sc, "_origin_lib", lambda cli: Kaputt())
    monkeypatch.setattr(sc, "_origin_via_cli", lambda cli, cwd: {"project": "P", "context": "work"})
    assert sc.session_origin_section("/w").startswith("## Session-Herkunft\nProjekt: P · Kontext: work")


_CHECKS = ["check_ai_rem", "check_token", "check_smb", "check_mcp_servers",
           "check_and_sync_settings", "check_tools", "check_ollama_and_catchup",
           "check_embed_pending", "check_auto_memory", "check_client_artifacts",
           "session_origin_section", "check_cleanup_pending"]


def _stub_checks(monkeypatch, **override):
    monkeypatch.setattr(sc, "_hook_input", lambda: {"cwd": "/w"})
    monkeypatch.setattr(sc, "open_tasks_md", "")
    for name in _CHECKS:
        monkeypatch.setattr(sc, name, override.get(name, lambda *a: None))


def test_haengender_check_haelt_den_hook_nicht_auf(monkeypatch):
    """Ein Check, der haengt (Netz/Server weg), darf die Ausgabe nicht ueber die
    Deadline hinaus verzoegern — er erscheint als '<name> ⏱'."""
    import time

    def haengt():
        time.sleep(30)

    def ok():
        sc.results.append("settings ✓")

    _stub_checks(monkeypatch, check_smb=haengt, check_and_sync_settings=ok,
                 check_cleanup_pending=lambda: "[ai-rem] cleanup-hinweis")
    monkeypatch.setattr(sc, "DEADLINE_S", 1)
    t0 = time.monotonic()
    out = sc.main()
    assert time.monotonic() - t0 < sc.DEADLINE_S + 1
    assert out["systemMessage"] == "SMB ⏱ | settings ✓"
    assert out["hookSpecificOutput"]["additionalContext"] == "[ai-rem] cleanup-hinweis"
    assert out["suppressOutput"] is True


def test_reihenfolge_bleibt_trotz_paralleler_checks(monkeypatch):
    """Die Statuszeile folgt der festen Check-Reihenfolge, nicht der Laufzeit."""
    import time

    def langsam_ai_rem():
        time.sleep(0.3)
        sc.results.append("ai-rem ✓")

    def schnell_llm():
        sc.results.append("llm ❌")
        return False

    _stub_checks(monkeypatch, check_ai_rem=langsam_ai_rem, check_ollama_and_catchup=schnell_llm,
                 check_token=lambda: sc.results.append("token ✓ (Env)"),
                 session_origin_section=lambda cwd: "## Session-Herkunft\nX",
                 check_auto_memory=lambda: "am-fault")
    out = sc.main()
    assert out["systemMessage"] == "ai-rem ✓ | token ✓ (Env) | llm ❌"
    assert out["hookSpecificOutput"]["additionalContext"] == "## Session-Herkunft\nX\nam-fault"
    assert list(sc.results) == []  # nichts in die globale Liste durchgerutscht


def test_ai_rem_teilergebnis_bei_langsamen_tasks(monkeypatch):
    """Steht die MCP-Session, haengt aber memory_get_context: 'ai-rem ✓' bleibt."""
    import time

    def ai_rem():
        sc.results.append("ai-rem ✓")
        time.sleep(30)

    _stub_checks(monkeypatch, check_ai_rem=ai_rem)
    monkeypatch.setattr(sc, "DEADLINE_S", 0.5)
    assert sc.main()["systemMessage"] == "ai-rem ✓"


# ── SMB: smb_automount-Waechter beim Start anstossen ─────────────────────────

def _smb_setup(monkeypatch, tmp_path, mounted, after_kick=None):
    """Fake-LaunchAgent unter tmp_path, `mount`-Ausgabe und kickstart gestubbt.
    after_kick: Mount-Zustand nach dem Anstossen (None = unveraendert)."""
    import plistlib
    import subprocess as sp
    la = tmp_path / "Library" / "LaunchAgents"
    la.mkdir(parents=True)
    label = "tools.smb-automount.nas.lan-share"
    with open(la / f"{label}.plist", "wb") as f:
        plistlib.dump({"Label": label, "ProgramArguments":
                       ["/bin/bash", "/x/smb-automount", "smb://u@nas.lan/share"]}, f)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sc.platform, "system", lambda: "Darwin")
    state = {"mounted": mounted, "kicked": []}
    line = "//u@nas.lan/share on /Volumes/share (smbfs, nodev, nosuid, mounted by u)\n"
    monkeypatch.setattr(sc.subprocess, "check_output",
                        lambda cmd, **k: line if state["mounted"] else "/dev/disk1 on / (apfs)\n")

    def run(cmd, **k):
        state["kicked"].append(cmd)
        if after_kick is not None:
            state["mounted"] = after_kick
        return sp.CompletedProcess(cmd, 0)

    monkeypatch.setattr(sc.subprocess, "run", run)
    monkeypatch.setattr(sc, "_smb_responsive", lambda mp, timeout=2: mp == "/Volumes/share")
    monkeypatch.setattr(sc, "results", sc._Results())
    monkeypatch.setattr(sc, "SMB_BUDGET_S", 1)
    return label, state


def test_smb_eingehaengt_kein_anstoss(monkeypatch, tmp_path):
    _, state = _smb_setup(monkeypatch, tmp_path, mounted=True)
    assert sc.check_smb() == ""
    assert list(sc.results) == ["SMB ✓"]
    assert state["kicked"] == []


def test_smb_fehlt_waechter_haengt_neu_ein(monkeypatch, tmp_path):
    label, state = _smb_setup(monkeypatch, tmp_path, mounted=False, after_kick=True)
    assert sc.check_smb() == ""
    assert list(sc.results) == ["SMB ✓ (neu eingehängt)"]
    assert state["kicked"][0][:2] == ["launchctl", "kickstart"]
    assert state["kicked"][0][2].endswith("/" + label)


def test_smb_noch_weg_hinweis_fuer_claude(monkeypatch, tmp_path):
    _, state = _smb_setup(monkeypatch, tmp_path, mounted=False)
    hint = sc.check_smb()
    assert list(sc.results) == ["SMB ↻ Wächter angestoßen"]
    assert "smb_automount" in hint and "mode=reconnect" in hint
    assert "smb://u@nas.lan/share" in hint


def test_smb_ohne_waechter_und_template_still(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sc.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(sc, "SMB_MOUNT", "")
    monkeypatch.setattr(sc, "results", sc._Results())
    assert not sc.check_smb()
    assert list(sc.results) == []
