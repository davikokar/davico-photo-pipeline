"""
Integration test for the bracketed image alignment adapter.
"""

from pathlib import Path

import pytest

from pipeline.state import SessionState
from pipeline.steps.hdr.aligner import adapter as aligner_adapter
from pipeline.utils.logger import get_logger
from run import DEFAULT_CONFIG, load_config

logger = get_logger("integration_test")


@pytest.mark.parametrize(
    "session_dir", ["C:\\Temp\\pipeline_tests\\output\\20260430_154800"]
)
def test_aligner_integration(session_dir):
    """
    Args:
        session_dir: session folder containing raw_conversions.json produced
            by the raw_to_jpg step.
    """

    config = load_config(DEFAULT_CONFIG)

    workspace = Path(session_dir).parent
    session_id = Path(session_dir).name
    state = SessionState(workspace=workspace, session_id=session_id)

    output = aligner_adapter.run(state, config=config, log=logger)
    assert output is not None and output.exists(), "alignments.json was not written"
