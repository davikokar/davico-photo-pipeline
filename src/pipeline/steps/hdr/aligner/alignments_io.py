"""Versioned persistence for HDR alignment results.

Mirrors the role of ``raw_conversions_io`` for the alignment step.
Reads/writes the aggregate ``alignments.json`` file in the session
directory.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pipeline.utils.logger import get_logger

logger = get_logger(__name__)

ALIGNMENTS_VERSION = 1
ALIGNMENTS_FILENAME = "alignments.json"


# ---------------------------------------------------------------------------
# Per-bracket payload
# ---------------------------------------------------------------------------


def build_bracket_payload(
    bracket_index: int,
    reference: dict,
    aligned_originals: list[dict],
    aligned_normalized: list[dict],
) -> dict:
    """Build the JSON payload for one aligned bracket.

    :param int bracket_index: Zero-based bracket index within the group
    :param dict reference: Reference shot info (``filename``, ``relative_path``)
    :param list[dict] aligned_originals: Aligned original-exposure outputs
    :param list[dict] aligned_normalized: Aligned exposure-normalized outputs
    :return: Serialisable bracket payload
    :rtype: dict
    """
    return {
        "index": bracket_index,
        "reference": reference,
        "aligned_originals": aligned_originals,
        "aligned_normalized": aligned_normalized,
    }


def build_aligned_entry(
    source_filename: str,
    aligned_filename: str,
    relative_path: Path | str,
    step_offset: float,
) -> dict:
    """Describe one aligned output file in the JSON payload."""
    return {
        "source_filename": source_filename,
        "filename": aligned_filename,
        "relative_path": str(relative_path).replace("\\", "/"),
        "step_offset": float(step_offset),
    }


# ---------------------------------------------------------------------------
# Aggregate JSON read/write
# ---------------------------------------------------------------------------


def load_alignments_json(session_dir: Path) -> dict | None:
    """Load the aggregate ``alignments.json``, or ``None`` if absent."""
    path = Path(session_dir) / ALIGNMENTS_FILENAME
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    _validate(data)
    return data


def upsert_group_in_alignments_json(
    session_dir: Path,
    source_payload: dict,
    group_payload: dict,
) -> Path:
    """Insert or replace one group in the aggregate ``alignments.json``.

    :param Path session_dir: Session directory
    :param dict source_payload: Upstream JSON payload (raw_conversions or groups) — used for session metadata
    :param dict group_payload: Serialisable group payload
    :return: Path to the aggregate JSON
    :rtype: Path
    """
    aggregate_path = Path(session_dir) / ALIGNMENTS_FILENAME

    if aggregate_path.exists():
        with open(aggregate_path, encoding="utf-8") as handle:
            payload = json.load(handle)
    else:
        payload = {
            "version": ALIGNMENTS_VERSION,
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
    if data.get("version") != ALIGNMENTS_VERSION:
        raise ValueError(
            f"Unsupported alignments.json version: {data.get('version')} "
            f"(expected {ALIGNMENTS_VERSION})"
        )
    if "groups" not in data or not isinstance(data["groups"], list):
        raise ValueError("alignments.json: missing or invalid 'groups' field")
