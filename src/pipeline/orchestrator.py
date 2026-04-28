"""
Pipeline orchestrator.

Coordinates execution of all steps across all groups,
manages review points, and handles errors.
"""

from pathlib import Path
from typing import Callable

from pipeline.state import SessionState, StepStatus, GroupType, PIPELINE_STEPS
from pipeline.utils.logger import get_logger, step_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Review point helpers
# ---------------------------------------------------------------------------

def _ask(prompt: str, choices: list[str] = ["y", "n"]) -> str:
    """Simple CLI prompt. Returns lowercased user input."""
    opts = "/".join(choices)
    while True:
        answer = input(f"\n{prompt} [{opts}]: ").strip().lower()
        if answer in choices:
            return answer
        print(f"  Please enter one of: {opts}")


def _review_grouping(state: SessionState) -> bool:
    """Show detected groups and ask for confirmation."""
    print("\n" + "─" * 60)
    print("REVIEW POINT — Grouping")
    print("─" * 60)
    for g in state.all_groups():
        print(f"\n  {g['id']}  type={g['type']}")
        for f in g["files"]:
            print(f"    · {f}")
    answer = _ask("Proceed with these groups?", ["y", "n", "edit"])
    if answer == "edit":
        print("  (Manual edit: modify workspace/state.json then re-run with `resume`)")
        return False
    return answer == "y"


def _review_hdr(state: SessionState) -> bool:
    """After HDR merge, show any ghost warnings from AI review notes."""
    print("\n" + "─" * 60)
    print("REVIEW POINT — HDR merge complete")
    print("─" * 60)
    warnings = []
    for g in state.all_groups():
        notes = [n for n in g.get("notes", []) if "ghost" in n["text"].lower()]
        if notes:
            warnings.append((g["id"], notes))

    if warnings:
        print("\n  ⚠️  Ghost warnings detected:")
        for gid, notes in warnings:
            for n in notes:
                print(f"    [{gid}] {n['text']}")
    else:
        print("\n  ✅ No ghost issues detected.")

    return _ask("Continue to next steps?") == "y"


def _review_final(state: SessionState, output_dir: Path) -> bool:
    """Final review before closing session."""
    done = [g for g in state.all_groups()
            if all(s["status"] in (StepStatus.DONE, StepStatus.SKIPPED)
                   for s in g["steps"].values())]
    print("\n" + "─" * 60)
    print("REVIEW POINT — Pipeline complete")
    print("─" * 60)
    print(f"\n  {len(done)} image(s) ready in: {output_dir}")
    for g in done:
        final = g["steps"]["cleanup"]["output"] or g["steps"]["color"]["output"]
        print(f"    · {final}")
    return _ask("Mark session as finished?") == "y"


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class Orchestrator:
    """
    Runs the full pipeline for a session.

    Steps are defined as methods named `_run_<stepname>`.
    Each method receives a group dict and the session state,
    and is responsible for calling state.step_start / step_done / step_failed.
    """

    def __init__(self, state: SessionState, config: dict, output_dir: Path):
        self.state      = state
        self.config     = config
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Attach file logger to session log dir
        self.logger = get_logger(
            "orchestrator",
            log_file=state.log_dir / "pipeline.log",
        )

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def run(self):
        """Run full pipeline from current state (skips already-done steps)."""
        self.logger.info("Pipeline started")

        # STEP: grouping (special — produces the groups themselves)
        if not self._grouping_done():
            self._run_grouping()
            if not _review_grouping(self.state):
                self.logger.warning("User aborted after grouping review")
                return

        # Reload groups from most recent JSON (picks up manual edits via HTML tool)
        self._reload_groups_from_json()

        # Process each group through the remaining steps
        for group in self.state.all_groups():
            self._process_group(group)

        # HDR review point (after all groups have done hdr_merge)
        if not _review_hdr(self.state):
            self.logger.warning("User aborted after HDR review")
            return

        # Continue remaining steps
        for group in self.state.all_groups():
            self._process_group_post_hdr(group)

        # Final review
        if _review_final(self.state, self.output_dir):
            self.state.mark_finished()
            self.logger.info("Session marked as finished")

        self.logger.info("Pipeline ended")

    def rerun_step(self, group_id: str, step: str):
        """Force reprocess a single step for a group."""
        group = self.state.get_group(group_id)
        if group is None:
            self.logger.error(f"Group {group_id} not found")
            return
        if step not in PIPELINE_STEPS:
            self.logger.error(f"Unknown step: {step}. Valid: {PIPELINE_STEPS}")
            return

        # Reset the step status so it runs again
        self.state._set_step(group_id, step, status=StepStatus.PENDING, output=None, error=None)
        self.logger.info(f"Re-running step {step} for {group_id}")
        self._dispatch_step(group, step)

    # ------------------------------------------------------------------
    # Group processing
    # ------------------------------------------------------------------

    def _grouping_done(self) -> bool:
        return len(self.state.all_groups()) > 0

    def _process_group(self, group: dict):
        """Run RAW conversion, HDR merge, and stitch steps for a group."""
        for step in ["raw_to_jpg", "hdr_merge", "stitch"]:
            self._dispatch_step(group, step)

    def _process_group_post_hdr(self, group: dict):
        """Run all steps after the HDR review point."""
        for step in ["geometry", "crop", "optics", "color", "cleanup"]:
            self._dispatch_step(group, step)

    def _dispatch_step(self, group: dict, step: str):
        """Route a step to the appropriate handler method."""
        status = self.state.get_step_status(group["id"], step)

        if status == StepStatus.DONE:
            self.logger.debug(f"Skipping {step} for {group['id']} (already done)")
            return

        handler: Callable | None = getattr(self, f"_run_{step}", None)
        if handler is None:
            self.logger.warning(f"No handler for step {step} — marking skipped")
            self.state.step_skip(group["id"], step, reason="no handler")
            return

        log = step_logger(self.logger, step=step, group=group["id"])
        log.info("Starting")
        self.state.step_start(group["id"], step)

        try:
            output = handler(group, log)
            self.state.step_done(group["id"], step, output=output)
            log.info(f"Done → {output}")
        except Exception as e:
            self.state.step_failed(group["id"], step, error=str(e))
            log.error(f"Failed: {e}")

    # ------------------------------------------------------------------
    # Step handlers (stubs — to be implemented module by module)
    # ------------------------------------------------------------------

    def _run_grouping(self):
        """
        Detect groups, save versioned JSON, generate HTML review page.

        After this runs, the session directory will contain:
          groups_001.json     ← auto-detected groups
          groups_review.html  ← interactive review/edit page

        The pipeline pauses at _review_grouping() so the user can open
        the HTML page, drag-and-drop any corrections, export a new JSON
        (groups_002.json), drop it in the session directory, then continue.
        The HDR step will automatically use the highest-numbered JSON.
        """
        from pipeline.steps.grouping.grouper import run_grouper, export_groups
        from pipeline.steps.grouping.groups_io import (
            load_latest_groups_json, json_to_state_groups, _next_version_number
        )

        input_dir = Path(self.state.session["input_dir"])
        pano_groups = run_grouper(self.state, self.config)

        json_path, html_path = export_groups(
            pano_groups  = pano_groups,
            state    = self.state,
        )
        self.logger.info(
            f"Grouping complete.\n"
            f"  → JSON:  {json_path}\n"
            f"  → HTML:  {html_path}\n"
            f"  Open the HTML file to review/edit groups, export a new JSON,\n"
            f"  drop it in {self.state.session_dir}, then continue."
        )

    def _reload_groups_from_json(self):
        """
        Reload groups into state from the most recent groups_NNN.json.

        Called just before HDR merge so that any manual edits made via the
        HTML review tool are picked up automatically.
        """
        from pipeline.steps.grouping.groups_io import (
            load_latest_groups_json, json_to_state_groups
        )
        from pipeline.state import GroupType

        data = load_latest_groups_json(self.state.session_dir)
        if data is None:
            self.logger.warning("No groups JSON found — using current state")
            return

        state_groups = json_to_state_groups(data["groups"])
        self.logger.info(
            f"Loaded {len(state_groups)} group(s) from "
            f"{data.get('generated_at','?')[:19]}"
        )

        # Re-register groups (overwrite whatever was in state from auto-detection)
        self.state._state["groups"] = {}
        for g in state_groups:
            self.state.add_group(g["id"], g["files"], g["type"])
            # Preserve bracket structure for HDR step
            self.state._state["groups"][g["id"]]["brackets"] = g.get("brackets", [])
        self.state.save()

    def _run_hdr_merge(self, group: dict, log) -> str | None:
        """Merge HDR exposures and apply deghosting."""
        # TODO: implement pipeline/steps/hdr_merger.py
        log.info("HDR merge (stub)")
        return None

    def _run_raw_to_jpg(self, group: dict, log) -> str | None:
        """Convert RAW files to JPEG derivatives required by the HDR step."""
        from pipeline.steps.hdr.raw_to_jpg_converter import convert_group_from_groups_json

        try:
            return str(
                convert_group_from_groups_json(
                    session_dir=self.state.session_dir,
                    group_id=group["id"],
                    config=self.config,
                    log=log,
                )
            )
        except FileNotFoundError as exc:
            self.state.step_skip(group["id"], "raw_to_jpg", reason=str(exc))
            return None
        except ValueError as exc:
            self.state.step_skip(group["id"], "raw_to_jpg", reason=str(exc))
            return None

    def _run_stitch(self, group: dict, log) -> str | None:
        """Stitch panoramic sequence."""
        if group["type"] not in ("panorama", "hdr+panorama"):
            self.state.step_skip(group["id"], "stitch", reason="not a panorama")
            return None
        # TODO: implement pipeline/steps/stitcher.py
        log.info("Panorama stitch (stub)")
        return None

    def _run_geometry(self, group: dict, log) -> str | None:
        """Lens distortion + perspective + horizon correction."""
        # TODO: implement pipeline/steps/geometry.py
        log.info("Geometry correction (stub)")
        return None

    def _run_crop(self, group: dict, log) -> str | None:
        """Crop and/or content-aware fill irregular borders."""
        # TODO: implement pipeline/steps/crop.py
        log.info("Crop/fill (stub)")
        return None

    def _run_optics(self, group: dict, log) -> str | None:
        """Chromatic aberration + dust removal + noise reduction."""
        # TODO: implement pipeline/steps/optics.py
        log.info("Optics correction (stub)")
        return None

    def _run_color(self, group: dict, log) -> str | None:
        """Color grading and tone correction."""
        # TODO: implement pipeline/steps/color.py
        log.info("Color correction (stub)")
        return None

    def _run_cleanup(self, group: dict, log) -> str | None:
        """Remove distracting elements via inpainting."""
        # TODO: implement pipeline/steps/cleanup.py
        log.info("Cleanup/inpainting (stub)")
        return None
