"""Bracketed image alignment step adapter.

Bridges the pure aligner worker to the orchestrator and SessionState:
  1. Loads ``raw_conversions.json`` from the session directory
  2. For each HDR bracket: collects reference + non-reference shots and runs the worker
  3. Writes ``alignments.json`` and updates SessionState per group
"""

from __future__ import annotations

import cv2
from pathlib import Path

from pipeline.state import SessionState
from pipeline.steps.hdr.aligner.aligner import BracketedImagesAligner
from pipeline.steps.hdr.aligner.alignments_io import (
    build_aligned_entry,
    build_bracket_payload,
    upsert_group_in_alignments_json,
)
from pipeline.steps.hdr.raw_to_jpg.raw_conversions_io import load_raw_conversions_json
from pipeline.utils.logger import get_logger

logger = get_logger(__name__)

ALIGNMENT_OUTPUT_SUBDIR = "aligned"


def run(state: SessionState, config: dict, log=None) -> Path | None:
    """Run the bracketed image alignment step for a session.

    :param SessionState state: Active session state
    :param dict config: Full pipeline configuration (not used yet, reserved)
    :param log: Optional logger
    :return: Path to ``alignments.json``, or ``None`` if nothing to do
    :rtype: Path | None
    """
    log = log or logger
    session_dir = Path(state.session_dir)

    aligner_cfg = config.get("steps", {}).get("hdr", {}).get("aligner", {})
    diagnose = bool(aligner_cfg.get("diagnose", False))

    log.info("alignment: looking for raw_conversions.json in session_dir=%s", session_dir)
    raw_conversions = load_raw_conversions_json(session_dir)
    if raw_conversions is None:
        log.info("alignment skipped: raw_conversions.json not found at %s", session_dir / "raw_conversions.json")
        return None

    output_root = session_dir / ALIGNMENT_OUTPUT_SUBDIR
    aligner = BracketedImagesAligner()  # loads LoFTR model once for the whole session
    aggregate_path = None

    input_dir = Path(raw_conversions.get("input_dir", ""))
    log.info("alignment: input_dir=%s (exists=%s), groups=%d", input_dir, input_dir.exists(), len(raw_conversions.get("groups", [])))

    for group in raw_conversions["groups"]:
        group_id = group["id"]
        log.info("alignment: processing %s", group_id)

        bracket_payloads = []
        try:
            for bracket in group.get("brackets", []):
                payload = _process_bracket(
                    bracket=bracket,
                    session_dir=session_dir,
                    input_dir=input_dir,
                    output_root=output_root / group_id,
                    aligner=aligner,
                    diagnose=diagnose,
                    log=log,
                )
                if payload is not None:
                    bracket_payloads.append(payload)
        except Exception as exc:
            state.step_failed(group_id, "hdr_merge", error=f"alignment failed: {exc}")
            raise

        if not bracket_payloads:
            log.info("alignment: nothing to align for %s — skipping", group_id)
            continue

        group_payload = {
            "id": group_id,
            "type": group.get("type"),
            "brackets": bracket_payloads,
        }
        aggregate_path = upsert_group_in_alignments_json(
            session_dir=session_dir,
            source_payload=raw_conversions,
            group_payload=group_payload,
        )

    if aggregate_path:
        log.info("alignment: aggregate JSON written to %s", aggregate_path)
    else:
        log.info("alignment: produced no output (no HDR brackets?)")
    return aggregate_path


# ---------------------------------------------------------------------------
# Per-bracket processing
# ---------------------------------------------------------------------------


def _process_bracket(
    bracket: dict,
    session_dir: Path,
    input_dir: Path,
    output_root: Path,
    aligner: BracketedImagesAligner,
    diagnose: bool,
    log,
) -> dict | None:
    """Align one bracket and return its payload, or None if nothing to align."""
    normalized = bracket.get("normalized", [])
    shots = bracket.get("shots", [])
    source = bracket.get("source", [])

    log.info(
        "alignment: bracket %s — shots=%d, normalized=%d, source=%d",
        bracket.get("index", "?"), len(shots), len(normalized), len(source),
    )

    if not shots:
        log.info("alignment: no recipe-0 shots, falling back to source images from input_dir=%s", input_dir)
        shots = _shots_from_source(bracket, input_dir)
        log.info("alignment: built %d shot entries from source", len(shots))
        for s in shots:
            log.info("  source shot: %s ref=%s offset=%s path=%s", s["filename"], s["reference_shot"], s["step_offset"], s["relative_path"])

    reference_entry = next((s for s in shots if s.get("reference_shot")), None)
    if reference_entry is None:
        log.info(
            "alignment: bracket %d has no reference shot — skipping",
            bracket.get("index", -1),
        )
        return None

    reference_path = (session_dir / reference_entry["relative_path"]).resolve()
    log.info("alignment: reference=%s exists=%s", reference_path, reference_path.exists())

    # Pair each non-reference original (in shots / noghost) with its exposure-normalized counterpart.
    originals = [s for s in shots if not s.get("reference_shot")]
    log.info("alignment: originals=%d, normalized=%d", len(originals), len(normalized))
    if not originals or not normalized:
        log.info(
            "alignment: bracket %d has no non-reference shots to align — skipping",
            bracket.get("index", -1),
        )
        return None

    normalized_by_offset = {round(float(s["step_offset"]), 4): s for s in normalized}
    log.info("alignment: normalized offsets available: %s", sorted(normalized_by_offset.keys()))

    pairs: list[tuple[dict, dict]] = []  # (original_shot, normalized_shot)
    for original in originals:
        # The normalized counterpart of an original at offset +X is the file produced
        # by applying the opposite recipe (-X) → its step_offset is the original's offset.
        offset = round(float(original["step_offset"]), 4)
        norm = normalized_by_offset.get(offset)
        if norm is None:
            log.warning(
                "alignment: no normalized counterpart for offset %+.2f — skipping shot %s",
                offset, original["filename"],
            )
            continue
        log.info("alignment: paired %s (offset %+.2f) with normalized %s", original["filename"], offset, norm["filename"])
        pairs.append((original, norm))

    if not pairs:
        log.info("alignment: no pairs found — skipping bracket")
        return None

    output_dir = output_root
    output_dir.mkdir(parents=True, exist_ok=True)

    original_paths = [(session_dir / o["relative_path"]).resolve() for o, _ in pairs]
    normalized_paths = [(session_dir / n["relative_path"]).resolve() for _, n in pairs]

    for orig_path, norm_path in zip(original_paths, normalized_paths):
        log.info("alignment: original=%s exists=%s", orig_path, orig_path.exists())
        log.info("alignment: normalized=%s exists=%s", norm_path, norm_path.exists())

    # Run the worker (returns reference image as element 0 of each list)
    aligned_normalized, aligned_original = aligner.align(
        ref_image_path=reference_path,
        normalized_images_paths=normalized_paths,
        original_images_paths=original_paths,
        output_folder=output_dir,
    )

    if diagnose:
        _write_diagnostics(
            output_dir=output_dir,
            reference_path=reference_path,
            normalized_paths=normalized_paths,
            aligned_normalized=aligned_normalized,
            log=log,
        )

    # The aligner writes files to disk with a fixed naming scheme
    aligned_originals_payload = []
    aligned_normalized_payload = []
    for (original, norm), orig_path, norm_path in zip(
        pairs, original_paths, normalized_paths
    ):
        original_aligned_name = f"{orig_path.stem}_original_aligned{orig_path.suffix}"
        normalized_aligned_name = (
            f"{norm_path.stem}_normalized_aligned{norm_path.suffix}"
        )

        aligned_originals_payload.append(
            build_aligned_entry(
                source_filename=original["filename"],
                aligned_filename=original_aligned_name,
                relative_path=(output_dir / original_aligned_name).relative_to(
                    session_dir
                ),
                step_offset=float(original["step_offset"]),
            )
        )
        aligned_normalized_payload.append(
            build_aligned_entry(
                source_filename=norm["filename"],
                aligned_filename=normalized_aligned_name,
                relative_path=(output_dir / normalized_aligned_name).relative_to(
                    session_dir
                ),
                step_offset=float(norm["step_offset"]),
            )
        )

    # Point to the saved reference copy (rotation baked in by OpenCV,
    # ensuring pixel dimensions match all other aligned outputs).
    ref_copy_name = f"{Path(reference_path).stem}_reference{Path(reference_path).suffix}"
    ref_copy_path = output_dir / ref_copy_name

    reference_payload = {
        "filename": ref_copy_name,
        "relative_path": str(ref_copy_path.relative_to(session_dir)).replace("\\", "/"),
    }

    return build_bracket_payload(
        bracket_index=int(bracket.get("index", 0)),
        reference=reference_payload,
        aligned_originals=aligned_originals_payload,
        aligned_normalized=aligned_normalized_payload,
    )


def _shots_from_source(bracket: dict, input_dir: Path) -> list[dict]:
    """Build shot entries from source metadata when recipe-0 conversions were skipped."""
    return [
        {
            "filename": src["filename"],
            "relative_path": str(input_dir / src["filename"]),
            "reference_shot": src.get("reference_shot", False),
            "step_offset": src.get("step_offset", 0.0),
        }
        for src in bracket.get("source", [])
    ]


def _write_diagnostics(
    output_dir: Path,
    reference_path: Path,
    normalized_paths: list[Path],
    aligned_normalized: list,
    log,
) -> None:
    """Write checkerboard + difference diagnostics for each aligned non-reference shot.

    Each aligned normalized shot is compared against the reference image.
    Both have matching exposure, so any visible difference is misalignment
    or ghosting (which is what we want to inspect).

    :param Path output_dir: Bracket output directory (diagnostics go in ``diagnostics/``)
    :param Path reference_path: Path to the reference (middle exposure) image
    :param list[Path] normalized_paths: Source paths of the non-reference normalized shots
    :param list aligned_normalized: Aligner output — index 0 is the reference image,
        indices 1..N are the aligned non-reference normalized images
    :param log: Logger
    """
    diagnostics_dir = output_dir / "diagnostics"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)

    ref_image = aligned_normalized[0]
    ref_stem = Path(reference_path).stem

    for source_path, aligned_image in zip(normalized_paths, aligned_normalized[1:]):
        target_stem = Path(source_path).stem
        base = f"{ref_stem}_vs_{target_stem}_aligned"

        checker = BracketedImagesAligner.create_checkerboard_comparison(
            ref_image, aligned_image
        )
        cv2.imwrite(str(diagnostics_dir / f"checker_{base}.jpg"), checker)

        diff = BracketedImagesAligner.create_difference_image(ref_image, aligned_image)
        cv2.imwrite(str(diagnostics_dir / f"diff_{base}.jpg"), diff)

    log.info("alignment diagnostics written to %s", diagnostics_dir)
