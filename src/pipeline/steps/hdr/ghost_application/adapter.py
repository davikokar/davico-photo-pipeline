"""Ghost application step adapter.

Bridges the applicator worker to the orchestrator:
  1. Loads ``hdr_merges.json`` and ``ghosts.json``
  2. For each bracket with both ``aligned_originals`` and ``noghost`` source sets,
     blends the merge outputs using the ghost mask
  3. Writes results to ``ghost_applications.json``
"""

from __future__ import annotations

from pathlib import Path

from pipeline.steps.grouping.groups_io import load_latest_groups_json
from pipeline.steps.hdr.ghost_detector.ghosts_io import load_ghosts_json
from pipeline.steps.hdr.ghost_application.applicator import apply_ghost_mask
from pipeline.steps.hdr.ghost_application.ghost_applications_io import (
    build_application_entry,
    build_bracket_payload,
    upsert_group_in_ghost_applications_json,
)
from pipeline.steps.hdr.merger.hdr_merges_io import load_hdr_merges_json
from pipeline.utils.logger import get_logger

logger = get_logger(__name__)

GHOST_APPLICATION_OUTPUT_SUBDIR = "ghost_applied"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_group(
    group_id: str,
    session_dir: Path,
    config: dict,
    log=None,
) -> Path | None:
    """Run ghost application for a single group.

    :param str group_id: ID of the group to process
    :param Path session_dir: Session workspace directory
    :param dict config: Full pipeline configuration
    :param log: Optional logger
    :return: Path to ``ghost_applications.json``, or ``None`` if nothing to apply
    """
    log = log or logger
    session_dir = Path(session_dir)

    hdr_merges = load_hdr_merges_json(session_dir)
    ghosts = load_ghosts_json(session_dir)
    groups_payload = load_latest_groups_json(session_dir)

    if hdr_merges is None:
        log.info("ghost_application: no hdr_merges.json — skipping %s", group_id)
        return None

    if ghosts is None:
        log.info("ghost_application: no ghosts.json — skipping %s", group_id)
        return None

    merges_group = _find_group(hdr_merges, group_id)
    ghosts_group = _find_group(ghosts, group_id)

    if merges_group is None:
        log.info("ghost_application: group %s not in hdr_merges — skipping", group_id)
        return None

    if ghosts_group is None:
        log.info("ghost_application: group %s not in ghosts — skipping", group_id)
        return None

    output_dir = session_dir / GHOST_APPLICATION_OUTPUT_SUBDIR / group_id
    bracket_payloads = []

    for merge_bracket in merges_group.get("brackets", []):
        bracket_index = merge_bracket.get("index", 0)

        ghost_bracket = _find_bracket(ghosts_group, bracket_index)
        if ghost_bracket is None:
            log.info(
                "ghost_application: no ghost data for bracket %d — skipping",
                bracket_index,
            )
            continue

        ghost_mask_entry = ghost_bracket.get("ghost_mask")
        if ghost_mask_entry is None:
            log.info(
                "ghost_application: no merged ghost_mask for bracket %d — skipping",
                bracket_index,
            )
            continue

        mask_path = session_dir / ghost_mask_entry["relative_path"]
        if not mask_path.exists():
            log.warning(
                "ghost_application: mask file not found: %s — skipping bracket %d",
                mask_path, bracket_index,
            )
            continue

        reference = merge_bracket.get("reference", {})
        ref_stem = Path(reference.get("filename", "unknown")).stem

        application_entries = _process_bracket(
            merges=merge_bracket.get("merges", []),
            ref_stem=ref_stem,
            mask_path=mask_path,
            session_dir=session_dir,
            output_dir=output_dir,
            log=log,
        )

        if application_entries:
            bracket_payloads.append(
                build_bracket_payload(
                    bracket_index=bracket_index,
                    reference=reference,
                    applications=application_entries,
                )
            )

    if not bracket_payloads:
        log.info("ghost_application: no applications produced for %s", group_id)
        return None

    groups_group = _find_group(groups_payload, group_id) if groups_payload else None
    group_payload = {
        "id": group_id,
        "type": groups_group.get("type") if groups_group else merges_group.get("type"),
        "brackets": bracket_payloads,
    }

    source_payload = hdr_merges
    aggregate_path = upsert_group_in_ghost_applications_json(
        session_dir=session_dir,
        source_payload=source_payload,
        group_payload=group_payload,
    )

    log.info("ghost_application: results written to %s", aggregate_path)
    return aggregate_path


# ---------------------------------------------------------------------------
# Per-bracket processing
# ---------------------------------------------------------------------------


def _process_bracket(
    merges: list[dict],
    ref_stem: str,
    mask_path: Path,
    session_dir: Path,
    output_dir: Path,
    log,
) -> list[dict]:
    """Find matching style pairs and blend them using the ghost mask."""
    by_source_set: dict[str, dict[str, dict]] = {}
    for merge in merges:
        source_set = merge.get("source_set", "")
        style = merge.get("style", "")
        by_source_set.setdefault(source_set, {})[style] = merge

    aligned_by_style = by_source_set.get("aligned_originals", {})
    noghost_by_style = by_source_set.get("noghost", {})

    common_styles = set(aligned_by_style.keys()) & set(noghost_by_style.keys())
    if not common_styles:
        return []

    application_entries: list[dict] = []

    for style in sorted(common_styles):
        aligned_merge = aligned_by_style[style]
        noghost_merge = noghost_by_style[style]

        aligned_path = session_dir / aligned_merge["relative_path"]
        noghost_path = session_dir / noghost_merge["relative_path"]

        if not aligned_path.exists():
            log.warning("ghost_application: aligned file missing: %s", aligned_path)
            continue
        if not noghost_path.exists():
            log.warning("ghost_application: noghost file missing: %s", noghost_path)
            continue

        suffix = aligned_path.suffix
        output_filename = f"{ref_stem}_deghosted_{style}{suffix}"
        output_path = output_dir / output_filename

        log.info("ghost_application: blending %s (%s)", style, output_filename)
        apply_ghost_mask(
            aligned_path=aligned_path,
            noghost_path=noghost_path,
            mask_path=mask_path,
            output_path=output_path,
        )

        try:
            relative = output_path.relative_to(session_dir)
        except ValueError:
            relative = output_path

        source_files = [
            str(aligned_merge["relative_path"]),
            str(noghost_merge["relative_path"]),
            str(mask_path.relative_to(session_dir)).replace("\\", "/"),
        ]

        application_entries.append(
            build_application_entry(
                style=style,
                source_files=source_files,
                output_filename=output_filename,
                relative_path=relative,
            )
        )

    return application_entries


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_group(json_data: dict | None, group_id: str) -> dict | None:
    if json_data is None:
        return None
    for group in json_data.get("groups", []):
        if group["id"] == group_id:
            return group
    return None


def _find_bracket(group: dict, bracket_index: int) -> dict | None:
    for bracket in group.get("brackets", []):
        if bracket.get("index") == bracket_index:
            return bracket
    return None
