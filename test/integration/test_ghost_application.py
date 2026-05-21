"""Integration test for the ghost application adapter."""

from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.state import SessionState
from pipeline.steps.hdr.ghost_application.adapter import run_group
from pipeline.steps.hdr.ghost_application.ghost_applications_io import (
    load_ghost_applications_json,
)
from pipeline.steps.hdr.ghost_detector.ghosts_io import load_ghosts_json
from pipeline.steps.hdr.merger.hdr_merges_io import load_hdr_merges_json
from pipeline.utils.logger import get_logger
from run import DEFAULT_CONFIG, load_config

logger = get_logger("integration_test")


# ---------------------------------------------------------------------------
# Mock apply_ghost_mask — writes an empty file instead of blending images
# ---------------------------------------------------------------------------


def _fake_apply_ghost_mask(aligned_path, noghost_path, mask_path, output_path):
    """Replaces apply_ghost_mask: creates an empty file at output_path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"")
    return output_path


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mock_applicator", [True, False])
@pytest.mark.parametrize(
    "session_dir", ["C:\\Temp\\pipeline_tests\\output\\20260520_144910"]
)
def test_ghost_application_integration(session_dir, mock_applicator):
    """Requires hdr_merges.json and ghosts.json to already exist in session_dir
    (produced by the hdr_merge and ghost_detector steps).

    When mock_applicator=True, the actual image blending is replaced with a
    stub that creates an empty file. This allows testing JSON I/O logic
    without requiring real image data (e.g. using outputs from a mocked
    hdr_merge run).
    """
    config = load_config(DEFAULT_CONFIG)

    workspace = Path(session_dir).parent
    session_id = Path(session_dir).name
    state = SessionState(workspace=workspace, session_id=session_id)

    merges = load_hdr_merges_json(state.session_dir)
    assert merges is not None, "hdr_merges.json not found — run hdr_merge step first"

    ghosts = load_ghosts_json(state.session_dir)
    assert ghosts is not None, "ghosts.json not found — run ghost_detector step first"

    # Find groups that have both aligned_originals and noghost merges
    applicable_groups = []
    for group in merges["groups"]:
        for bracket in group.get("brackets", []):
            source_sets = {m["source_set"] for m in bracket.get("merges", [])}
            if "aligned_originals" in source_sets and "noghost" in source_sets:
                applicable_groups.append(group)
                break

    assert applicable_groups, (
        "no groups with both aligned_originals and noghost source sets — "
        "nothing to test"
    )

    mock_target = "pipeline.steps.hdr.ghost_application.adapter.apply_ghost_mask"

    with patch(mock_target, side_effect=_fake_apply_ghost_mask) if mock_applicator else _noop_context():
        for group in applicable_groups:
            output = run_group(
                group_id=group["id"],
                session_dir=state.session_dir,
                config=config,
                log=logger,
            )
            assert output is not None and output.exists(), (
                f"ghost_applications.json was not written for {group['id']}"
            )

    applications = load_ghost_applications_json(state.session_dir)
    assert applications is not None, (
        "ghost_applications.json not found after processing"
    )
    assert len(applications["groups"]) == len(applicable_groups), (
        f"expected {len(applicable_groups)} groups in ghost_applications.json, "
        f"got {len(applications['groups'])}"
    )

    for group in applications["groups"]:
        for bracket in group["brackets"]:
            assert bracket["applications"], (
                f"no applications in bracket {bracket['index']} of {group['id']}"
            )
            for app in bracket["applications"]:
                output_path = Path(session_dir) / app["relative_path"]
                assert output_path.exists(), (
                    f"ghost-applied file not found: {output_path}"
                )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _noop_context:
    """Context manager that does nothing (used when mock is disabled)."""

    def __enter__(self):
        return None

    def __exit__(self, *_):
        return False
