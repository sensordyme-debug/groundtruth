#!/usr/bin/env bash
# (PC WSL) smoke the intelligence-branch endpoints on the running API (no secrets printed)
TOKF=/mnt/c/Users/orynt/GeoSpatial/Geospatial-SearchEngine/data/app/v1/admin_token
AUTH=(); [ -n "${GROUNDTRUTH_API_TOKEN:-}" ] && AUTH=(-H "Authorization: Bearer $GROUNDTRUTH_API_TOKEN")
[ ${#AUTH[@]} -eq 0 ] && [ -f "$TOKF" ] && AUTH=(-H "Authorization: Bearer $(cat "$TOKF")")
echo "== unauthenticated (expect 401)"; curl -s -o /dev/null -w "%{http_code}\n" localhost:8000/api/v1/sessions
for u in places/providers validation/progress sources runtime; do
  echo "== $u"; curl -s "localhost:8000/api/v1/$u" "${AUTH[@]}" -H "X-GT-Owner: regression-0001" | head -c 400; echo
done
curl -s -X POST localhost:8000/api/v1/places/search "${AUTH[@]}" -H "Content-Type: application/json" -d '{"query":"restaurants","area":{"place":"Myers Park"}}' | head -c 300; echo
