"""Umschaltbares Embedding-Backend: Dimensionswechsel, Fail-soft, HTTP-Parsing.

Der Wechsel zwischen in-process fastembed (384 Dim) und einem externen Dienst ueber
EMBED_URL (bge-m3, 1024 Dim) ist ein STILLER Fehlerfall: die Suche bleibt "an",
vergleicht aber Vektoren aus zwei Geometrien. Darum drei Faelle, die genau das
absichern.

Laeuft wie test_embed_backfill_chunking.py im SUBPROZESS mit eigener Temp-DB: die
Szenarien stubben Modul-Globals, das darf die anderen Testmodule nicht treffen, die
sich ueber den pytest-Modulcache eine server-Instanz teilen.
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _boot():
    """Server mit frischer Temp-DB importieren, Embeddings zunaechst aus."""
    tmp = tempfile.mkdtemp(prefix="ai-rem-embed-")
    os.environ["KUZU_DB_PATH"] = os.path.join(tmp, "kg.db")
    os.environ["BACKUP_DIR"] = os.path.join(tmp, "backups")
    os.environ["EMBED_ENABLED"] = "0"
    os.environ.setdefault("AI_REM_API_TOKEN", "test-token")
    sys.path.insert(0, ROOT)
    import server
    return server


def _scenario_dim_change() -> None:
    """Wechsel der Vektorlaenge → alle alten Vektoren weg, Backfill rechnet neu."""
    server = _boot()
    for i in range(5):
        server.memory_add(f"Ent{i}", "Task", description=f"Beschreibung {i}")

    server.EMBED_ENABLED = True
    server._checkpoint_wal = lambda force=False: None

    # Erster Lauf mit 3 Dimensionen — Ausgangszustand wie ein fastembed-Bestand.
    server._embed_texts = lambda texts, prefix: [[0.1, 0.2, 0.3] for _ in texts]
    server._embed_backfill()
    dims = {len(json.loads(r[0])) for r in server._rows(server.db_exec(
        "MATCH (e:Entity) WHERE e.embedding <> '' RETURN e.embedding"))}
    assert dims == {3}, f"Aufbau unerwartet: {dims}"

    # Backend gewechselt: 5 Dimensionen. Alles muss neu gerechnet werden, nicht nur
    # die (hier gar nicht vorhandenen) leeren Zeilen.
    calls = []
    server._embed_texts = lambda texts, prefix: (calls.append(len(texts))
                                                 or [[0.5] * 5 for _ in texts])
    server._embed_backfill()
    rows = server._rows(server.db_exec(
        "MATCH (e:Entity) WHERE e.embedding <> '' RETURN e.embedding"))
    dims = {len(json.loads(r[0])) for r in rows}
    assert dims == {5}, f"nicht neu gerechnet: {dims}"
    assert len(rows) == 5, f"Vektoren fehlen: {len(rows)}"
    assert sum(calls) >= 5 + 1, f"Probe + Reembed erwartet, war: {calls}"

    # Idempotent: ohne Dimensionswechsel rechnet der naechste Lauf nichts neu.
    calls.clear()
    server._embed_backfill()
    assert calls == [1], f"zweiter Lauf embeddet erneut: {calls}"  # nur die Dim-Probe

    print("OK")


def _scenario_fail_soft() -> None:
    """Backend down: kein Crash, kein Datenverlust — und der Reset loescht NICHTS."""
    server = _boot()
    server.EMBED_ENABLED = True
    server._checkpoint_wal = lambda force=False: None
    server._embed_texts = lambda texts, prefix: [[0.1, 0.2, 0.3] for _ in texts]
    server.memory_add("Bestand", "Task", description="hat schon einen Vektor")
    server._embed_backfill()
    vorher = server._rows(server.db_exec(
        "MATCH (e:Entity) WHERE e.embedding <> '' RETURN count(e)"))[0][0]
    assert int(vorher) == 1, f"Aufbau unerwartet: {vorher}"

    def kaputt(texts, prefix):
        raise OSError("embedding backend unreachable")

    server._embed_texts = kaputt

    # Schreiben geht weiter, die Entity landet nur ohne Vektor in der DB.
    server.memory_add("Waehrend Ausfall", "Task", description="kein Vektor moeglich")
    leer = server._rows(server.db_exec(
        "MATCH (e:Entity) WHERE (e.embedding IS NULL OR e.embedding = '') RETURN count(e)"))[0][0]
    assert int(leer) == 1, f"Entity nicht gespeichert oder Vektor unerwartet: {leer}"

    # Suche faellt auf leer zurueck statt zu werfen.
    assert server._semantic_hits("irgendwas") == []

    # Und der Dimensions-Reset darf bei totem Backend nichts abraeumen.
    server._embed_backfill()
    noch_da = server._rows(server.db_exec(
        "MATCH (e:Entity) WHERE e.embedding <> '' RETURN count(e)"))[0][0]
    assert int(noch_da) == 1, f"Reset hat bei totem Backend geloescht: {noch_da}"

    print("OK")


def _run(scenario: str) -> None:
    r = subprocess.run(
        [sys.executable, __file__, scenario],
        capture_output=True, text=True,
        env={**os.environ, "AI_REM_API_TOKEN": "test-token"},
    )
    assert r.returncode == 0, f"Szenario fehlgeschlagen:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    assert "OK" in r.stdout


def test_backend_wechsel_rechnet_alle_vektoren_neu():
    _run("dim_change")


def test_backend_ausfall_ist_fail_soft():
    _run("fail_soft")


def test_embed_http_sortiert_nach_index():
    """Die Antwort eines /v1/embeddings-Endpoints ist nicht garantiert in
    Eingabereihenfolge — sonst bekaeme die falsche Entity den falschen Vektor."""
    import importlib.util
    os.environ.setdefault("AI_REM_API_TOKEN", "test-token")
    src = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
    ns = {"json": json, "EMBED_URL": "http://embed.test/v1/embeddings",
          "EMBED_HTTP_MODEL": "bge-m3", "EMBED_HTTP_TIMEOUT": 5}
    start = src.index("def _embed_http(")
    exec(compile(src[start:src.index("\ndef ", start + 1)], "server.py", "exec"), ns)

    antwort = json.dumps({"data": [
        {"index": 1, "embedding": [0.4, 0.5, 0.6]},
        {"index": 0, "embedding": [0.1, 0.2, 0.3]},
    ]}).encode()

    class _Resp:
        def read(self):
            return antwort

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    import urllib.request
    orig, urllib.request.urlopen = urllib.request.urlopen, lambda *a, **k: _Resp()
    try:
        assert ns["_embed_http"](["erst", "zweit"]) == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    finally:
        urllib.request.urlopen = orig
    assert importlib.util.find_spec("urllib.request") is not None


if __name__ == "__main__":
    {"dim_change": _scenario_dim_change, "fail_soft": _scenario_fail_soft}[sys.argv[1]]()
