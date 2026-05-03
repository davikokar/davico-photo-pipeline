"""
Integration test for the raw to JPEG converter.
"""

from pathlib import Path

import pytest

from pipeline.state import SessionState
from pipeline.steps.hdr.raw_to_jpg import adapter as raw_to_jpg_adapter
from pipeline.utils.logger import get_logger
from run import DEFAULT_CONFIG, load_config

logger = get_logger("integration_test")


# @pytest.mark.parametrize("input_folder", ['C:\\Temp\\pipeline_tests\\mavic'])
@pytest.mark.parametrize("raw_dir", ["C:\\Temp\\pipeline_tests\\canon\\raw"])
@pytest.mark.parametrize(
    "session_dir", ["C:\\Temp\\pipeline_tests\\output\\20260430_154800"]
)
def test_raw_to_jpg_conversion(raw_dir, session_dir):
    """
    Args:
        raw_dir: where the raw files are located.
        session_dir: where the grouper output (groups json file) has been written.
    """

    config = load_config(DEFAULT_CONFIG)

    workspace = Path(session_dir).parent
    session_id = Path(session_dir).name
    state = SessionState(workspace=workspace, session_id=session_id, raw_dir=raw_dir)

    output = raw_to_jpg_adapter.run(state, config=config, log=logger)
    assert output is not None and output.exists(), (
        "raw_conversions.json was not written"
    )
