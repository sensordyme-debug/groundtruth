"""The DataProvider interface. Engine logic never fetches data directly; it asks a provider.

``PublicDataProvider`` is the first implementation (public data: Census, NAIP via Planetary Computer,
OSM, FEMA, Overture). ORYN will add its own provider later behind this same interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from groundtruth.envelope import Source

BBox = tuple[float, float, float, float]  # (west, south, east, north) in EPSG:4326


@dataclass(frozen=True)
class ImageryScene:
    """One imagery scene (e.g. one NAIP quarter-quad COG)."""

    scene_id: str
    year: int
    bbox: BBox
    gsd_m: float
    href: str  # unsigned asset href; providers sign on read
    geometry: dict[str, Any] | None = None


@dataclass
class Imagery:
    """An RGB raster window: ``rgb`` is uint8 (H, W, 3); ``transform`` is a rasterio Affine in ``crs``."""

    rgb: np.ndarray
    transform: Any
    crs: str
    year: int
    scene_ids: list[str]
    bbox: BBox
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TileSource:
    """An XYZ tile source the UI can display."""

    url_template: str  # {z}/{x}/{y}
    attribution: str
    year: int | None = None
    min_zoom: int = 0
    max_zoom: int = 20


class DataProvider(ABC):
    """Every data read the engine needs. Implementations must be side-effect free for callers."""

    #: short identifier recorded in envelopes
    name: str = "abstract"

    @abstractmethod
    def get_boundary(self) -> dict[str, Any]:
        """Area-of-service boundary as a GeoJSON Feature (EPSG:4326)."""

    @abstractmethod
    def imagery_years(self) -> list[int]:
        """Imagery years available with full coverage, newest first."""

    @abstractmethod
    def list_scenes(self, bbox: BBox, year: int) -> list[ImageryScene]:
        """Imagery scenes intersecting ``bbox`` for ``year``."""

    @abstractmethod
    def get_imagery(self, bbox: BBox, year: int, *, gsd_m: float | None = None) -> Imagery:
        """RGB pixels covering ``bbox`` for ``year`` (mosaicked across scenes if needed)."""

    @abstractmethod
    def get_tiles(self, year: int) -> TileSource:
        """XYZ tile source for displaying imagery of ``year``."""

    @abstractmethod
    def get_layer(self, name: str, bbox: BBox | None = None) -> dict[str, Any]:
        """A vector layer (GeoJSON FeatureCollection, EPSG:4326), optionally clipped to ``bbox``."""

    @abstractmethod
    def layer_names(self) -> list[str]:
        """Names accepted by :meth:`get_layer`."""

    @abstractmethod
    def sources(self) -> list[Source]:
        """Provenance for everything this provider can return."""

    def source_for(self, key: str) -> list[Source]:
        """Provenance for one dataset key; default: all sources whose name contains ``key``."""
        k = key.lower()
        return [s for s in self.sources() if k in s.name.lower()]
