"""OKF-Import: Round-Trip (export → import) und Body-Parser.

Bootstrap wie die übrigen Tests: temporäre Kuzu-DB, Embedding aus.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMPDIR = tempfile.mkdtemp(prefix="ai-rem-okfimp-")
os.environ["KUZU_DB_PATH"] = os.path.join(_TMPDIR, "kg.db")
os.environ["EMBED_ENABLED"] = "0"
os.environ.setdefault("AI_REM_API_TOKEN", "test-token")

import server  # noqa: E402


def test_body_parser_extracts_descr_extra_rels():
    body = ("\n# Imp_Alice\n\nQA tester\nzweite Zeile\n\n"
            "## Relationen\n- → ARBEITS_AN [Imp_Proj](/project/proj.md)\n"
            "- ← KENNT [Imp_Bob](/person/bob.md)\n\n"
            "## Extra\n```json\n{\"role\": \"qa\"}\n```\n")
    descr, extra, rels = server._parse_okf_body(body)
    assert descr == "QA tester\nzweite Zeile"
    assert extra == {"role": "qa"}
    assert rels == [("ARBEITS_AN", "/project/proj.md")]  # nur ausgehend (→)


def test_roundtrip_export_import_replace():
    server.memory_add("Imp_Alice", "Person", description="QA tester\nzweite Zeile",
                      extra={"role": "qa"}, context="work")
    server.memory_add("Imp_Proj", "Project", description="ein Projekt")
    server.memory_relate("Imp_Alice", "ARBEITS_AN", "Imp_Proj")

    bundle = server._okf_bundle()
    res = server._okf_import(bundle, mode="replace")

    # >= statt == : Tests teilen sich eine DB, das Bundle enthält den ganzen Graph.
    assert res["entities_created"] >= 2
    assert res["relations_created"] >= 1
    assert res["concepts_parsed"] >= 2

    # Inhalte korrekt rekonstruiert
    eid = server._id("Imp_Alice")
    row = server._rows(server.db_exec(
        "MATCH (e:Entity {id:$i}) RETURN e.type, e.descr, e.context, e.extra", {"i": eid}))
    assert row, "Imp_Alice nicht importiert"
    typ, descr, ctx, extra = row[0]
    assert typ == "Person"
    assert "QA tester" in descr
    assert ctx == "work"
    assert "qa" in (extra or "")

    rel = server._rows(server.db_exec(
        "MATCH (a:Entity {id:$a})-[r:Rel]->(b:Entity {id:$b}) RETURN r.name",
        {"a": server._id("Imp_Alice"), "b": server._id("Imp_Proj")}))
    assert rel and rel[0][0] == "ARBEITS_AN"
    # Round-Trip: eigener Export (source: ai-rem) → NICHT als importiert getaggt
    assert "imported" not in (extra or "")


def test_foreign_bundle_is_tagged_imported():
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("topic/extern.md",
                   "---\ntype: Topic\ntitle: ExternKonzept\n---\n\n# ExternKonzept\n\nvon außerhalb\n")
    server._okf_import(buf.getvalue(), mode="merge")
    row = server._rows(server.db_exec(
        "MATCH (e:Entity {id:$i}) RETURN e.extra", {"i": server._id("ExternKonzept")}))
    assert row and "imported" in (row[0][0] or "")  # Fremd-Eintrag bekommt Marker
