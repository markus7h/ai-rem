"""Tests für Auto-Versionierung (extra.history) und supersedes in memory_add.

A: geänderte descr snapshottet den alten Stand nach extra.history[]; unverändertes
Re-Add fügt nichts hinzu. B: supersedes archiviert den alten Eintrag + VERALTET_DURCH.
In-process gegen Temp-DB, Embedding aus.
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMPDIR = tempfile.mkdtemp(prefix="ai-rem-ver-")
os.environ["LADYBUG_DB_PATH"] = os.path.join(_TMPDIR, "kg.db")
os.environ["EMBED_ENABLED"] = "0"
os.environ.setdefault("AI_REM_API_TOKEN", "test-token")

import server  # noqa: E402


def _extra(name: str) -> dict:
    rows = server._rows(server.db_exec(
        "MATCH (e:Entity {id:$id}) RETURN e.extra", {"id": server._id(name)}))
    return json.loads(rows[0][0] or "{}")


def test_changed_descr_snapshots_history():
    server.memory_add("VerEntity", "Tool", description="Stand 1")
    assert "history" not in _extra("VerEntity")  # Neuanlage → keine History

    server.memory_add("VerEntity", "Tool", description="Stand 2")
    hist = _extra("VerEntity")["history"]
    assert len(hist) == 1
    assert hist[0]["descr"] == "Stand 1"
    assert hist[0]["ts"]

    server.memory_add("VerEntity", "Tool", description="Stand 3")
    hist = _extra("VerEntity")["history"]
    assert len(hist) == 2
    assert hist[0]["descr"] == "Stand 2"  # neueste vorn
    assert hist[1]["descr"] == "Stand 1"


def test_unchanged_descr_no_snapshot():
    server.memory_add("VerNoop", "Tool", description="fix")
    server.memory_add("VerNoop", "Tool", description="fix")  # identisch
    server.memory_add("VerNoop", "Tool", extra={"k": "v"})   # descr=None → kein Snapshot
    assert "history" not in _extra("VerNoop")


def test_history_survives_extra_replace():
    server.memory_add("VerKeep", "Tool", description="alt", extra={"a": 1})
    server.memory_add("VerKeep", "Tool", description="neu", extra={"b": 2})
    ex = _extra("VerKeep")
    assert ex["b"] == 2 and "a" not in ex          # extra ersetzt
    assert ex["history"][0]["descr"] == "alt"      # history trotzdem erhalten


def test_history_capped_at_10():
    for i in range(13):
        server.memory_add("VerCap", "Tool", description=f"v{i}")
    hist = _extra("VerCap")["history"]
    assert len(hist) == 10
    assert hist[0]["descr"] == "v11"  # letzter alter Stand vor v12


def test_supersedes_archives_and_links():
    server.memory_add("AltStand", "Decision", description="wir nutzen X")
    server.memory_add("NeuStand", "Decision", description="wir nutzen Y",
                      supersedes="AltStand")
    arch = server._rows(server.db_exec(
        "MATCH (e:Entity {id:$id}) RETURN e.archived", {"id": server._id("AltStand")}))
    assert arch[0][0] == "true"
    rel = server._rows(server.db_exec(
        "MATCH (a:Entity {id:$a})-[r:Rel {name:'VERALTET_DURCH'}]->(b:Entity {id:$b}) "
        "RETURN r.name",
        {"a": server._id("AltStand"), "b": server._id("NeuStand")}))
    assert rel


def test_supersedes_missing_target_is_noop():
    out = server.memory_add("NeuAllein", "Decision", description="neu",
                            supersedes="GibtEsNicht")
    assert "nicht gefunden" in out
