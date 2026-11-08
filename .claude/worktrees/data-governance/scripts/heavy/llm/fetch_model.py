"""(laptop) Download the official Qwen3 GGUF and verify its SHA-256 against the Hugging Face repo's LFS metadata.
  ~/groundtruth-heavy/.venv/bin/python scripts/heavy/llm/fetch_model.py [Q4_K_M]
No token is used or needed (public repo)."""
import hashlib, json, sys
from pathlib import Path
from huggingface_hub import HfApi, hf_hub_download
REPO = "Qwen/Qwen3-4B-GGUF"
quant = sys.argv[1] if len(sys.argv) > 1 else "Q4_K_M"
fname = f"Qwen3-4B-{quant}.gguf"
dest = Path.home() / "groundtruth-heavy" / "llm" / "models"
info = next(iter(HfApi().get_paths_info(REPO, [fname], expand=True)))
want = info.lfs.sha256 if info.lfs else None
p = Path(hf_hub_download(REPO, fname, local_dir=str(dest)))
h = hashlib.sha256()
with open(p, "rb") as f:
    for b in iter(lambda: f.read(16 << 20), b""):
        h.update(b)
got = h.hexdigest()
rec = {"repo": REPO, "file": fname, "size_bytes": p.stat().st_size, "sha256": got, "repo_sha256": want,
       "verified": got == want, "path": str(p)}
(dest / f"{fname}.json").write_text(json.dumps(rec, indent=1))
print(json.dumps(rec))
