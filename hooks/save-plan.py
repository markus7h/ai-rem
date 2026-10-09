#!/usr/bin/env python3
# Claude Code PostToolUse hook for ExitPlanMode: stores the just-finalized plan as
# an open Task in ai-rem so plans become a central, cross-machine list ("any open
# plans?") instead of just slug files on disk.
#
# Source of the fields is the plan file's YAML-style frontmatter (name / description
# / status) — no heuristic extraction from prose. Claude is expected to start every
# plan file with such a block:
#
#   ---
#   name: "Plan: <title>"
#   description: "<one short sentence>"
#   status: offen
#   ---
#
# Install: copy to ~/.claude/hooks/save-plan.py (chmod +x) and register in
# ~/.claude/settings.json:
#
#   "hooks": { "PostToolUse": [
#     { "matcher": "ExitPlanMode",
#       "hooks": [{ "type": "command",
#                   "command": "<HOME>/.claude/hooks/save-plan.py",
#                   "timeout": 10 }] } ] }
#
# Transport mirrors the other ai-rem hooks (initialize -> notifications/initialized
# -> tools/call). Auth: AI_REM_TOKEN env, else the device token from the OS keychain
# (lib/keychain.py), else the legacy Bearer header still sitting in ~/.claude.json.
# Fail-silent: never blocks ExitPlanMode.
import datetime
import glob
import json
import os
import re
import urllib.request

ENDPOINT = os.environ.get("AI_REM_ENDPOINT", "http://localhost:3456/mcp")
TIMEOUT = 8
_CC = os.environ.get("CLAUDE_CONFIG_DIR", "").split(os.pathsep)[0].strip()
CLAUDE_DIR = _CC or os.path.expanduser("~/.claude")
CLAUDE_JSON = os.path.join(_CC, ".claude.json") if _CC else os.path.expanduser("~/.claude.json")
PLANS_DIR = os.path.join(CLAUDE_DIR, "plans")


def _keychain_token():
    """Geraete-Token aus dem OS-Keychain via lib/keychain.py. Das Modul liegt nach dem
    Setup neben der CLI (nicht neben dem Hook), darum per Pfad laden: zuerst relativ
    zu $AI_REM_CLI, sonst die Standard-Installation. Jeder Fehler -> "" — der Hook
    bleibt fail-silent, und Secrets werden nie geloggt. (Bewusst dupliziert aus
    system-check.py: die Hooks laufen standalone in ~/.claude/hooks.)"""
    import importlib.util

    cands = []
    cli = os.environ.get("AI_REM_CLI", "")
    if cli:
        cands.append(os.path.join(os.path.dirname(os.path.dirname(cli)), "lib", "keychain.py"))
    cands.append(os.path.expanduser("~/.local/share/ai-rem/lib/keychain.py"))
    for p in cands:
        if not os.path.isfile(p):
            continue
        try:
            spec = importlib.util.spec_from_file_location("ai_rem_keychain", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return (mod.get() or "").strip()
        except Exception:
            return ""
    return ""


def auth_header():
    """Env AI_REM_TOKEN -> OS-Keychain -> Legacy-Header aus ~/.claude.json (nur lesen,
    Uebergangszeit bis `ai-rem update` ihn entfernt hat). None, wenn nichts da ist."""
    tok = os.environ.get("AI_REM_TOKEN", "").strip() or _keychain_token()
    if tok:
        return tok if tok.lower().startswith("bearer ") else f"Bearer {tok}"
    try:
        cfg = json.load(open(CLAUDE_JSON))
        return cfg["mcpServers"]["ai-rem"]["headers"]["Authorization"]
    except Exception:
        return None


AUTH = auth_header()


def post(body, sid=None):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if AUTH:
        headers["Authorization"] = AUTH
    if sid:
        headers["mcp-session-id"] = sid
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    return urllib.request.urlopen(req, timeout=TIMEOUT)


def strip_quotes(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def parse_frontmatter(path):
    """Only the block between the first two '---' lines; simple key: value."""
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fm = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if m:
            fm[m.group(1)] = strip_quotes(m.group(2))
    return fm


def newest_plan():
    files = glob.glob(os.path.join(PLANS_DIR, "*.md"))
    return max(files, key=os.path.getmtime) if files else None


def main():
    path = newest_plan()
    if not path:
        return
    fm = parse_frontmatter(path)
    name = fm.get("name")
    if not name:
        return  # no frontmatter / no name -> deliberately write nothing
    args = {
        "name": name,
        "type": "Task",
        "description": fm.get("description", ""),
        "extra": {
            "kind": "plan",
            "status": fm.get("status", "offen") or "offen",
            "plan_file": os.path.abspath(path),
            "created": datetime.date.today().isoformat(),
        },
        "context": "private",
    }

    resp = post({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "claude-code-save-plan", "version": "1.0"}},
    })
    sid = resp.headers.get("mcp-session-id")
    resp.read()
    if not sid:
        return
    try:
        post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid=sid).read()
    except Exception:
        pass
    post({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "memory_add", "arguments": args},
    }, sid=sid).read()


try:
    main()
except Exception:
    pass
