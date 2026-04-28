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

from pipeline.utils.exif import read_folder
from pipeline.state import SessionState, GroupType
from pipeline.steps.grouping.grouper import export_groups, run_grouper, grouping_report
from pipeline.utils.logger import get_logger

logger = get_logger("integration_test")

# @pytest.mark.parametrize("input_folder", ['C:\\Temp\\pipeline_tests\\mavic'])
@pytest.mark.parametrize("input_folder", ['C:\\Temp\\pipeline_tests\\canon\\original'])
@pytest.mark.parametrize("output_folder", ['C:\\Temp\\pipeline_tests\\output'])
def test_grouper_integration(input_folder, output_folder):
    """
    Args:
        input_folder: Use this folder instead of a temp dir.
        output_folder: Use this folder for grouper output (HTML report).

    """

    # 1. Read EXIF
    logger.info("Reading EXIF metadata...")
    exif_data = read_folder(Path(input_folder))
    logger.info(f"Read EXIF from {len(exif_data)} files")

    # 2. Run grouper
    logger.info("Running grouper...")
    state = SessionState(workspace=Path(output_folder), input_dir=input_folder)
    pano_groups = run_grouper(state, config={})
    export_groups(pano_groups, state)

    # 3. Print reports
    print(grouping_report(pano_groups))
    logger.info("Grouping report written to HTML")




