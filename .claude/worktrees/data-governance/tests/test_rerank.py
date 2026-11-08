import numpy as np

from groundtruth.rerank import _cell_patches, _hsv, colour_patches, concept_conf


def test_cell_patches_map_8x8_cells_to_2x2_patches():
    assert _cell_patches(0, 8) == [0, 1, 16, 17]
    assert _cell_patches(63, 8) == [238, 239, 254, 255]
    assert len(set(p for c in range(64) for p in _cell_patches(c, 8))) == 256


def test_hsv_of_pool_cyan():
    h, s, v = _hsv(np.array([[[40, 200, 230]]], np.uint8))[0, 0]
    assert 180 < h < 200 and s > 150 and v > 220


def test_colour_patches_finds_cyan_block():
    img = np.full((224, 224, 3), (60, 110, 50), np.uint8)  # lawn
    img[28:42, 56:70] = (40, 200, 230)  # one 14 px patch of pool-cyan at patch (2, 4)
    rule = concept_conf("swimming_pool")["rerank"]["colour"]
    m = colour_patches(img, rule)
    assert m.shape == (16, 16)
    assert m[2, 4] == 1.0 and m.sum() == 1.0
    assert colour_patches(img, None).sum() == 0


def test_concept_conf_falls_back_to_default():
    assert concept_conf("unknown_thing")["rerank"]["colour_weight"] == 0.0
    assert concept_conf("swimming_pool")["rerank"]["colour_weight"] > 0
