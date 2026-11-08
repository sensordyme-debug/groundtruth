#!/usr/bin/env bash
# Run a command on a remote build/API host over SSH (e.g. via Tailscale). Never prints keys.
# Host details come from the environment only (nothing machine-specific is committed):
#   export GT_REMOTE=user@host GT_REMOTE_KEY=~/.ssh/<keyfile>
#   bash scripts/mac.sh 'uname -a'
#   bash scripts/mac.sh --script scripts/remote/mac_inventory.sh     (streams a local script to remote bash)
set -euo pipefail
: "${GT_REMOTE:?set GT_REMOTE=user@host}"
OPTS=(-o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=15 -o ServerAliveInterval=30
      -o StrictHostKeyChecking=accept-new)
[ -n "${GT_REMOTE_KEY:-}" ] && OPTS+=(-i "$GT_REMOTE_KEY")
if [ "${1:-}" = "--script" ]; then
  f="$2"; shift 2
  exec ssh "${OPTS[@]}" "$GT_REMOTE" "bash -s -- $*" < "$f"
fi
exec ssh "${OPTS[@]}" "$GT_REMOTE" "$@"
