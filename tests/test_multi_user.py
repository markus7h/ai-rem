"""Mehrbenutzer: Task + Preference gehören ihrem owner, Wissen bleibt geteilt.
Ein zweiter Nutzer kommt per Kopplung (Admin gibt für "anna" frei) und sieht nur
seine eigenen Tasks/Preferences. Läuft im SUBPROZESS mit eigener Temp-DB."""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _scenario() -> None:
    tmp = tempfile.mkdtemp(prefix="ai-rem-mu-")
    os.environ["LADYBUG_DB_PATH"] = os.path.join(tmp, "kg.db")
    os.environ["BACKUP_DIR"] = os.path.join(tmp, "backups")
    os.environ["EMBED_ENABLED"] = "0"
    os.environ["AI_REM_API_TOKEN"] = "admin-token"
    os.environ["AI_REM_ADMIN_USER"] = "markus"
    sys.path.insert(0, ROOT)

    import server
    from starlette.testclient import TestClient

    app = server.AuthMiddleware(server.mcp.http_app())
    xff = {"X-Forwarded-For": "10.0.0.9"}  # kein Loopback-Bypass
    admin = TestClient(app, base_url="https://testserver",
                       headers={**xff, "Authorization": "Bearer admin-token"})
    anon = TestClient(app, base_url="https://testserver", headers=xff)

    # anna koppeln: nur der Admin darf für einen anderen Nutzer freigeben
    s = anon.post("/api/pair/start", json={"device": "annas-laptop"}).json()
    assert admin.post("/api/pair/approve",
                      json={"code": s["code"], "approve": True, "user": "anna"}).status_code == 200
    anna_tok = anon.post("/api/pair/poll", json={"pair_id": s["pair_id"]}).json()["ai_rem_token"]
    assert server._user_for_token(anna_tok) == "anna"
    anna = TestClient(app, base_url="https://testserver",
                      headers={**xff, "Authorization": f"Bearer {anna_tok}"})

    def tool(c, _tool, **args):
        r = c.post("/api/tool", json={"name": _tool, "arguments": args})
        assert r.status_code == 200, r.text
        return r.json()["result"]

    tool(admin, "memory_add", name="Gemeinsames Projekt", type="Project", description="geteilt")
    tool(admin, "memory_add", name="Markus Steuer", type="Task", description="Steuererklärung")
    tool(admin, "memory_add", name="Markus Stil", type="Preference", description="kurz antworten")
    tool(anna, "memory_add", name="Anna Einkauf", type="Task", description="Milch holen")
    tool(anna, "memory_relate", from_name="Anna Einkauf", relation="TEIL_VON",
         to_name="Gemeinsames Projekt")

    ctx_m = tool(admin, "memory_get_context")
    ctx_a = tool(anna, "memory_get_context")
    assert "Gemeinsames Projekt" in ctx_m and "Gemeinsames Projekt" in ctx_a
    assert "Markus Steuer" in ctx_m and "Markus Steuer" not in ctx_a
    assert "kurz antworten" in ctx_m and "kurz antworten" not in ctx_a
    assert "Anna Einkauf" not in ctx_m

    assert "Markus Steuer" not in tool(anna, "memory_search", query="Steuer")
    assert "Anna Einkauf" not in tool(admin, "memory_search", query="Milch")
    assert "Anna Einkauf" in tool(anna, "memory_search", query="Milch")
    assert "Anna Einkauf" not in tool(admin, "memory_get_relations", name="Gemeinsames Projekt")
    assert "Anna Einkauf" in tool(anna, "memory_get_relations", name="Gemeinsames Projekt")
    assert [t["name"] for t in anna.get("/api/tasks").json()] == ["Anna Einkauf"]
    assert [p["name"] for p in anna.get("/api/preferences").json()] == []

    # Fremde Einträge: weder überschreiben noch löschen noch verlinken
    assert "anderen Nutzer" in tool(anna, "memory_add", name="Markus Steuer", type="Task",
                                    description="gekapert")
    assert "anderen Nutzer" in tool(anna, "memory_delete", name="Markus Steuer")
    assert "anderen Nutzer" in tool(anna, "memory_relate", from_name="Markus Steuer",
                                    relation="TEIL_VON", to_name="Gemeinsames Projekt")
    assert "Steuererklärung" in tool(admin, "memory_search_full", query="Markus Steuer")

    # Admin-Bereiche
    assert anna.get("/export").status_code == 403
    assert anna.post("/api/backup/now").status_code == 403
    assert "Nur für den Admin" in tool(anna, "memory_user_list")
    assert anna.get("/api/client-config").json()["vault_token"] == ""
    assert admin.get("/export").status_code == 200
    assert "anna" in tool(admin, "memory_user_list")

    # Cleanup faltet nie Einträge zweier Nutzer zusammen
    tool(anna, "memory_add", name="Steuer Markus", type="Task", description="annas eigene")
    merges = server._cleanup_candidates()["auto_merge"]
    assert not any({"Markus Steuer", "Steuer Markus"} == set(m) for m in merges), merges

    # Backup/Restore behält den owner
    dump = server._dump_graph()
    owners = {e["name"]: e["owner"] for e in dump["entities"]}
    assert owners["Anna Einkauf"] == "anna" and owners["Markus Steuer"] == "markus"
    assert owners["Gemeinsames Projekt"] == ""

    # Revoke: Token und UI-Cookie sofort ungültig, Daten bleiben
    tool(admin, "memory_token_revoke", user="anna")
    assert anna.post("/api/tool", json={"name": "memory_get_context", "arguments": {}}).status_code == 401
    assert "Anna Einkauf" in server.memory_search("Milch")
    print("OK")


def test_multi_user():
    r = subprocess.run([sys.executable, __file__], capture_output=True, text=True,
                       env={**os.environ, "EMBED_ENABLED": "0"})
    assert r.returncode == 0, f"Szenario fehlgeschlagen:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    assert "OK" in r.stdout


if __name__ == "__main__":
    _scenario()
