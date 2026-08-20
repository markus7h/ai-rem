"""llama.cpp erzwingt response_format=json_object nur weich: das Modell haengt
hinter das JSON-Objekt gelegentlich noch Prosa oder ein zweites Fragment. Ein
strenges json.loads scheiterte daran ("Extra data: line 1 column 817") und schob
das ganze Transcript in die Fallback-Queue, statt die brauchbare Antwort zu nehmen.
"""
import io
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib import extractor  # noqa: E402

PAYLOAD = {"entities": [{"type": "Problem", "name": "x", "description": "y"}], "relations": []}


def _reply(content, monkeypatch):
    envelope = {"choices": [{"message": {"content": content}}]}
    monkeypatch.setattr(extractor.urllib.request, "urlopen",
                        lambda *a, **kw: io.BytesIO(json.dumps(envelope).encode()))
    return extractor.call_llm("transcript", "m", "sys")


def test_nachgestellte_prosa_wird_verworfen(monkeypatch):
    content = json.dumps(PAYLOAD) + '\nHoffe, das hilft!\n{"entities":'
    assert _reply(content, monkeypatch) == PAYLOAD


def test_sauberes_json_und_fences_weiterhin_ok(monkeypatch):
    assert _reply(json.dumps(PAYLOAD), monkeypatch) == PAYLOAD
    assert _reply("```json\n%s\n```" % json.dumps(PAYLOAD), monkeypatch) == PAYLOAD


def test_ohne_fuehrendes_json_bleibt_es_ein_fehler(monkeypatch):
    with pytest.raises(RuntimeError, match="kein gültiges JSON"):
        _reply("Tut mir leid, ich kann das nicht.", monkeypatch)
