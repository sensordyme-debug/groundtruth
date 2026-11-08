# Model licence review

Date: 2026-11-08. These are research notes, not legal advice. Each classification is my **recommendation pending
owner/counsel sign-off**. It builds on `docs/LICENCE_GATE.md`, including decision D1, which the owner accepted on
2026-11-08 for Groundtruth with the note "legal review required before ORYN commercial use".

Each model is reviewed on three separate layers:
1. **Weights + code:** the licence the publisher attaches.
2. **Training data:** the terms of the data the weights were learned from. These usually do not purport to bind the
   weights, but they create residual copyright, contract and reputational risk.
3. **Derived outputs:** embeddings, boxes, masks and plans that Groundtruth produces and stores or exports.

## Checkpoints actually present

These are in the HF cache on the PC (`~/.cache/huggingface/hub`, WSL):

| model | snapshot | loaded at |
|---|---|---|
| `facebook/dinov2-base` | `f9e44c814b77203eaa57a6bdbbd535f21ede1415` | `engine/groundtruth/embed.py:78`, with a pinned `revision` |
| `laion/CLIP-ViT-B-16-laion2B-s34B-b88K` | `7288da5a0d6f0b51c4a2b27c624837a9236d0112` | `embed.py:125`, via `open_clip` `pretrained=` with no revision pin |
| `facebook/sam2.1-hiera-tiny` | `de431c4043854a71d8101e17995dfe596bf101a5` | `outline.py:39-40`, **no revision pin** |
| `IDEA-Research/grounding-dino-tiny` | `a2bb814dd30d776dcf7e30523b00659f4f141c71` | `openvocab.py:51-52`, **no revision pin** |
| Qwen3-4B Q4_K_M GGUF (`Qwen/Qwen3-4B-GGUF`, file `Qwen3-4B-Q4_K_M.gguf`) | SHA-256 verified against HF LFS by `scripts/heavy/llm/fetch_model.py` | laptop only, llama.cpp, `scripts/heavy/llm/llm.conf` |

No other `.pth`, `.pt`, `.ckpt`, `.safetensors` or `.gguf` files were found under `~` (excluding ORYN and venvs) or
under `data/`. `engine/groundtruth/conf/dino_cells8_pca192.npz` is a PCA projection fitted by Groundtruth on DINOv2
embeddings of NAIP, so it is a derived output.

## Per model

### DINOv2 ViT-B/14 (`facebook/dinov2-base`)
- **Weights + code.** Apache-2.0 on the HF card (https://huggingface.co/facebook/dinov2-base) and in the repo
  (https://github.com/facebookresearch/dinov2, LICENSE). The repo was relicensed from CC-BY-NC to Apache-2.0 in 2023;
  the pinned snapshot is from after that change, as recorded in SOURCES.md.
- **Training data.** LVD-142M. These are curated web images; the dataset is not released and its terms are not
  public. No dataset licence passes through to the weights.
- **Derived outputs.** Chip embeddings, the PCA, and similarity scores. No licence obligation; Apache only requires
  the notice if weights or code are redistributed.
- **Recommendation: APPROVED.** Keep the Apache-2.0 NOTICE with any redistribution.

### OpenCLIP ViT-B-16 `laion2b_s34b_b88k`
- **Weights.** The HF card says `license: mit`
  (https://huggingface.co/laion/CLIP-ViT-B-16-laion2B-s34B-b88K). The code is MIT
  (https://github.com/mlfoundations/open_clip/blob/main/LICENSE).
- **The model card's use statement (read 2026-11-08).** "Any deployed use case of the model - whether commercial or
  not - is currently out of scope." The card also calls the model a research output and says the dataset should not
  be used for "creating ready-to-go industrial products". This is guidance, not a licence condition, and MIT grants
  commercial use. It is still an explicit publisher statement that counsel should see. `docs/SOURCES.md` does not
  record it.
- **Training data.** LAION-2B-en, a subset of LAION-5B (https://laion.ai/blog/laion-5b/). It consists of URL and
  caption pairs pointing at third-party copyrighted images. LAION-5B was withdrawn in December 2023 after CSAM links
  were reported, and re-released as Re-LAION-5B in 2024. These weights were trained on the **original** set.
  The risks are:
  - copyright and TDM claims over the images;
  - reputational risk from the contaminated set;
  - no licence for the images themselves.
- **Derived outputs.** Text↔image embeddings that feed `search_text` and `pick_candidates`. Nothing from LAION
  appears in the outputs.
- **Recommendation.**
  - Groundtruth (demo, internal): **APPROVED WITH RESTRICTIONS**. Keep it.
  - Commercial deployment: **HUMAN/LEGAL REVIEW REQUIRED**, because of the "deployed use out of scope" statement and
    the LAION lineage.
  - Mitigation to evaluate: an OpenCLIP checkpoint trained on DataComp
    (`laion/CLIP-ViT-B-16-DataComp.XL-s13B-b90K`), or Apple DFN / MobileCLIP. Each needs its own licence check:
    DFN weights carry Apple's sample-code licence. I have not verified this.

### SAM 2.1 hiera-tiny (`facebook/sam2.1-hiera-tiny`)
- **Weights + code.** Apache-2.0 (https://huggingface.co/facebook/sam2.1-hiera-tiny;
  https://github.com/facebookresearch/sam2/blob/main/LICENSE).
- **Training data.**
  - SA-V (CC BY 4.0, https://ai.meta.com/datasets/segment-anything-video/).
  - SA-1B, under the "SA-1B Dataset Research License" (research only;
    https://ai.meta.com/datasets/segment-anything/).
  - Meta releases the weights under Apache regardless of these dataset terms (D1 precedent).
- **Derived outputs.** Masks and outlines (polygons) stored in caches and exports. No obligation.
- **Recommendation: APPROVED** (lineage note D1). Pin the `revision` in `outline.py` for reproducibility.

### Grounding DINO tiny (`IDEA-Research/grounding-dino-tiny`)
- **Weights + code.**
  - HF `license: apache-2.0` (https://huggingface.co/IDEA-Research/grounding-dino-tiny).
  - Original repo Apache-2.0 (https://github.com/IDEA-Research/GroundingDINO/blob/main/LICENSE).
  - Runs through the transformers port: no compiled ops and no `trust_remote_code`.
- **Training data** (paper arXiv 2303.05499):
  - Objects365: "academic purpose only" images (https://www.objects365.org/), CC BY 4.0 annotations.
  - GoldG, which includes Flickr30k Entities ("non-commercial research and/or educational purposes") and Visual
    Genome / GQA.
  - Cap4M, unreleased web data.
- **Derived outputs.** Candidate boxes for `find_anything`. These are verified downstream and stored in
  `data/cache/openvocab` and in the Lake Norman dock layer.
- **Recommendation.**
  - Groundtruth: **APPROVED WITH RESTRICTIONS**, as the owner already decided under D1.
  - Any commercial (ORYN) use: **HUMAN/LEGAL REVIEW REQUIRED**. The owner's own D1 note requires legal review first.
  - Also pin the `revision` in `openvocab.py`.

### Qwen3-4B (GGUF Q4_K_M, `Qwen/Qwen3-4B-GGUF`)
- **Weights.** Apache-2.0 (https://huggingface.co/Qwen/Qwen3-4B-GGUF; base model https://huggingface.co/Qwen/Qwen3-4B).
  This is first-party quantisation by the Qwen team.
- **Training data.** Undisclosed (Qwen3 technical report, arXiv 2505.09388). No dataset licence is published.
- **Derived outputs.**
  - JSON plans and short replies. They are number-guarded and are not used as facts (`api/planner/brain.py`).
  - People-data prompts never reach the model: the policy check runs first (`brain.py:19-20`).
- **Recommendation: APPROVED.** Keep the Apache NOTICE. Do **not** substitute Qwen2.5-3B (Qwen Research Licence,
  non-commercial).

### Listed in SOURCES/LICENCE_GATE but not present
Florence-2, Clay, GeoAI weights, DINOv3, SAM 3 and OWLv2 have no checkpoints on disk. Their gate verdicts are
unchanged.

## Derived-output policy (applies to all models)
- Embeddings, masks, boxes and plans are Groundtruth's outputs. None of the licences above (Apache-2.0, MIT) claims
  rights in outputs.
- Outputs computed **over** ODbL layers, such as houses-with-pools that join Overture buildings, are governed by the
  **data** licence (ODbL), not the model licence. See `DATA_SOURCE_LICENSE_REVIEW.md`.
- Exported reports cite the imagery and data sources in each envelope's `sources[]`. Model attribution is needed only
  when weights or code are redistributed. A short "Models used" line in exported reports is recommended courtesy.

## Summary

| model | weights | training-data concern | Groundtruth | commercial (ORYN/product) |
|---|---|---|---|---|
| DINOv2 base | Apache-2.0 | low (undisclosed curated web) | APPROVED | APPROVED |
| OpenCLIP ViT-B-16 LAION-2B | MIT | **high**: LAION-2B (copyright; withdrawn set), plus the model card says deployment is out of scope | APPROVED WITH RESTRICTIONS | **HUMAN/LEGAL REVIEW REQUIRED** |
| SAM 2.1 tiny | Apache-2.0 | medium: SA-1B research licence (D1) | APPROVED | APPROVED (D1 lineage) |
| Grounding DINO tiny | Apache-2.0 | **high**: Objects365 academic-only, Flickr30k non-commercial | APPROVED WITH RESTRICTIONS (owner D1) | **HUMAN/LEGAL REVIEW REQUIRED** (owner's own condition) |
| Qwen3-4B GGUF | Apache-2.0 | undisclosed | APPROVED | APPROVED |
