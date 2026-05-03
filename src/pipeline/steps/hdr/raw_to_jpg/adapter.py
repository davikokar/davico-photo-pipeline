"""RAW to JPG step adapter.

Bridges the pure converter worker to the orchestrator and SessionState:
  1. Reads raw_dir from the session (skips the step if not configured)
  2. Loads the latest groups JSON
  3. Resolves config (dpp4cli, recipes, raw extensions)
  4. For each group: plans, executes, writes payload to raw_conversions.json
  5. Marks the raw_to_jpg step as done in SessionState
"""

from __future__ import annotations

from pathlib import Path

from pipeline.state import SessionState
from pipeline.steps.grouping.groups_io import load_latest_groups_json
from pipeline.steps.hdr.raw_to_jpg.converter import (
    DEFAULT_RAW_EXTENSIONS,
    Dpp4Settings,
    build_raw_index,
    execute_conversion_plan,
    normalize_recipe_key,
    plan_group_conversions,
)
from pipeline.steps.hdr.raw_to_jpg.raw_conversions_io import (
    build_group_payload,
    upsert_group_in_raw_conversions_json,
)
from pipeline.utils.logger import get_logger

logger = get_logger(__name__)

RAW_TO_JPG_OUTPUT_SUBDIR = "raw_to_jpg"


def run(state: SessionState, config: dict, log=None) -> Path | None:
    """Run the RAW to JPG step for a session.

    :param SessionState state: Active session state (raw_dir is read from here)
    :param dict config: Full pipeline configuration
    :param log: Optional logger
    :return: Path to raw_conversions.json, or None if the step was skipped
    :rtype: Path | None
    """
    log = log or logger
    session_dir = Path(state.session_dir)
    raw_dir = state.session.get("raw_dir")

    # 1. Skip conditions
    if not raw_dir:
        log.info("raw_to_jpg skipped: raw_dir not set in session")
        return None
    raw_dir = Path(raw_dir)
    if not raw_dir.exists():
        log.info("raw_to_jpg skipped: raw_dir not found: %s", raw_dir)
        return None

    # 2. Inputs
    groups_payload = load_latest_groups_json(session_dir)
    if groups_payload is None:
        raise ValueError("raw_to_jpg: no groups JSON found in session directory")

    # 3. Resolve config
    raw_to_jpg_cfg = config.get("steps", {}).get("hdr", {}).get("raw_to_jpg", {})
    config_dir = _config_dir(config)
    settings = _build_dpp4_settings(raw_to_jpg_cfg, config_dir)
    recipe_paths = _parse_recipe_paths(raw_to_jpg_cfg, config_dir)
    raw_extensions = _resolve_raw_extensions(raw_to_jpg_cfg)
    raw_index = build_raw_index(raw_dir, raw_extensions)

    output_dir = session_dir / RAW_TO_JPG_OUTPUT_SUBDIR
    aggregate_path = None

    # 4. Process every group
    for group in groups_payload["groups"]:
        group_id = group["id"]
        log.info("raw_to_jpg: processing %s", group_id)

        requests = plan_group_conversions(group, raw_index, recipe_paths, output_dir)
        if not requests:
            log.info("raw_to_jpg: no RAW files for %s — skipping", group_id)
            state.step_skip(group_id, "raw_to_jpg", reason="no RAW files")
            continue

        try:
            execute_conversion_plan(requests, settings, log)
        except Exception as exc:
            state.step_failed(group_id, "raw_to_jpg", error=str(exc))
            raise

        # Group requests back by bracket for the JSON payload
        requests_per_bracket = _split_requests_by_bracket(group, requests)
        group_payload = build_group_payload(group, requests_per_bracket, session_dir)

        aggregate_path = upsert_group_in_raw_conversions_json(
            session_dir=session_dir,
            source_groups_payload=groups_payload,
            raw_dir=raw_dir,
            group_payload=group_payload,
        )
        state.step_done(group_id, "raw_to_jpg", output=str(aggregate_path))

    if aggregate_path:
        log.info("raw_to_jpg: aggregate JSON written to %s", aggregate_path)
    return aggregate_path


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _config_dir(config: dict) -> Path:
    config_dir = config.get("__config_dir__")
    return Path(config_dir).resolve() if config_dir else Path.cwd()


def _resolve_path(value: str | Path, config_dir: Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (config_dir / path).resolve()


def _build_dpp4_settings(raw_to_jpg_cfg: dict, config_dir: Path) -> Dpp4Settings:
    exe_value = raw_to_jpg_cfg.get("dpp4cli_exe", "")
    if not exe_value:
        raise ValueError("steps.hdr.raw_to_jpg.dpp4cli_exe is required")
    exe_path = _resolve_path(exe_value, config_dir)
    if not exe_path.exists():
        raise ValueError(f"dpp4cli executable not found: {exe_path}")

    return Dpp4Settings(
        exe=exe_path,
        jpeg_quality=int(raw_to_jpg_cfg.get("jpeg_quality", 100)),
        output_format=str(raw_to_jpg_cfg.get("format", "jpg")),
        dpp4dir=str(raw_to_jpg_cfg.get("dpp4dir", "")),
        verbose=bool(raw_to_jpg_cfg.get("verbose", False)),
        timeout_sec_per_img=int(raw_to_jpg_cfg.get("timeout_sec_per_img", 180)),
    )


def _parse_recipe_paths(raw_to_jpg_cfg: dict, config_dir: Path) -> dict[str, Path]:
    recipes = raw_to_jpg_cfg.get("recipes", {})
    if not recipes:
        raise ValueError("steps.hdr.raw_to_jpg.recipes is required")
    return {
        normalize_recipe_key(key): _resolve_path(value, config_dir)
        for key, value in recipes.items()
    }


def _resolve_raw_extensions(raw_to_jpg_cfg: dict) -> tuple[str, ...]:
    configured = raw_to_jpg_cfg.get("raw_extensions")
    if not configured:
        return DEFAULT_RAW_EXTENSIONS
    return tuple(str(value).lower() for value in configured)


# ---------------------------------------------------------------------------
# Bracket-level grouping of completed requests
# ---------------------------------------------------------------------------


def _split_requests_by_bracket(
    group: dict,
    requests: list,
) -> list[tuple[list[dict], list]]:
    """Pair each bracket's source shots with the requests that were planned for it."""
    by_bracket: dict[int, list] = {}
    for request in requests:
        by_bracket.setdefault(request.bracket_index, []).append(request)

    result = []
    for bracket_index, bracket in enumerate(group.get("brackets", [])):
        shots = [dict(shot) for shot in bracket.get("shots", [])]
        result.append((shots, by_bracket.get(bracket_index, [])))
    return result
