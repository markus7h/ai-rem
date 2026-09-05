"""Tests für den Graph-Export-Vertrag, auf dem die /graph-Filter aufsetzen.

Die Kontext-/Typ-/Archiv-Filter in der /graph-UI sind reines Client-JS und lesen
`fetch('/export')` → `_dump_graph()`. Sie filtern pro Entity auf `type`, `context`
('' = global) und `archived` ('true' = archiviert). Diese Tests sichern genau diesen
Daten-Vertrag ab — bricht er, filtert die UI still falsch, ohne Fehler.

Bootstrap identisch zu test_purge_archived: temporäre Kuzu-DB, Embedding aus.
"""
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMPDIR = tempfile.mkdtemp(prefix="ai-rem-graph-")
os.environ["LADYBUG_DB_PATH"] = os.path.join(_TMPDIR, "kg.db")
os.environ["EMBED_ENABLED"] = "0"
os.environ.setdefault("AI_REM_API_TOKEN", "test-token")

import server  # noqa: E402

# Die Suite teilt EINE globale Kuzu-DB über alle Testdateien (erster server-Import
# gewinnt LADYBUG_DB_PATH). Unsere archivierte GArchived-Entity würde sonst in den
# später laufenden Purge-Test hineinlecken → hier deterministisch wieder aufräumen.
_CREATED = ["GFields", "GWork", "GPriv", "GGlobal", "GActive", "GArchived",
            "GPerson", "GTask", "GTool"]


@pytest.fixture(autouse=True, scope="module")
def _cleanup():
    yield
    for name in _CREATED:
        server.memory_delete(name)


def _entity(dump, name):
    return next(e for e in dump["entities"] if e["name"] == name)


def test_dump_exposes_filter_fields():
    server.memory_add("GFields", "Topic", description="x")
    dump = server._dump_graph()
    for e in dump["entities"]:
        assert "type" in e and "context" in e and "archived" in e


def test_context_values_match_filter_contract():
    server.memory_add("GWork", "Topic", description="x", context="work")
    server.memory_add("GPriv", "Topic", description="x", context="private")
    server.memory_add("GGlobal", "Topic", description="x")  # ohne context → global
    dump = server._dump_graph()
    assert _entity(dump, "GWork")["context"] == "work"
    assert _entity(dump, "GPriv")["context"] == "private"
    # JS-Kriterium für '__global': context !== '' filtert raus → global heißt ''
    assert _entity(dump, "GGlobal")["context"] == ""


def test_archived_flag_is_true_string():
    server.memory_add("GActive", "Topic", description="bleibt")
    server.memory_add("GArchived", "Topic", description="weg")
    server.memory_archive("GArchived")
    dump = server._dump_graph()
    # exakt das, worauf `e.archived === 'true'` im /graph-JS prüft
    assert _entity(dump, "GArchived")["archived"] == "true"
    assert _entity(dump, "GActive")["archived"] == ""


def test_type_present_for_legend_and_hide():
    server.memory_add("GPerson", "Person", description="x")
    server.memory_add("GTask", "Task", description="x")
    server.memory_add("GTool", "Tool", description="x")
    types = {e["type"] for e in server._dump_graph()["entities"]}
    assert {"Person", "Task", "Tool"} <= types
