"""
Heuristic Extractor für Auto-Memory: Pattern-Matching statt Claude-Call.
Sucht nach Markern (Decision, Problem, Solution, etc.) im Chat.
"""

import json
import re
from typing import Any


def extract_heuristic(flattened: str) -> dict[str, Any]:
    """
    Sucht Pattern wie "Decision:", "Problem:", "Solution:" im Chat-Text
    und extrahiert Entitäten mit heuristischen Grenzen.
    """
    entities = []
    relations = []

    # Pattern: "Decision: ..." oder "DECISION: ..." (case-insensitive)
    decision_pattern = r"\*?\*?Decision\*?\*?:?\s+([^.!?]+[.!?])"
    problem_pattern = r"\*?\*?Problem\*?\*?:?\s+([^.!?]+[.!?])"
    solution_pattern = r"\*?\*?Solution\*?\*?:?\s+([^.!?]+[.!?])"

    for match in re.finditer(decision_pattern, flattened, re.IGNORECASE):
        text = match.group(1).strip()
        if len(text) > 10:
            entities.append({
                "type": "Decision",
                "name": text[:60],  # gekürzt für Name
                "description": text
            })

    for match in re.finditer(problem_pattern, flattened, re.IGNORECASE):
        text = match.group(1).strip()
        if len(text) > 10:
            entities.append({
                "type": "Problem",
                "name": text[:60],
                "description": text
            })

    for match in re.finditer(solution_pattern, flattened, re.IGNORECASE):
        text = match.group(1).strip()
        if len(text) > 10:
            entities.append({
                "type": "Solution",
                "name": text[:60],
                "description": text
            })

    # Heuristik: Wenn ein Problem N Lines vor einer Solution ist → SOLVES-Relation
    # (sehr grob, aber better than nothing)

    return {"entities": entities, "relations": relations}


def test_heuristic():
    """Quick-Test."""
    chat = """
    USER: Wie triggert man Auto-Memory?
    ASSISTANT: Decision: PreCompact-Hook sollte besser sein als SessionEnd.
    USER: Warum?
    ASSISTANT: Problem: Kurze Sessions generieren zu viel Overhead.
    Solution: Nur bei PreCompact extrahieren wenn context groß genug ist.
    """
    result = extract_heuristic(chat)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    test_heuristic()
