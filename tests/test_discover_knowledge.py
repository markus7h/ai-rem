"""Knowledge-Auswahl von /discover: Praezision vor Recall.

/discover injiziert unaufgefordert in jeden Prompt — falsche Treffer kosten
Kontext-Tokens und lenken ab. Drei Eigenschaften werden festgebunden:
Alltagswoerter ziehen keine Substring-Streuner mehr ('lassen' traf
'weggelassene Felder'), inhaltsleere Prompts injizieren NICHTS, und
Ein-Wort-Prompts mit exaktem Namen finden weiterhin ihren Eintrag.

Laeuft im SUBPROZESS mit eigener Temp-DB (Muster wie test_search_ranking.py).
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _boot():
    tmp = tempfile.mkdtemp(prefix="ai-rem-disc-")
    os.environ["KUZU_DB_PATH"] = os.path.join(tmp, "kg.db")
    os.environ["BACKUP_DIR"] = os.path.join(tmp, "backups")
    os.environ["EMBED_ENABLED"] = "0"
    os.environ.setdefault("AI_REM_API_TOKEN", "test-token")
    sys.path.insert(0, ROOT)
    import server
    return server


def _scenario() -> None:
    server = _boot()
    server.memory_add("tvheadend", "Tool", description="TV-Server auf mystorage")
    server.memory_add("ai-rem memory_add: Update überschreibt weggelassene Felder",
                      "Problem", description="Update-Semantik von memory_add")

    def kn(prompt):
        kw = server._discover_keywords(prompt)
        return [k["name"] for k in
                server._discover_compute(prompt, kw, "", 3)["knowledge"]]

    # 1) Wortgrenzen: 'lassen' darf 'weggelassene' nicht mehr treffen.
    assert server._name_word_match("lassen", "weggelassene Felder") is False
    assert server._name_word_match("ipv6", "IPv6-Adresse konfigurieren") is True
    assert server._name_word_match("tvhead", "tvheadend") is True

    # 2) Inhaltsleerer Folge-Prompt → keine Injektion (vorher: drei Streuner).
    assert kn("ja dann so lassen und 1+2") == [], kn("ja dann so lassen und 1+2")

    # 3) Ein-Wort-Prompt mit exaktem Namen findet den Eintrag weiterhin.
    assert kn("ja tvheadend mal gerade") == ["tvheadend"], kn("ja tvheadend mal gerade")

    print("OK")


def test_discover_knowledge():
    r = subprocess.run(
        [sys.executable, __file__],
        capture_output=True, text=True,
        env={**os.environ, "AI_REM_API_TOKEN": "test-token"},
    )
    assert r.returncode == 0, f"Szenario fehlgeschlagen:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    assert "OK" in r.stdout


if __name__ == "__main__":
    _scenario()
