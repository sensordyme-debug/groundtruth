#!/usr/bin/env bash
# Dry-run the shard hand-off (pack → verify → unpack) on a slice, into a throwaway AOI name.
set -euo pipefail
cd "$(dirname "$0")/.."
V=$(bash scripts/gt.sh python -c "from groundtruth import config; print(config.INDEX_VERSION)")
SRC="data/precompute/$V/south_charlotte/2022"
T=$(mktemp -d)
bash scripts/gt.sh python scripts/pack_shards.py "$SRC/shards" "$T/out" --part-mb 5 --meta "$SRC/plan.json"
bash scripts/gt.sh python scripts/verify_shards.py "$T/out" --year 2022 --aoi transfer_test --no-index
echo "--- tamper test (flip one byte in a part; must fail)"
python3 - "$T/out/part_000.zip" <<'EOF'
import sys
p = sys.argv[1]; b = bytearray(open(p, "rb").read()); b[len(b)//2] ^= 0xFF; open(p, "wb").write(b)
EOF
if bash scripts/gt.sh python scripts/verify_shards.py "$T/out" --year 2022 --aoi transfer_test --no-index; then echo "TAMPER NOT DETECTED"; exit 1; else echo "tamper detected (expected)"; fi
rm -rf "$T" "data/precompute/$V/transfer_test"
