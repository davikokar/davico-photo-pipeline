"""Versioned persistence for ghost application results.

Reads/writes the aggregate ``ghost_applications.json`` file in the session directory.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pipeline.utils.logger import get_logger

logger = get_logger(__name__)

GHOST_APPLICATIONS_VERSION = 1
GHOST_APPLICATIONS_FILENAME = "ghost_applications.json"


# ---------------------------------------------------------------------------
# Per-application / per-bracket payload builders
# ---------------------------------------------------------------------------


def build_application_entry(
    style: str,
    source_files: list[str],
    output_filename: str,
    relative_path: Path | str,
) -> dict:
    """Describe one ghost-applied output in the JSON payload.

    :param str style: Merge style (``natural``, ``realistic``, ``photographic``)
    :param list[str] source_files: Relative paths of the two merge inputs + mask
    :param str output_filename: Name of the produced file
    :param relative_path: Output path relative to session_dir
    """
    return {
        "style": style,
        "source_files": list(source_files),
        "output_filename": output_filename,
        "relative_path": str(relative_path).replace("\\", "/"),
    }


def build_bracket_payload(
    bracket_index: int,
    reference: dict,
    applications: list[dict],
) -> dict:
    """Build the JSON payload for one bracket's ghost-applied outputs.

    :param int bracket_index: Zero-based bracket index within the group
    :param dict reference: Reference shot info (``filename``, ``relative_path``)
    :param list[dict] applications: One entry per ghost-applied output
    """
    return {
        "index": bracket_index,
        "reference": reference,
        "applications": applications,
    }


# ---------------------------------------------------------------------------
# Aggregate JSON read/write
# ---------------------------------------------------------------------------


def load_ghost_applications_json(session_dir: Path) -> dict | None:
    """Load the aggregate ``ghost_applications.json``, or ``None`` if absent."""
    path = Path(session_dir) / GHOST_APPLICATIONS_FILENAME
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    _validate(data)
    return data


def upsert_group_in_ghost_applications_json(
    session_dir: Path,
    source_payload: dict,
    group_payload: dict,
) -> Path:
    """Insert or replace one group in the aggregate ``ghost_applications.json``.

    :param Path session_dir: Session directory
    :param dict source_payload: Upstream JSON payload — used for session metadata
    :param dict group_payload: Serialisable group payload
    :return: Path to the aggregate JSON
    """
    aggregate_path = Path(session_dir) / GHOST_APPLICATIONS_FILENAME

    if aggregate_path.exists():
        with open(aggregate_path, encoding="utf-8") as handle:
            payload = json.load(handle)
    else:
        payload = {
            "version": GHOST_APPLICATIONS_VERSION,
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
    if data.get("version") != GHOST_APPLICATIONS_VERSION:
        raise ValueError(
            f"Unsupported ghost_applications.json version: {data.get('version')} "
            f"(expected {GHOST_APPLICATIONS_VERSION})"
        )
    if "groups" not in data or not isinstance(data["groups"], list):
        raise ValueError("ghost_applications.json: missing or invalid 'groups' field")
