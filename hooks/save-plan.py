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
# -> tools/call). Auth: AI_REM_TOKEN env, else the Bearer header already written to
# ~/.claude.json by the SessionStart hook. Fail-silent: never blocks ExitPlanMode.
import datetime
import glob
import json
import os
import re
import urllib.request

ENDPOINT = os.environ.get("AI_REM_ENDPOINT", "http://localhost:3456/mcp")
TIMEOUT = 8
PLANS_DIR = os.path.expanduser("~/.claude/plans")


def auth_header():
    tok = os.environ.get("AI_REM_TOKEN")
    if tok:
        return tok if tok.lower().startswith("bearer ") else f"Bearer {tok}"
    try:
        cfg = json.load(open(os.path.expanduser("~/.claude.json")))
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
