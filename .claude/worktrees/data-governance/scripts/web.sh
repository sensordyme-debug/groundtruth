#!/usr/bin/env bash
# Run npm in web/ with the fnm-installed Node. Usage: scripts/web.sh install | build | run dev
set -euo pipefail
cd "$(dirname "$0")/../web"
NODE_BIN="$(ls -d "$HOME"/.local/share/fnm/node-versions/*/installation/bin 2>/dev/null | sort -V | tail -1)"
export PATH="$NODE_BIN:$PATH"
case "${1:-build}" in
  install) npm install --no-audit --no-fund ;;
  build) npm run build ;;
  *) npm "$@" ;;
esac
