from typing import Any

import numpy as np
import pytest
from shapely.geometry import box

from groundtruth import config
from groundtruth.envelope import Source
from groundtruth.geo import area_acres, to_measure
from groundtruth.providers.base import DataProvider, Imagery, ImageryScene, TileSource
from groundtruth.tiler import assign_scenes, chip_id, chips_for_area, chips_for_bbox, make_chip


def test_chip_size_and_stride():
    c = make_chip(2022, 10, 20)
    assert c.x1_m - c.x0_m == pytest.approx(134.4)
    assert c.y1_m - c.y0_m == pytest.approx(134.4)
    n = make_chip(2022, 11, 20)
    assert n.x0_m - c.x0_m == pytest.approx(115.2)
    assert c.x1_m - n.x0_m == pytest.approx(19.2)  # overlap = 32 px * 0.6 m
    assert c.chip_id == chip_id(2022, 10, 20) == "2022_e10_n20"


def test_chips_cover_area_exactly():
    area = box(500_000, 3_900_000, 501_000, 3_901_000)  # 1 km² in UTM
    chips = chips_for_area(area, 2022)
    cols = sorted({c.col for c in chips}); rows = sorted({c.row for c in chips})
    assert all(box(*c.bounds_m).intersects(area) for c in chips)
    # every point of the area is covered
    from shapely.ops import unary_union
    assert unary_union([box(*c.bounds_m) for c in chips]).contains(area)
    # neighbours just outside are not included
    assert not box(*make_chip(2022, cols[0] - 1, rows[0]).bounds_m).intersects(area)
    # deterministic, N→S then W→E
    assert chips == chips_for_area(area, 2022)
    assert chips[0].row == max(rows) and chips[0].col == min(cols)


def test_chip_wgs84_bbox_roundtrip():
    bb = (-80.745, 35.297, -80.722, 35.315)
    chips = chips_for_bbox(bb, 2022)
    c = chips[0]
    w, s, e, n = c.bbox_wgs84()
    assert w < e and s < n
    assert -80.76 < w < -80.70 and 35.28 < s < 35.33
    assert area_acres(box(w, s, e, n)) == pytest.approx(134.4**2 / 4046.856, rel=0.02)


def test_assign_scenes_prefers_full_containment():
    chips = chips_for_area(box(500_000, 3_900_000, 500_300, 3_900_300), 2022)
    big = box(499_000, 3_899_000, 502_000, 3_902_000)
    partial = box(500_000, 3_900_000, 500_050, 3_900_050)
    out = assign_scenes(chips, [("partial", partial), ("big", big)])
    assert {c.scene_id for c in out} == {"big"}
    assert len(assign_scenes(chips, [("far", box(0, 0, 1, 1))])) == 0


class FakeProvider(DataProvider):
    name = "fake"

    def get_boundary(self) -> dict[str, Any]:
        return {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}}

    def imagery_years(self) -> list[int]:
        return [2022]

    def list_scenes(self, bbox, year):
        return [ImageryScene("s1", year, (0, 0, 1, 1), 0.6, "mem://s1")]

    def get_imagery(self, bbox, year, *, gsd_m=None) -> Imagery:
        return Imagery(np.zeros((4, 4, 3), np.uint8), None, "EPSG:32617", year, ["s1"], bbox)

    def get_tiles(self, year) -> TileSource:
        return TileSource("x/{z}/{x}/{y}", "fake", year)

    def get_layer(self, name, bbox=None):
        return {"type": "FeatureCollection", "features": []}

    def layer_names(self):
        return ["roads"]

    def sources(self):
        return [Source(name="Fake NAIP 2022", url="mem://", date="2022", licence="PD")]


def test_provider_interface_contract():
    p = FakeProvider()
    assert p.get_imagery((0, 0, 1, 1), 2022).rgb.dtype == np.uint8
    assert p.get_layer("roads")["type"] == "FeatureCollection"
    assert p.source_for("naip")[0].licence == "PD"
    with pytest.raises(TypeError):
        DataProvider()  # abstract


def test_public_provider_is_a_dataprovider():
    from groundtruth.providers.public import PublicDataProvider

    assert issubclass(PublicDataProvider, DataProvider)
