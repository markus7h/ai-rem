# Token-Ersparnis

[← Zurück zur README](../README.de.md)

ai-rem hängt nicht jedem Prompt Wissen an — es **lädt bedarfsweise** nur den relevanten Subgraph, statt alles über die ganze Session in der `CLAUDE.md` mitzuschleppen. Die Last pro Session bleibt nahezu konstant (~1–3k Token), egal wie stark der Graph wächst, während die Alternative — alles Wissen in die `CLAUDE.md` packen — ~20k Token in *jede* Session lädt.

**Beispielrechnung — auf Basis gemessener Nutzung (~4,3 Sessions/Tag):**

| Parameter | Wert | Quelle |
|---|---|---|
| Sessions / Monat | ~4,3 × 30 = **~130** | gemessen (141 Sessions über 33 Tage) |
| Sessions mit echtem Recall | ~59 % → **~76** | gemessen (83/141 Sessions nutzten ai-rem) |
| Triviale Sessions | ~54 | abgeleitet |
| Ersparnis pro Recall-Session | ~12k Token | modelliert (vermiedenes Re-Discovery / kein dauerhafter `CLAUDE.md`-Ballast) |
| Retrieval-Payload pro Recall-Session | ~2,8k Token | gemessen (~7,8 ai-rem-Aufrufe/Session, ~360 Token/Aufruf) |
| Hook-Overhead (jede Session) | ~300 Token | modelliert |

```
Gewinn:     76 Recall-Sessions × 12.000 =  912.000
Retrieval:  76 Recall-Sessions ×  2.800 =  212.800
Hook:      130 Sessions        ×    300 =   39.000
───────────────────────────────────────────────────
Netto ≈ 660.000 Token / Monat gespart
```

**Ergebnis: ~0,7 Mio Token/Monat** bei ~4,3 Sessions/Tag — grob **3 volle 200k-Kontextfenster**, die nicht für Re-Erklären von Kontext, Re-Discovery von Infrastruktur oder dauerhaften `CLAUDE.md`-Ballast draufgehen. Pro Tag ~22k Token, pro Jahr ~8 Mio.

**Bandbreite** (je nachdem, wie wissensintensiv die Sessions sind):

| Szenario | Recall-Sessions | Token/Session | Netto / Monat |
|---|---|---|---|
| Konservativ | 65 (50 %) | 8k | **~0,3 Mio** |
| Typisch | 76 (59 %) | 12k | **~0,7 Mio** |
| Intensiv | 91 (70 %) | 16k | **~1,2 Mio** |

**Die Ersparnis steigt, je größer der Graph wird.** Weil immer nur der *relevante* Subgraph bedarfsweise geladen wird, bleiben ai-rems Kosten pro Session flach — unabhängig von der Graph-Größe —, während der `CLAUDE.md`-Ansatz **linear** skaliert: Jeder neue Fakt wird in *jeder* Session aufs Neue bezahlt, für immer. Die Zahlen oben (262 Entities) sind eine Momentaufnahme der Frühphase; bei 500+ Entities spart dasselbe Nutzungsmuster deutlich mehr.

> Session-Zahl, Recall-Rate und Retrieval-Payload sind aus echter Nutzung **gemessen** (141 Sessions über 33 Tage, 11.05.–12.06.2026, nachgemessen aus den Claude-Code-Transcripts via `bin/measure-savings.py`). Die Ersparnis pro Session (8–16k) ist ein Modell, keine Messung — das „was es ohne ai-rem gekostet hätte" lässt sich nicht direkt beobachten. Die Summen sind also eine fundierte Schätzung, kein Benchmark.

---

## Bonus: Built-in-Tool-Surface verschlanken

Unabhängig vom Knowledge Graph gibt es einen zweiten Hebel im **Sockel**, den Claude Code bei *jeder* Session vor der ersten Aufgabe lädt. Größter Einzelposten dort sind die **System tools** — die JSON-Schemas aller eingebauten Tools (`Bash`, `Workflow`, `DesignSync`, `Monitor`, die Cron-/Worktree-Familie …), vollständig geladen, egal ob genutzt oder nicht.

Einen offiziellen Per-Tool-Schalter gibt es noch nicht ([anthropics/claude-code#54716](https://github.com/anthropics/claude-code/issues/54716)), aber ein Tool-Name in `permissions.deny` entfernt dessen Instruktionen/Schema aus dem Systemprompt (jedes Built-in ist intern ein MCP-Server, dessen Beschreibung echte Token kostet).

Das ai-rem-Setup trägt deshalb einen Default-`permissions.deny`-Satz für selten genutzte, schema-schwere Built-ins ein:

```
DesignSync, Workflow, NotebookEdit, RemoteTrigger, ScheduleWakeup,
CronCreate, CronDelete, CronList, EnterWorktree, ExitWorktree,
Monitor, PushNotification, ListMcpResourcesTool, ReadMcpResourceTool
```

**Gemessener Effekt (Opus 4.8, `/context`):** System tools von **33,2k → 10,6k Token** — eine einmalige **~22k-Ersparnis im Sockel jeder Session**, zusätzlich zur Lazy-Loading-Ersparnis oben. Kernwerkzeuge (`Bash`, `Read`/`Edit`/`Write`, `Skill`, Plan-Modus) bleiben unangetastet. Um eine geblockte Fähigkeit zurückzuholen, die entsprechende Zeile aus `permissions.deny` in `~/.claude/settings.json` entfernen. Ein erneuter Setup-Lauf ist idempotent — er ergänzt nur Fehlendes und dupliziert nie.
