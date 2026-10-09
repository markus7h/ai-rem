"""save-plan.py holt den Bearer wie system-check: Env -> OS-Keychain -> Legacy-Header.

Der Hook laeuft standalone in ~/.claude/hooks und bringt darum seine eigene
_keychain_token()-Kopie mit — die muss sich genauso verhalten wie die im
SessionStart-Hook (Modul neben der CLI laden, jeder Fehler -> "").
"""
import importlib.util
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Nur bis zur ersten Top-Level-Ausfuehrung laden (AUTH = auth_header() fragt sonst
# den echten Keychain).
_src = open(os.path.join(ROOT, "hooks", "save-plan.py")).read()
_cut = _src.index("\nAUTH = auth_header()")
_spec = importlib.util.spec_from_loader("save_plan_auth", loader=None)
sp = importlib.util.module_from_spec(_spec)
exec(compile(_src[:_cut], "save-plan.py", "exec"), sp.__dict__)

ENV_TOK = "plan-env-tok"  # pragma: allowlist secret
KC_TOK = "plan-keychain-tok"  # pragma: allowlist secret
LEGACY_TOK = "plan-legacy-tok"  # pragma: allowlist secret


def test_auth_header_vorrang(tmp_path, monkeypatch):
    cj = tmp_path / ".claude.json"
    cj.write_text(json.dumps({"mcpServers": {"ai-rem": {
        "headers": {"Authorization": "Bearer " + LEGACY_TOK}}}}))
    monkeypatch.setattr(sp, "CLAUDE_JSON", str(cj))
    monkeypatch.setattr(sp, "_keychain_token", lambda: KC_TOK)

    monkeypatch.setenv("AI_REM_TOKEN", ENV_TOK)
    assert sp.auth_header() == "Bearer " + ENV_TOK
    monkeypatch.setenv("AI_REM_TOKEN", "Bearer " + ENV_TOK)  # schon mit Schema
    assert sp.auth_header() == "Bearer " + ENV_TOK

    monkeypatch.delenv("AI_REM_TOKEN")
    assert sp.auth_header() == "Bearer " + KC_TOK

    monkeypatch.setattr(sp, "_keychain_token", lambda: "")
    assert sp.auth_header() == "Bearer " + LEGACY_TOK

    monkeypatch.setattr(sp, "CLAUDE_JSON", str(tmp_path / "fehlt.json"))
    assert sp.auth_header() is None


def test_keychain_token_laedt_modul_neben_cli(tmp_path, monkeypatch):
    (tmp_path / "bin").mkdir()
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "keychain.py").write_text("def get():\n    return '%s'\n" % KC_TOK)
    monkeypatch.setenv("AI_REM_CLI", str(tmp_path / "bin" / "ai-rem"))
    assert sp._keychain_token() == KC_TOK

    (tmp_path / "lib" / "keychain.py").write_text("def get():\n    raise OSError('zu')\n")
    assert sp._keychain_token() == ""

    monkeypatch.setenv("AI_REM_CLI", str(tmp_path / "nirgends" / "ai-rem"))
    monkeypatch.setenv("HOME", str(tmp_path))
    assert sp._keychain_token() == ""
