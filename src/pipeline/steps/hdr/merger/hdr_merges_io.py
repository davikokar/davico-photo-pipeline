"""Versioned persistence for HDR merge results.

Reads/writes the aggregate ``hdr_merges.json`` file in the session directory.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pipeline.utils.logger import get_logger

logger = get_logger(__name__)

HDR_MERGES_VERSION = 1
HDR_MERGES_FILENAME = "hdr_merges.json"


# ---------------------------------------------------------------------------
# Per-merge / per-bracket payload builders
# ---------------------------------------------------------------------------


def build_merge_entry(
    style: str,
    source_set: str,
    source_files: list[str],
    output_filename: str,
    relative_path: Path | str,
) -> dict:
    """Describe one PhotomatixCL merge output in the JSON payload.

    :param str style: Merge style used (``natural``, ``realistic``, ``photographic``)
    :param str source_set: Which input set was merged
        (``aligned_originals``, ``noghost``, ``originals``)
    :param list[str] source_files: Relative paths of the input images
    :param str output_filename: Name of the produced file
    :param relative_path: Output path relative to session_dir
    """
    return {
        "style": style,
        "source_set": source_set,
        "source_files": list(source_files),
        "output_filename": output_filename,
        "relative_path": str(relative_path).replace("\\", "/"),
    }


def build_bracket_payload(
    bracket_index: int,
    reference: dict,
    merges: list[dict],
) -> dict:
    """Build the JSON payload for one bracket's merge outputs.

    :param int bracket_index: Zero-based bracket index within the group
    :param dict reference: Reference shot info (``filename``, ``relative_path``)
    :param list[dict] merges: One entry per merge output
    """
    return {
        "index": bracket_index,
        "reference": reference,
        "merges": merges,
    }


# ---------------------------------------------------------------------------
# Aggregate JSON read/write
# ---------------------------------------------------------------------------


def load_hdr_merges_json(session_dir: Path) -> dict | None:
    """Load the aggregate ``hdr_merges.json``, or ``None`` if absent."""
    path = Path(session_dir) / HDR_MERGES_FILENAME
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    _validate(data)
    return data


def upsert_group_in_hdr_merges_json(
    session_dir: Path,
    source_payload: dict,
    group_payload: dict,
) -> Path:
    """Insert or replace one group in the aggregate ``hdr_merges.json``.

    :param Path session_dir: Session directory
    :param dict source_payload: Upstream JSON payload — used for session metadata
    :param dict group_payload: Serialisable group payload
    :return: Path to the aggregate JSON
    """
    aggregate_path = Path(session_dir) / HDR_MERGES_FILENAME

    if aggregate_path.exists():
        with open(aggregate_path, encoding="utf-8") as handle:
            payload = json.load(handle)
    else:
        payload = {
            "version": HDR_MERGES_VERSION,
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
    if data.get("version") != HDR_MERGES_VERSION:
        raise ValueError(
            f"Unsupported hdr_merges.json version: {data.get('version')} "
            f"(expected {HDR_MERGES_VERSION})"
        )
    if "groups" not in data or not isinstance(data["groups"], list):
        raise ValueError("hdr_merges.json: missing or invalid 'groups' field")
