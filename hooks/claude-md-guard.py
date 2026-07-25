#!/usr/bin/env python3
"""PreToolUse-Hook: warnt (non-blocking) bei Schreibzugriff auf ~/.claude/CLAUDE.md.

Zweck: verhindert das stille Ansammeln von Regeln/Wissen in CLAUDE.md statt in
ai-rem. CLAUDE.md soll nur den minimalen ai-rem-Pointer enthalten. Der Hook blockt
NICHT — er injiziert nur einen Reminder (additionalContext), der Edit laeuft normal.
"""
import json
import os
import sys


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if data.get("tool_name", "") not in ("Write", "Edit", "MultiEdit"):
        return
    fp = (data.get("tool_input") or {}).get("file_path", "") or ""
    if not fp:
        return
    target = os.path.realpath(os.path.expanduser(fp))
    _cc = os.environ.get("CLAUDE_CONFIG_DIR", "").split(os.pathsep)[0].strip()
    _cdir = _cc or os.path.expanduser("~/.claude")
    claude_md = os.path.realpath(os.path.join(_cdir, "CLAUDE.md"))
    if target != claude_md:
        return
    msg = (
        "Reminder: ~/.claude/CLAUDE.md soll nur den minimalen ai-rem-Pointer "
        "enthalten. Falls hier Regeln/Praeferenzen/Wissen hinzukommen, gehoeren "
        "die nach ai-rem (memory_add), nicht in CLAUDE.md. Ist es nur der "
        "Pointer/@-Import, kann dieser Hinweis ignoriert werden."
    )
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": msg,
    }}))


if __name__ == "__main__":
    main()
    sys.exit(0)
