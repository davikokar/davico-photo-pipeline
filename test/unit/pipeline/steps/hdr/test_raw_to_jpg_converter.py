"""Unit tests for RAW to JPG conversion planning."""

from pathlib import Path

import pytest

from pipeline.steps.hdr.raw_to_jpg_converter import (
    build_raw_index,
    normalize_bracket_shots,
    parse_recipe_paths,
    plan_group_conversions,
    write_group_conversion_json,
)


def _make_hdr_config(tmp_path: Path, recipes: dict[str, str] | None = None) -> dict:
    recipe_map = recipes or {
        "0": str(tmp_path / "recipe_0.dr4"),
        "-2": str(tmp_path / "recipe_-2.dr4"),
        "+2": str(tmp_path / "recipe_+2.dr4"),
    }
    for path in recipe_map.values():
        Path(path).write_text("recipe", encoding="utf-8")

    return {
        "raw_dir": str(tmp_path / "raw"),
        "raw_to_jpg": {
            "recipes": recipe_map,
            "raw_extensions": [".cr3"],
        },
    }


def test_plan_single_shot_creates_three_exposures(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "IMG_0001.CR3").write_text("raw", encoding="utf-8")

    hdr_config = _make_hdr_config(tmp_path)
    raw_index = build_raw_index(raw_dir, [".cr3"])
    group = {
        "id": "group_001",
        "type": "single",
        "brackets": [{
            "shots": [{"filename": "IMG_0001.JPG", "ev": 12.0, "shutter": 0.01}],
        }],
    }

    requests, payload = plan_group_conversions(group, raw_index, hdr_config, tmp_path)

    assert len(requests) == 6
    assert [item["filename"] for item in payload["brackets"][0]["hdr"]] == [
        "IMG_0001_-2.jpg",
        "IMG_0001.jpg",
        "IMG_0001_+2.jpg",
    ]
    assert [item["filename"] for item in payload["brackets"][0]["noghost"]] == [
        "IMG_0001_-2.jpg",
        "IMG_0001.jpg",
        "IMG_0001_+2.jpg",
    ]
    assert payload["brackets"][0]["normalized"] == []


def test_plan_hdr_bracket_creates_hdr_noghost_and_normalized_sets(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    for stem in ("IMG_0100", "IMG_0101", "IMG_0102"):
        (raw_dir / f"{stem}.CR3").write_text("raw", encoding="utf-8")

    hdr_config = _make_hdr_config(tmp_path)
    raw_index = build_raw_index(raw_dir, [".cr3"])
    group = {
        "id": "group_002",
        "type": "hdr",
        "brackets": [{
            "shots": [
                {"filename": "IMG_0100.JPG", "ev": 12.0, "shutter": 0.01, "step_offset": 0.0, "reference_shot": True},
                {"filename": "IMG_0101.JPG", "ev": 14.0, "shutter": 0.005, "step_offset": -2.0, "reference_shot": False},
                {"filename": "IMG_0102.JPG", "ev": 10.0, "shutter": 0.02, "step_offset": 2.0, "reference_shot": False},
            ],
        }],
    }

    requests, payload = plan_group_conversions(group, raw_index, hdr_config, tmp_path)

    assert len(requests) == 7
    bracket_payload = payload["brackets"][0]
    assert [item["filename"] for item in bracket_payload["hdr"]] == [
        "IMG_0100.jpg",
        "IMG_0101.jpg",
        "IMG_0102.jpg",
    ]
    assert [item["filename"] for item in bracket_payload["noghost"]] == [
        "IMG_0100.jpg",
        "IMG_0100_-2.jpg",
        "IMG_0100_+2.jpg",
    ]
    assert [item["filename"] for item in bracket_payload["normalized"]] == [
        "IMG_0101_+2.jpg",
        "IMG_0102_-2.jpg",
    ]


def test_plan_hdr_bracket_requires_missing_recipe(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    for stem in ("IMG_0200", "IMG_0201", "IMG_0202"):
        (raw_dir / f"{stem}.CR3").write_text("raw", encoding="utf-8")

    hdr_config = _make_hdr_config(
        tmp_path,
        recipes={
            "0": str(tmp_path / "recipe_0.dr4"),
            "+1": str(tmp_path / "recipe_+1.dr4"),
        },
    )
    raw_index = build_raw_index(raw_dir, [".cr3"])
    group = {
        "id": "group_003",
        "type": "hdr",
        "brackets": [{
            "shots": [
                {"filename": "IMG_0200.JPG", "ev": 12.0, "shutter": 0.01, "reference_shot": True, "step_offset": 0.0},
                {"filename": "IMG_0201.JPG", "ev": 11.0, "shutter": 0.02, "reference_shot": False, "step_offset": 1.0},
            ],
        }],
    }

    with pytest.raises(ValueError, match="missing recipes"):
        plan_group_conversions(group, raw_index, hdr_config, tmp_path)


def test_write_group_conversion_json_upserts_groups(tmp_path: Path):
    aggregate_path = tmp_path / "raw_conversions.json"
    source_groups_payload = {
        "session_id": "session_001",
        "input_dir": str(tmp_path / "input"),
    }

    write_group_conversion_json(
        aggregate_path=aggregate_path,
        source_groups_payload=source_groups_payload,
        raw_dir=tmp_path / "raw",
        group_payload={"id": "group_001", "type": "hdr", "brackets": []},
    )
    write_group_conversion_json(
        aggregate_path=aggregate_path,
        source_groups_payload=source_groups_payload,
        raw_dir=tmp_path / "raw",
        group_payload={"id": "group_002", "type": "single", "brackets": []},
    )

    payload = aggregate_path.read_text(encoding="utf-8")
    assert '"group_001"' in payload
    assert '"group_002"' in payload