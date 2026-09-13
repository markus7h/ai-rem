"""Der Build-Cache im image-smoke-Job muss rotieren.

Mit festem `cache-from: type=gha` friert der Layer `apt-get update && apt-get
upgrade` (Dockerfile:12) fuer immer ein — ausgerechnet der, der die Debian-Patches
holt. Das Trivy-Gate im selben Job blockt dann auf CVEs, die ein echter Build laengst
gefixt haette, und ein Re-Run aendert nichts, weil er denselben Cache zieht (erlebt am
13.09.2026: zwoelf fixbare perl-CVEs, Fix lag im Mirror bereit).

Faellt der Scope wieder weg, ist die Ursache beim naechsten Mal nicht mehr zu sehen —
der Job sieht einfach rot aus. Deshalb hier festgenagelt.
"""
import pathlib
import re

CI = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"


def test_cache_scope_rotiert_mit_dem_datum():
    ci = CI.read_text(encoding="utf-8")
    scope = re.search(r'key=ci-\$\(date -u \+((?:%[YmdH])+)\)', ci)
    assert scope, "kein datumsbasierter Cache-Scope — der apt-Layer wuerde wieder einfrieren"

    for richtung in ("cache-from", "cache-to"):
        zeile = re.search(richtung + r": type=gha[^\n]*", ci)
        assert zeile, richtung + " fehlt"
        assert "scope=${{ steps.cachescope.outputs.key }}" in zeile.group(0), \
            richtung + " ohne rotierenden Scope: " + zeile.group(0)


def test_trivy_gate_steht_noch():
    """Der Scope repariert die Ursache. Wer stattdessen das Gate entfernt, hat nur
    die Anzeige abgeschaltet."""
    ci = CI.read_text(encoding="utf-8")
    assert "trivy-action" in ci
    assert "exit-code: '1'" in ci or 'exit-code: "1"' in ci
