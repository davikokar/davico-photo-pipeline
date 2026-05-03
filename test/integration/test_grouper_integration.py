"""
Integration test for the grouping step.
"""

from pathlib import Path

import pytest

from pipeline.state import SessionState
from pipeline.steps.grouping import adapter as grouping_adapter
from pipeline.steps.grouping.grouper import grouping_report, run_grouper
from pipeline.utils.logger import get_logger

logger = get_logger("integration_test")


# @pytest.mark.parametrize("input_folder", ['C:\\Temp\\pipeline_tests\\mavic'])
@pytest.mark.parametrize("input_folder", ["C:\\Temp\\pipeline_tests\\canon\\original"])
@pytest.mark.parametrize("output_folder", ["C:\\Temp\\pipeline_tests\\output"])
def test_grouper_integration(input_folder, output_folder):
    """
    Args:
        input_folder: Use this folder instead of a temp dir.
        output_folder: Use this folder for grouper output (HTML report).

    """
    input_dir = Path(input_folder)
    workspace = Path(output_folder)

    # 1. Worker call
    logger.info("Running grouper worker...")
    pano_groups = run_grouper(input_dir, config={}, log=logger)
    assert len(pano_groups) > 0, "Grouper produced no groups"
    print(grouping_report(pano_groups))

    # 2. Adapter call — registers groups in state + writes groups_NNN.json + HTML
    logger.info("Running grouping adapter...")
    state = SessionState(workspace=workspace, input_dir=str(input_dir))
    json_path, html_path = grouping_adapter.run(state, config={}, log=logger)

    # 3. Verify outputs
    assert json_path.exists(), f"Groups JSON not written: {json_path}"
    assert html_path.exists(), f"Review HTML not written: {html_path}"
    assert len(state.all_groups()) == len(pano_groups), (
        "Adapter and worker produced a different number of groups"
    )

    logger.info(f"Groups JSON: {json_path}")
    logger.info(f"Review HTML: {html_path}")
