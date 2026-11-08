"""Pre-render the landing background: a crisp NAIP 2022 crop of the Lake Norman shore (homes, docks, lake), 2560×1440 at
the native 0.6 m (≈1.5 × 0.9 km), from the local COGs → web/app-v2/public/v2assets/landing.webp (served static by the app).

  GROUNDTRUTH_DATA=… python scripts/build_landing_bg.py [--centre -80.8770,35.4993]
"""
import argparse
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
from groundtruth.geo import to_measure, to_wgs84  # noqa: E402
from groundtruth.providers.public import PublicDataProvider  # noqa: E402
from shapely.geometry import Point, box  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--centre", default="-80.8770,35.4993")
ap.add_argument("--out", default=str(ROOT / "web/app-v2/public/v2assets/landing.webp"))
ap.add_argument("--size", default="2560x1440", help="WxH pixels at the native 0.6 m")
a = ap.parse_args()
lon, lat = (float(v) for v in a.centre.split(","))
c = to_measure(Point(lon, lat))
W, H = (int(v) for v in a.size.split("x"))
G = 0.6
bb = to_wgs84(box(c.x - W * G / 2, c.y - H * G / 2, c.x + W * G / 2, c.y + H * G / 2)).bounds
im = PublicDataProvider(download_scenes=False).get_imagery(bb, 2022)
img = Image.fromarray(im.rgb[..., :3])
w, h = img.size  # crop the reprojected window to 16:9 around its centre, then to exactly 2560×1440
s = min(w / W, h / H)
cw, ch = int(W * s), int(H * s)
img = img.crop(((w - cw) // 2, (h - ch) // 2, (w - cw) // 2 + cw, (h - ch) // 2 + ch)).resize((W, H), Image.LANCZOS)
img.save(a.out, "WEBP", quality=80, method=6)
print(a.out, Path(a.out).stat().st_size // 1024, "kB", im.scene_ids)
