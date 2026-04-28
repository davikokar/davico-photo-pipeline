"""
groups_io — versioned JSON persistence for grouping results.

After the grouper runs, it saves the detected groups to a numbered JSON file:
    groups_001.json   ← initial auto-detection
    groups_002.json   ← after manual review/edit via HTML tool
    groups_003.json   ← another revision, etc.

The HDR merger always reads the file with the highest number, so manual
edits are automatically picked up just by placing the new file in the
session directory.

JSON format
-----------
{
  "version": 1,
  "session_id": "20250306_173045",
  "input_dir": "/path/to/photos",
  "generated_at": "2025-03-06T17:30:45",
  "groups": [
    {
      "id": "group_001",
      "type": "hdr",
      "brackets": [
        {
          "shots": [
            {"filename": "IMG_001.jpg", "ev": -2.0, "shutter": 0.001},
            {"filename": "IMG_002.jpg", "ev":  0.0, "shutter": 0.004},
            {"filename": "IMG_003.jpg", "ev": +2.0, "shutter": 0.016}
          ]
        }
      ]
    }
  ]
}
"""

import json
import re
from datetime import datetime
from pathlib import Path

from pipeline.utils.logger import get_logger

logger = get_logger(__name__)

GROUPS_FILE_PATTERN = re.compile(r"^groups_(\d+)\.json$")
GROUPS_FORMAT_VERSION = 1


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def save_groups_json(
    groups_data: list[dict],
    session_dir: Path,
    session_id: str,
    input_dir: str,
) -> Path:
    """
    Save groups to the next versioned JSON file in session_dir.

    Args:
        groups_data:  List of group dicts (see format above).
        session_dir:  Session workspace directory.
        session_id:   Session identifier string.
        input_dir:    Original input photos directory.

    Returns:
        Path to the saved JSON file.
    """
    session_dir = Path(session_dir)
    session_dir.mkdir(parents=True, exist_ok=True)

    next_num = _next_version_number(session_dir)
    filename = f"groups_{next_num:03d}.json"
    path = session_dir / filename

    payload = {
        "version":      GROUPS_FORMAT_VERSION,
        "session_id":   session_id,
        "input_dir":    str(input_dir),
        "generated_at": datetime.now().isoformat(),
        "groups":       groups_data,
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    logger.info(f"Groups saved → {path.name}  ({len(groups_data)} groups)")
    return path


def _next_version_number(session_dir: Path) -> int:
    """Return the next available version number (1-based)."""
    existing = _list_group_files(session_dir)
    if not existing:
        return 1
    return max(n for n, _ in existing) + 1


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def load_latest_groups_json(session_dir: Path) -> dict | None:
    """
    Load the most recent groups JSON file from session_dir.

    Returns the parsed dict, or None if no groups file exists.
    """
    session_dir = Path(session_dir)
    files = _list_group_files(session_dir)
    if not files:
        logger.warning(f"No groups_NNN.json found in {session_dir}")
        return None

    _, latest_path = max(files, key=lambda x: x[0])
    logger.info(f"Loading groups from {latest_path.name}")

    with open(latest_path, encoding="utf-8") as f:
        data = json.load(f)

    _validate(data)
    return data


def _list_group_files(session_dir: Path) -> list[tuple[int, Path]]:
    """Return list of (version_number, path) for all groups_NNN.json files."""
    result = []
    for p in session_dir.iterdir():
        m = GROUPS_FILE_PATTERN.match(p.name)
        if m:
            result.append((int(m.group(1)), p))
    return sorted(result)


def _validate(data: dict):
    """Basic schema validation — raises ValueError on bad format."""
    if data.get("version") != GROUPS_FORMAT_VERSION:
        raise ValueError(
            f"Unsupported groups JSON version: {data.get('version')} "
            f"(expected {GROUPS_FORMAT_VERSION})"
        )
    if "groups" not in data or not isinstance(data["groups"], list):
        raise ValueError("groups_json: missing or invalid 'groups' field")


# ---------------------------------------------------------------------------
# Convert between PanoramaGroup objects and JSON dicts
# ---------------------------------------------------------------------------

def panorama_groups_to_json(pano_groups: list, input_dir: Path) -> list[dict]:
    """
    Convert a list of PanoramaGroup objects (from grouper) to JSON-serialisable dicts.

    Args:
        pano_groups: Output of run_grouper().
        input_dir:   Base directory (used to make paths relative).

    Returns:
        List of group dicts ready for save_groups_json().
    """
    groups_data = []
    for i, pg in enumerate(pano_groups):
        group_id = f"group_{i+1:03d}"
        brackets = []
        for bracket in pg.brackets:
            shots = []
            offsets = bracket.step_offsets
            for shot, offset_info in zip(bracket.shots, offsets):
                shot_dict = {
                    "filename":       shot.path.name,
                    "ev":             round(shot.ev, 2) if shot.ev is not None else None,
                    "shutter":        shot.shutter,
                    "step_offset":    offset_info["step_offset"],
                    "reference_shot": offset_info["reference_shot"],
                }
                shots.append(shot_dict)
            brackets.append({"shots": shots})

        groups_data.append({
            "id":       group_id,
            "type":     pg.group_type.value,
            "brackets": brackets,
        })

    return groups_data


def json_to_state_groups(groups_data: list[dict]) -> list[dict]:
    """
    Convert JSON group dicts to the flat format used by SessionState.

    SessionState stores groups as {id, type, files, steps, notes}.
    The bracket structure is preserved in a 'brackets' field for HDR merge.

    Args:
        groups_data: List of group dicts from load_latest_groups_json().

    Returns:
        List of state-compatible group dicts.
    """
    state_groups = []
    for g in groups_data:
        all_files = [
            shot["filename"]
            for bracket in g["brackets"]
            for shot in bracket["shots"]
        ]
        state_groups.append({
            "id":       g["id"],
            "type":     g["type"],
            "files":    all_files,
            "brackets": g["brackets"],   # preserved for HDR merge
        })
    return state_groups
