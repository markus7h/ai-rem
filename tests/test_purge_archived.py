"""Tests für _purge_archived / memory_purge_archived.

Bootstrap identisch zu test_project_context: temporäre Kuzu-DB, Embedding aus.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMPDIR = tempfile.mkdtemp(prefix="ai-rem-purge-")
os.environ["LADYBUG_DB_PATH"] = os.path.join(_TMPDIR, "kg.db")
os.environ["EMBED_ENABLED"] = "0"
os.environ.setdefault("AI_REM_API_TOKEN", "test-token")

import server  # noqa: E402


def _archive_aged(name, days_ago):
    """Eintrag anlegen, archivieren, archived_at künstlich in die Vergangenheit setzen."""
    server.memory_add(name, "Topic", description="x")
    server.memory_archive(name)
    old = (server.datetime.now() - server.timedelta(days=days_ago)).isoformat(timespec="seconds")
    eid = server._id(name)
    import json
    row = server._rows(server.db_exec("MATCH (e:Entity {id:$id}) RETURN e.extra", {"id": eid}))
    extra = json.loads(row[0][0] or "{}")
    extra["archived_at"] = old
    server.db_exec("MATCH (e:Entity {id:$id}) SET e.extra=$x, e.updated_at=$t",
                   {"id": eid, "x": json.dumps(extra), "t": old})


def test_keep_days_protects_recent_purges_old():
    _archive_aged("Alt", 100)
    _archive_aged("Jung", 2)
    server.memory_add("Aktiv", "Topic", description="bleibt")  # nicht archiviert

    res = server._purge_archived(keep_days=30)
    assert res["deleted"] == 1 and "Alt" in res["names"]
    assert res["kept"] == 1  # "Jung" bleibt
    # Aktiv ist nie betroffen, Jung noch da
    assert server._rows(server.db_exec("MATCH (e:Entity {id:$i}) RETURN e.id", {"i": server._id("Aktiv")}))
    assert server._rows(server.db_exec("MATCH (e:Entity {id:$i}) RETURN e.id", {"i": server._id("Jung")}))
    assert not server._rows(server.db_exec("MATCH (e:Entity {id:$i}) RETURN e.id", {"i": server._id("Alt")}))


def test_keep_days_zero_purges_all_archived():
    _archive_aged("A2", 1)
    _archive_aged("B2", 50)
    res = server._purge_archived(keep_days=0)
    assert res["deleted"] >= 2 and res["kept"] == 0


def test_tool_message_when_nothing_to_delete():
    out = server.memory_purge_archived(keep_days=99999)
    assert "Nichts zu löschen" in out
