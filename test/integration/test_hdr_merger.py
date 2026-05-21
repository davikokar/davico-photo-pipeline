"""Integration test for the HDR merger adapter (PhotomatixCL)."""

import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.state import SessionState
from pipeline.steps.grouping.groups_io import load_latest_groups_json
from pipeline.steps.hdr.merger.adapter import run_group
from pipeline.steps.hdr.merger.hdr_merges_io import load_hdr_merges_json
from pipeline.utils.logger import get_logger
from run import DEFAULT_CONFIG, load_config

logger = get_logger("integration_test")

ALL_STYLES = ["natural", "realistic", "photographic"]


# ---------------------------------------------------------------------------
# Mock subprocess.run — drops a timestamped empty .jpg into output_dir
# ---------------------------------------------------------------------------


def _fake_subprocess_run(command, **kwargs):
    """Replaces subprocess.run: creates a fake output image in the -d directory."""

    class _FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    dest_dir = None
    for i, arg in enumerate(command):
        if arg == "-d" and i + 1 < len(command):
            dest_dir = command[i + 1].rstrip("\\").rstrip("/")
            break

    if dest_dir is None:
        return _FakeResult()

    dest_path = Path(dest_dir)
    dest_path.mkdir(parents=True, exist_ok=True)

    stem = Path(command[-1]).stem
    timestamp = datetime.now().strftime("%H%M%S%f")
    fake_file = dest_path / f"{stem}_{timestamp}.jpg"
    fake_file.write_bytes(b"")

    return _FakeResult()


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mock_photomatix", [True, False])
@pytest.mark.parametrize(
    "session_dir", ["C:\\Temp\\pipeline_tests\\output\\20260520_144910"]
)
def test_hdr_merger_integration(session_dir, mock_photomatix):
    """Requires alignments.json and raw_conversions.json to already exist
    in session_dir (produced by earlier pipeline steps).

    When mock_photomatix=True, subprocess.run is replaced with a stub that
    creates an empty timestamped file so the output-detection logic in
    photomatix.py works unchanged without requiring the real executable.
    All three styles (natural, realistic, photographic) are tested.
    """
    config = load_config(DEFAULT_CONFIG)

    # Enable all styles including photographic
    config["steps"]["hdr"]["merging"]["styles"] = ALL_STYLES

    # Provide a dummy xmp file for the photographic style
    if mock_photomatix:
        tmp_xmp = Path(tempfile.gettempdir()) / "test_photographic.xmp"
        tmp_xmp.write_text("<x:xmpmeta/>", encoding="utf-8")
        config["steps"]["hdr"]["merging"]["xmp_settings"] = str(tmp_xmp)

    workspace = Path(session_dir).parent
    session_id = Path(session_dir).name
    state = SessionState(workspace=workspace, session_id=session_id)

    groups_payload = load_latest_groups_json(state.session_dir)
    assert groups_payload is not None, "no groups JSON found in session directory"

    # Include groups that are explicitly HDR, plus "single" groups that have
    # raw conversions with multiple exposure shots (developed from one RAW).
    raw_conversions = None
    raw_conv_path = state.session_dir / "raw_conversions.json"
    if raw_conv_path.exists():
        import json
        with open(raw_conv_path, encoding="utf-8") as f:
            raw_conversions = json.load(f)

    def _is_mergeable(g):
        if g.get("type") in ("hdr", "hdr+panorama"):
            return True
        # Single groups with raw conversions that produced multiple shots
        if raw_conversions is not None:
            rc_group = next(
                (rg for rg in raw_conversions.get("groups", []) if rg["id"] == g["id"]),
                None,
            )
            if rc_group:
                for bracket in rc_group.get("brackets", []):
                    if len(bracket.get("shots", [])) >= 2:
                        return True
        return False

    hdr_groups = [g for g in groups_payload["groups"] if _is_mergeable(g)]
    assert hdr_groups, "no HDR-mergeable groups found — nothing to test"

    mock_target = "pipeline.steps.hdr.merger.photomatix.subprocess.run"

    with patch(mock_target, side_effect=_fake_subprocess_run) if mock_photomatix else _noop_context():
        for group in hdr_groups:
            output = run_group(
                group_id=group["id"],
                session_dir=state.session_dir,
                config=config,
                log=logger,
            )
            assert output is not None and output.exists(), (
                f"hdr_merges.json was not written for {group['id']}"
            )

    merges = load_hdr_merges_json(state.session_dir)
    assert merges is not None, "hdr_merges.json not found after processing"
    assert len(merges["groups"]) == len(hdr_groups), (
        f"expected {len(hdr_groups)} groups in hdr_merges.json, "
        f"got {len(merges['groups'])}"
    )

    for group in merges["groups"]:
        for bracket in group["brackets"]:
            assert bracket["merges"], (
                f"no merges in bracket {bracket['index']} of {group['id']}"
            )
            styles_produced = {m["style"] for m in bracket["merges"]}
            for style in ALL_STYLES:
                assert style in styles_produced, (
                    f"style '{style}' missing in bracket {bracket['index']} "
                    f"of {group['id']}"
                )
            for merge in bracket["merges"]:
                output_path = Path(session_dir) / merge["relative_path"]
                assert output_path.exists(), (
                    f"merged file not found: {output_path}"
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
