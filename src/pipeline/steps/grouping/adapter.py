"""
Grouping step adapter.

Bridges the pure grouper worker to the orchestrator and session state:
  1. Calls run_grouper() on the session's input folder
  2. Registers the detected groups in SessionState
  3. Writes the versioned groups_NNN.json checkpoint
  4. Generates the interactive HTML review page

This is the single entry point the orchestrator should call for the
grouping step.
"""

from pathlib import Path

from pipeline.state import SessionState
from pipeline.steps.grouping.grouper import PanoramaGroup, run_grouper
from pipeline.steps.grouping.groups_html import generate_review_html
from pipeline.steps.grouping.groups_io import (
    _next_version_number,
    panorama_groups_to_json,
    save_groups_json,
)
from pipeline.utils.logger import get_logger

logger = get_logger(__name__)


def run(state: SessionState, config: dict, log=None) -> tuple[Path, Path]:
    """Run the grouping step for a session.

    :param SessionState state: Session state (groups will be registered here)
    :param dict config: Full pipeline configuration
    :param log: Optional logger
    :return: Tuple of (groups JSON path, HTML review path)
    :rtype: tuple[Path, Path]
    """
    log = log or logger

    input_dir = Path(state.session["input_dir"])

    # 1. Run pure worker
    pano_groups = run_grouper(input_dir, config=config, log=log)

    # 2. Register groups in state and mark step as done
    _register_groups_in_state(state, pano_groups)

    # 3. Write JSON + HTML checkpoint
    json_path, html_path = _export_groups(state, pano_groups, input_dir)

    log.info("Grouping complete:")
    log.info(f"  → JSON: {json_path}")
    log.info(f"  → HTML: {html_path}")
    return json_path, html_path


def _register_groups_in_state(
    state: SessionState, pano_groups: list[PanoramaGroup]
) -> None:
    """Register every detected group in the session state and mark grouping done."""
    for i, pg in enumerate(pano_groups):
        group_id = f"group_{i + 1:03d}"
        file_names = [s.path.name for s in pg.all_shots]
        state.add_group(group_id, file_names, pg.group_type)
        state.step_done(group_id, "grouping")


def _export_groups(
    state: SessionState,
    pano_groups: list[PanoramaGroup],
    input_dir: Path,
) -> tuple[Path, Path]:
    """Write the versioned groups JSON and the HTML review page.

    :param SessionState state: Active session state
    :param list pano_groups: Detected groups from the worker
    :param Path input_dir: Source folder with JPEG files
    :return: Tuple of (json path, html path)
    :rtype: tuple[Path, Path]
    """
    session_dir = Path(state.session_dir)
    session_id = state.session_id

    groups_data = panorama_groups_to_json(pano_groups, input_dir)
    json_path = save_groups_json(groups_data, session_dir, session_id, str(input_dir))

    next_ver = _next_version_number(session_dir)
    html_path = session_dir / "groups_review.html"
    generate_review_html(
        groups_data=groups_data,
        input_dir=input_dir,
        output_path=html_path,
        session_id=session_id,
        next_version=next_ver,
    )

    return json_path, html_path
