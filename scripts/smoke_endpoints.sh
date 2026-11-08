#!/usr/bin/env bash
# (PC WSL) smoke the intelligence-branch endpoints on the running API (no secrets printed)
for u in places/providers validation/progress sources runtime; do
  echo "== $u"; curl -s "localhost:8000/api/v1/$u" -H "X-GT-Owner: regression-0001" | head -c 400; echo
done
curl -s -X POST localhost:8000/api/v1/places/search -H "Content-Type: application/json" -d '{"query":"restaurants","area":{"place":"Myers Park"}}' | head -c 300; echo
