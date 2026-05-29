"""EXIF restore step adapter.

Restores EXIF metadata to aligned images produced by the alignment step.
OpenCV's cv2.imwrite strips all EXIF data, leaving aligned images without
orientation tags and camera metadata. This step copies EXIF from the
original raw-converted source images back onto their aligned counterparts,
ensuring downstream tools (PhotomatixCL, manual HDR merging) see consistent
metadata across all bracket images.

Updates ``alignments.json`` with ``exif_restored: true`` on each processed
entry.
"""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.steps.grouping.groups_io import load_latest_groups_json
from pipeline.steps.hdr.aligner.alignments_io import load_alignments_json
from pipeline.steps.hdr.exif_restore.restorer import copy_exif_tags
from pipeline.steps.hdr.raw_to_jpg.raw_conversions_io import load_raw_conversions_json
from pipeline.utils.logger import get_logger

logger = get_logger(__name__)

ALIGNMENTS_FILENAME = "alignments.json"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_group(
    group_id: str,
    session_dir: Path,
    config: dict,
    log=None,
) -> Path | None:
    """Restore EXIF metadata to aligned images for a single group.

    :param str group_id: ID of the group to process
    :param Path session_dir: Session workspace directory
    :param dict config: Full pipeline configuration
    :param log: Optional logger
    :return: Path to updated ``alignments.json``, or ``None`` if nothing to do
    """
    log = log or logger
    session_dir = Path(session_dir)

    exiftool_exe = config.get("grouper", {}).get("exitool_exe", "exiftool")

    alignments = load_alignments_json(session_dir)
    if alignments is None:
        log.info("exif_restore: no alignments.json found — skipping")
        return None

    raw_conversions = load_raw_conversions_json(session_dir)

    groups_payload = load_latest_groups_json(session_dir)

    alignments_group = _find_group(alignments, group_id)
    if alignments_group is None:
        log.info("exif_restore: group %s not in alignments — skipping", group_id)
        return None

    rc_group = _find_group(raw_conversions, group_id) if raw_conversions else None
    rc_brackets = rc_group.get("brackets", []) if rc_group else []

    input_dir = _resolve_input_dir(groups_payload)
    log.info("exif_restore: exiftool = %s", exiftool_exe)
    log.info("exif_restore: input_dir (original images) = %s", input_dir)

    restored_count = 0

    for bracket in alignments_group.get("brackets", []):
        bracket_index = bracket.get("index", 0)
        rc_bracket = (
            rc_brackets[bracket_index]
            if bracket_index < len(rc_brackets)
            else None
        )

        sources_by_name = _build_sources_lookup(rc_bracket, input_dir, session_dir)

        # Restore EXIF for aligned_originals
        for entry in bracket.get("aligned_originals", []):
            if entry.get("exif_restored"):
                continue
            restored = _restore_entry(
                entry, sources_by_name, session_dir, exiftool_exe, log,
            )
            if restored:
                entry["exif_restored"] = True
                restored_count += 1


    if restored_count == 0:
        log.info("exif_restore: nothing to restore for %s", group_id)
        return None

    # Write the updated alignments back
    alignments_path = session_dir / ALIGNMENTS_FILENAME
    with open(alignments_path, "w", encoding="utf-8") as f:
        json.dump(alignments, f, indent=2, ensure_ascii=False)

    log.info(
        "exif_restore: restored EXIF on %d file(s) for %s",
        restored_count, group_id,
    )
    return alignments_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_sources_lookup(
    rc_bracket: dict | None,
    input_dir: Path | None,
    session_dir: Path,
) -> dict[str, Path]:
    """Build a filename -> absolute path mapping for all possible EXIF sources.

    Searches across all raw_conversions collections (shots, noghost, normalized)
    and falls back to the original input directory for camera JPEGs.
    """
    lookup: dict[str, Path] = {}

    # Add raw_conversions entries (all collections)
    if rc_bracket is not None:
        for collection in ("shots", "noghost", "normalized"):
            for entry in rc_bracket.get(collection, []):
                filename = entry["filename"]
                lookup[filename] = session_dir / entry["relative_path"]

    # Add original camera files from input directory
    if input_dir is not None and input_dir.exists():
        for f in input_dir.iterdir():
            if f.is_file():
                lookup[f.name] = f

    return lookup


def _restore_entry(
    entry: dict,
    sources_by_name: dict[str, Path],
    session_dir: Path,
    exiftool_exe: str,
    log,
) -> bool:
    """Copy EXIF from the original source image to an aligned file."""
    source_name = entry.get("source_filename")
    if not source_name:
        log.debug("exif_restore: entry has no source_filename — skipped")
        return False

    aligned_path = session_dir / entry["relative_path"]
    original_path = sources_by_name.get(source_name)

    if original_path is None:
        log.warning(
            "exif_restore: no original found for '%s' "
            "(searched %d known sources)",
            source_name, len(sources_by_name),
        )
        return False

    log.info(
        "exif_restore: %s -> %s",
        original_path, aligned_path,
    )

    if not original_path.exists():
        log.warning("exif_restore: original not found at %s", original_path)
        return False
    if not aligned_path.exists():
        log.warning("exif_restore: aligned file not found at %s", aligned_path)
        return False

    return copy_exif_tags(original_path, aligned_path, exiftool_exe, log)


def _resolve_input_dir(groups_payload: dict | None) -> Path | None:
    """Extract the input directory from the groups payload."""
    if groups_payload is None:
        return None
    input_dir_str = groups_payload.get("input_dir", "")
    if not input_dir_str:
        return None
    return Path(input_dir_str)


def _find_group(json_data: dict, group_id: str) -> dict | None:
    """Find a group by id in a loaded JSON payload."""
    for group in json_data.get("groups", []):
        if group["id"] == group_id:
            return group
    return None
