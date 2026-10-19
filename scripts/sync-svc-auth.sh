#!/usr/bin/env bash
# lib/svc_auth.py (kanonisch) in die Dienste kopieren, die das Auth-Gate nutzen.
# Kopie statt Paket: die Images bauen unabhängig und sollen nichts von ai-rem ziehen.
# Aufruf: scripts/sync-svc-auth.sh [--check]   (--check: nur Abweichungen melden, Exit 1)
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)/lib/svc_auth.py"
BASE="$(cd "$(dirname "$0")/../.." && pwd)"
TARGETS=("$BASE/doc-graph/svc_auth.py" "$BASE/case-assist/svc_auth.py")
rc=0
for t in "${TARGETS[@]}"; do
  if [ ! -d "$(dirname "$t")" ]; then echo "übersprungen (Repo fehlt): $t"; continue; fi
  if cmp -s "$SRC" "$t"; then echo "aktuell: $t"; continue; fi
  if [ "${1:-}" = "--check" ]; then echo "VERALTET: $t"; rc=1; else cp "$SRC" "$t"; echo "kopiert: $t"; fi
done
exit $rc
