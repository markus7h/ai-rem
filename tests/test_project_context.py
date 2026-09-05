"""Tests für memory_set_project_context (Merge) und memory_project_context (Voll-Abruf).

Importiert server.py gegen eine temporäre Kuzu-DB (LADYBUG_DB_PATH) mit deaktiviertem
Embedding (EMBED_ENABLED=0), damit kein Modell geladen und keine /data-DB berührt wird.
"""
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Muss VOR dem server-Import stehen — DB + Embedding werden beim Import initialisiert.
_TMPDIR = tempfile.mkdtemp(prefix="ai-rem-test-")
os.environ["LADYBUG_DB_PATH"] = os.path.join(_TMPDIR, "kg.db")
os.environ["EMBED_ENABLED"] = "0"
os.environ.setdefault("AI_REM_API_TOKEN", "test-token")

import server  # noqa: E402


def test_set_creates_project_with_schema_fields():
    server.memory_set_project_context(
        "TestProj",
        description="Ein Testprojekt",
        dev_dir="/home/markus/dev/testproj",
        repo="github.com/markus7h/testproj",
        skills=["code-review", "ibcs"],
        rules=["immer aus Repo X deployen"],
    )
    out = server.memory_project_context("TestProj")
    assert "/home/markus/dev/testproj" in out
    assert "code-review" in out
    assert "immer aus Repo X deployen" in out
    assert "[aktiv" in out  # status defaultet auf aktiv


def test_partial_update_does_not_clobber_other_fields():
    """Kernzusicherung: zweites Set mit nur einem Feld plättet die übrigen nicht."""
    server.memory_set_project_context(
        "MergeProj",
        dev_dir="/dev/merge",
        deploy_dir="/var/local/mydocker/merge",
        rules=["regel-eins"],
    )
    # Nur skills nachtragen — dev_dir/deploy_dir/rules müssen erhalten bleiben.
    server.memory_set_project_context("MergeProj", skills=["verify"])

    out = server.memory_project_context("MergeProj")
    assert "/dev/merge" in out
    assert "/var/local/mydocker/merge" in out
    assert "regel-eins" in out
    assert "verify" in out


def test_explicit_empty_list_clears_field():
    server.memory_set_project_context("ClearProj", skills=["a", "b"])
    assert "a" in server.memory_project_context("ClearProj")
    server.memory_set_project_context("ClearProj", skills=[])
    out = server.memory_project_context("ClearProj")
    assert "### Skills" not in out


def test_extra_is_not_truncated():
    long_rule = "X" * 500
    server.memory_set_project_context("LongProj", rules=[long_rule])
    out = server.memory_project_context("LongProj")
    assert long_rule in out  # ungekürzt, anders als get_context/search


def test_related_entities_appear():
    server.memory_set_project_context("RelProj", dev_dir="/dev/rel")
    server.memory_add("RelTask", "Task", description="offene Aufgabe")
    server.memory_relate("RelProj", "HAT_TASK", "RelTask")
    out = server.memory_project_context("RelProj")
    assert "Verknüpfte Entities" in out
    assert "RelTask" in out


def test_fuzzy_lookup_by_partial_name():
    server.memory_set_project_context("KaiGuard Release v9", dev_dir="/dev/kg")
    out = server.memory_project_context("kaiguard")  # Teil-/Kleinschreibung
    assert "/dev/kg" in out


def test_refuses_non_project_entity():
    server.memory_add("SomeTool", "Tool", description="ein Tool")
    res = server.memory_set_project_context("SomeTool", dev_dir="/x")
    assert "nicht als Project" in res


def test_unknown_project_returns_message():
    assert "Kein Projektkontext gefunden" in server.memory_project_context("GibtsNicht XYZ")


def test_mcp_roundtrip_emits_writable_block():
    cfg = {"mcpServers": {"foo": {"url": "https://x", "type": "http"}}}
    server.memory_set_project_context("McpProj", dev_dir="/dev/mcp", mcp=cfg)
    out = server.memory_project_context("McpProj")
    assert "/dev/mcp/.mcp.json" in out      # Zielpfad aus dev_dir
    assert "neu starten" in out.lower()     # Neustart-Hinweis
    assert '"mcpServers"' in out            # schreibbarer Inhalt
    # leeren entfernt den Block wieder
    server.memory_set_project_context("McpProj", mcp={})
    assert "MCP-Setup" not in server.memory_project_context("McpProj")
