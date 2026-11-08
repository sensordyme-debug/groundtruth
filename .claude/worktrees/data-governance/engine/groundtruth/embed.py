"""Image/text embedders. All outputs are float32, L2-normalised (so inner product == cosine).

- DINOv2 ViT-B/14 (``facebook/dinov2-base`` @ pinned revision, Apache-2.0): image↔image.
  Input 224×224 RGB, ImageNet mean/std. ``embed_cells`` → (N, 16, 768): final-layer-normed patch tokens
  (16×16 patches of 14 px) average-pooled to a grid×grid set of cells (format v3: 8×8 of 28 px = 16.8 m, then a fixed
  PCA basis 768→192 via ``project_cells``), each L2-normalised.
  Cell index = row·4 + col, row 0 = north.
- OpenCLIP ViT-B-16 ``laion2b_s34b_b88k`` (MIT): text↔image. ``embed_multi`` → (N, 5, 512): the whole chip plus
  the four 112 px quadrants (NW, NE, SW, SE) upsampled to 224, each L2-normalised. Text queries use a small
  aerial-imagery prompt ensemble.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Sequence

import numpy as np

from groundtruth.device import pick_device, torch_device

DINO_MODEL = "facebook/dinov2-base"
DINO_REVISION = "f9e44c814b77203eaa57a6bdbbd535f21ede1415"  # pinned (same snapshot already in the HF cache)
DINO_ID = "dinov2-vitb14-cells8x8-pca192-v3"
CLIP_ARCH = "ViT-B-16"
CLIP_PRETRAINED = "laion2b_s34b_b88k"
CLIP_ID = "openclip-vitb16-laion2b_s34b_b88k-whole-v3"

IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
CLIP_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
CLIP_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)

PROMPTS = (
    "an aerial photo of {}.",
    "a satellite image of {}.",
    "a high-resolution overhead view of {}.",
    "{} seen from above.",
)


def _threads() -> None:
    import torch

    n = int(os.environ.get("GROUNDTRUTH_THREADS", "0") or 0)
    if n > 0:
        torch.set_num_threads(n)


def _l2(x: np.ndarray, axis: int = -1) -> np.ndarray:
    x = x.astype(np.float32, copy=False)
    return x / np.clip(np.linalg.norm(x, axis=axis, keepdims=True), 1e-12, None)


def _to_tensor(chips: np.ndarray, mean: np.ndarray, std: np.ndarray, device):
    import torch

    if chips.ndim == 3:
        chips = chips[None]
    if chips.shape[1:3] != (224, 224):
        raise ValueError(f"expected (N,224,224,3) uint8 chips, got {chips.shape}")
    x = (chips.astype(np.float32) / 255.0 - mean) / std
    return torch.from_numpy(np.ascontiguousarray(x.transpose(0, 3, 1, 2))).to(device)


class DinoEmbedder:
    model_id = DINO_ID
    dim = 768

    def __init__(self, device: str | None = None) -> None:
        import torch
        from transformers import AutoModel

        _threads()
        self.device_name = pick_device(device)
        self.device = torch_device(self.device_name)
        self.model = AutoModel.from_pretrained(DINO_MODEL, revision=DINO_REVISION).to(self.device).eval()
        self._torch = torch

    def embed_cells(self, chips: np.ndarray, batch: int = 32, grid: int = 4) -> np.ndarray:
        """(N, grid*grid, 768) L2-normalised cell vectors."""
        import torch.nn.functional as F

        out = []
        with self._torch.inference_mode():
            for i in range(0, len(chips), batch):
                x = _to_tensor(chips[i : i + batch], IMAGENET_MEAN, IMAGENET_STD, self.device)
                h = self.model(pixel_values=x).last_hidden_state[:, 1:, :]  # (B, 256, 768), layer-normed
                b, n, d = h.shape
                s = int(n**0.5)
                h = h.transpose(1, 2).reshape(b, d, s, s)
                h = F.adaptive_avg_pool2d(h, grid)  # (B, 768, g, g)
                out.append(h.flatten(2).transpose(1, 2).float().cpu().numpy())
        if not out:
            return np.zeros((0, grid * grid, self.dim), np.float32)
        return _l2(np.concatenate(out), axis=-1)


@lru_cache(maxsize=1)
def _pca() -> tuple[np.ndarray, np.ndarray]:
    from groundtruth import config

    z = np.load(config.DINO_PCA_PATH)
    return z["mean"].astype(np.float32), z["components"].astype(np.float32)  # (768,), (768, d)


def project_cells(cells: np.ndarray) -> np.ndarray:
    """Raw DINOv2 cell vectors (..., 768) → fixed PCA basis (..., d), L2-normalised. Same basis for every year."""
    mu, P = _pca()
    return _l2((cells.astype(np.float32) - mu) @ P, axis=-1)


class ClipEmbedder:
    model_id = CLIP_ID
    dim = 512

    def __init__(self, device: str | None = None) -> None:
        import open_clip
        import torch

        _threads()
        self.device_name = pick_device(device)
        self.device = torch_device(self.device_name)
        self.model, _, _ = open_clip.create_model_and_transforms(CLIP_ARCH, pretrained=CLIP_PRETRAINED)
        self.model = self.model.to(self.device).eval()
        self.tokenizer = open_clip.get_tokenizer(CLIP_ARCH)
        self._torch = torch

    def embed_images(self, chips: np.ndarray, batch: int = 32) -> np.ndarray:
        return self.embed_multi(chips, batch=batch, subcrops=False)[:, 0, :]

    def embed_multi(self, chips: np.ndarray, batch: int = 32, subcrops: bool = True) -> np.ndarray:
        """(N, 5, 512): [whole, NW, NE, SW, SE] (or (N, 1, 512) without subcrops)."""
        import torch.nn.functional as F

        out = []
        with self._torch.inference_mode():
            for i in range(0, len(chips), batch):
                x = _to_tensor(chips[i : i + batch], CLIP_MEAN, CLIP_STD, self.device)
                views = [x]
                if subcrops:
                    h = x.shape[-1] // 2
                    for r0, c0 in ((0, 0), (0, h), (h, 0), (h, h)):
                        views.append(F.interpolate(x[:, :, r0 : r0 + h, c0 : c0 + h], size=224, mode="bilinear",
                                                   align_corners=False))
                v = self._torch.cat(views, 0)
                e = self.model.encode_image(v).float().cpu().numpy()  # (B*V, 512) grouped by view
                b = x.shape[0]
                out.append(e.reshape(len(views), b, -1).transpose(1, 0, 2))
        if not out:
            return np.zeros((0, 5 if subcrops else 1, self.dim), np.float32)
        return _l2(np.concatenate(out), axis=-1)

    def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        with self._torch.inference_mode():
            tok = self.tokenizer(list(texts)).to(self.device)
            return _l2(self.model.encode_text(tok).float().cpu().numpy())

    def embed_query(self, query: str) -> np.ndarray:
        """Prompt-ensembled query vector (512,)."""
        v = self.embed_texts([p.format(query) for p in PROMPTS]).mean(axis=0)
        return _l2(v[None])[0]


@lru_cache(maxsize=2)
def get_clip(device: str | None = None) -> ClipEmbedder:
    return ClipEmbedder(device)


@lru_cache(maxsize=2)
def get_dino(device: str | None = None) -> DinoEmbedder:
    return DinoEmbedder(device)
