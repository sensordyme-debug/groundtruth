#!/usr/bin/env bash
# PREPARED, NOT ENABLED. Tailnet-only access to the demo (single port, NO Funnel). Monish runs it (docs/DEMO_DAY.md).
#   bash scripts/tailnet_serve.sh status
#   bash scripts/tailnet_serve.sh on  --i-am-monish-and-approve   → http://oryn:8000/ inside the tailnet only
#   bash scripts/tailnet_serve.sh off --i-am-monish-and-approve
# The API binds 127.0.0.1:8000 inside WSL; Windows forwards localhost to WSL; `tailscale serve --tcp` proxies the
# tailnet port to that localhost port. Nothing reaches the public internet (never `tailscale funnel`).
set -euo pipefail
cmd="${1:-status}"
if [ "$cmd" != status ] && [ "${2:-}" != "--i-am-monish-and-approve" ]; then
  echo "Not run. Read the header of $0 and docs/DEMO_DAY.md; add --i-am-monish-and-approve to execute." >&2
  exit 2
fi
case "$cmd" in
  status) powershell.exe -NoProfile -Command "tailscale serve status" ;;
  on) powershell.exe -NoProfile -Command "tailscale serve --bg --tcp 8000 tcp://localhost:8000; tailscale serve status" ;;
  off) powershell.exe -NoProfile -Command "tailscale serve --tcp=8000 off; tailscale serve status" ;;
  *) echo "usage: tailnet_serve.sh status | on|off --i-am-monish-and-approve"; exit 2 ;;
esac
