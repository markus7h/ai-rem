"""`/manifest` muss zu dem passen, was die Ausliefer-Routen tatsaechlich schicken.

Der Client (`ai-rem update`, system-check-Hook) entscheidet allein anhand dieser
Hashes, ob seine lokalen Kopien veraltet sind. Faengt das Manifest eine neue Datei
nicht mit ein, bleibt sie auf dem Client fuer immer alt — ohne Fehlermeldung, weil
alles Uebrige ja stimmt. Genau dieser Fall wird hier geprueft.
"""
import hashlib
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMPDIR = tempfile.mkdtemp(prefix="ai-rem-manifest-")
os.environ.setdefault("LADYBUG_DB_PATH", os.path.join(_TMPDIR, "kg.db"))
os.environ["EMBED_ENABLED"] = "0"
os.environ.setdefault("AI_REM_API_TOKEN", "test-token")

import server  # noqa: E402


def _manifest():
    return {path: hashlib.sha256(src.encode("utf-8")).hexdigest()
            for path, (_route, src) in server._CLIENT_ARTIFACTS.items()}


def test_jede_ausgelieferte_datei_steht_im_manifest():
    """Alles, was install_hooks/install_cli/install_commands holt, muss drin sein."""
    erwartet = {"hooks/system-check.py", "hooks/auto-memory.py",
                "hooks/claude-md-guard.py", "hooks/save-plan.py",
                "hooks/vault-secret-reminder.py", "bin/ai-rem",
                "commands/setup-ai-rem.md", "commands/memory-cleanup.md",
                "commands/migrate-claude-md.md", "commands/ai-rem-update.md"}
    erwartet |= {"lib/" + n for n in server.CLI_LIB_FILES}
    assert erwartet == set(server._CLIENT_ARTIFACTS)


def test_hash_passt_zur_datei_neben_server_py():
    """Gehasht wird der Modul-String; der stammt aus _pkg_text() und muss Byte fuer
    Byte der Repo-Datei entsprechen (nur __VERSION__ wird ersetzt, und das kommt in
    keinem Client-Artefakt vor — sonst waere jeder Release ein Zwangs-Update)."""
    manifest = _manifest()
    for rel in manifest:
        if rel.startswith("commands/"):
            continue  # Commands sind Inline-Strings in server.py, keine Repo-Datei
        with open(os.path.join(ROOT, rel), "rb") as f:
            roh = f.read()
        assert "__VERSION__" not in roh.decode("utf-8"), rel
        assert hashlib.sha256(roh).hexdigest() == manifest[rel], rel


def test_zu_jedem_artefakt_gibt_es_eine_route():
    """Ohne Route kann der Client die Datei zwar als veraltet erkennen, aber nicht
    nachziehen — `ai-rem update` liefe dann bei jedem Start wieder ins Leere."""
    with open(os.path.join(ROOT, "server.py"), encoding="utf-8") as f:
        quelltext = f.read()
    for rel, (route, _src) in server._CLIENT_ARTIFACTS.items():
        if rel.startswith("lib/"):
            route = "/lib/{name}"  # eine parametrisierte Route fuer alle lib-Module
        assert '@mcp.custom_route("%s"' % route in quelltext, (rel, route)


def test_manifest_route_gibt_version_und_hashes():
    import asyncio
    import json as _json

    resp = asyncio.run(server.manifest_route(None))
    payload = _json.loads(bytes(resp.body).decode("utf-8"))
    assert payload["version"] == server.VERSION
    assert payload["files"] == _manifest()


def test_manifest_ist_oeffentlich():
    """Ohne das Prefix antwortet die Route 401 — und der Hook meldete still nichts."""
    assert any("/manifest".startswith(p) for p in server._PUBLIC_PATH_PREFIXES)
