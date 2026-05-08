"""
Test source_classifier — aerial/terrestrial auto-detection from camera make.
"""

from datetime import datetime
from pathlib import Path

from pipeline.state import CaptureSource
from pipeline.steps.grouping.source_classifier import classify_group, classify_shot
from pipeline.utils.exif import ExifData


def _shot(camera_make: str = "") -> ExifData:
    return ExifData(
        path=Path("/fake/IMG.jpg"),
        timestamp=datetime(2024, 6, 15, 10, 0, 0),
        camera_make=camera_make,
    )


def test_canon_is_terrestrial():
    assert classify_shot(_shot("Canon")) == CaptureSource.TERRESTRIAL


def test_dji_is_aerial():
    assert classify_shot(_shot("DJI")) == CaptureSource.AERIAL


def test_case_insensitive_canon():
    assert classify_shot(_shot("CANON")) == CaptureSource.TERRESTRIAL
    assert classify_shot(_shot("canon")) == CaptureSource.TERRESTRIAL


def test_case_insensitive_dji():
    assert classify_shot(_shot("dji")) == CaptureSource.AERIAL
    assert classify_shot(_shot("Dji")) == CaptureSource.AERIAL


def test_unknown_make_defaults_terrestrial():
    assert classify_shot(_shot("Nikon")) == CaptureSource.TERRESTRIAL
    assert classify_shot(_shot("Sony")) == CaptureSource.TERRESTRIAL


def test_empty_make_defaults_terrestrial():
    assert classify_shot(_shot("")) == CaptureSource.TERRESTRIAL
    assert classify_shot(_shot("  ")) == CaptureSource.TERRESTRIAL


def test_group_all_canon():
    shots = [_shot("Canon") for _ in range(5)]
    assert classify_group(shots) == CaptureSource.TERRESTRIAL


def test_group_all_dji():
    shots = [_shot("DJI") for _ in range(3)]
    assert classify_group(shots) == CaptureSource.AERIAL


def test_group_empty():
    assert classify_group([]) == CaptureSource.TERRESTRIAL


def test_group_mixed_majority_aerial():
    shots = [_shot("DJI"), _shot("DJI"), _shot("Canon")]
    assert classify_group(shots) == CaptureSource.AERIAL


def test_group_mixed_majority_terrestrial():
    shots = [_shot("Canon"), _shot("Canon"), _shot("DJI")]
    assert classify_group(shots) == CaptureSource.TERRESTRIAL


def test_group_tie_defaults_terrestrial():
    shots = [_shot("DJI"), _shot("Canon")]
    assert classify_group(shots) == CaptureSource.TERRESTRIAL


def test_make_with_extra_text():
    assert classify_shot(_shot("Canon Inc.")) == CaptureSource.TERRESTRIAL
    assert classify_shot(_shot("DJI Technology")) == CaptureSource.AERIAL
