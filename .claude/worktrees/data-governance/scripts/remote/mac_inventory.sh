#!/bin/bash
# Read-only: what is listening / running on the Mac mini (no secrets, no file contents).
echo "== listening TCP (local address)"
netstat -anv -p tcp 2>/dev/null | awk '/LISTEN/ {print $4}' | sort -u
echo "== listening processes (own user)"
lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null | awk 'NR>1 {print $1, $9}' | sort -u
echo "== home dirs"
ls ~ | head -50
echo "== oryn-like dirs"
ls -d ~/oryn* ~/*/oryn* ~/Projects/* 2>/dev/null | head -20
echo "== launchd jobs (filtered)"
launchctl list 2>/dev/null | grep -iE 'oryn|titiler|postgres|uvicorn|node|python|docker|ollama' | head -20
echo "== docker"
(command -v docker && docker ps --format '{{.Names}} {{.Ports}}') 2>/dev/null | head
echo "== tailscale serve/funnel (status only)"
(/Applications/Tailscale.app/Contents/MacOS/Tailscale serve status 2>&1 || tailscale serve status 2>&1) | head -10
