#!/usr/bin/env bash
ls -la /mnt/c/Users/*/Downloads/ 2>/dev/null | grep -iE "DESIGN_TARGET|Main\.dc|Groundtruth Conversational" 
echo "== API health"; curl -s -m 5 localhost:8000/api/v1/health | python3 -c "import sys,json; h=json.load(sys.stdin); print({k:h.get(k) for k in ('ok','contract','primary_year')}, [(i['aoi'],i['year'],i['chips']) for i in h.get('indexes',[])])" 2>&1
echo "== API process env"; p=$(pgrep -f "uvicorn api.app:app" | head -1); echo "pid $p cwd $(readlink /proc/$p/cwd)"; tr '\0' '\n' < /proc/$p/environ | grep -E "^GROUNDTRUTH_(DATA|GPU|DEVICE)" 
echo "== MOCK occurrences"; cd /mnt/c/Users/orynt/GeoSpatial/gt-redesign && grep -rIl "MOCK" web/src web/index.html api engine 2>/dev/null | head; ls web/dist 2>/dev/null | head -3
echo "== south_charlotte refs"; grep -rIn "south_charlotte" web/src | head -5
