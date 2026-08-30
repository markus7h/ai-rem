"""CHANGELOG.md und der Extraktor, aus dem Release-Body und Docker-Hub-Block entstehen.

Beides faellt sonst erst beim Tag-Push auf — also genau dann, wenn das Release schon
raus ist: ein Tippfehler in der Ueberschrift, und `changelog.py section` liefert leer,
das Release bekommt generierte Platzhalter-Notizen.
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHANGELOG = os.path.join(ROOT, "CHANGELOG.md")
SCRIPT = os.path.join(ROOT, "scripts", "changelog.py")


def _run(*args) -> str:
    r = subprocess.run([sys.executable, SCRIPT, *args],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, f"{args} → rc={r.returncode}\n{r.stderr}"
    return r.stdout.strip()


def test_selbstcheck_des_extraktors():
    """Der eingebaute --demo deckt Parsing, Fallback und Ebenen-Umschrift ab."""
    r = subprocess.run([sys.executable, SCRIPT, "--demo"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout


def test_aktuelle_version_hat_einen_abschnitt():
    """VERSION in server.py muss im CHANGELOG stehen — sonst bekaeme das Release
    beim naechsten Tag generierte Notizen statt der gepflegten."""
    src = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
    version = re.search(r"^VERSION\s*=\s*[\"']([^\"']+)", src, re.M).group(1)
    text = open(CHANGELOG, encoding="utf-8").read()
    assert f"## [{version}]" in text or "## [Unreleased]" in text, (
        f"Weder ein Abschnitt fuer {version} noch [Unreleased] in CHANGELOG.md")


def test_abschnitte_sind_absteigend_und_datiert():
    versionen = re.findall(r"^## \[(\d+\.\d+\.\d+)\]\s*[–-]\s*(\d{4}-\d{2}-\d{2})\s*$",
                           open(CHANGELOG, encoding="utf-8").read(), re.M)
    assert versionen, "keine datierten Versions-Abschnitte gefunden"
    nummern = [tuple(int(x) for x in v.split(".")) for v, _ in versionen]
    assert nummern == sorted(nummern, reverse=True), f"nicht absteigend: {nummern}"


def test_section_liefert_den_richtigen_abschnitt():
    text = open(CHANGELOG, encoding="utf-8").read()
    jüngste = re.search(r"^## \[(\d+\.\d+\.\d+)\]", text, re.M).group(1)
    body = _run("section", f"v{jüngste}")
    assert body, f"leerer Abschnitt fuer v{jüngste}"
    assert "## [" not in body, "Abschnitt laeuft in den naechsten hinein"
    assert not re.search(r"^\[[^\]]+\]:\s*http", body, re.M), "Link-Defs im Body"
    assert _run("section", "v99.99.99") == "", "unbekannte Version muss leer bleiben"


def test_docker_hub_block_passt_ins_limit():
    """Docker Hub deckelt full_description bei 25000 Bytes; der Workflow bricht
    hart ab, wenn das reisst — hier faellt es schon im PR auf."""
    seite = open(os.path.join(ROOT, "docs", "dockerhub.md"), encoding="utf-8").read()
    start, ende = "<!-- CHANGELOG:START -->", "<!-- CHANGELOG:END -->"
    assert start in seite and ende in seite, "Marker fehlen in docs/dockerhub.md"
    gefuellt = seite.replace(f"{start}\n{ende}", f"{start}\n\n{_run('latest', '3')}\n\n{ende}")
    groesse = len(gefuellt.encode())
    assert groesse <= 25000, f"{groesse} Bytes ueber dem 25000-Byte-Limit"


def _version() -> str:
    src = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
    return re.search(r"^VERSION\s*=\s*[\"']([^\"']+)", src, re.M).group(1)


def test_patch_historie_ist_gepflegt():
    """Jeder Abschnitt braucht seine compare-Link-Definition, und [Unreleased] muss
    gegen die juengste diffen. Faellt sonst nur beim Lesen der gerenderten Datei auf:
    ohne Link-Def rendert `## [0.8.24]` als Klartext statt als Vergleichslink."""
    text = open(CHANGELOG, encoding="utf-8").read()
    abschnitte = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", text, re.M)
    defs = set(re.findall(r"^\[(\d+\.\d+\.\d+)\]:\s*http", text, re.M))
    fehlend = [v for v in abschnitte if v not in defs]
    assert not fehlend, f"Link-Definition fehlt fuer: {', '.join(fehlend)}"

    unreleased = re.search(r"^\[Unreleased\]:\s*\S+/compare/v(\S+?)\.\.\.HEAD\s*$",
                           text, re.M)
    assert unreleased, "[Unreleased]-Link fehlt oder hat ein fremdes Format"
    assert unreleased.group(1) == abschnitte[0], (
        f"[Unreleased] difft gegen v{unreleased.group(1)}, "
        f"juengster Abschnitt ist {abschnitte[0]}")


def test_readme_versionsanker_zeigt_auf_die_aktuelle_version():
    """Der Anker oben in beiden READMEs blieb ueber vier Releases auf v0.8.21 stehen —
    Leser bekamen die Doku als aelter verkauft, als sie war."""
    version = _version()
    for name in ("README.md", "README.de.md"):
        kopf = open(os.path.join(ROOT, name), encoding="utf-8").read()[:600]
        anker = re.search(r"releases/tag/v(\d+\.\d+\.\d+)", kopf)
        assert anker, f"kein Versionsanker im Kopf von {name}"
        assert anker.group(1) == version, (
            f"{name} nennt v{anker.group(1)}, server.py sagt {version}")
