"""Fetch FEMA FIRM panel effective dates for Mecklenburg (DFIRM 37119C) from NFHL MapServer layer 3 (FIRM Panels)
into data/firm_panels/v1/: panels.json (one row per panel: FIRM_PAN, EFF_DATE as YYYY-MM-DD, PANEL_TYP) and
panels.geojson (the panel outlines, EPSG:4326) so each result can carry its own panel's effective date. Public
FEMA data, no key. These are the maps' *effective* dates, distinct from when we fetched them."""
import datetime as dt
import json
import os
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

URL = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/3/query"
out = Path(os.environ["GROUNDTRUTH_DATA"]) / "firm_panels" / "v1"
q = {"where": "DFIRM_ID='37119C'", "outFields": "FIRM_PAN,EFF_DATE,PRE_DATE,PANEL_TYP", "returnGeometry": "true",
     "outSR": 4326, "f": "geojson", "resultRecordCount": 2000}
d = json.load(urllib.request.urlopen(URL + "?" + urllib.parse.urlencode(q), timeout=120))
rows = []
for f in d.get("features", []):
    a = f["properties"]
    ms = a.get("EFF_DATE")
    rows.append({"firm_pan": a.get("FIRM_PAN"), "panel_typ": a.get("PANEL_TYP"),
                 "eff_date": dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).date().isoformat() if ms else None})
if not rows:
    raise SystemExit(f"no panels returned: {str(d)[:300]}")
out.mkdir(parents=True, exist_ok=True)
dates = Counter(r["eff_date"] for r in rows)
doc = {"source": URL.rsplit("/", 1)[0], "dfirm_id": "37119C", "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
       "panels": len(rows), "eff_date_counts": dict(sorted(dates.items(), key=lambda kv: str(kv[0]))),
       "latest_eff_date": max(x for x in dates if x), "earliest_eff_date": min(x for x in dates if x), "rows": rows}
(out / "panels.json").write_text(json.dumps(doc, indent=1))
gj = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": f["geometry"], "properties": {"firm_pan": r["firm_pan"], "eff_date": r["eff_date"],
                                                               "panel_typ": r["panel_typ"]}}
    for f, r in zip(d["features"], rows)]}
(out / "panels.geojson").write_text(json.dumps(gj))
print(json.dumps({k: v for k, v in doc.items() if k != "rows"}))
