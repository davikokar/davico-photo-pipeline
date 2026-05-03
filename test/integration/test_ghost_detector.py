"""Integration test for the ghost detection adapter."""

from pathlib import Path

import pytest

from pipeline.state import SessionState
from pipeline.steps.hdr.ghost_detector import adapter as ghost_adapter
from pipeline.utils.logger import get_logger
from run import DEFAULT_CONFIG, load_config

logger = get_logger("integration_test")


@pytest.mark.parametrize("session_dir", ['C:\\Temp\\pipeline_tests\\output\\20260430_154800'])
def test_ghost_detector_integration(session_dir):
    """Requires alignments.json to already exist in session_dir."""
    config = load_config(DEFAULT_CONFIG)

    workspace = Path(session_dir).parent
    session_id = Path(session_dir).name
    state = SessionState(workspace=workspace, session_id=session_id)

    output = ghost_adapter.run(state, config=config, log=logger)
    assert output is not None and output.exists(), "ghosts.json was not written"