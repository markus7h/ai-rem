#!/usr/bin/env python3
"""Abschnitte aus CHANGELOG.md ziehen — für GitHub Release und Docker-Hub-Beschreibung.

Der Publish-Workflow ruft das zweimal auf:

    changelog.py section v0.8.21     → Notizen genau dieser Version (Release-Body)
    changelog.py latest 3            → die jüngsten 3 Versionen (Docker-Hub-Block)
    changelog.py verify v0.8.22 v0.8.21 [bis]
                                     → fehlt ein gemergter PR im Abschnitt? (rc=1)

Ohne passenden Abschnitt bleibt die Ausgabe leer und der Workflow fällt auf
`gh release --generate-notes` zurück — ein vergessener Eintrag darf das Release
nicht blockieren.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"

# "## [0.8.21] – 2026-08-15" (Gedankenstrich oder Bindestrich, Datum optional)
_HEADING = re.compile(r"^## \[(?P<version>[^\]]+)\](?:\s*[–-]\s*(?P<date>\S+))?\s*$")


def _sections(text: str) -> list[tuple[str, str, str]]:
    """(Version, Datum, Rumpf) je Abschnitt, in Dateireihenfolge."""
    out: list[tuple[str, str, str]] = []
    version = date = None
    body: list[str] = []
    for line in text.splitlines():
        m = _HEADING.match(line)
        if m:
            if version:
                out.append((version, date or "", "\n".join(body).strip()))
            version, date, body = m.group("version"), m.group("date"), []
            continue
        if version is not None:
            # Die Link-Definitionen am Dateiende gehören zu keinem Abschnitt.
            if re.match(r"^\[[^\]]+\]:\s*http", line):
                continue
            body.append(line)
    if version:
        out.append((version, date or "", "\n".join(body).strip()))
    return out


def section(version: str) -> str:
    wanted = version.lstrip("v")
    for v, _date, body in _sections(CHANGELOG.read_text(encoding="utf-8")):
        if v.lstrip("v") == wanted:
            return body
    return ""


def latest(count: int) -> str:
    out = []
    for v, date, body in _sections(CHANGELOG.read_text(encoding="utf-8")):
        if v.lower() == "unreleased" or not body:
            continue
        # Die Rubriken ("### Fixed") eine Ebene tiefer, damit die Version die
        # Überschrift bleibt und nicht gleichrangig neben ihren Rubriken steht.
        body = re.sub(r"^### ", "#### ", body, flags=re.M)
        out.append(f"### v{v.lstrip('v')}" + (f" — {date}" if date else "") + f"\n\n{body}")
        if len(out) >= count:
            break
    return "\n\n".join(out)


def _merged_prs(seit: str, bis: str = "HEAD") -> list[str]:
    """PR-Nummern der Commits zwischen zwei Refs — bei Squash-Merges steht die
    Nummer im Titel ('feat: … (#83)')."""
    import subprocess
    log = subprocess.run(["git", "log", "--format=%s", f"{seit}..{bis}"],
                         capture_output=True, text=True, cwd=ROOT).stdout
    out: list[str] = []
    for zeile in log.splitlines():
        if zeile.startswith("chore: Release "):
            continue  # der Release-Commit selbst ist kein Inhalt
        for nr in re.findall(r"\(#(\d+)\)", zeile):
            if nr not in out:
                out.append(nr)
    return out


def verify(version: str, seit: str, bis: str = "HEAD") -> list[str]:
    """PR-Nummern, die seit `seit` gemergt wurden, aber im Abschnitt fehlen.

    Das ist die eigentliche Garantie fuer 'jeder PR steht im Changelog': der
    Datei-geaenderte-Check im PR faengt nur, DASS jemand etwas geschrieben hat,
    nicht ob am Ende alles Gemergte drinsteht (Dependabot schreibt z.B. nie).
    """
    body = section(version) or ""
    genannt = set(re.findall(r"#(\d+)", body))
    return [nr for nr in _merged_prs(seit, bis) if nr not in genannt]


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in ("section", "latest", "verify"):
        print(__doc__, file=sys.stderr)
        return 2
    if not CHANGELOG.exists():
        return 0  # kein Changelog → leere Ausgabe, Workflow nutzt den Fallback
    if sys.argv[1] == "section":
        if len(sys.argv) < 3:
            print("section braucht eine Version", file=sys.stderr)
            return 2
        print(section(sys.argv[2]))
    elif sys.argv[1] == "verify":
        if len(sys.argv) < 4:
            print("verify braucht Version und Vergleichs-Ref", file=sys.stderr)
            return 2
        fehlend = verify(sys.argv[2], sys.argv[3],
                         sys.argv[4] if len(sys.argv) > 4 else "HEAD")
        if fehlend:
            print("Nicht im CHANGELOG-Abschnitt %s: %s"
                  % (sys.argv[2], ", ".join("#" + n for n in fehlend)), file=sys.stderr)
            return 1
        print(f"Alle gemergten PRs seit {sys.argv[3]} stehen in {sys.argv[2]}.")
    else:
        print(latest(int(sys.argv[2]) if len(sys.argv) > 2 else 3))
    return 0


def _demo() -> None:
    """Selbstcheck gegen das echte CHANGELOG.md — kein Framework nötig."""
    beispiel = """# Changelog

## [Unreleased]

### Added
- nothing released yet

## [0.9.0] – 2026-09-01

### Changed
- second line

## [0.8.21] – 2026-08-15

### Fixed
- something repaired

[0.9.0]: https://example.invalid/compare/v0.8.21...v0.9.0
"""
    global CHANGELOG
    echt, tmp = CHANGELOG, ROOT / "_changelog_demo.md"
    tmp.write_text(beispiel, encoding="utf-8")
    CHANGELOG = tmp
    try:
        assert "second line" in section("v0.9.0"), section("v0.9.0")
        assert "something repaired" in section("0.8.21")  # auch ohne v-Präfix
        assert section("v1.2.3") == "", "unbekannte Version muss leer bleiben"
        neu = latest(2)
        assert "Unreleased" not in neu, neu
        assert neu.startswith("### v0.9.0"), neu
        assert "v0.8.21" in neu and "example.invalid" not in neu, neu
        assert latest(1).count("\n### v") + latest(1).startswith("### v") == 1
        # Rubriken eine Ebene tiefer als die Version, sonst stehen sie gleichrangig
        assert "#### Changed" in neu and "\n### Changed" not in neu, neu
    finally:
        CHANGELOG = echt
        tmp.unlink()
    print("OK")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        _demo()
    else:
        sys.exit(main())
