"""Transcript → Ollama-Extraktion → bulk-upsert in ai-rem.

Liest eine Claude-Code-Session-JSONL, flattened sie auf eine Konversation
(User-Texte + Assistant-Texte, ohne Tool-Calls/Results), schickt sie an
Ollama auf myubuntu mit format=json und bulk-upsert per MCPClient.

Backend-Wahl: Ollama statt `claude -p`, weil
- deterministisches `format: json` (kein Skill/Agent-Override-Risiko)
- kein Rate-Limit / kein API-Stress
- läuft eh schon auf myubuntu (paperless-ollama Container, GPU)

Anti-Rekursion:
- MIN_TRANSCRIPT_CHARS: kleine Sessions skippen (verhindert dass der
  Hook für triviale Subprocess-Sessions feuert).
- Lock-File /tmp/ai-rem-ingest.lock: parallele/verschachtelte ingest
  beißen sich gegenseitig ab.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, List, Optional

from .mcp_client import MCPClient

LOG_DIR = Path(os.path.expanduser("~/.claude/auto-memory"))
LOCK_FILE = Path("/tmp/ai-rem-ingest.lock")
FALLBACK_MD = LOG_DIR / "fallback.md"        # klassisches md-Auto-Memory wenn Ollama down
PENDING_JSONL = LOG_DIR / "pending.jsonl"    # verpasste Sessions → vom catchup nachgezogen
LAST_RUN = LOG_DIR / "last-run.json"         # Sichtbarkeit: was zuletzt gespeichert wurde
MAX_CHARS_PER_MSG = 4000
MAX_TOTAL_CHARS = 80_000
MIN_TRANSCRIPT_CHARS = 500
OLLAMA_URL = os.environ.get("AI_REM_OLLAMA_URL", "http://192.168.2.11:11434")
OLLAMA_MODEL_DEFAULT = "qwen3:14b"
OLLAMA_TIMEOUT_S = 300

SYSTEM_PROMPT_BASE = """OUTPUT: JSON nur. Kein Text.

{"entities":[{"type":"Decision|Problem|Solution|Tool|Preference|Project|Topic|Task|Person","name":"<60 Zeichen","description":"1-3 Sätze","context":"private|work|"}],"relations":[{"from_name":"...","relation":"NUTZT|LÄUFT_AUF|GELÖST_DURCH|HÄNGT_AB_VON|INTEGRIERT_MIT|BEVORZUGT|ARBEITET_AN|GETROFFEN_VON","to_name":"..."}]}

Leer: {"entities":[],"relations":[]}

SPEICHERN NUR wenn wörtlich im Transcript + (neuer Insight oder Muster). Types: Decision (mit Why+How), Problem→Solution, neue Tools/Infra, Feedback (Präfix "Feedback: "), Projekte, externe Topic-Pointer.

context="private" für: ai-rem, tools-mcp, myubuntu, mykeyvault, paperless-ai, mystorage, mydns, myM1Pro, evcc, ollama, chromadb, Finanzanalyse, zigbee2mqtt, openhab, photoprism, tvheadend, oscam, mosquitto, caddy, ~/.claude
context="work" nur: b-imtec Beratung/Kunden

NICHT speichern: Code-Pfade, git-log, Funktionsnamen, Rezepte, Smalltalk, triviale sofort-behobene Fehler.

DEDUP: Name aus Liste unten? Exakt verwenden. Sonst neu.

JSON sofort."""


def _content_to_text(content: Any) -> str:
    """Extract semantic text from a message content block.

    Drops tool_use / tool_result entirely (pure noise for extraction).
    Keeps text and thinking blocks (thinking often reveals reasoning
    that didn't make it into the visible reply).
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            t = block.get("type")
            if t == "text":
                parts.append(block.get("text", ""))
            elif t == "thinking":
                txt = block.get("thinking", "")
                if txt:
                    parts.append(f"[thinking] {txt}")
        return "\n".join(p for p in parts if p)
    return ""


def flatten_transcript(path: Path) -> str:
    """Reduce a JSONL session to a USER/ASSISTANT text dialogue."""
    lines = []
    total = 0
    with path.open(encoding="utf-8") as f:
        for raw in f:
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            t = rec.get("type")
            msg = rec.get("message")
            if t == "user" and isinstance(msg, dict):
                content = msg.get("content", "")
                if isinstance(content, list) and any(
                    isinstance(b, dict) and b.get("type") == "tool_result" for b in content
                ):
                    continue
                text = _content_to_text(content)
                role = "USER"
            elif t in ("assistant", "message") and isinstance(msg, dict):
                text = _content_to_text(msg.get("content", ""))
                role = "ASSISTANT"
            else:
                continue
            text = text.strip()
            if not text:
                continue
            if len(text) > MAX_CHARS_PER_MSG:
                text = text[:MAX_CHARS_PER_MSG] + "…[truncated]"
            chunk = f"{role}: {text}"
            total += len(chunk)
            if total > MAX_TOTAL_CHARS:
                lines.append("…[transcript truncated]")
                break
            lines.append(chunk)
    return "\n\n".join(lines)


_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _strip_json_envelope(text: str) -> str:
    text = text.strip()
    m = _JSON_FENCE.search(text)
    if m:
        return m.group(1).strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        return text.strip()
    return text


_NAME_LINE = re.compile(r"^-\s+\*\*(.+?)\*\*", re.MULTILINE)


def _fetch_known_entity_names(client: MCPClient) -> List[str]:
    """Pull all entity names from ai-rem so the prompt can encourage reuse.

    Format from memory_list is "### <Type>\\n- **<name>** ...".
    """
    try:
        listing = client.call("memory_list", {})
    except Exception:
        return []
    return [m.group(1).strip() for m in _NAME_LINE.finditer(listing)]


def _build_system_prompt(known_names: List[str]) -> str:
    if not known_names:
        return SYSTEM_PROMPT_BASE + "\n\nBekannte Entities: (keine)"
    limited = sorted(known_names)[-50:]
    block = "\n".join(f"- {n}" for n in limited)
    return (
        SYSTEM_PROMPT_BASE
        + f"\n\n({len(limited)}/{len(known_names)} bekannte — bevorzuge diese Namen):\n"
        + block
    )


def _ollama_up() -> bool:
    """Quick reachability probe so we can fall back to md before a slow timeout."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=3) as r:
            return getattr(r, "status", 200) == 200
    except Exception:
        return False


def call_ollama(transcript: str, model: str, system_prompt: str) -> dict:
    """POST /api/chat to Ollama with format=json for deterministic JSON output."""
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": transcript},
            ],
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.2, "num_ctx": 32768},
        }
    ).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT_S) as resp:
            envelope = json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        raise RuntimeError(f"Ollama unreachable ({OLLAMA_URL}): {e}") from e

    content = envelope.get("message", {}).get("content", "").strip()
    if not content:
        raise RuntimeError(f"Ollama returned empty content: {envelope}")
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Ollama lieferte kein gültiges JSON trotz format=json: {e}\n---\n{content[:500]}"
        ) from e


def upsert_entity(client: MCPClient, ent: dict) -> str:
    name = ent.get("name", "").strip()
    typ = ent.get("type", "").strip()
    if not name or not typ:
        return f"skip (incomplete): {ent}"
    args = {
        "name": name,
        "type": typ,
        "description": ent.get("description", "").strip(),
    }
    ctx = ent.get("context", "").strip()
    if ctx:
        args["context"] = ctx
    existing = client.call("memory_search", {"query": name, "limit": 5})
    matched = any(name.lower() in line.lower() for line in existing.splitlines())
    prefix = "[exists]" if matched else "[new]   "
    return f"{prefix} {client.call('memory_add', args)}"


def upsert_relation(client: MCPClient, rel: dict) -> str:
    args = {
        "from_name": rel.get("from_name", "").strip(),
        "relation": rel.get("relation", "").strip(),
        "to_name": rel.get("to_name", "").strip(),
    }
    if not all(args.values()):
        return f"skip (incomplete rel): {rel}"
    return f"[rel]   {client.call('memory_relate', args)}"


def _acquire_lock() -> bool:
    """Return True if we got the lock, False if someone else has it.

    Stale locks (file older than 10 minutes) are stolen.
    """
    if LOCK_FILE.exists():
        try:
            age = time.time() - LOCK_FILE.stat().st_mtime
        except FileNotFoundError:
            age = 999
        if age < 600:
            return False
    try:
        LOCK_FILE.write_text(str(os.getpid()))
    except OSError:
        return False
    return True


def _release_lock() -> None:
    try:
        LOCK_FILE.unlink()
    except FileNotFoundError:
        pass


def _write_last_run(session: str, extracted: dict, applied: list, mode: str) -> None:
    """Sichtbarkeit: kompakte Notiz was zuletzt gespeichert wurde (vom SessionStart-Hook gelesen)."""
    try:
        ents = [e.get("name") for e in extracted.get("entities", []) if e.get("name")]
        LAST_RUN.write_text(json.dumps({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "session": session, "mode": mode,
            "entities": ents, "entity_count": len(ents),
            "relations": len(extracted.get("relations", [])), "applied": len(applied),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _fallback_to_md(transcript_path: Path, flat: str, log_dir: Path, dry_run: bool) -> dict:
    """Ollama nicht erreichbar → klassisches Auto-Memory ins md schreiben + Session für
    späteren Catch-up vormerken. Inhalt aus dem Offline-Heuristik-Extraktor."""
    from .extractor_heuristic import extract_heuristic

    extracted = extract_heuristic(flat)
    session = transcript_path.stem
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    ents = extracted.get("entities", [])

    if not dry_run:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        block = [f"\n## {ts} — session {session} (provisorisch, Ollama offline)"]
        block += [f"- **[{e.get('type', '?')}] {e.get('name', '')}**: {e.get('description', '')}"
                  for e in ents] or ["- (keine heuristischen Treffer)"]
        with FALLBACK_MD.open("a", encoding="utf-8") as f:
            f.write("\n".join(block) + "\n")
        with PENDING_JSONL.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"session_id": session, "transcript_path": str(transcript_path),
                                "ts": ts}, ensure_ascii=False) + "\n")
        _write_last_run(session, extracted, [], mode="md")

    print(f"Ollama offline → md-Fallback ({len(ents)} heuristische Einträge) + gequeued",
          file=sys.stderr)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    log_path = log_dir / f"{stamp}-{session[:12]}.json"
    log_path.write_text(json.dumps(
        {"transcript": str(transcript_path), "fallback": "md",
         "extracted": extracted, "applied": []}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"extracted": extracted, "applied": [], "fallback": "md", "log": str(log_path)}


def _clear_fallback() -> None:
    """md leeren (Datei bleibt für den @import in CLAUDE.md erhalten)."""
    try:
        if FALLBACK_MD.exists():
            FALLBACK_MD.write_text("", encoding="utf-8")
    except Exception:
        pass


def catchup(client: MCPClient, log_dir: Optional[Path] = None) -> dict:
    """Verpasste (md-fallback) Sessions sauber nach ai-rem nachziehen, sobald Ollama
    wieder erreichbar ist — danach das md leeren. No-op wenn nichts ansteht / Ollama down."""
    if not PENDING_JSONL.exists():
        return {"skipped": "empty"}
    if not _ollama_up():
        return {"skipped": "ollama_down"}
    log_dir = log_dir or LOG_DIR

    entries = []
    for line in PENDING_JSONL.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not entries:
        PENDING_JSONL.unlink(missing_ok=True)
        _clear_fallback()
        return {"skipped": "empty"}

    processed, remaining = [], []
    for e in entries:
        tp = Path(e.get("transcript_path", ""))
        if not tp.exists():
            continue  # verwaist → still verwerfen
        try:
            ingest_transcript(client, tp, dry_run=False,
                              model=OLLAMA_MODEL_DEFAULT, log_dir=log_dir)
            processed.append(e.get("session_id"))
        except Exception as ex:
            print(f"catchup: {tp} failed: {ex}", file=sys.stderr)
            remaining.append(e)

    if remaining:
        PENDING_JSONL.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in remaining) + "\n",
            encoding="utf-8")
    else:
        PENDING_JSONL.unlink(missing_ok=True)
        _clear_fallback()

    result = {"processed": processed, "processed_count": len(processed),
              "remaining": len(remaining)}
    stamp = time.strftime("%Y%m%dT%H%M%S")
    (log_dir / f"{stamp}-catchup.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"catchup: {len(processed)} nachgezogen, {len(remaining)} offen", file=sys.stderr)
    return result


def ingest_transcript(
    client: MCPClient,
    transcript_path: Path,
    dry_run: bool = False,
    model: str = OLLAMA_MODEL_DEFAULT,
    log_dir: Optional[Path] = None,
) -> dict:
    if not transcript_path.exists():
        raise FileNotFoundError(transcript_path)

    log_dir = log_dir or LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)

    flat = flatten_transcript(transcript_path)
    if len(flat) < MIN_TRANSCRIPT_CHARS:
        print(
            f"transcript zu klein ({len(flat)} < {MIN_TRANSCRIPT_CHARS} chars) — skip",
            file=sys.stderr,
        )
        return {"entities": [], "relations": [], "applied": [], "skipped": "too_small"}

    if not _acquire_lock():
        print(f"ingest läuft bereits (lock {LOCK_FILE}) — skip", file=sys.stderr)
        return {"entities": [], "relations": [], "applied": [], "skipped": "locked"}

    try:
        known = _fetch_known_entity_names(client)
        prompt = _build_system_prompt(known)
        print(
            f"transcript: {transcript_path} ({len(flat):,} Zeichen, "
            f"Modell={model}, bekannte Entities: {len(known)})",
            file=sys.stderr,
        )
        try:
            extracted = call_ollama(flat, model=model, system_prompt=prompt)
        except RuntimeError as e:
            # Ollama nicht erreichbar → klassisches md-Auto-Memory + Catch-up-Queue.
            # Andere Fehler (z.B. ungültiges JSON) bleiben echte Fehler.
            if "unreachable" in str(e).lower() or not _ollama_up():
                return _fallback_to_md(transcript_path, flat, log_dir, dry_run)
            raise

        applied = []
        if dry_run:
            print("--- DRY-RUN: extrahiert ---")
            print(json.dumps(extracted, ensure_ascii=False, indent=2))
        else:
            for ent in extracted.get("entities", []):
                try:
                    applied.append(upsert_entity(client, ent))
                except Exception as e:
                    applied.append(f"[err]   entity {ent.get('name','?')}: {e}")
            for rel in extracted.get("relations", []):
                try:
                    applied.append(upsert_relation(client, rel))
                except Exception as e:
                    applied.append(f"[err]   rel {rel}: {e}")
            for line in applied:
                print(line)

        stamp = time.strftime("%Y%m%dT%H%M%S")
        log_path = log_dir / f"{stamp}-{transcript_path.stem[:12]}.json"
        log_path.write_text(
            json.dumps(
                {
                    "transcript": str(transcript_path),
                    "model": model,
                    "dry_run": dry_run,
                    "extracted": extracted,
                    "applied": applied,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"log: {log_path}", file=sys.stderr)
        if not dry_run:
            _write_last_run(transcript_path.stem, extracted, applied, mode="ollama")
        return {"extracted": extracted, "applied": applied, "log": str(log_path)}
    finally:
        _release_lock()
