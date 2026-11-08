#!/usr/bin/env bash
# npm in web/ with the fnm Node (args passed through)
set -euo pipefail
cd "$(dirname "$0")/../web"
NODE_BIN="$(ls -d "$HOME"/.local/share/fnm/node-versions/*/installation/bin 2>/dev/null | sort -V | tail -1)"
export PATH="$NODE_BIN:$PATH"
npm "$@"
