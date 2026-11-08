#!/usr/bin/env bash
# Build (default) or dev-serve the conversational app (web/app-v2) with the fnm-installed Node.
set -euo pipefail
cd "$(dirname "$0")/../web"
NODE_BIN="$(ls -d "$HOME"/.local/share/fnm/node-versions/*/installation/bin 2>/dev/null | sort -V | tail -1)"
export PATH="$NODE_BIN:$PATH"
case "${1:-build}" in
  build) ./node_modules/.bin/tsc -p app-v2/tsconfig.json && ./node_modules/.bin/vite build app-v2 ;;
  dev) ./node_modules/.bin/vite app-v2 ;;
esac
