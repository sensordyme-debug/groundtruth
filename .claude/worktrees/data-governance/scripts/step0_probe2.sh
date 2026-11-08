#!/usr/bin/env bash
cd /mnt/c/Users/orynt/GeoSpatial/gt-redesign
grep -n "_DIST\|def ui\|StaticFiles\|index.html" api/app.py | head -8
curl -s -m 5 localhost:8000/ | head -c 400; echo
js=$(curl -s localhost:8000/ | grep -oE 'assets/[^"]+\.js' | head -1); echo "bundle: $js"
[ -n "$js" ] && curl -s "localhost:8000/$js" | grep -oE "MOCK[^\"']{0,40}|south_charlotte|unc_charlotte" | sort | uniq -c | head
ls -la /mnt/c/Users/orynt/GeoSpatial/gt-redesign/web/ | head; ls -la /mnt/c/Users/orynt/GeoSpatial/Geospatial-SearchEngine/web/dist 2>/dev/null | head -3
