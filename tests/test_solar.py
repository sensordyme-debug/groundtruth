"""Solar verifier (conf/concepts.json "solar_installation"): the observed false-positive patterns are rejected on
synthetic inputs, a synthetic rooftop array passes, and the generic object-check rules behave (docs/COMPUTER_VISION_EVALUATION.md#solar)."""

import io

import numpy as np
import pytest
from PIL import Image
from shapely.geometry import box, mapping

from groundtruth import config, lidar
from groundtruth.geo import to_wgs84
from groundtruth.rerank import colour_patches, concept_conf
from groundtruth.tools import Engine

X0, Y0 = 512000.0, 3897000.0  # one synthetic chip in EPSG:32617 (Mecklenburg)
PM = config.CHIP_M / 16  # patch size, m
PANEL = (120, 140, 175)  # NAIP blue panel: H≈218°, S≈80, V≈175
ASPHALT = (62, 64, 72)  # dark grey-blue asphalt
SHADOW = (38, 46, 66)  # canopy / building shadow (bluish, dark)
LEAF = (52, 84, 40)  # sunlit canopy
ROOF = (150, 140, 130)  # beige shingle roof


class _Index:
    def __init__(self, img: np.ndarray) -> None:
        buf = io.BytesIO()
        Image.fromarray(img.astype(np.uint8)).save(buf, format="JPEG", quality=98)
        self._jpg = buf.getvalue()
        self.ids = ["2022_synthetic"]
        self.pos = {"2022_synthetic": 0}
        self.meta = {"x0_m": np.array([X0]), "y0_m": np.array([Y0])}

    def thumb_jpeg(self, i: int) -> bytes:
        return self._jpg


def _engine(img: np.ndarray, bover: float, monkeypatch, height: float | None = None) -> Engine:
    e = Engine.__new__(Engine)
    e.index = _Index(img)
    monkeypatch.setattr(Engine, "_building_overlap", lambda self, gm: bover)
    monkeypatch.setattr(lidar, "place_for", lambda bounds: None if height is None else "Synthetic")
    monkeypatch.setattr(lidar, "values", lambda gm, place, kind="ndsm": np.full(10, height if height is not None else 0.0))
    return e


def _chip(fill, patch_fill=None, rows=slice(98, 126), cols=slice(98, 126)) -> np.ndarray:
    img = np.zeros((224, 224, 3), np.uint8)
    img[:] = fill
    if patch_fill is not None:
        img[rows, cols] = patch_fill
    return img


def _pending(patch: int = 7 * 16 + 7, n_patches: int = 1, oid: str = "t") -> dict:
    pr, pc = divmod(patch, 16)
    b = box(X0 + pc * PM, Y0 + config.CHIP_M - (pr + 1) * PM, X0 + (pc + 1) * PM, Y0 + config.CHIP_M - pr * PM)
    return {"id": oid, "geometry": mapping(to_wgs84(b)),
            "properties": {"chip_id": "2022_synthetic", "patch": patch, "pending_box": True, "area_m2": n_patches * PM * PM}}


def _outline(b, oid: str) -> dict:
    return {"id": oid, "geometry": mapping(to_wgs84(b)),
            "properties": {"chip_id": "2022_synthetic", "patch": 7 * 16 + 7, "area_m2": b.area}}


# ---------------------------------------------------------------- config

def test_solar_config_loads_with_verifier():
    cc = concept_conf("solar_installation")
    vp, oc = cc["verify_pending"], cc["object_check"]
    assert vp["max_green_frac"] <= 0.5 and oc["max_green_frac"] <= 0.5
    for rules in (vp, oc):
        alts = rules["any_of"]
        assert any("min_building_overlap" in a for a in alts), "a rooftop alternative"
        assert any(a.get("area_m2", [0])[0] >= 500 for a in alts), "a large-array alternative"
    assert cc.get("experimental"), "solar stays experimental until human reviews exist (planner note)"
    lo, hi = cc["rerank"]["colour"]["hsv_lo"], cc["rerank"]["colour"]["hsv_hi"]
    assert lo[2] >= 100, "NAIP panels are mid-bright blue, not dark (the dark rule matched shadows)"


def test_pools_have_no_pending_verifier():
    """Pools keep their behaviour: no verify_pending, so pending boxes are not checked (object_check → None)."""
    assert "verify_pending" not in concept_conf("swimming_pool")
    e = Engine.__new__(Engine)
    e.index = _Index(_chip(ROOF))
    assert e.object_check(_pending(oid="pool"), "swimming_pool") is None


def test_panel_colour_rule_ignores_shadows_and_asphalt():
    rule = concept_conf("solar_installation")["rerank"]["colour"]
    assert colour_patches(_chip(SHADOW), rule).max() == 0
    assert colour_patches(_chip(ASPHALT), rule).max() == 0
    assert colour_patches(_chip(LEAF), rule).max() == 0
    assert colour_patches(_chip(PANEL), rule).min() > 0.9


# ---------------------------------------------------------------- known false-positive patterns (pending boxes)

def test_dark_asphalt_without_building_or_height_is_rejected(monkeypatch):
    e = _engine(_chip(ROOF, ASPHALT), bover=0.0, monkeypatch=monkeypatch)
    r = e.object_check(_pending(oid="asphalt"), "solar_installation")
    assert r["pass"] is False and "building footprint" in r["reason"]


def test_canopy_shadow_is_rejected(monkeypatch):
    img = _chip(LEAF)
    img[98:126, 98:126] = SHADOW
    img[98:126:2, 98:126] = LEAF  # shadow interleaved with lit leaves, as in the before contact sheet
    e = _engine(img, bover=0.0, monkeypatch=monkeypatch)
    r = e.object_check(_pending(oid="canopy"), "solar_installation")
    assert r["pass"] is False and "vegetation" in r["reason"]


def test_shadow_on_a_footprint_without_height_is_rejected_where_lidar_exists(monkeypatch):
    """Tree shadow over an Overture footprint (canopy overhangs roofs): lidar says ground level."""
    e = _engine(_chip(ROOF, PANEL), bover=0.8, monkeypatch=monkeypatch, height=0.3)
    r = e.object_check(_pending(oid="lowshadow"), "solar_installation")
    assert r["pass"] is False and "lidar" in r["reason"]


def test_small_off_roof_blue_object_is_rejected(monkeypatch):
    """A tennis court / blue tarp: panel-like colour, not on a building, smaller than a ground array."""
    e = _engine(_chip(ROOF, PANEL), bover=0.0, monkeypatch=monkeypatch)
    r = e.object_check(_pending(n_patches=6, oid="court"), "solar_installation")
    assert r["pass"] is False and "none of" in r["reason"]


# ---------------------------------------------------------------- true patterns: box rules pass, counted once outlined

def _awaiting(r):
    """Box rules passed; require_outline keeps it out of the count until its SAM outline passes object_check."""
    return r["pass"] is False and r.get("awaiting_outline") is True and "awaiting SAM outline" in r["reason"]


def test_rooftop_array_box_passes_and_awaits_outline(monkeypatch):
    e = _engine(_chip(ROOF, PANEL), bover=0.8, monkeypatch=monkeypatch, height=6.0)
    r = e.object_check(_pending(oid="roof"), "solar_installation")
    assert _awaiting(r), r["reason"]
    assert r["pending_box"] and r["building_overlap"] == 0.8 and r["height_m"] == 6.0


def test_rooftop_array_box_passes_without_lidar(monkeypatch):
    """No lidar built for the place → the height rule is not applied (stated in the docs), the roof rule still is."""
    e = _engine(_chip(ROOF, PANEL), bover=0.6, monkeypatch=monkeypatch, height=None)
    r = e.object_check(_pending(oid="roof_nolidar"), "solar_installation")
    assert _awaiting(r) and r["height_m"] is None


def test_large_ground_array_box_passes(monkeypatch):
    e = _engine(_chip(PANEL), bover=0.0, monkeypatch=monkeypatch)
    r = e.object_check(_pending(n_patches=20, oid="farm"), "solar_installation")
    assert _awaiting(r), r["reason"]


# ---------------------------------------------------------------- post-SAM outline check

WHITE_ROOF = (215, 215, 210)  # bright commercial membrane roof (the South End arrays sit on these)
BLUE_GREY_ROOF = (125, 140, 168)  # blue-grey shingles: panel-coloured, the main false positive after the colour fix


def _px_box(r0, r1, c0, c1):
    return box(X0 + c0 * 0.6, Y0 + config.CHIP_M - r1 * 0.6, X0 + c1 * 0.6, Y0 + config.CHIP_M - r0 * 0.6)


def _with_host(monkeypatch, host):
    monkeypatch.setattr(Engine, "_host_geom", lambda self, gm: host)


def test_outlined_rooftop_array_passes_and_thin_strip_fails(monkeypatch):
    img = _chip(WHITE_ROOF)
    img[98:126, 98:126] = PANEL  # 28 px = 16.8 m square array on a white roof
    img[60:63, 20:200] = PANEL  # thin 1.8 m strip (roof-edge pattern)
    e = _engine(img, bover=0.9, monkeypatch=monkeypatch, height=7.0)
    _with_host(monkeypatch, _px_box(10, 214, 10, 214))  # the roof: ~122 m square
    r = e.object_check(_outline(_px_box(98, 126, 98, 126), "sq"), "solar_installation")
    assert r["pass"] is True, r["reason"]
    assert r["rectangularity"] > 0.9 and r["host_cover"] < 0.1 and r["host_contrast"] >= 50
    strip = _px_box(60, 63, 20, 200)
    r2 = e.object_check({**_outline(strip, "strip"), "properties": {**_outline(strip, "strip")["properties"], "patch": None}},
                        "solar_installation")
    assert r2["pass"] is False and "compactness" in r2["reason"]


def test_blue_grey_roof_plane_is_rejected(monkeypatch):
    """One rectangular plane of a blue-grey house roof: panel colour, rectangular, on a raised footprint, but it looks
    like the rest of its roof (the residual false positive after the colour fix)."""
    img = _chip(LEAF)
    img[80:140, 80:140] = BLUE_GREY_ROOF
    e = _engine(img, bover=1.0, monkeypatch=monkeypatch, height=7.0)
    _with_host(monkeypatch, _px_box(80, 140, 80, 140))
    r = e.object_check(_outline(_px_box(80, 140, 80, 110), "plane"), "solar_installation")
    assert r["pass"] is False and "rest of its roof" in r["reason"]


def test_whole_blue_roof_is_rejected(monkeypatch):
    img = _chip(LEAF)
    img[80:140, 80:140] = BLUE_GREY_ROOF
    e = _engine(img, bover=1.0, monkeypatch=monkeypatch, height=7.0)
    _with_host(monkeypatch, _px_box(80, 140, 80, 140))
    r = e.object_check(_outline(_px_box(80, 140, 80, 140), "wholeroof"), "solar_installation")
    assert r["pass"] is False and "of its building footprint" in r["reason"]


# ---------------------------------------------------------------- generic rules

@pytest.mark.parametrize("rules,vals,fails", [
    ({"min_building_overlap": 0.3}, {"bover": 0.1}, True),
    ({"min_building_overlap": 0.3}, {"bover": 0.5}, False),
    ({"min_height_m": 2.5}, {"height": 1.0}, True),
    ({"min_height_m": 2.5}, {"height": None}, False),  # no lidar → not applicable
    ({"max_green_frac": 0.3}, {"green_frac": 0.6}, True),
    ({"max_height_m": 1.5, "max_building_overlap": 0.5}, {"height": 0.2, "bover": 0.1}, False),  # pool rules unchanged
    ({"max_height_m": 1.5}, {"height": 4.0}, True),
    ({"rectangularity_min": 0.8}, {"rect": 0.7}, True),
    ({"max_host_cover": 0.6}, {"host_cover": 0.9}, True),
    ({"max_host_cover": 0.6}, {"host_cover": None}, False),  # not on a building → not applicable
    ({"min_host_contrast": 50}, {"host_contrast": 20.0}, True),
])
def test_check_rules(rules, vals, fails):
    v = {"area": 50.0, "comp": 0.8, "colour_frac": None, "colour_area": None, "green_frac": None, "height": None,
         "bover": None, **vals}
    assert bool(Engine._check_rules(rules, v)) is fails
