"""Versioned persistence for ghost detection results.

Reads/writes the aggregate ``ghosts.json`` file in the session directory.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pipeline.utils.logger import get_logger

logger = get_logger(__name__)

GHOSTS_VERSION = 1
GHOSTS_FILENAME = "ghosts.json"


# ---------------------------------------------------------------------------
# Per-bracket payload
# ---------------------------------------------------------------------------

def build_bracket_payload(
    bracket_index: int,
    reference: dict,
    masks: list[dict],
) -> dict:
    """Build the JSON payload for one bracket's ghost masks.

    :param int bracket_index: Zero-based bracket index within the group
    :param dict reference: Reference shot info (``filename``, ``relative_path``)
    :param list[dict] masks: One entry per non-reference aligned shot
    :return: Serialisable bracket payload
    :rtype: dict
    """
    return {
        "index": bracket_index,
        "reference": reference,
        "masks": masks,
    }


def build_mask_entry(
    source_filename: str,
    mask_filename: str,
    relative_path: Path | str,
    step_offset: float,
    coverage_pct: float,
) -> dict:
    """Describe one ghost mask file in the JSON payload.

    :param str source_filename: Aligned image the mask was computed from
    :param str mask_filename: Output mask filename
    :param relative_path: Mask path relative to session_dir
    :param float step_offset: EV offset of the source aligned shot
    :param float coverage_pct: Percentage of the image flagged as ghost (0-100)
    """
    return {
        "source_filename": source_filename,
        "filename": mask_filename,
        "relative_path": str(relative_path).replace("\\", "/"),
        "step_offset": float(step_offset),
        "coverage_pct": round(float(coverage_pct), 3),
    }


# ---------------------------------------------------------------------------
# Aggregate JSON read/write
# ---------------------------------------------------------------------------

def load_ghosts_json(session_dir: Path) -> dict | None:
    """Load the aggregate ``ghosts.json``, or ``None`` if absent."""
    path = Path(session_dir) / GHOSTS_FILENAME
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    _validate(data)
    return data


def upsert_group_in_ghosts_json(
    session_dir: Path,
    source_payload: dict,
    group_payload: dict,
) -> Path:
    """Insert or replace one group in the aggregate ``ghosts.json``.

    :param Path session_dir: Session directory
    :param dict source_payload: Upstream JSON payload (alignments) — used for session metadata
    :param dict group_payload: Serialisable group payload
    :return: Path to the aggregate JSON
    :rtype: Path
    """
    aggregate_path = Path(session_dir) / GHOSTS_FILENAME

    if aggregate_path.exists():
        with open(aggregate_path, encoding="utf-8") as handle:
            payload = json.load(handle)
    else:
        payload = {
            "version": GHOSTS_VERSION,
            "session_id": source_payload.get("session_id"),
            "input_dir": source_payload.get("input_dir"),
            "generated_at": datetime.now().isoformat(),
            "groups": [],
        }

    payload["generated_at"] = datetime.now().isoformat()

    groups_by_id = {group["id"]: group for group in payload.get("groups", [])}
    groups_by_id[group_payload["id"]] = group_payload
    payload["groups"] = sorted(groups_by_id.values(), key=lambda item: item["id"])

    with open(aggregate_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)

    return aggregate_path


def _validate(data: dict) -> None:
    if data.get("version") != GHOSTS_VERSION:
        raise ValueError(
            f"Unsupported ghosts.json version: {data.get('version')} "
            f"(expected {GHOSTS_VERSION})"
        )
    if "groups" not in data or not isinstance(data["groups"], list):
        raise ValueError("ghosts.json: missing or invalid 'groups' field")