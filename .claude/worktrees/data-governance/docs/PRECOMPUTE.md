# Precompute: locked storage format, cost and where it runs

## Locked storage format v3 (`INDEX_VERSION = chips224s192-g0.6-v3c8p192`, repo tag `precompute-v3`)
Approved 2026-11-07. It changes v2 (4×4 cells) to 8×8 cells + PCA-192: same storage, about 5× better `find_similar`.
| item | value |
|---|---|
| chip grid | EPSG:32617, 224×224 px at 0.6 m (134.4 m); stride 192 px (115.2 m, 19.2 m overlap); `chip_id = {year}_e{col}_n{row}` |
| DINOv2 | `facebook/dinov2-base` @ `f9e44c81…` (Apache-2.0). Final-layer-normed patch tokens (16×16 of 14 px), average-pooled to an **8×8 grid** of 28 px (16.8 m) cells, projected with the **fixed PCA basis** `engine/groundtruth/conf/dino_cells8_pca192.npz` (768 → 192; 91.9% of variance; fitted on 45,504 cells from campus, suburb, airport/industrial and rural samples, NAIP 2022 + 2020), each L2-normalised. **(N, 64, 192) float16**. The same basis is used for every year, so cross-year similarity works. |
| why 8×8 | `find_similar` from a picked community-pool cell (south_charlotte, top 24, matching cell on a pool by eye): 4×4 cells ≈ 2/24; 8×8 = 9–10/24; 8×8 + PCA-192 = 10/24. 16×16 was worse. Sheets: `data/out/exp_grid*_south_charlotte_*.png` |
| OpenCLIP | ViT-B-16 `laion2b_s34b_b88k` (MIT). **Whole chip only, (N, 1, 512) float16**, L2-normalised |
| CLIP 2×2 sub-crops | **Off everywhere.** The A/B below showed no gain. The code keeps the option (`GROUNDTRUTH_CLIP_SUBCROPS=1`) but it is not used. |
| thumbnails | JPEG q88, 224×224, in the shard; packed into `thumbs.bin` + `thumbs_offsets.npy` |
| shard | one parquet per NAIP scene: `chip_id, year, scene_id, col, row, x0_m, y0_m, west, south, east, north, valid_frac, jpeg, dino[16·768], clip[512]` |
| index | `chips.parquet` (meta), `dino.npy` (N,64,192) fp16, `clip.npy` (N,1,512) fp16, FAISS `IndexScalarQuantizer(fp16, inner product)` over all cell rows (`dino.faiss`) and chip rows (`clip.faiss`) |
| throughput (this PC, CPU, 8 threads) | 4.6 chips/s, the same as v1: the 8×8 pooling and PCA are free |

### A/B: CLIP sub-crops (UNC Charlotte slice, NAIP 2022, 342 chips, 2026-11-07)
| | embed time (CPU, 8 threads) | `search_text("swimming pool")` top 24: pools by eye (clear / incl. possible) |
|---|---|---|
| whole chip only | 79 s (4.4 chips/s) | **6 / 8** |
| whole + 2×2 sub-crops | 208 s (1.6 chips/s), 2.6× | **6 / 9** |
Decision rule: adopt only if sub-crops add ≥ 3 pools. They added 0 clear pools, so they are dropped everywhere.
Sheets: `data/out/ab_unc_charlotte_{whole,subcrops}.png`.

## Cost per year (107,770 chips, 54 NAIP scenes)
| | value |
|---|---|
| storage per chip | ~17 KB JPEG + 24.6 KB DINOv2 cells (64×192×2 B) + 1 KB CLIP ≈ **43 KB** |
| shards per year | ≈ **4.6 GB** (+ index build: dino.npy 2.6 GB, FAISS ≈ 2.7 GB, thumbs ≈ 1.8 GB) |
| NAIP download | ≈ 25 GB per year, streamed one scene at a time (~470 MB each, deleted after use) |
| this PC, CPU only | ~7 h per year. **Not used** (production machine) |
| Colab T4 (CUDA) | **2022**. Estimated ~20–40 min embedding + download |
| Mac mini (Apple Silicon, MPS) | **2020** proposed. **On hold:** ORYN production services run on the Mac mini (see the report). |

## Run elsewhere
- **Colab (2022):** `notebooks/precompute_colab.ipynb`. It is self-contained and resumable, and writes shards to the Colab disk.
  It zips them into parts with a SHA-256 manifest. Download the parts and the manifest into `data/incoming/2022/`, then run
  `python scripts/verify_shards.py data/incoming/2022` to check the hashes, unpack, and build the index.
- **Any CUDA/MPS box:** `python scripts/precompute_county.py --aoi county --year <year> --batch 64`.
  It is resumable at scene granularity and retries failed reads 3×. The device is picked automatically (cuda → mps → dml → cpu).
