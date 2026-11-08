#!/usr/bin/env bash
# (PC, inside WSL) Pull a built display mosaic from the laptop into the PC's data/tiles/mosaic/<version>/.
#   GROUNDTRUTH_DATA=/mnt/c/.../data bash scripts/heavy/pull_mosaic.sh v1
# Same transport as sync.sh pull (rsync through ssh.exe hits EAGAIN on big binaries): stage on the laptop's Windows
# side, scp.exe into a .part folder, verify every file's size against the laptop's list, then rename into place.
# Refuses to overwrite an existing version folder. Checks the PC keeps > 40 GB free on C:.
set -euo pipefail
cd "$(dirname "$0")/../.."
ver="${1:?usage: pull_mosaic.sh <version>}"
DATA="${GROUNDTRUTH_DATA:-$PWD/data}"
dst="$DATA/tiles/mosaic/$ver"; part="$DATA/tiles/mosaic/.$ver.part"
[ -e "$dst" ] && { echo "refused: $dst exists (versions are never overwritten)"; exit 1; }
SSH="/mnt/c/Windows/System32/OpenSSH/ssh.exe -i C:/Users/orynt/.ssh/id_ed25519_oryn -o BatchMode=yes -o ConnectTimeout=20"
R=ashur@quantcompute
filt() { grep -v "post-quantum\|store now\|openssh.com/pq" || true; }
list=$($SSH $R "wsl -d Ubuntu-24.04 -- bash -lc 'cd ~/groundtruth-heavy/data/tiles/mosaic/$ver && find . -type f -printf \"%P %s\n\" | sort'" 2>&1 | filt)
need=$(( $(echo "$list" | awk '{s+=$2} END {print s}') / 1073741824 + 1 ))
free=$(( $(df --output=avail -B1 /mnt/c | tail -1) / 1073741824 ))
echo "mosaic $ver: $(echo "$list" | wc -l) files, ~${need} GB; PC C: free ${free} GB"
[ $(( free - need )) -gt 40 ] || { echo "refused: C: would drop below 40 GB free"; exit 1; }
$SSH $R "wsl -d Ubuntu-24.04 -- bash -lc 'mkdir -p /mnt/c/Users/ashur/gt-out && rm -rf /mnt/c/Users/ashur/gt-out/mosaic_$ver && cp -r ~/groundtruth-heavy/data/tiles/mosaic/$ver /mnt/c/Users/ashur/gt-out/mosaic_$ver'" 2>&1 | filt
rm -rf "$part"; mkdir -p "$(dirname "$part")"
/mnt/c/Windows/System32/OpenSSH/scp.exe -q -r -i C:/Users/orynt/.ssh/id_ed25519_oryn -o BatchMode=yes \
  "$R:C:/Users/ashur/gt-out/mosaic_$ver" "$(wslpath -w "$part")" 2>&1 | filt
got=$(cd "$part" && find . -type f -printf "%P %s\n" | sort)
if [ "$got" != "$list" ]; then echo "size/file mismatch after copy; left in $part"; diff <(echo "$list") <(echo "$got") | head; exit 1; fi
mv "$part" "$dst"
$SSH $R "wsl -d Ubuntu-24.04 -- bash -lc 'rm -rf /mnt/c/Users/ashur/gt-out/mosaic_$ver'" 2>&1 | filt
echo "pulled mosaic $ver -> $dst ($(du -sh "$dst" | cut -f1))"
