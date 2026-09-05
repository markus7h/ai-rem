"""Tests für den Log-Ringpuffer hinter /logs bzw. /api/logs.

Der Puffer haengt am Root-Logger und speist die UI-Seite. Wichtig sind zwei
Eigenschaften: er laeuft nicht voll (deque-maxlen) und er schreibt keine Secrets
im Klartext mit — die Seite ist zwar auth-pflichtig, aber ein Log ist der falsche
Ort fuer Tokens. In-process gegen Temp-DB, Embedding aus.
"""
import logging
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMPDIR = tempfile.mkdtemp(prefix="ai-rem-log-")
os.environ["LADYBUG_DB_PATH"] = os.path.join(_TMPDIR, "kg.db")
os.environ["EMBED_ENABLED"] = "0"
os.environ.setdefault("AI_REM_API_TOKEN", "test-token")

import server  # noqa: E402


def _reset():
    """Ring leeren und INFO wieder durchlassen.

    server.py setzt den Root-Logger per basicConfig auf INFO, pytest hebt ihn
    danach auf WARNING — ohne das kaeme in diesen Tests kein INFO-Record am
    Handler an. Der Handler selbst haengt regulaer am Root.
    """
    logging.getLogger().setLevel(logging.INFO)
    server._LOG_RING.clear()


def test_ring_captures_and_caps():
    _reset()
    for i in range(server._LOG_RING_SIZE + 25):
        server.log.info("ring probe %d", i)
    assert len(server._LOG_RING) == server._LOG_RING_SIZE, len(server._LOG_RING)
    # aeltestes ist rausgefallen, neuestes steht drin
    assert "ring probe 0" not in server._LOG_RING[0]["msg"]
    assert server._LOG_RING[-1]["msg"].endswith(
        "ring probe %d" % (server._LOG_RING_SIZE + 24))


def test_levels_recorded_numerically():
    _reset()
    server.log.warning("heads up")
    server.log.error("broken")
    rows = list(server._LOG_RING)
    assert [r["level"] for r in rows] == ["WARNING", "ERROR"]
    # numerisch, damit der Filter in /api/logs nicht auf Namens-Rueckabbildung angewiesen ist
    assert [r["lvlno"] for r in rows] == [logging.WARNING, logging.ERROR]


def test_secrets_are_redacted():
    _reset()
    server.log.info("auth failed for Bearer sk-abc123XYZ789 from 1.2.3.4")
    server.log.info("GET /api/tool?token=geheim12345 HTTP/1.1")
    joined = "\n".join(r["msg"] for r in server._LOG_RING)
    assert "sk-abc123XYZ789" not in joined, joined
    assert "geheim12345" not in joined, joined
    assert joined.count("<redacted>") == 2, joined
    # Drumherum bleibt lesbar, sonst taugt das Log nichts
    assert "from 1.2.3.4" in joined


def test_plain_lines_untouched():
    _reset()
    server.log.info("Schema ready — DB at /data/kg.db")
    assert server._LOG_RING[-1]["msg"].endswith("Schema ready — DB at /data/kg.db")
    assert "<redacted>" not in server._LOG_RING[-1]["msg"]


if __name__ == "__main__":
    test_ring_captures_and_caps()
    test_levels_recorded_numerically()
    test_secrets_are_redacted()
    test_plain_lines_untouched()
    print("ok")
