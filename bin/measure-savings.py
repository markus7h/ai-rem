#!/usr/bin/env python3
"""Re-measure the token-savings basis in README.md ("Token savings" section)
from the local Claude Code transcripts (~/.claude/projects/*/*.jsonl).

Measured per session: date, whether ai-rem MCP tools were used, number of
ai-rem calls, and the size of the ai-rem tool-result payload (chars / ~4 as a
rough token estimate). Agent sidechains and near-empty sessions are skipped.

Note: Claude Code prunes transcripts after ~30 days (cleanupPeriodDays), so
the window only covers what is still on disk. The per-recall-session savings
itself stays a model — this script only measures usage, recall rate and
retrieval overhead.
"""
import json, glob, os, datetime, collections

files = glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl"))
sessions = []  # (date, used_airem, n_calls, result_chars)

for f in files:
    first_ts = None
    used = False
    n_calls = 0
    result_chars = 0
    n_msgs = 0
    sidechain = False
    try:
        with open(f, encoding="utf-8", errors="replace") as fh:
            pending_airem = set()
            for line in fh:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                ts = rec.get("timestamp")
                if ts and first_ts is None:
                    first_ts = ts
                if rec.get("isSidechain"):
                    sidechain = True
                msg = rec.get("message") or {}
                content = msg.get("content")
                if isinstance(content, list):
                    for c in content:
                        if not isinstance(c, dict):
                            continue
                        if c.get("type") == "tool_use" and str(c.get("name", "")).startswith("mcp__ai-rem__"):
                            used = True
                            n_calls += 1
                            pending_airem.add(c.get("id"))
                        elif c.get("type") == "tool_result" and c.get("tool_use_id") in pending_airem:
                            result_chars += len(json.dumps(c.get("content", ""), ensure_ascii=False))
                if rec.get("type") in ("user", "assistant"):
                    n_msgs += 1
    except Exception:
        continue
    if first_ts is None or sidechain or n_msgs < 2:
        continue
    sessions.append((first_ts[:10], used, n_calls, result_chars))

sessions.sort()
if not sessions:
    print("no sessions found")
    raise SystemExit(1)

dates = [s[0] for s in sessions]
days = (datetime.date.fromisoformat(dates[-1]) - datetime.date.fromisoformat(dates[0])).days + 1
total = len(sessions)
recall = [s for s in sessions if s[1]]
calls = sum(s[2] for s in recall)
chars = sum(s[3] for s in recall)

print(f"Transcript window: {dates[0]} .. {dates[-1]}  ({days} days)")
print(f"Total sessions: {total}  ({total/days:.1f}/day)")
print(f"Sessions using ai-rem: {len(recall)}  ({100*len(recall)/total:.0f} %)")
print(f"ai-rem tool calls total: {calls}  (avg {calls/max(len(recall),1):.1f}/recall-session)")
print(f"ai-rem result payload: {chars} chars ≈ {chars//4} tokens total, "
      f"≈ {chars//4//max(len(recall),1)} tokens/recall-session")

by_month = collections.Counter(d[:7] for d in dates)
by_month_recall = collections.Counter(s[0][:7] for s in recall)
print("\nPer month: total / with-ai-rem")
for m in sorted(by_month):
    print(f"  {m}: {by_month[m]} / {by_month_recall.get(m, 0)}")
