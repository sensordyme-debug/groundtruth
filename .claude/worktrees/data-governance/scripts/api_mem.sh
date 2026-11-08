#!/usr/bin/env bash
# API process memory (anon = heap/models/FAISS; file = memory-mapped index pages) + WSL + Windows free.
cd "$(dirname "$0")/.."
p=$(cat data/logs/api.pid 2>/dev/null)
[ -n "$p" ] && grep -E "VmRSS|RssAnon|RssFile" /proc/$p/status | tr -s ' ' | tr '\n' ' '; echo
free -m | sed -n 2p
echo "windows_free_gb $(powershell.exe -NoProfile -Command '[math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1MB,1)' | tr -d '\r')"
