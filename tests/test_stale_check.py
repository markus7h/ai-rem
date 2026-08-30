"""Veraltungs-Check des Nightly-Cleanups.

_stale_candidates() schlägt Infra-Einträge zur Realitäts-Prüfung vor: verderbliche
Fakten im descr (IP/Port/Version) + Prüf-Alter über der Schwelle. Läuft im SUBPROZESS
mit eigener Temp-DB, weil der pytest-Modulcache server.py sonst zwischen Tests teilt.
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _scenario() -> None:
    tmp = tempfile.mkdtemp(prefix="ai-rem-stale-")
    os.environ["KUZU_DB_PATH"] = os.path.join(tmp, "kg.db")
    os.environ["BACKUP_DIR"] = os.path.join(tmp, "backups")
    os.environ["EMBED_ENABLED"] = "0"
    os.environ.setdefault("AI_REM_API_TOKEN", "test-token")
    sys.path.insert(0, ROOT)

    import server

    alt = "2020-01-01T00:00:00"  # weit jenseits von CLEANUP_VERIFY_AFTER_DAYS

    def age(name: str) -> None:
        server.db_exec("MATCH (e:Entity {id:$id}) SET e.updated_at = $ts",
                       {"id": server._id(name), "ts": alt})

    server.memory_add("NAS", "Tool", description="Fileserver auf 192.168.1.15, Port 445")
    server.memory_add("Konvention", "Topic", description="Branch-Namen immer klein schreiben")
    server.memory_add("Router", "Tool", description="OpenWrt auf 192.168.1.1",
                      extra={"verify_checked": server._now()})
    server.memory_add("Fix", "Solution", description="VERALTET: lief mal auf 192.168.1.99")
    server.memory_add("Gepinnt", "Tool", description="Dienst auf 10.0.0.5:8080", pinned=True)
    server.memory_add("Regel", "Preference", description="Immer 192.168.1.15 nutzen")
    for n in ("NAS", "Konvention", "Router", "Fix", "Gepinnt", "Regel"):
        age(n)

    names = {c["name"] for c in server._cleanup_candidates()["verify"]}
    assert "NAS" in names, f"Infra-Eintrag mit IP nicht erkannt: {names}"
    assert "Konvention" not in names, "Eintrag ohne verderbliche Fakten gemeldet"
    assert "Router" not in names, "frischer verify_checked-Cooldown ignoriert"
    assert "Fix" not in names, "bereits als VERALTET markierter Eintrag erneut gemeldet"
    assert "Gepinnt" not in names, "gepinnter Eintrag gemeldet"
    assert "Regel" not in names, "Preference gemeldet"

    # Bestands-Konvention (geprueft_am) zählt als Prüfung, updated_at bleibt alt.
    server.memory_add("Switch", "Tool", description="10G-Uplink auf 192.168.1.2",
                      extra={"geprueft_am": server._now()[:10]})
    age("Switch")
    assert "Switch" not in {c["name"] for c in server._cleanup_candidates()["verify"]}, \
        "geprueft_am aus dem Bestand wird nicht respektiert"

    # Markierung setzt den Cooldown, ohne updated_at zu verschieben.
    before = server._rows(server.db_exec(
        "MATCH (e:Entity {id:'nas'}) RETURN e.updated_at"))[0][0]
    assert server._mark_verify_checked("NAS", server._now()) is True
    after, extra = server._rows(server.db_exec(
        "MATCH (e:Entity {id:'nas'}) RETURN e.updated_at, e.extra"))[0]
    assert after == before, f"updated_at verschoben: {before} -> {after}"
    assert "verify_checked" in extra, f"verify_checked nicht gesetzt: {extra}"
    assert "NAS" not in {c["name"] for c in server._cleanup_candidates()["verify"]}, \
        "Cooldown greift nach der Markierung nicht"
    assert server._mark_verify_checked("GibtsNicht", server._now()) is False

    # Queue-Abarbeitung: 'Verwerfen' setzt den Cooldown genauso wie 'Passt noch',
    # sonst stünde der Eintrag in der nächsten Nacht wieder da.
    server.memory_add("AP", "Tool", description="Access Point auf 192.168.1.30")
    age("AP")
    assert server._add_pending([{"kind": "verify", "target": "AP", "reason": "test"}]) == 1
    pid = server._load_pending()[0]["id"]
    assert "AP" not in {c["name"] for c in server._cleanup_candidates()["verify"]}, \
        "Eintrag mit offenem Queue-Item erneut vorgeschlagen"
    res = server._resolve_pending_action(pid, "dismiss")
    assert res["status"] == "ok" and server._load_pending() == []
    assert server._rows(server.db_exec("MATCH (e:Entity {id:'ap'}) RETURN e.archived"))[0][0] != "true", \
        "verify-Item wurde archiviert"
    assert "AP" not in {c["name"] for c in server._cleanup_candidates()["verify"]}, \
        "Verwerfen setzt den Cooldown nicht"

    print("OK")


def test_stale_candidates():
    r = subprocess.run(
        [sys.executable, __file__],
        capture_output=True, text=True,
        env={**os.environ, "EMBED_ENABLED": "0", "AI_REM_API_TOKEN": "test-token"},
    )
    assert r.returncode == 0, f"Szenario fehlgeschlagen:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    assert "OK" in r.stdout


if __name__ == "__main__":
    _scenario()
