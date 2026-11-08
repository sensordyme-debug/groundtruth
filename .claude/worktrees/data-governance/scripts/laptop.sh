#!/usr/bin/env bash
# Run a bash script (stdin) inside Ubuntu-24.04 on the ASUS laptop ("quantcompute") over Tailscale SSH.
#   bash scripts/laptop.sh <<'EOF'
#   free -g
#   EOF
# Uses the existing key (never printed). No SSH server inside Ubuntu: SSH lands in Windows, then `wsl -- bash -ls`.
exec ssh -i "$HOME/.ssh/id_ed25519_oryn" -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=30 \
  ashur@quantcompute "wsl -d Ubuntu-24.04 -- bash -ls"
