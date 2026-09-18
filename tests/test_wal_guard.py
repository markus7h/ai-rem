"""Ein harter Tod darf kg.db nicht dauerhaft unstartbar machen.

Am 16.09.2026 segfaultete LadybugDB mitten im WAL-Checkpoint. Zurueck blieb eine
beschaedigte kg.db.wal.checkpoint, an der danach JEDER Start scheiterte — mit
`restart: unless-stopped` drehte der Container 20 Runden, ohne je hochzukommen,
bis jemand die Datei von Hand wegschob. Genau diese Handarbeit macht der Guard.

Am 18.09.2026 passierte dasselbe noch einmal, nur stiller: diesmal segfaultete
schon der Konstruktor. Der Guard aus v1.2.5 hing an `except RuntimeError` und sah
nichts — ein SIGSEGV ist keine Exception, der Prozess ist einfach weg. Deshalb
faellt die Entscheidung jetzt in einem Wegwerf-Subprozess, wo derselbe Tod als
Exit-Code sichtbar wird. Diese Tests decken beide Todesarten ab.
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


def test_segfault_beim_open_wird_erkannt(tmp_path, monkeypatch):
    """Der Fall vom 18.09.: der Konstruktor stirbt per Signal, ohne je zu melden
    warum. Im eigenen Prozess waere das nicht beobachtbar — genau dafuer laeuft
    der Probe als Kindprozess."""
    p = _frische_db(tmp_path)
    _harter_tod(p, 300, 340)
    assert os.path.exists(p + ".wal"), "ohne liegengebliebene WAL testet das hier nichts"

    monkeypatch.setattr(server, "_PROBE_SRC",
                        "import os, signal; os.kill(os.getpid(), signal.SIGSEGV)")
    db = server._open_database(p)
    assert _zeilen(db) == 50, "der Stand bis zum letzten Merge muss stehen"
    db.close()

    assert [f for f in os.listdir(tmp_path) if ".corrupt-" in f], \
        "ein per Signal gestorbener Probe muss die WAL wegschieben"


def test_oom_kill_raeumt_nichts_weg(tmp_path, monkeypatch):
    """SIGKILL kommt vom OOM-Killer, nicht von der WAL. Wer hier quarantaeniert,
    wirft gesunde Transaktionen weg, weil der Speicher knapp war."""
    p = _frische_db(tmp_path)
    _harter_tod(p, 400, 440)

    monkeypatch.setattr(server, "_PROBE_SRC",
                        "import os, signal; os.kill(os.getpid(), signal.SIGKILL)")
    db = server._open_database(p)
    db.close()

    assert not [f for f in os.listdir(tmp_path) if ".corrupt-" in f], \
        "ein SIGKILL darf die WAL nicht kosten"


def test_ueberlebter_probe_schuetzt_die_wal(tmp_path, monkeypatch):
    """Ueberlebt der Probe, liegt es nicht an der WAL. Scheitert der echte Open
    danach trotzdem, fliegt der Fehler — aber die Dateien bleiben liegen, sonst
    raeumt ein Tippfehler in der Konfiguration die WAL ab."""
    p = _frische_db(tmp_path)
    _harter_tod(p, 500, 540)

    def explodiert(*a, **kw):
        raise RuntimeError("buffer pool is full")

    monkeypatch.setattr(server.ladybug, "Database", explodiert)
    with pytest.raises(RuntimeError, match="buffer pool"):
        server._open_database(p)

    assert not [f for f in os.listdir(tmp_path) if ".corrupt-" in f], \
        "fremder Fehler darf nichts verschieben"


def test_ohne_reste_kein_subprozess(tmp_path, monkeypatch):
    """Nach einem sauberen Stop gibt es nichts zu pruefen. Der Probe kostet einen
    kompletten zweiten Open — der darf nicht bei jedem Start anfallen."""
    p = _frische_db(tmp_path)
    assert not server._wal_leftovers(p), "sauber geschlossen, also nichts liegen"

    monkeypatch.setattr(server, "_probe_open",
                        lambda *a: pytest.fail("Probe ohne WAL-Reste gestartet"))
    db = server._open_database(p)
    assert _zeilen(db) == 50
    db.close()


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
