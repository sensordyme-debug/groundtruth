import json

import pytest

from groundtruth.labels import LabelStore


def test_add_undo_delete_persist(tmp_path):
    s = LabelStore(tmp_path)
    a = s.add(concept="swimming_pool", kind="true_match", lon=-80.73, lat=35.30, naip_year=2022)
    b = s.add(concept="warehouse", kind="look_alike", lon=-80.74, lat=35.31)
    assert len(s.labels()) == 2
    assert s.counts()["swimming_pool"]["true_match"] == 1
    assert (tmp_path / "labels.geojson").exists()
    assert len(list((tmp_path / "backups").glob("*.geojson"))) == 2

    assert s.undo().id == b.id
    s.add(concept="parking_lot", kind="true_match", lon=-80.75, lat=35.32)

    # reopen: rebuilt from journal
    s2 = LabelStore(tmp_path)
    assert [l.concept for l in s2.labels()] == ["swimming_pool", "parking_lot"]
    s2.delete(a.id)
    assert [l.concept for l in LabelStore(tmp_path).labels()] == ["parking_lot"]
    snap = json.loads((tmp_path / "labels.geojson").read_text())
    assert len(snap["features"]) == 1


def test_idempotent_client_id(tmp_path):
    s = LabelStore(tmp_path)
    s.add(concept="swimming_pool", kind="true_match", lon=-80.7, lat=35.3, label_id="abc")
    s.add(concept="swimming_pool", kind="true_match", lon=-80.7, lat=35.3, label_id="abc")
    assert len(s.labels()) == 1


def test_torn_journal_line_is_skipped(tmp_path):
    s = LabelStore(tmp_path)
    s.add(concept="swimming_pool", kind="true_match", lon=-80.7, lat=35.3)
    with open(tmp_path / "labels.jsonl", "a") as f:
        f.write('{"op":"add","label":{"id"')  # simulated crash mid-write
    assert len(LabelStore(tmp_path).labels()) == 1


def test_snapshot_seeds_missing_journal(tmp_path):
    s = LabelStore(tmp_path)
    s.add(concept="solar_installation", kind="true_match", lon=-80.7, lat=35.3)
    (tmp_path / "labels.jsonl").unlink()
    s2 = LabelStore(tmp_path)
    assert len(s2.labels()) == 1 and (tmp_path / "labels.jsonl").exists()


def test_rejects_bad_input(tmp_path):
    s = LabelStore(tmp_path)
    with pytest.raises(ValueError):
        s.add(concept="cats", kind="true_match", lon=0, lat=0)
    with pytest.raises(ValueError):
        s.add(concept="warehouse", kind="maybe", lon=0, lat=0)
