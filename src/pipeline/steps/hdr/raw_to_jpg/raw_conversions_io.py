"""Versioned persistence for RAW conversion results.

Mirrors the role of ``groups_io`` for the grouping step. Reads/writes
the aggregate ``raw_conversions.json`` file in the session directory.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pipeline.steps.hdr.raw_to_jpg.converter import ConversionRequest
from pipeline.utils.logger import get_logger

logger = get_logger(__name__)

RAW_CONVERSIONS_VERSION = 1
RAW_CONVERSIONS_FILENAME = "raw_conversions.json"


# ---------------------------------------------------------------------------
# Serialisation of one group
# ---------------------------------------------------------------------------


def build_group_payload(
    group: dict,
    requests_per_bracket: list[tuple[list[dict], list[ConversionRequest]]],
    session_dir: Path,
) -> dict:
    """Build the JSON payload for one converted group.

    :param dict group: Source group entry from the groups JSON
    :param list requests_per_bracket: Pairs of (normalized shots, executed requests) per bracket
    :param Path session_dir: Session directory used to compute relative paths
    :return: Serialisable group payload
    :rtype: dict
    """
    return {
        "id": group["id"],
        "type": group["type"],
        "brackets": [
            _build_bracket_payload(shots, requests, session_dir)
            for shots, requests in requests_per_bracket
        ],
    }


def _build_bracket_payload(
    normalized_shots: list[dict],
    requests: list[ConversionRequest],
    session_dir: Path,
) -> dict:
    collections: dict[str, list[dict]] = {"shots": [], "noghost": [], "normalized": []}
    for request in requests:
        relative_path = request.output_path.relative_to(session_dir)
        collections[request.collection].append(
            {
                "filename": request.output_filename,
                "relative_path": str(relative_path).replace("\\", "/"),
                "raw_filename": request.raw_path.name,
                "recipe": request.recipe_key,
                "reference_shot": request.reference_shot,
                "step_offset": request.step_offset,
            }
        )

    return {
        "source": [
            {
                "filename": shot["filename"],
                "ev": shot.get("ev"),
                "shutter": shot.get("shutter"),
                "step_offset": shot.get("step_offset", 0.0),
                "reference_shot": shot.get("reference_shot", False),
            }
            for shot in normalized_shots
        ],
        "shots": collections["shots"],
        "noghost": collections["noghost"],
        "normalized": collections["normalized"],
    }


# ---------------------------------------------------------------------------
# Aggregate JSON read/write
# ---------------------------------------------------------------------------


def load_raw_conversions_json(session_dir: Path) -> dict | None:
    """Load the aggregate raw_conversions.json, or None if absent."""
    path = Path(session_dir) / RAW_CONVERSIONS_FILENAME
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    _validate(data)
    return data


def upsert_group_in_raw_conversions_json(
    session_dir: Path,
    source_groups_payload: dict,
    raw_dir: Path,
    group_payload: dict,
) -> Path:
    """Insert or replace one group in the aggregate raw_conversions.json.

    :param Path session_dir: Session directory
    :param dict source_groups_payload: Latest groups JSON payload (for session metadata)
    :param Path raw_dir: RAW directory used for lookup
    :param dict group_payload: Serialisable group payload (from build_group_payload)
    :return: Path to the aggregate JSON
    :rtype: Path
    """
    aggregate_path = Path(session_dir) / RAW_CONVERSIONS_FILENAME

    if aggregate_path.exists():
        with open(aggregate_path, encoding="utf-8") as handle:
            payload = json.load(handle)
    else:
        payload = {
            "version": RAW_CONVERSIONS_VERSION,
            "session_id": source_groups_payload.get("session_id"),
            "input_dir": source_groups_payload.get("input_dir"),
            "raw_dir": str(raw_dir),
            "generated_at": datetime.now().isoformat(),
            "groups": [],
        }

    payload["generated_at"] = datetime.now().isoformat()
    payload["raw_dir"] = str(raw_dir)

    groups_by_id = {group["id"]: group for group in payload.get("groups", [])}
    groups_by_id[group_payload["id"]] = group_payload
    payload["groups"] = sorted(groups_by_id.values(), key=lambda item: item["id"])

    with open(aggregate_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)

    return aggregate_path


def _validate(data: dict) -> None:
    if data.get("version") != RAW_CONVERSIONS_VERSION:
        raise ValueError(
            f"Unsupported raw_conversions.json version: {data.get('version')} "
            f"(expected {RAW_CONVERSIONS_VERSION})"
        )
    if "groups" not in data or not isinstance(data["groups"], list):
        raise ValueError("raw_conversions.json: missing or invalid 'groups' field")
