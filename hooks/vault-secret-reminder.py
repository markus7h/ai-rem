#!/usr/bin/env python3
# PostToolUse(Bash)-Hook: erkennt Auth-/Secret-Fehler in der Befehlsausgabe (401,
# "gh auth login", Permission denied (publickey), ...) und erinnert daran, das Secret
# aus mykeyvault zu holen statt den User um Token/Login zu bitten.
# Anlass: ein abgelaufenes gh-PAT — Claude bat den User um `gh auth login`, obwohl
# das passende PAT im Vault lag und sofort funktionierte.
# Fail-silent: blockiert nie.
# ponytail: reine Regex auf der Ausgabe, kein Parsen pro Tool-Typ. False Positive
#           kostet eine überflüssige Zeile Kontext; ein verpasster Vault-Griff kostet
#           eine Rückfrage an den User. Falls zu geschwätzig: Patterns kürzen.
import json
import re
import sys

# Muster, die auf fehlende/abgelaufene Credentials hindeuten.
AUTH_PATTERNS = [
    r"HTTP 401",
    r"\b401\s+Unauthorized\b",
    r"\b403\s+Forbidden\b",
    r"gh auth login",
    r"Requires authentication",
    r"authentication required",
    r"Permission denied \(publickey\)",
    r"Bad credentials",
    r"invalid[_ ]token",
    r"token .{0,20}(expired|invalid)",
    r"could not read Username",
    r"Login failed",
    r"\bENOTAUTH\b",
    r"Unauthorized",
]

_RX = re.compile("|".join(AUTH_PATTERNS), re.IGNORECASE)

# Befehle, die fremden Text nur ANZEIGEN: dort ist ein Auth-Muster fast immer Zitat
# (ein `git diff` dieser Datei, ein grep über die Doku), kein echter Fehler.
# ponytail: Befehlsname genügt als Ausschluss. Ein `git diff && curl` verliert damit
#           die Erinnerung — dann sagt der nächste echte Fehlversuch es erneut.
READERS = re.compile(
    r"\b(git\s+(diff|log|show|blame)|grep|rg|ag|cat|less|head|tail|sed|awk)\b"
)


def extract_text(data):
    """Sammelt allen Ausgabetext aus dem PostToolUse-Payload.

    Feldname/Form variieren je Version (tool_response vs. tool_output; str vs. dict),
    darum defensiv alles einsammeln, was danach aussieht.
    """
    parts = []
    for key in ("tool_response", "tool_output"):
        val = data.get(key)
        if isinstance(val, str):
            parts.append(val)
        elif isinstance(val, dict):
            for k in ("stdout", "stderr", "output", "content", "error"):
                sub = val.get(k)
                if isinstance(sub, str):
                    parts.append(sub)
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    for sub in item.values():
                        if isinstance(sub, str):
                            parts.append(sub)
    return "\n".join(parts)


def detect(text, cmd=""):
    if cmd and READERS.search(cmd):
        return None
    m = _RX.search(text or "")
    return m.group(0) if m else None


def main():
    data = json.load(sys.stdin)
    if data.get("tool_name") != "Bash":
        return
    cmd = (data.get("tool_input") or {}).get("command", "")
    hit = detect(extract_text(data), cmd)
    if not hit:
        return
    msg = (
        f"⏺ Auth-/Credential-Fehler erkannt ({hit!r}). Regel 'Secrets aus mykeyvault': "
        "Bitte den User NICHT um Token, Passwort oder interaktives Login. Prüfe ZUERST "
        "mykeyvault — vault_list_items zeigt die Einträge, vault_run_with_secret "
        "injiziert das Secret wertblind als Env-Var (vault_run_with_secret_file für "
        "SSH-Keys/PEM). Erst wenn dort nichts Passendes liegt, den User fragen."
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": msg,
        }
    }))


if __name__ == "__main__" and "--selftest" in sys.argv:
    assert detect("HTTP 401: Requires authentication") == "HTTP 401"
    assert detect("Try authenticating with: gh auth login") == "gh auth login"
    assert detect("git@github.com: Permission denied (publickey).")
    assert detect("remote: Bad credentials")
    assert detect("Everything up-to-date") is None
    assert detect("2 files changed") is None
    assert detect("") is None
    # Payload-Formen: dict mit stdout/stderr, sowie flacher String
    assert detect(extract_text({"tool_response": {"stderr": "HTTP 401"}})) == "HTTP 401"
    assert detect(extract_text({"tool_response": "gh auth login"})) == "gh auth login"
    assert detect(extract_text({"tool_output": {"stdout": "Bad credentials"}}))
    assert detect(extract_text({"tool_response": {"stdout": "ok"}})) is None
    # Anzeige-Befehle: Muster im ZITAT darf nicht triggern ...
    assert detect("HTTP 401", "git diff docs/") is None
    assert detect("gh auth login", "grep -rn auth hooks/") is None
    assert detect("Bad credentials", "cat hook.py") is None
    # ... ein echter Auth-Fehler aber weiterhin schon
    assert detect("HTTP 401", "gh pr create") == "HTTP 401"
    assert detect("HTTP 401", "git push origin main") == "HTTP 401"
    print("selftest ok")
    sys.exit(0)

try:
    main()
except Exception:
    pass
