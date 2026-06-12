# Token savings

[← Back to README](../README.md)

ai-rem doesn't add knowledge to every prompt — it **lazy-loads** only the relevant subgraph on demand instead of carrying everything in `CLAUDE.md` all session long. The per-session footprint stays roughly constant (~1–3k tokens) no matter how large the graph grows, while the alternative — stuffing all knowledge into `CLAUDE.md` — costs ~20k tokens loaded into *every* session.

**Worked estimate — based on measured usage (~4.3 sessions/day):**

| Parameter | Value | Source |
|---|---|---|
| Sessions / month | ~4.3 × 30 = **~130** | measured (141 sessions over 33 days) |
| Sessions with real recall | ~59 % → **~76** | measured (83/141 sessions used ai-rem) |
| Trivial sessions | ~54 | derived |
| Savings per recall session | ~12k tokens | modelled (avoided re-discovery / no permanent `CLAUDE.md` ballast) |
| Retrieval payload per recall session | ~2.8k tokens | measured (~7.8 ai-rem calls/session, ~360 tokens/call) |
| Hook overhead (every session) | ~300 tokens | modelled |

```
Gain:       76 recall sessions × 12,000 =  912,000
Retrieval:  76 recall sessions ×  2,800 =  212,800
Hook:      130 sessions        ×    300 =   39,000
───────────────────────────────────────────────────
Net ≈ 660,000 tokens / month saved
```

**Result: ~0.7 million tokens/month** at ~4.3 sessions/day — roughly **3 full 200k context windows** you don't burn on re-explaining context, re-discovering infrastructure, or permanent `CLAUDE.md` bloat. Per day that's ~22k tokens; per year ~8M.

**Range** (depending on how knowledge-heavy your sessions are):

| Scenario | Recall sessions | Tokens/session | Net / month |
|---|---|---|---|
| Conservative | 65 (50 %) | 8k | **~0.3M** |
| Typical | 76 (59 %) | 12k | **~0.7M** |
| Intensive | 91 (70 %) | 16k | **~1.2M** |

**The savings grow as the graph grows.** Because only the *relevant* subgraph is loaded on demand, ai-rem's per-session cost stays flat regardless of graph size, while the `CLAUDE.md` alternative scales **linearly** — every new fact is paid for in *every* session forever. The numbers above (262 entities) are an early-stage snapshot; at 500+ entities the same usage pattern saves substantially more.

> The session count, recall rate, and retrieval payload are **measured** from real usage (141 sessions over 33 days, 2026-05-11 – 2026-06-12, re-measured from the Claude Code transcripts via `bin/measure-savings.py`). The per-session savings (8–16k) is a model, not a measurement — the "what it would have cost without ai-rem" can't be observed directly. Treat the totals as an informed estimate, not a benchmark.
