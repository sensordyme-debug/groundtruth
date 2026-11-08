"""Insert a timestamped BUILD_LOG row (newest first, America/New_York) with free RAM / VRAM / disk appended.
Usage: python scripts/log_line.py "text"  (also drops any malformed row with an empty timestamp)."""
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

p = Path(__file__).resolve().parents[1] / "docs" / "BUILD_LOG.md"
avail = int(next(ln.split()[1] for ln in open("/proc/meminfo") if ln.startswith("MemAvailable"))) // 1024
try:
    vram = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader"],
                          capture_output=True, text=True, timeout=10).stdout.strip()
except Exception:
    vram = "n/a"
disk = shutil.disk_usage("/mnt/c").free // 2**30
now = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M")
row = f"| {now} | {sys.argv[1]} *(free: WSL RAM {avail:,} MB · VRAM used {vram} · disk C: {disk} GB)* |"
lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if not ln.startswith("|  |")]
sep = next(i for i, ln in enumerate(lines) if ln.startswith("|---"))
lines.insert(sep + 1, row)
p.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(row)
