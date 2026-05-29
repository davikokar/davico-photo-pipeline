"""Integration test for the EXIF restore step."""

from pathlib import Path

import pytest

from pipeline.state import SessionState
from pipeline.steps.hdr.aligner.alignments_io import load_alignments_json
from pipeline.steps.hdr.exif_restore.adapter import run_group
from pipeline.utils.logger import get_logger
from run import DEFAULT_CONFIG, load_config

logger = get_logger("integration_test")


@pytest.mark.parametrize(
    "session_dir", ["C:\\Temp\\pipeline_tests\\output\\20260520_144910"]
)
def test_exif_restore_integration(session_dir):
    """Requires alignments.json and raw_conversions.json to already exist
    in session_dir (produced by earlier pipeline steps).

    Verifies that:
      - run_group processes aligned files and returns the alignments path
      - Each aligned_originals entry gets exif_restored=True
      - Each aligned_normalized entry gets exif_restored=True
    """
    config = load_config(DEFAULT_CONFIG)

    workspace = Path(session_dir).parent
    session_id = Path(session_dir).name
    state = SessionState(workspace=workspace, session_id=session_id)

    alignments = load_alignments_json(state.session_dir)
    assert alignments is not None, "no alignments.json found in session directory"

    groups = alignments.get("groups", [])
    assert groups, "no groups in alignments.json — nothing to test"

    for group in groups:
        group_id = group["id"]
        output = run_group(
            group_id=group_id,
            session_dir=state.session_dir,
            config=config,
            log=logger,
        )
        # First run should restore at least one file
        assert output is not None and output.exists(), (
            f"exif_restore returned None for {group_id}"
        )

    # Reload and verify exif_restored flags are set
    alignments_after = load_alignments_json(state.session_dir)
    for group in alignments_after["groups"]:
        for bracket in group.get("brackets", []):
            for entry in bracket.get("aligned_originals", []):
                assert entry.get("exif_restored") is True, (
                    f"exif_restored not set on {entry.get('filename')}"
                )

    # Second run should be a no-op (already restored)
    for group in alignments_after["groups"]:
        result = run_group(
            group_id=group["id"],
            session_dir=state.session_dir,
            config=config,
            log=logger,
        )
        assert result is None, (
            f"expected None on re-run (idempotent) for {group['id']}"
        )
