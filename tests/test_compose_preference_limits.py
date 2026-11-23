"""Die Preference-Limits müssen aus der `.env` im Container ankommen.

`server.py` liest `DISCOVER_ROUTINES_LIMIT` (Routinen pro /discover-Prompt) und
`CONTEXT_PREF_LIMIT` (Preferences in memory_get_context, dieselbe Schnittlinie, die die
Web-UI /prefs malt) beide aus der Umgebung. Fehlt einer der beiden in der
`environment`-Liste von docker-compose.yml, gilt stillschweigend der Code-Default: auf
mystorage standen 26 in der `.env`, die /prefs-UI zeigte trotzdem 15 und sechs von 21
gepinnten Rules fehlten im Session-Kontext — ohne jeden Fehler (#226).
"""
import pathlib

import yaml

COMPOSE = pathlib.Path(__file__).resolve().parent.parent / "docker-compose.yml"

# Beide Limits kuratieren dieselben gepinnten Preferences, also müssen beide konfigurierbar sein.
GRENZEN = ("DISCOVER_ROUTINES_LIMIT", "CONTEXT_PREF_LIMIT")


def _env_keys() -> set:
    environment = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["ai-rem"]["environment"]
    if isinstance(environment, dict):
        return set(environment)
    return {str(entry).split("=", 1)[0] for entry in environment}


def test_beide_Preference_Limits_werden_durchgereicht():
    keys = _env_keys()
    for var in GRENZEN:
        assert var in keys, (
            f"{var} fehlt in der environment-Liste von docker-compose.yml — "
            "ein Wert in der .env bliebe wirkungslos"
        )
