#!/usr/bin/env bash
cd /mnt/c/Users/orynt/GeoSpatial/gt-redesign
git add scripts/rollback_demo.sh scripts/step0_*.sh && git commit -q -m "rollback_demo.sh rebuilds web/dist from the tag (dist is not in git); step-0 probes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
timeout 120 git push -q groundtruth redesign/conversational && echo "pushed $(git log --oneline -1)"
free -m | sed -n 2p; nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader
echo "windows_free_gb $(powershell.exe -NoProfile -Command '[math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1MB,1)' | tr -d '\r')"
