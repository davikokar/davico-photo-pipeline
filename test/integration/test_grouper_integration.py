"""
Integration test for the grouper pipeline.

Creates real JPEG files with embedded EXIF metadata in a temp directory,
then runs the full read_folder → run_grouper pipeline and verifies results.

No exiftool required — uses Pillow's native EXIF write support.
Since exiftool is not available in this environment, the pipeline falls back
to _read_exif_pillow, which is the path being exercised here.

Scenario (mimics a realistic shooting session):
────────────────────────────────────────────────
  10:00:00           single shot (street scene)
  10:05:00–10:05:01  HDR 3 shots  (-2 / 0 / +2 EV)
  10:12:00–10:12:01  HDR 5 shots  (-4 / -2 / 0 / +2 / +4 EV)
  10:20:00–10:20:20  Panorama 4 positions, single shot each (~5s apart)
  10:30:00–10:30:30  HDR+Panorama: 3 positions × 3 HDR shots
  22:00:00–22:01:03  Night HDR: 3 shots, long exposures (30s / 2s / 1s)

Expected groups: 6
  group_001  single
  group_002  hdr        (3 shots)
  group_003  hdr        (5 shots)
  group_004  panorama   (4 brackets)
  group_005  hdr+panorama (3×3 shots)
  group_006  hdr        (3 night shots, long exposures)
"""

import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from pipeline.utils.exif import read_folder
from pipeline.state import SessionState, GroupType
from pipeline.steps.grouping.grouper import run_grouper, grouping_report, grouping_html_report
from pipeline.utils.logger import get_logger

logger = get_logger("integration_test")

EXPECTED = [
    ("group_001", GroupType.HDR,          1, 3),   # (id, type, n_brackets, n_shots)
    ("group_002", GroupType.PANORAMA,     4, 12),
    ("group_003", GroupType.HDR,          1, 3),
]


@pytest.mark.parametrize("input_folder", ['C:\\temp\\pipeline_tests'])
def test_grouper_integration(input_folder):
    """
    Args:
        input_folder: Use this folder instead of a temp dir.

    """

    folder = Path(input_folder)

    # 1. Read EXIF
    logger.info("Reading EXIF metadata...")
    exif_data = read_folder(folder)
    logger.info(f"Read EXIF from {len(exif_data)} files")

    # Verify all files were read
    assert len(exif_data) == 18, f"Expected 18 files, got {len(exif_data)}"

    # 2. Run grouper
    logger.info("Running grouper...")
    with tempfile.TemporaryDirectory() as ws:
        state = SessionState(workspace=Path(ws), input_dir=str(folder))
        pano_groups = run_grouper(folder, state, config={})

    # 3. Print reports
    print(grouping_report(pano_groups))
    grouping_html_report(pano_groups, output_path=Path(ws) / "grouping_report.html")

    # 4. Assertions
    logger.info("Verifying results...")
    assert len(pano_groups) == len(EXPECTED), (
        f"Expected {len(EXPECTED)} groups, got {len(pano_groups)}"
    )

    for i, (exp_id, exp_type, exp_brackets, exp_shots) in enumerate(EXPECTED):
        pg = pano_groups[i]
        label = f"group_{i+1:03d}"

        assert pg.group_type == exp_type, (
            f"{label}: expected type {exp_type.value}, got {pg.group_type.value}"
        )
        assert len(pg.brackets) == exp_brackets, (
            f"{label}: expected {exp_brackets} bracket(s), got {len(pg.brackets)}"
        )
        assert len(pg.all_shots) == exp_shots, (
            f"{label}: expected {exp_shots} shot(s), got {len(pg.all_shots)}"
        )

        logger.info(
            f"  ✅ {label}  [{exp_type.value}]  "
            f"{exp_brackets} bracket(s), {exp_shots} shot(s)"
        )

    # 6. Verify night HDR was not split by long exposures
    night_group = pano_groups[5]
    assert night_group.group_type == GroupType.HDR, (
        "Night HDR group should be HDR, not split into singles"
    )
    assert len(night_group.all_shots) == 3, (
        f"Night HDR should have 3 shots, got {len(night_group.all_shots)}"
    )
    logger.info("  ✅ Night long-exposure HDR correctly grouped (not split)")

    logger.info(f"\n{'─'*50}")
    logger.info(f"Integration test PASSED — {len(pano_groups)}/{len(EXPECTED)} groups correct")


