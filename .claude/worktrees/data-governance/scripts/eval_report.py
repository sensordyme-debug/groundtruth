"""Re-render docs/EVALUATION.md from data/out/evaluation.json (no recomputation)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
import evaluate  # noqa: E402

o = json.loads((Path(__file__).resolve().parents[1] / "data" / "out" / "evaluation.json").read_text())
o["models"]["dino"], o["models"]["clip"] = "dinov2-vitb14-cells8x8-pca192-v3", "openclip-vitb16-laion2b_s34b_b88k-whole-v3"
evaluate.write_md(o)
print("rendered docs/EVALUATION.md")
