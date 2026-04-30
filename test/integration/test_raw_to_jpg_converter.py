"""
Integration test for the raw to JPEG converter.
"""

import sys
import tempfile
from datetime import datetime
from pathlib import Path
from run import load_config, DEFAULT_CONFIG
import pytest

from pipeline.steps.hdr.raw_to_jpg_converter import convert_all_groups_from_groups_json
from pipeline.utils.exif import read_folder
from pipeline.state import SessionState, GroupType
from pipeline.steps.grouping.grouper import export_groups, run_grouper, grouping_report
from pipeline.utils.logger import get_logger

logger = get_logger("integration_test")

# @pytest.mark.parametrize("input_folder", ['C:\\Temp\\pipeline_tests\\mavic'])
@pytest.mark.parametrize("raw_dir", ['C:\\Temp\\pipeline_tests\\canon\\raw'])
@pytest.mark.parametrize("session_dir", ['C:\\Temp\\pipeline_tests\\output\\20260429_161243'])
def test_raw_to_jpg_conversion(raw_dir, session_dir):
    """
    Args:
        raw_dir: where the raw files are located.
        session_dir: where the grouper output (groups json file) has been written.
    """

    # 1. Get config
    config = load_config(DEFAULT_CONFIG)

    # 2. run conversion
    output = convert_all_groups_from_groups_json(
        session_dir=session_dir,
        raw_dir=raw_dir,
        config=config,
        log=logger,)






