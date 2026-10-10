"""Tests für die automatische Task→Project-Zuordnung und die Abschluss-Heuristik.

Früher setzte niemand TEIL_VON: Extractor und Clients legten Tasks ohne Projekt-Kante
an, get_context zeigte sie fast alle unter "_ohne Projekt_". Dazu zählten Tasks als
offen, deren Text längst "PR #225 gemergt" meldete.

In-process gegen Temp-DB, Embedding aus. Entity-Namen mit TPL_ geprefixt — alle
Testmodule teilen sich die DB des zuerst importierenden Moduls.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMPDIR = tempfile.mkdtemp(prefix="ai-rem-tpl-")
os.environ["LADYBUG_DB_PATH"] = os.path.join(_TMPDIR, "kg.db")
os.environ["EMBED_ENABLED"] = "0"
os.environ.setdefault("AI_REM_API_TOKEN", "test-token")

import server  # noqa: E402
from lib.extractor import is_done_report, upsert_entity  # noqa: E402


def _projekt_von(task: str) -> set:
    return {r[0] for r in server._rows(server.db_exec(
        "MATCH (t:Entity {id: $id})-[:Rel {name: 'TEIL_VON'}]->(p:Entity) RETURN p.name",
        {"id": server._id(task)}))}


def test_keyword_treffer_verknuepft_beim_anlegen():
    server.memory_add("TPL_Seeprojekt", "Project", extra={"keywords": ["tplsee", "tpl-diabi"]})
    msg = server.memory_add("TPL_Task1", "Task", description="tpl-diabi Rollen prüfen")
    assert "TEIL_VON TPL_Seeprojekt" in msg
    assert _projekt_von("TPL_Task1") == {"TPL_Seeprojekt"}


def test_ohne_keywords_kein_namens_fallback():
    server.memory_add("TPL_Kaiwache", "Project")
    server.memory_add("TPL_Task2", "Task", description="Container von tpl_kaiwache updaten")
    assert _projekt_von("TPL_Task2") == set()


def test_mehrdeutig_bleibt_ohne_projekt():
    server.memory_add("TPL_ProjA", "Project", extra={"keywords": ["tplmehrdeutig"]})
    server.memory_add("TPL_ProjB", "Project", extra={"keywords": ["tplmehrdeutig"]})
    server.memory_add("TPL_Task3", "Task", description="tplmehrdeutig klären")
    assert _projekt_von("TPL_Task3") == set()


def test_wortgrenze_kein_teilwort():
    server.memory_add("TPL_Kurz", "Project", extra={"keywords": ["tplrem"]})
    server.memory_add("TPL_Task4", "Task", description="tplremote prüfen")
    assert _projekt_von("TPL_Task4") == set()


def test_erledigtes_projekt_wird_ignoriert():
    server.memory_add("TPL_Alt", "Project", extra={"keywords": ["tplalt"], "status": "abgeschlossen"})
    server.memory_add("TPL_Task5", "Task", description="tplalt nachziehen")
    assert _projekt_von("TPL_Task5") == set()


def test_bestehende_projektkante_bleibt():
    server.memory_add("TPL_Eigen", "Project", extra={"keywords": ["tpleigen"]})
    server.memory_add("TPL_Fremd", "Project", extra={"keywords": ["tplfremd"]})
    server.memory_add("TPL_Task6", "Task", description="vorher")
    server.memory_relate("TPL_Task6", "TEIL_VON", "TPL_Eigen")
    server.memory_add("TPL_Task6", "Task", description="jetzt tplfremd")
    assert _projekt_von("TPL_Task6") == {"TPL_Eigen"}


def test_backfill_trockenlauf_und_ausfuehren():
    server.memory_add("TPL_Backfill", "Project", extra={"keywords": ["tpl_backfill"]})
    # Projekt existiert erst nach dem Task → beim Anlegen kein Treffer.
    server.db_exec("MATCH (p:Entity {id: $id}) SET p.archived = 'true'",
                   {"id": server._id("TPL_Backfill")})
    server.memory_add("TPL_Task7", "Task", description="tpl_backfill aufräumen")
    server.db_exec("MATCH (p:Entity {id: $id}) SET p.archived = ''",
                   {"id": server._id("TPL_Backfill")})
    assert _projekt_von("TPL_Task7") == set()

    trocken = server.memory_link_projects(dry_run=True)
    assert "TPL_Task7 → TPL_Backfill" in trocken
    assert _projekt_von("TPL_Task7") == set()

    server.memory_link_projects(dry_run=False)
    assert _projekt_von("TPL_Task7") == {"TPL_Backfill"}


def test_abschluss_im_fliesstext_geht_in_review():
    server.memory_add("TPL_Gemergt", "Task", description="PR #225 gemergt und Branch gelöscht")
    server.memory_add("TPL_Geprueft", "Task", description="Der Server wurde geprüft, alles ok.")
    server.memory_add("TPL_Echt", "Task", description="Deploy muss noch durchgeführt werden.")
    ziele = {a["target"] for a in server._cleanup_candidates()["archive_review"]}
    assert {"TPL_Gemergt", "TPL_Geprueft"} <= ziele
    assert "TPL_Echt" not in ziele


def test_lange_unberuehrter_task_geht_in_review():
    server.memory_add("TPL_Liegt", "Task", description="irgendwann mal machen")
    alt = (datetime.now() - timedelta(days=server.CLEANUP_TASK_STALE_DAYS + 1)
           ).strftime("%Y-%m-%dT%H:%M:%S")
    server.db_exec("MATCH (e:Entity {id: $id}) SET e.updated_at = $ts",
                   {"id": server._id("TPL_Liegt"), "ts": alt})
    review = {a["target"]: a["reason"] for a in server._cleanup_candidates()["archive_review"]}
    assert "unverändert" in review.get("TPL_Liegt", "")
    server._mark_extra("TPL_Liegt", "done_marker_dismissed", server._now())
    assert "TPL_Liegt" not in {a["target"] for a in server._cleanup_candidates()["archive_review"]}


class _FakeClient:
    def __init__(self, search_hit: str = ""):
        self.search_hit = search_hit
        self.adds: list = []

    def call(self, tool, args):
        if tool == "memory_search":
            return self.search_hit
        self.adds.append(args)
        return f"Angelegt: [{args['type']}] {args['name']}"


def test_extractor_skippt_neue_abschlussmeldung():
    c = _FakeClient()
    out = upsert_entity(c, {"type": "Task", "name": "MCP-Server-Prüfung",
                            "description": "Der MCP-Server wurde geprüft."})
    assert out.startswith("[skip]") and not c.adds


def test_extractor_schliesst_bekannten_task():
    c = _FakeClient(search_hit="- **Deploy auf mystorage**: …")
    upsert_entity(c, {"type": "Task", "name": "Deploy auf mystorage",
                      "description": "Deploy auf mystorage wurde durchgeführt."})
    assert c.adds[0]["extra"] == {"status": "erledigt"}


def test_extractor_laesst_offene_arbeit_durch():
    assert not is_done_report("Task", "Login im Browser bestätigen")
    assert not is_done_report("Task", "Der Deploy muss manuell durchgeführt werden")
    assert not is_done_report("Decision", "PR #12 gemergt")
