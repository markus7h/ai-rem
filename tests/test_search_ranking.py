"""Ranking der hybriden Suche (_combined_hits, RRF).

Die alte Lexik-first-Auffuellung ordnete Substring-Treffer nach updated_at und
liess sie ALLE vor dem ersten semantischen Treffer stehen — ein junger Eintrag,
der ein Query-Token zufaellig in der Beschreibung erwaehnt, verdraengte den
eigentlich gemeinten. Diese Tests binden die drei Eigenschaften fest, die das
beheben: Name-Treffer schlagen Beschreibungs-Treffer, Korroboration aus mehreren
Listen schlaegt einen einzelnen jungen Streuner, und semantische Treffer koennen
lexikalische ueberholen.

Laeuft im SUBPROZESS mit eigener Temp-DB (Muster wie test_embed_backend.py).
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _boot():
    tmp = tempfile.mkdtemp(prefix="ai-rem-rank-")
    os.environ["LADYBUG_DB_PATH"] = os.path.join(tmp, "kg.db")
    os.environ["BACKUP_DIR"] = os.path.join(tmp, "backups")
    os.environ["EMBED_ENABLED"] = "0"
    os.environ.setdefault("AI_REM_API_TOKEN", "test-token")
    sys.path.insert(0, ROOT)
    import server
    return server


def _scenario() -> None:
    server = _boot()

    # "mykeyvault" traegt die Query im NAMEN, ist aber aelter als der Streuner,
    # der "manager" nur in der Beschreibung erwaehnt und juenger ist.
    server.memory_add("mykeyvault", "Tool",
                      description="Vault fuer Secrets, der Passwort-Manager des Homelabs")
    server.memory_add("mydocker-man", "Tool",
                      description="Steuerzentrale; der Manager fuer Docker-Stacks")
    server.memory_add("Backup mydocker", "Decision",
                      description="Taegliches Backup nach /mnt/backup")

    # 1) Name-Boost: Volltreffer im Namen schlaegt juengeren Beschreibungs-Treffer.
    hits = server._lexical_hits("mykeyvault", limit=5)
    assert hits and hits[0]["name"] == "mykeyvault", hits

    # 2) RRF-Korroboration: "Passwort Manager" — mykeyvault steht in der
    # Volltext-Liste ("passwort-manager" als Substring? nein) nicht, aber in
    # beiden Token-Listen (passwort, manager); mydocker-man nur in einer.
    hits = server._combined_hits("Passwort Manager", limit=5)
    namen = [h["name"] for h in hits]
    assert namen and namen[0] == "mykeyvault", namen

    # 3) Semantik kann Lexik ueberholen: Paraphrase ohne woertlichen Treffer.
    server._semantic_hits = lambda query, context="", limit=10: [
        {"type": "Decision", "name": "Backup mydocker", "descr": "…",
         "updated_at": "", "context": "", "score": 0.7}]
    hits = server._combined_hits("Datensicherung wiederherstellen", limit=5)
    namen = [h["name"] for h in hits]
    assert "Backup mydocker" in namen[:2], namen
    # Metadaten des Treffers kommen aus der Semantik-Liste, existieren aber
    # vollstaendig (kein KeyError im Formatter).
    treffer = next(h for h in hits if h["name"] == "Backup mydocker")
    assert set(treffer) >= {"type", "name", "descr", "updated_at", "context"}, treffer

    print("OK")


def test_search_ranking():
    r = subprocess.run(
        [sys.executable, __file__],
        capture_output=True, text=True,
        env={**os.environ, "AI_REM_API_TOKEN": "test-token"},
    )
    assert r.returncode == 0, f"Szenario fehlgeschlagen:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    assert "OK" in r.stdout


if __name__ == "__main__":
    _scenario()
