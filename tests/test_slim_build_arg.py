"""Das :latest-slim-Tag muss auch wirklich die schlanke Variante enthalten.

docker-compose.yml hat `image:` UND `build:`. `compose up --build` ueberschreibt
das gezogene Image mit dem lokalen Build und behaelt den Tag-Namen — ohne
passendes EMBED_BACKEND baute es monatelang die volle Variante (fastembed +
Modell, ~382 MB) und taggte sie als latest-slim. Funktional faellt das nie auf,
weil der Server bei gesetztem EMBED_URL den eingebackenen Code gar nicht anfasst.
Deshalb dieser Waechter statt Aufmerksamkeit.
"""
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy.sh"


def test_compose_reicht_embed_backend_an_den_build_durch():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "EMBED_BACKEND: ${EMBED_BACKEND:-local}" in compose, \
        "build.args.EMBED_BACKEND fehlt — `up --build` faellt auf den Dockerfile-Default zurueck"


@pytest.mark.skipif(not DEPLOY.exists(), reason="deploy.sh ist gitignored (privat) — laeuft nur lokal")
def test_deploy_leitet_backend_aus_dem_tag_ab():
    """Die case-Zuordnung aus deploy.sh selbst ausfuehren, keine Kopie davon."""
    skript = DEPLOY.read_text(encoding="utf-8")
    start = skript.index('case "${REMOTE_TAG:-latest}"')
    block = skript[start:skript.index("esac", start) + 4]

    for tag, erwartet in [("latest-slim", "external"), ("v1.2.3-slim", "external"),
                          ("latest", "local"), ("v1.2.3", "local"), ("", "local")]:
        out = subprocess.run(["bash", "-c", f'REMOTE_TAG="{tag}"\n{block}\necho "$BACKEND"'],
                             capture_output=True, text=True, check=True).stdout.strip()
        assert out == erwartet, f"AI_REM_TAG={tag!r} -> {out!r}, erwartet {erwartet!r}"
