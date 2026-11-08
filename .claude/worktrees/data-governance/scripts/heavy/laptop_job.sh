#!/usr/bin/env bash
# (PC, inside WSL) Laptop heavy jobs. Jobs run detached on the laptop (PID file + log); nothing here needs to stay open.
#   bash scripts/heavy/laptop_job.sh start <name>    sync code, start scripts/heavy/jobs/<name>.sh
#   bash scripts/heavy/laptop_job.sh status [name]
#   bash scripts/heavy/laptop_job.sh follow <name>   optional: stream the log until the job ends
set -euo pipefail
cd "$(dirname "$0")/../.."
cmd="${1:-status}"; name="${2:-}"
[ "$cmd" = start ] && bash scripts/heavy/sync.sh code >/dev/null
[ "$cmd" = follow ] && cmd=log
/mnt/c/Windows/System32/OpenSSH/ssh.exe -i C:/Users/orynt/.ssh/id_ed25519_oryn -o BatchMode=yes -o ConnectTimeout=20 \
  -o ServerAliveInterval=30 ashur@quantcompute \
  "wsl -d Ubuntu-24.04 -- bash -l /home/ashur/groundtruth-heavy/scripts/heavy/run_job.sh $cmd $name" 2>&1 \
  | grep -v "post-quantum\|store now\|openssh.com/pq" || true
