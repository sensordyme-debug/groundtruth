# Display mosaic (F0.2): seam-free NAIP map tiles

Adjacent NAIP quarter-quads come from different flights (2022: 06-02, 06-22, 09-27; 2020: 07-11, 07-12), so the
raw tile composite (first valid pixel wins, newest first) shows hard colour/brightness steps at scene edges. The
clearest one: Lake Norman water at lon -80.875, lat 35.50 (2022-09-27 scene over 2022-06-02).

The display mosaic fixes this **for map tiles only**. The engine's index, crops and every tool keep reading the raw
scenes (`read_grid` / `get_imagery` are unchanged), so no measurement or embedding changes.

## Build (`scripts/heavy/build_mosaic.py`, laptop job `scripts/heavy/jobs/mosaic_v1.sh`)

Per year, deterministic (sorted scenes and blocks, fixed reference rule, no randomness):

1. **Areas**: every place in `engine/groundtruth/conf/places.json` (18 places, which include the 8 original demo
   neighbourhoods), bbox padded 1 km in EPSG:32617. Union ≈ 1,008 km².
2. **Grid**: EPSG:32617, 0.6 m, square blocks of 8,192 px (4,915.2 m) aligned to multiples of 4,915.2 m. Every block
   touching an area is built (71 blocks per year). Scenes = every scene whose footprint touches a block
   (2022: 52, 2020: 53), fetched into the laptop's COG cache from Planetary Computer (same public files as the PC).
3. **Colour balance**: each scene read once at 4.8 m (COG overviews). For each overlapping pair, 49 per-band quantiles
   (2–98 %) of the shared pixels ≥ 30 m inside both scenes give robust correspondences (insensitive to small
   misregistration and to cars/shadows). One least-squares solve per band fits gain/offset per scene; the
   **reference** (scene with the largest area inside the padded areas, ties by id) is fixed at gain 1 / offset 0;
   a weak prior pulls the others to identity; gain clamped to [0.75, 1.33], offset to [-40, 40]. Applied as 8-bit LUTs
   with soft knees below 12 and above 232, so nothing clips hard to 0/255.
4. **Feathered seams**: a seam runs along the medial line of each overlap (where both scenes are equally far from
   their own data edge). Weight of scene i = smoothstep of `(d_i - max_{j≠i} d_j)` over 96 px (57.6 m), where `d` is
   the distance to the scene's valid-data edge (4.8 m grid, bilinear). Weights are normalised; a pixel covered by one
   scene takes that scene.
5. **Output**: `data/tiles/mosaic/v1/<year>/blocks/b_<i>_<j>.tif`: COG, 3-band RGB, JPEG (YCbCr) q90, 512 px tiles,
   internal mask, AVERAGE overviews; plus `meta.json` (grid, blocks + their scenes, reference, per-scene gains/offsets,
   per-pair fit residuals before/after, saturation check, areas, build time, script sha256, GDAL version).

Versioning: `conf/data_versions.json` key `"mosaic"` names the folder under `data/tiles/mosaic/`. A rebuild writes a
new `vN` (the script refuses to overwrite a built version) and is switched there.

## Serving (`PublicDataProvider.render_tile`)

`render_tile` first calls `render_tile_mosaic`: when the year's mosaic exists and its blocks cover the **whole** tile
(tile bounds → EPSG:32617 → every block cell under it present), the tile is rendered from the blocks with rio-tiler;
otherwise it falls back to `render_tile_raw` (the previous behaviour, unchanged). Contract unchanged: `None` = not
renderable locally, `b""` = no imagery. Switch: `GROUNDTRUTH_MOSAIC=0` disables the mosaic (default `1`).

Known differences from the raw composite: in the ~300–600 m scene overlaps, the mosaic shows the scene nearer its own
centre (the raw path shows the newest acquisition), so the display there can be from the other flight than the crop
the engine reads. Within-scene brightness gradients are not modelled (one gain/offset per scene and band).

## Evidence

`scripts/mosaic_evidence.py shots|bench` renders tiles directly with the provider (raw vs mosaic) and writes
`docs/media/v3/f0/seam_<area>_z<z>_{before,after}.png` and `docs/media/v3/f0/evidence.json`.
