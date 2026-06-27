"""Graph-Invarianten-Check (Issue #28, Tier 3).

_graph_invariants() meldet korruptions-artige Verstöße als Assertion (nicht als
Cleanup-Vorschlag): ungültiges extra-JSON, nicht-kanonische Flags, kaputtes embedding,
id-vs-_id(name)-Drift. Läuft im SUBPROZESS mit eigener Temp-DB — das Szenario injiziert
absichtlich korruptes extra, das über den geteilten pytest-Modulcache sonst andere
Tests (z.B. okf-Export, der den Graph dumpt) brechen würde.
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _scenario() -> None:
    tmp = tempfile.mkdtemp(prefix="ai-rem-inv-")
    os.environ["KUZU_DB_PATH"] = os.path.join(tmp, "kg.db")
    os.environ["BACKUP_DIR"] = os.path.join(tmp, "backups")
    os.environ["EMBED_ENABLED"] = "0"
    os.environ.setdefault("AI_REM_API_TOKEN", "test-token")
    sys.path.insert(0, ROOT)

    import server

    # Sauberer Graph → keine Verstöße.
    server.memory_add("Eins", "Topic", description="a")
    server.memory_add("Zwei", "Tool", description="b", extra={"x": 1})
    server.memory_add("Drei", "Task", description="c", pinned=True)
    assert server._graph_invariants() == [], "sauberer Graph meldet Verstöße"

    # Korruptes extra → genau dieser eine Verstoß.
    server.memory_add("Kaputt", "Topic", description="x")
    server.db_exec("MATCH (e:Entity {id:'kaputt'}) SET e.extra = '{bad'")
    viols = server._graph_invariants()
    assert len(viols) == 1 and "kaputt" in viols[0] and "extra" in viols[0], \
        f"erwartet 1 extra-Verstoß, bekam: {viols}"

    # Nicht-kanonisches Flag → eigener Verstoß.
    server.db_exec("MATCH (e:Entity {id:'kaputt'}) SET e.extra = '{}'")  # extra reparieren
    server.memory_add("FlagWeird", "Topic", description="x")
    server.db_exec("MATCH (e:Entity {id:'flagweird'}) SET e.archived = 'yes'")
    viols = server._graph_invariants()
    assert any("flagweird" in v and "archived" in v for v in viols), \
        f"nicht-kanonisches archived nicht erkannt: {viols}"

    print("OK")


def test_graph_invariants_detect_corruption():
    r = subprocess.run(
        [sys.executable, __file__],
        capture_output=True, text=True,
        env={**os.environ, "EMBED_ENABLED": "0", "AI_REM_API_TOKEN": "test-token"},
    )
    assert r.returncode == 0, f"Szenario fehlgeschlagen:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    assert "OK" in r.stdout


if __name__ == "__main__":
    _scenario()
