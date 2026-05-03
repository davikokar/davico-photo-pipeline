"""Ghost detection step adapter.

Bridges the pure GhostDetector worker to the orchestrator and SessionState:
  1. Loads ``alignments.json`` from the session directory
  2. For each bracket: detects ghost masks for every aligned non-reference shot
  3. Writes mask images and ``ghosts.json``
"""

from __future__ import annotations

import cv2
import numpy as np
from pathlib import Path

from pipeline.state import SessionState
from pipeline.steps.hdr.aligner.alignments_io import load_alignments_json
from pipeline.steps.hdr.ghost_detector.detector import GhostDetector
from pipeline.steps.hdr.ghost_detector.ghosts_io import (
    build_bracket_payload,
    build_mask_entry,
    upsert_group_in_ghosts_json,
)
from pipeline.utils.logger import get_logger

logger = get_logger(__name__)

GHOSTS_OUTPUT_SUBDIR = "ghosts"


def run(state: SessionState, config: dict, log=None) -> Path | None:
    """Run the ghost detection step for a session.

    :param SessionState state: Active session state
    :param dict config: Full pipeline configuration
    :param log: Optional logger
    :return: Path to ``ghosts.json``, or ``None`` if nothing to do
    :rtype: Path | None
    """
    log = log or logger
    session_dir = Path(state.session_dir)

    detector_cfg = config.get("steps", {}).get("hdr", {}).get("ghost_detector", {})
    diagnose = bool(detector_cfg.get("diagnose", False))

    alignments = load_alignments_json(session_dir)
    if alignments is None:
        log.info("ghost_detection skipped: alignments.json not found")
        return None

    detector = _build_detector(detector_cfg)
    output_root = session_dir / GHOSTS_OUTPUT_SUBDIR
    aggregate_path = None

    for group in alignments["groups"]:
        group_id = group["id"]
        log.info("ghost_detection: processing %s", group_id)

        bracket_payloads = []
        try:
            for bracket in group.get("brackets", []):
                payload = _process_bracket(
                    bracket=bracket,
                    session_dir=session_dir,
                    output_dir=output_root / group_id,
                    detector=detector,
                    diagnose=diagnose,
                    log=log,
                )
                if payload is not None:
                    bracket_payloads.append(payload)
        except Exception as exc:
            state.step_failed(group_id, "hdr_merge", error=f"ghost detection failed: {exc}")
            raise

        if not bracket_payloads:
            log.info("ghost_detection: no masks produced for %s — skipping", group_id)
            continue

        group_payload = {
            "id": group_id,
            "type": group.get("type"),
            "brackets": bracket_payloads,
        }
        aggregate_path = upsert_group_in_ghosts_json(
            session_dir=session_dir,
            source_payload=alignments,
            group_payload=group_payload,
        )

    if aggregate_path:
        log.info("ghost_detection: aggregate JSON written to %s", aggregate_path)
    else:
        log.info("ghost_detection: produced no output")
    return aggregate_path


# ---------------------------------------------------------------------------
# Detector construction
# ---------------------------------------------------------------------------

def _build_detector(detector_cfg: dict) -> GhostDetector:
    """Instantiate GhostDetector from the ``steps.hdr.ghost_detector`` config section."""
    return GhostDetector(
        threshold=int(detector_cfg.get("threshold", 20)),
        min_area=int(detector_cfg.get("min_area", 50)),
        dilation_size=int(detector_cfg.get("dilation_size", 31)),
        blur_size=int(detector_cfg.get("blur_size", 151)),
        kernel_size=tuple(detector_cfg.get("kernel_size", (5, 5))),
    )


# ---------------------------------------------------------------------------
# Per-bracket processing
# ---------------------------------------------------------------------------

def _process_bracket(
    bracket: dict,
    session_dir: Path,
    output_dir: Path,
    detector: GhostDetector,
    diagnose: bool,
    log,
) -> dict | None:
    """Detect ghost masks for every aligned non-reference shot in one bracket."""
    reference_entry = bracket.get("reference")
    aligned_normalized = bracket.get("aligned_normalized", [])

    if reference_entry is None or not aligned_normalized:
        log.info(
            "ghost_detection: bracket %d has no reference or aligned shots — skipping",
            bracket.get("index", -1),
        )
        return None

    reference_path = (session_dir / reference_entry["relative_path"]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    mask_entries = []
    ref_stem = reference_path.stem

    for aligned in aligned_normalized:
        aligned_path = (session_dir / aligned["relative_path"]).resolve()
        mask = detector.detect_ghost_mask(reference_path, aligned_path)
        coverage_pct = float((mask > 0).sum()) / mask.size * 100.0

        mask_filename = f"ghost_mask_{ref_stem}_vs_{aligned_path.stem}.jpg"
        mask_path = output_dir / mask_filename
        cv2.imwrite(str(mask_path), (mask * 255).astype(np.uint8))

        mask_entries.append(
            build_mask_entry(
                source_filename=aligned["filename"],
                mask_filename=mask_filename,
                relative_path=mask_path.relative_to(session_dir),
                step_offset=float(aligned.get("step_offset", 0.0)),
                coverage_pct=coverage_pct,
            )
        )

        if diagnose:
            _write_diagnostic(
                output_dir=output_dir,
                reference_path=reference_path,
                aligned_path=aligned_path,
                mask=mask,
                detector=detector,
                log=log,
            )

    if not mask_entries:
        return None

    reference_payload = {
        "filename": reference_entry["filename"],
        "relative_path": reference_entry["relative_path"],
    }

    return build_bracket_payload(
        bracket_index=int(bracket.get("index", 0)),
        reference=reference_payload,
        masks=mask_entries,
    )


def _write_diagnostic(
    output_dir: Path,
    reference_path: Path,
    aligned_path: Path,
    mask: np.ndarray,
    detector: GhostDetector,
    log,
) -> None:
    """Write a red-overlay visualization of the ghost mask on the reference image."""
    diagnostics_dir = output_dir / "diagnostics"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)

    overlay = detector.visualize_ghosts(reference_path, mask)
    out_path = diagnostics_dir / f"overlay_{reference_path.stem}_vs_{aligned_path.stem}.jpg"
    cv2.imwrite(str(out_path), overlay)
    log.debug("ghost_detection diagnostic written to %s", out_path)