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


def test_project_bleibt_project_bei_anderem_typ():
    server.memory_add("TPL_Typfest", "Project", extra={"keywords": ["tpltypfest"]})
    msg = server.memory_add("TPL_Typfest", "Tool", description="vom Extractor als Tool erkannt")
    assert "Typ bleibt Project" in msg
    rows = server._rows(server.db_exec("MATCH (e:Entity {id: $id}) RETURN e.type",
                                       {"id": server._id("TPL_Typfest")}))
    assert rows[0][0] == "Project"


def test_tool_darf_project_werden():
    server.memory_add("TPL_Aufstieg", "Tool")
    msg = server.memory_add("TPL_Aufstieg", "Project")
    assert "Typ bleibt" not in msg and "[Project]" in msg


def test_extractor_erkennt_buero_abschluesse():
    assert is_done_report("Task", "Die Anfrage wurde an René Stegmann gesendet")
    assert is_done_report("Task", "23 Termine sind in den Kalender eingetragen")
    assert server._done_hint("Rechnung wurde bezahlt")
    assert not is_done_report("Task", "Termine in den Kalender eintragen")


def test_extractor_prompt_zeigt_neueste_tasks():
    from lib.extractor import _build_system_prompt, _fetch_recent_tasks

    class _Client:
        def call(self, tool, args):
            assert args == {"type": "Task"}
            return ("- **A alt**: x  _(2026-01-01)_\n"
                    "- **Behandlungsplan eintragen**: y  _(2026-10-13)_\n"
                    "- **Z mitte**: z  _(2026-05-01)_")

    tasks = _fetch_recent_tasks(_Client())
    assert tasks == ["Behandlungsplan eintragen", "Z mitte", "A alt"]
    prompt = _build_system_prompt(["Behandlungsplan eintragen", "Zebra"], [], tasks)
    assert "Bekannte Tasks (neueste zuerst):\n- Behandlungsplan eintragen" in prompt
    # nicht doppelt in der allgemeinen Namensliste
    assert prompt.count("- Behandlungsplan eintragen") == 1


# ── Abschluss-Pfad und Session-Herkunft im Extractor ────────────────────────

class _Srv:
    """Fake-Client, der die echten Server-Funktionen in-process aufruft."""

    def __init__(self):
        self.calls: list = []

    def call(self, tool, args):
        self.calls.append((tool, args))
        return server._ALL_TOOL_FUNCS[tool](**args)


def _status(name: str) -> str:
    import json
    rows = server._rows(server.db_exec("MATCH (e:Entity {id: $id}) RETURN e.extra, e.type",
                                       {"id": server._id(name)}))
    return json.loads(rows[0][0] or "{}").get("status", "")


def test_abschluss_als_decision_schliesst_bekannten_task():
    server.memory_add("TPL_Angebot an Kunde", "Task", description="Angebot schicken")
    c = _Srv()
    out = upsert_entity(c, {"type": "Decision", "name": "TPL_Angebot an Kunde",
                            "description": "Angebot an Kunde verschickt am 12.10."})
    assert out.startswith("[done]")
    assert _status("TPL_Angebot an Kunde") == "erledigt"
    rows = server._rows(server.db_exec("MATCH (e:Entity {id: $id}) RETURN e.type",
                                       {"id": server._id("TPL_Angebot an Kunde")}))
    assert rows[0][0] == "Task"


def test_abschluss_unter_anderer_schreibweise_schliesst_gespeicherten_namen():
    server.memory_add("TPL_Mail an Bob", "Task")
    c = _Srv()
    upsert_entity(c, {"type": "Task", "name": "tpl_mail  an bob", "description": "Mail an Bob gesendet."})
    assert c.calls[-1][1]["name"] == "TPL_Mail an Bob"
    assert _status("TPL_Mail an Bob") == "erledigt"


def test_teilstring_ist_kein_treffer():
    # "Deploy" kam frueher als Treffer fuer "Deploy auf mystorage" durch.
    c = _FakeClient(search_hit="[Task] **TPL Deploy auf mystorage** : …")
    out = upsert_entity(c, {"type": "Task", "name": "TPL Deploy",
                            "description": "Deploy wurde durchgeführt."})
    assert out.startswith("[skip]") and not c.adds


def test_verneinung_schliesst_nicht():
    for t in ("Rechnung ist noch nicht bezahlt", "Versand steht noch aus",
              "Freigabe ausstehend",
              "Mail an Bob ist nicht versendet", "Mail an Bob noch nicht gesendet."):
        assert not is_done_report("Task", t), t
    assert not server._done_hint("Rechnung ist noch nicht bezahlt")
    c = _FakeClient(search_hit="[Task] **TPL Rechnung** : …")
    upsert_entity(c, {"type": "Task", "name": "TPL Rechnung",
                      "description": "Rechnung ist noch nicht bezahlt"})
    assert "extra" not in c.adds[0]


def test_nacktes_partizip_zaehlt():
    for t in ("Mail an Bob gesendet.", "Mail versendet", "Angebot verschickt am 12.10."):
        assert is_done_report("Task", t), t
        assert server._done_hint(t), t
    assert not is_done_report("Task", "Mail wird versendet.")
    assert not is_done_report("Task", "Deploy muss noch durchgeführt werden.")


def test_herkunft_erzwingt_kontext_und_projekt():
    server.memory_add("TPL_herkunft", "Project", description="Repo-Projekt")
    origin = {"project": "TPL_herkunft", "context": "private", "repo": "markus7h/tpl_herkunft"}
    c = _Srv()
    tasks: set = set()
    upsert_entity(c, {"type": "Task", "name": "TPL_Neuer Task", "description": "Doku ergänzen",
                      "context": "work"}, origin, tasks)
    rows = server._rows(server.db_exec("MATCH (e:Entity {id: $id}) RETURN e.context",
                                       {"id": server._id("TPL_Neuer Task")}))
    assert rows[0][0] == "private"
    assert _projekt_von("TPL_Neuer Task") == {"TPL_herkunft"}
    # TEIL_VON vom LLM wird verworfen, das Projekt steht durch die Herkunft fest.
    from lib.extractor import upsert_relation
    out = upsert_relation(c, {"from_name": "TPL_Neuer Task", "relation": "TEIL_VON",
                              "to_name": "TPL_Pseudo #244"}, origin, tasks)
    assert out.startswith("[skip]")
    # Preference behaelt den LLM-Kontext.
    upsert_entity(c, {"type": "Preference", "name": "TPL_Pref", "description": "x",
                      "context": "work"}, origin, tasks)
    rows = server._rows(server.db_exec("MATCH (e:Entity {id: $id}) RETURN e.context",
                                       {"id": server._id("TPL_Pref")}))
    assert rows[0][0] == "work"


def test_herkunft_haengt_fremden_task_nicht_um():
    server.memory_add("TPL_Fremdprojekt", "Project")
    server.memory_add("TPL_Herkunft2", "Project")
    server.memory_add("TPL_Alter Task", "Task", description="läuft")
    server.memory_relate("TPL_Alter Task", "TEIL_VON", "TPL_Fremdprojekt")
    upsert_entity(_Srv(), {"type": "Task", "name": "TPL_Alter Task", "description": "läuft weiter"},
                  {"project": "TPL_Herkunft2", "context": "private"}, set())
    assert _projekt_von("TPL_Alter Task") == {"TPL_Fremdprojekt"}


def test_llm_projekte_werden_weder_angelegt_noch_ueberschrieben():
    server.memory_add("TPL_Kueche", "Project", description="Original")
    c = _Srv()
    out = upsert_entity(c, {"type": "Project", "name": "TPL_Kueche", "description": "Küche renovieren"})
    assert out.startswith("[skip]") and not any(t == "memory_add" for t, _ in c.calls)
    out = upsert_entity(c, {"type": "Project", "name": "TPL_Release v0.23.15", "description": "x"})
    assert out.startswith("[skip]")
    assert not server._rows(server.db_exec("MATCH (e:Entity {id: $id}) RETURN e.name",
                                           {"id": server._id("TPL_Release v0.23.15")}))
    rows = server._rows(server.db_exec("MATCH (e:Entity {id: $id}) RETURN e.descr",
                                       {"id": server._id("TPL_Kueche")}))
    assert rows[0][0] == "Original"


def test_prompt_zeigt_status_und_session():
    from lib.extractor import _build_system_prompt, _fetch_recent_tasks

    class _Client:
        def call(self, tool, args):
            return ("- **Mail an Bob** [erledigt]: x  _(2026-10-13)_\n"
                    "- **Offen** [offen]: y  _(2026-10-12)_")

    tasks = _fetch_recent_tasks(_Client())
    assert tasks == ["Mail an Bob [erledigt]", "Offen [offen]"]
    prompt = _build_system_prompt(["Mail an Bob", "Zebra"], [], tasks,
                                  {"project": "ai-rem", "context": "private"})
    assert "Session: Projekt ai-rem, Kontext private" in prompt
    assert "- Mail an Bob [erledigt]" in prompt
    assert prompt.count("Mail an Bob") == 1


def test_ingest_nutzt_herkunft(tmp_path, monkeypatch):
    """Ende-zu-Ende ohne LLM: cwd aus dem Transcript → Projekt angelegt, Task verknuepft."""
    import subprocess
    from pathlib import Path

    from lib import extractor

    repo = tmp_path / "tpl-ingestrepo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin",
                    "git@github-personal:markus7h/tpl-ingestrepo.git"], check=True)
    tr = tmp_path / "s.jsonl"
    tr.write_text("\n".join(
        '{"type":"user","cwd":"%s","message":{"content":"%s"}}' % (repo, "x" * 300)
        for _ in range(3)) + "\n")

    monkeypatch.setattr(extractor, "configure_llm", lambda c: {})
    monkeypatch.setattr(extractor, "LOCK_FILE", tmp_path / "lock")
    monkeypatch.setattr(extractor, "LAST_RUN", tmp_path / "last-run.json")
    monkeypatch.setattr(extractor, "call_llm", lambda *a, **k: {
        "entities": [{"type": "Task", "name": "TPL_Ingest-Task", "description": "README schreiben",
                      "context": "work"},
                     {"type": "Project", "name": "TPL_Pseudo-Release", "description": "x"}],
        "relations": [{"from_name": "TPL_Ingest-Task", "relation": "TEIL_VON",
                       "to_name": "TPL_Pseudo-Release"}]})

    class _C(_Srv):
        def client_config(self):
            return {}

    res = extractor.ingest_transcript(_C(), Path(tr), log_dir=tmp_path)
    assert any(x.startswith("[origin]") for x in res["applied"])
    assert _projekt_von("TPL_Ingest-Task") == {"tpl-ingestrepo"}
    rows = server._rows(server.db_exec("MATCH (e:Entity {id: $id}) RETURN e.context, e.extra",
                                       {"id": server._id("tpl-ingestrepo")}))
    assert rows[0][0] == "private" and "markus7h/tpl-ingestrepo" in rows[0][1]
    assert not server._rows(server.db_exec("MATCH (e:Entity {id: $id}) RETURN e.name",
                                           {"id": server._id("TPL_Pseudo-Release")}))


def test_offene_herkunft_setzt_keinen_geratenen_kontext():
    # Rückfrage steht noch aus (ask): kein LLM-Kontext, lieber global als falsch.
    origin = {"project": "", "context": "", "ask": True}
    c = _FakeClient()
    upsert_entity(c, {"type": "Task", "name": "TPL_Ask Task", "description": "Issue umsetzen",
                      "context": "work"}, origin, set())
    assert c.adds and "context" not in c.adds[-1]
    # Ohne Herkunft (alter Hook) bleibt der LLM-Kontext wie bisher.
    c = _FakeClient()
    upsert_entity(c, {"type": "Task", "name": "TPL_Alt Task", "description": "Issue umsetzen",
                      "context": "work"})
    assert c.adds[-1].get("context") == "work"
