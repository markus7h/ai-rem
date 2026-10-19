#!/usr/bin/env bash
# lib/svc_auth.py (kanonisch) in die Repos der Dienste kopieren, die das Auth-Gate nutzen.
# Kopie statt Paket: die Images bauen unabhängig und sollen nichts von ai-rem ziehen.
# Aufruf: scripts/sync-svc-auth.sh [--check] <repo-verzeichnis>...
#   --check: nur Abweichungen melden (Exit 1), nichts kopieren
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)/lib/svc_auth.py"
CHECK=0
[ "${1:-}" = "--check" ] && { CHECK=1; shift; }
[ $# -gt 0 ] || { echo "Aufruf: $0 [--check] <repo-verzeichnis>..." >&2; exit 2; }
rc=0
for d in "$@"; do
  t="$d/svc_auth.py"
  if [ ! -d "$d" ]; then echo "übersprungen (fehlt): $d"; continue; fi
  if cmp -s "$SRC" "$t"; then echo "aktuell: $t"; continue; fi
  if [ $CHECK = 1 ]; then echo "VERALTET: $t"; rc=1; else cp "$SRC" "$t"; echo "kopiert: $t"; fi
done
exit $rc
