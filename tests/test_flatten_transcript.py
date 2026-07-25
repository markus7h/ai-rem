"""Tests für flatten_transcript: bei Überlänge fällt die MITTE weg, nicht das Ende.

Vorher wurde von vorne gefüllt und beim Budget abgebrochen — damit ging genau das
Session-Ende verloren, wo Entscheidungen und Erkenntnisse stehen.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.extractor import MAX_TOTAL_CHARS, flatten_transcript  # noqa: E402


def _write(records):
    fd, name = tempfile.mkstemp(suffix=".jsonl", prefix="ai-rem-flat-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return Path(name)


def _msg(role, text):
    return {"type": role, "message": {"role": role, "content": text}}


def test_short_transcript_unchanged():
    p = _write([_msg("user", "Frage"), _msg("assistant", "Antwort")])
    try:
        out = flatten_transcript(p)
        assert out == "USER: Frage\n\nASSISTANT: Antwort", out
        assert "gekürzt" not in out
    finally:
        p.unlink()


def test_oversized_keeps_head_and_tail():
    filler = "x" * 3000
    records = [_msg("user", "AUFGABENSTELLUNG")]
    records += [_msg("assistant", filler) for _ in range(40)]
    records.append(_msg("assistant", "LETZTE ERKENNTNIS"))
    p = _write(records)
    try:
        out = flatten_transcript(p)
        assert len(out) <= MAX_TOTAL_CHARS, len(out)
        assert out.startswith("USER: AUFGABENSTELLUNG"), out[:80]
        assert out.endswith("ASSISTANT: LETZTE ERKENNTNIS"), out[-80:]
        assert "…[Mitte gekürzt]" in out
    finally:
        p.unlink()


def test_tool_results_skipped():
    rec = {"type": "user", "message": {"role": "user",
           "content": [{"type": "tool_result", "content": "irrelevant"}]}}
    p = _write([_msg("user", "Frage"), rec])
    try:
        assert flatten_transcript(p) == "USER: Frage"
    finally:
        p.unlink()


if __name__ == "__main__":
    test_short_transcript_unchanged()
    test_oversized_keeps_head_and_tail()
    test_tool_results_skipped()
    print("ok")
