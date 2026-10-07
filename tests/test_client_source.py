"""Herkunft: memory_add setzt extra.client beim Anlegen (aus X-AI-REM-Client bzw.
MCP clientInfo), ueberschreibt sie aber nie bei einem Update aus einem anderen Client.
Läuft im SUBPROZESS mit eigener Temp-DB, weil der pytest-Modulcache server.py sonst
zwischen Tests teilt."""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _scenario() -> None:
    tmp = tempfile.mkdtemp(prefix="ai-rem-client-")
    os.environ["LADYBUG_DB_PATH"] = os.path.join(tmp, "kg.db")
    os.environ["BACKUP_DIR"] = os.path.join(tmp, "backups")
    os.environ["EMBED_ENABLED"] = "0"
    os.environ.setdefault("AI_REM_API_TOKEN", "test-token")
    sys.path.insert(0, ROOT)

    import json
    import server

    def extra(name):
        raw = server._rows(server.db_exec(
            "MATCH (e:Entity {id:$id}) RETURN e.extra", {"id": server._id(name)}))[0][0]
        return json.loads(raw)

    server.memory_add("Ohne", "Topic", description="kein Client bekannt")
    assert "client" not in extra("Ohne")

    tok = server._REQUEST_CLIENT.set("opencode")
    server.memory_add("Neu", "Topic", description="aus opencode")
    server._REQUEST_CLIENT.reset(tok)
    assert extra("Neu")["client"] == "opencode"

    tok = server._REQUEST_CLIENT.set("claude-code")
    server.memory_add("Neu", "Topic", description="spaeter aus Claude ergaenzt")
    server._REQUEST_CLIENT.reset(tok)
    assert extra("Neu")["client"] == "opencode", "Herkunft beim Update ueberschrieben"

    tok = server._REQUEST_CLIENT.set("böse\nzeile<script>")
    server.memory_add("Roh", "Topic", description="x")
    server._REQUEST_CLIENT.reset(tok)
    assert extra("Roh")["client"] == "bösezeilescript", extra("Roh")

    server.memory_add("Explizit", "Topic", description="x", extra={"client": "import"})
    assert extra("Explizit")["client"] == "import"
    print("OK")


def test_client_source():
    r = subprocess.run(
        [sys.executable, __file__],
        capture_output=True, text=True,
        env={**os.environ, "EMBED_ENABLED": "0", "AI_REM_API_TOKEN": "test-token"},
    )
    assert r.returncode == 0, f"Szenario fehlgeschlagen:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    assert "OK" in r.stdout


if __name__ == "__main__":
    _scenario()
