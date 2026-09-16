"""Ein harter Tod darf kg.db nicht dauerhaft unstartbar machen.

Am 16.09.2026 segfaultete LadybugDB mitten im WAL-Checkpoint. Zurueck blieb eine
beschaedigte kg.db.wal.checkpoint, an der danach JEDER Start scheiterte — mit
`restart: unless-stopped` drehte der Container 20 Runden, ohne je hochzukommen,
bis jemand die Datei von Hand wegschob. Genau diese Handarbeit macht der Guard.

Die beiden Fehlertexte stammen aus echten Laeufen gegen ladybug 0.20.2 (sie
unterscheiden sich je nachdem, welche Datei es erwischt hat) — faengt eine
kuenftige Version sie anders zu formulieren an, fallen diese Tests auf.
"""
import os
import subprocess
import sys
import tempfile
import textwrap

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMPDIR = tempfile.mkdtemp(prefix="ai-rem-walguard-")
os.environ.setdefault("LADYBUG_DB_PATH", os.path.join(_TMPDIR, "kg.db"))
os.environ["EMBED_ENABLED"] = "0"
os.environ.setdefault("AI_REM_API_TOKEN", "test-token")

import ladybug  # noqa: E402
import server  # noqa: E402


def _frische_db(tmp_path, zeilen=50):
    """DB mit `zeilen` Knoten anlegen und sauber schliessen."""
    p = str(tmp_path / "kg.db")
    db = ladybug.Database(p)
    c = ladybug.Connection(db)
    c.execute("CREATE NODE TABLE E(name STRING, PRIMARY KEY(name))")
    for i in range(zeilen):
        c.execute("CREATE (:E {name: $n})", {"n": "e%d" % i})
    del c
    db.close()
    return p


def _harter_tod(p, von, bis):
    """Knoten schreiben und den Prozess per os._exit killen — nur so bleibt eine
    WAL liegen. `del db` reicht nicht: der Destruktor schliesst sauber und merged."""
    code = textwrap.dedent("""
        import os, sys, ladybug
        db = ladybug.Database(%r)
        c = ladybug.Connection(db)
        for i in range(%d, %d):
            c.execute("CREATE (:E {name: $n})", {"n": "e%%d" %% i})
        os._exit(9)
    """) % (p, von, bis)
    subprocess.run([sys.executable, "-c", code], check=False)


def _zeilen(db):
    c = ladybug.Connection(db)
    n = c.execute("MATCH (e:E) RETURN count(e)").get_next()[0]
    del c
    return n


def test_kaputte_checkpoint_datei_wird_weggeschoben(tmp_path):
    """Der Fall vom 16.09.: im Merge gestorben, .wal.checkpoint unbrauchbar."""
    p = _frische_db(tmp_path)
    with open(p + ".wal.checkpoint", "wb") as f:
        f.write(os.urandom(4096))

    # Ohne Guard kommt hier nur noch der RuntimeError.
    with pytest.raises(RuntimeError):
        ladybug.Database(p)

    db = server._open_database(p)
    assert _zeilen(db) == 50
    db.close()

    assert not os.path.exists(p + ".wal.checkpoint"), "Rest muss weg sein"
    quarantaene = [f for f in os.listdir(tmp_path) if ".corrupt-" in f]
    assert quarantaene, "Datei muss erhalten bleiben, nicht geloescht werden"


def test_kaputte_wal_wird_weggeschoben(tmp_path):
    """Zweite Variante: beschaedigter Record in der laufenden .wal."""
    p = _frische_db(tmp_path)
    _harter_tod(p, 100, 140)

    wal = p + ".wal"
    assert os.path.exists(wal), "ohne liegengebliebene WAL testet das hier nichts"
    with open(wal, "r+b") as f:
        f.seek(-512, os.SEEK_END)
        f.write(os.urandom(512))

    db = server._open_database(p)
    assert _zeilen(db) >= 50   # der Stand bis zum letzten Merge muss stehen
    db.close()


def test_intakte_wal_bleibt_unangetastet(tmp_path):
    """Der Normalfall nach einem harten Tod: die WAL ist heil und wird recovert.
    Wer sie hier wegraeumt, wirft ohne Not committete Transaktionen weg."""
    p = _frische_db(tmp_path)
    _harter_tod(p, 200, 240)
    assert os.path.exists(p + ".wal"), "ohne liegengebliebene WAL testet das hier nichts"

    db = server._open_database(p)
    assert _zeilen(db) == 90, "Recovery muss die WAL-Eintraege mitbringen"
    db.close()

    assert not [f for f in os.listdir(tmp_path) if ".corrupt-" in f], \
        "an einer intakten WAL darf der Guard nicht anfassen"


def test_fremder_fehler_wird_durchgereicht(tmp_path, monkeypatch):
    """Nur WAL-Schaeden rechtfertigen die Quarantaene. Bei jedem anderen Fehler
    muessen die Dateien liegen bleiben — sonst raeumt ein Tippfehler in der
    Konfiguration die WAL ab."""
    p = _frische_db(tmp_path)
    with open(p + ".wal", "wb") as f:
        f.write(b"nicht anfassen")

    def explodiert(*a, **kw):
        raise RuntimeError("buffer pool is full")

    monkeypatch.setattr(server.ladybug, "Database", explodiert)
    with pytest.raises(RuntimeError, match="buffer pool"):
        server._open_database(p)

    assert os.path.exists(p + ".wal"), "fremder Fehler darf nichts verschieben"


def test_quarantaene_meldet_was_sie_verschoben_hat(tmp_path):
    """Rueckgabe speist die Logzeile — ohne sie steht im Log nicht, was passierte."""
    p = str(tmp_path / "kg.db")
    for suffix in (".wal", ".shadow", ".checkpoint.apply.lock"):
        with open(p + suffix, "wb") as f:
            f.write(b"x")

    moved = server._quarantine_wal(p)
    assert len(moved) == 3
    assert all(".corrupt-" in name for name in moved)
    assert not os.path.exists(p + ".wal")
