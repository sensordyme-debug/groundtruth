#!/usr/bin/env bash
# Redact signed-URL query strings (SAS tokens) from local logs. Run only on logs no process is writing.
set -euo pipefail
cd "$(dirname "$0")/.."
for f in "$@"; do
  [ -f "$f" ] || continue
  sed -i -E 's/(\.tif)\?[^" ]*/\1?<sas-redacted>/g; s/(sig=)[^&" ]*/\1<redacted>/g' "$f"
  echo "scrubbed $f ($(grep -c 'sig=' "$f" || true) sig= left)"
done
