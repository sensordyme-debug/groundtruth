# Prior work reused from ORYN

ORYN is Monish's pre-existing geospatial platform. Groundtruth reuses a few of its **data caches** and
**design values**. It does not use ORYN's core product code: no search ranking, change-detection pipeline, auth,
billing, tenants or Leads. Everything else in this repo was built new at CrownHacks. Groundtruth runs without
ORYN: every reused file is copied into `data/` (gitignored) or into this repo.

## Copied data (into `data/`, never committed)
| asset | ORYN source path | copied to | size | original source · date · licence |
|---|---|---|---|---|
| North Carolina OSM extract | `/home/orynt/oryn-tools/valhalla/osm/north-carolina-261106.osm.pbf` | `data/osm/north-carolina.osm.pbf` | 431 MB | Geofabrik `north-carolina-latest.osm.pbf` · 2026-11-06 · ODbL 1.0 © OpenStreetMap contributors |

## Shared caches (pointed at, not copied)
| asset | location | notes |
|---|---|---|
| DINOv2 ViT-B/14 weights `facebook/dinov2-base` @ `f9e44c81…` | `~/.cache/huggingface/hub/models--facebook--dinov2-base` | Already in the user-level HF cache, so no re-download. Same revision ORYN used, pinned in `engine/groundtruth/embed.py`. Apache-2.0. |

## Copied code / design values
| file in Groundtruth | adapted from | what |
|---|---|---|
| `web/shared/tokens.css` | ORYN `web/src/app/tokens.css` and `docs/design/DEVIN_STUDY.md` | Colour, type, radius and motion tokens re-expressed as plain CSS variables (values only) |

## Reused as a reference only (not copied)
- The NAIP year finding: ORYN's logs confirm what our own STAC check showed. The 2019/2021/2023 NAIP over Mecklenburg's
  bbox are South Carolina quads, so only the even years cover the county.

## Considered but not imported (needs the owner's decision)
- **ORYN's DINOv2 embeddings of NAIP 2022 (91,272 chips) and 2020 (90,611)** exist only in Postgres (`imagery_tiles`).
  Using them needs a read-only `COPY … TO parquet` export from the production DB. They also sit on a different grid:
  EPSG:26917, 134.4 m, no overlap, not L2-normalised. Ours is EPSG:32617 with a 115.2 m stride. See `docs/ORYN_INVENTORY.md`.
- **ORYN change-detection candidates (NAIP 2020→2022, 47,655 scored pairs with an NDWI water flag)**: also only in
  Postgres. Same export decision.
