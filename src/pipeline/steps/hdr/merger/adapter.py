"""HDR merge step adapter.

Bridges the PhotomatixCL worker to the orchestrator:
  1. Loads upstream JSONs (alignments, raw_conversions, groups)
  2. Determines source images per bracket (RAW-aligned vs JPEG-only)
  3. Calls PhotomatixCL for each (source_set, style) combination
  4. Writes results to ``hdr_merges.json``
"""

from __future__ import annotations

from pathlib import Path

from pipeline.steps.grouping.groups_io import load_latest_groups_json
from pipeline.steps.hdr.aligner.alignments_io import load_alignments_json
from pipeline.steps.hdr.merger.hdr_merges_io import (
    build_bracket_payload,
    build_merge_entry,
    upsert_group_in_hdr_merges_json,
)
from pipeline.steps.hdr.merger.photomatix import (
    MergeRequest,
    PhotomatixSettings,
    build_output_name,
    execute_merge,
)
from pipeline.steps.hdr.raw_to_jpg.raw_conversions_io import load_raw_conversions_json
from pipeline.utils.logger import get_logger

logger = get_logger(__name__)

HDR_MERGE_OUTPUT_SUBDIR = "merged_hdrs"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_group(
    group_id: str,
    session_dir: Path,
    config: dict,
    log=None,
) -> Path | None:
    """Run HDR merging for a single group.

    :param str group_id: ID of the group to process
    :param Path session_dir: Session workspace directory
    :param dict config: Full pipeline configuration
    :param log: Optional logger
    :return: Path to ``hdr_merges.json``, or ``None`` if nothing to merge
    """
    log = log or logger
    session_dir = Path(session_dir)

    merging_cfg = config.get("steps", {}).get("hdr", {}).get("merging", {})
    config_dir = _config_dir(config)

    settings = _build_photomatix_settings(merging_cfg, config_dir)
    styles = merging_cfg.get("styles", ["natural", "realistic"])
    style_configs = _resolve_style_configs(merging_cfg, styles)

    if "photographic" in styles and settings.xmp_path is None:
        raise ValueError("photographic style requires xmp_settings to be configured")

    alignments = load_alignments_json(session_dir)
    raw_conversions = load_raw_conversions_json(session_dir)
    groups_payload = load_latest_groups_json(session_dir)

    if groups_payload is None:
        raise ValueError("hdr_merge: no groups JSON found in session directory")

    input_dir = Path(groups_payload.get("input_dir", ""))
    output_dir = session_dir / HDR_MERGE_OUTPUT_SUBDIR / group_id

    alignments_group = _find_group_in_json(alignments, group_id)
    raw_conversions_group = _find_group_in_json(raw_conversions, group_id)
    groups_group = _find_group_in_json(groups_payload, group_id)

    if groups_group is None:
        log.warning("hdr_merge: group %s not found in groups JSON", group_id)
        return None

    if alignments_group is not None and raw_conversions_group is not None:
        log.info("hdr_merge: processing %s (RAW mode)", group_id)
        bracket_payloads = _process_raw_group(
            group_id=group_id,
            alignments_group=alignments_group,
            raw_conversions_group=raw_conversions_group,
            session_dir=session_dir,
            output_dir=output_dir,
            settings=settings,
            styles=styles,
            style_configs=style_configs,
            log=log,
        )
    elif raw_conversions_group is not None:
        log.info("hdr_merge: processing %s (RAW no-align mode)", group_id)
        bracket_payloads = _process_raw_noalign_group(
            group_id=group_id,
            raw_conversions_group=raw_conversions_group,
            session_dir=session_dir,
            output_dir=output_dir,
            settings=settings,
            styles=styles,
            style_configs=style_configs,
            log=log,
        )
    else:
        log.info("hdr_merge: processing %s (JPEG mode)", group_id)
        bracket_payloads = _process_jpeg_group(
            group_id=group_id,
            groups_group=groups_group,
            input_dir=input_dir,
            output_dir=output_dir,
            settings=settings,
            styles=styles,
            style_configs=style_configs,
            log=log,
        )

    if not bracket_payloads:
        log.info("hdr_merge: no merges produced for %s", group_id)
        return None

    group_payload = {
        "id": group_id,
        "type": groups_group.get("type"),
        "brackets": bracket_payloads,
    }

    aggregate_path = upsert_group_in_hdr_merges_json(
        session_dir=session_dir,
        source_payload=groups_payload,
        group_payload=group_payload,
    )

    log.info("hdr_merge: results written to %s", aggregate_path)
    return aggregate_path


# ---------------------------------------------------------------------------
# Source A — RAW groups (alignments + raw_conversions available)
# ---------------------------------------------------------------------------


def _process_raw_group(
    group_id: str,
    alignments_group: dict,
    raw_conversions_group: dict,
    session_dir: Path,
    output_dir: Path,
    settings: PhotomatixSettings,
    styles: list[str],
    style_configs: dict[str, dict],
    log,
) -> list[dict]:
    """Process a group that went through the RAW pipeline."""
    rc_brackets = raw_conversions_group.get("brackets", [])
    bracket_payloads = []

    for align_bracket in alignments_group.get("brackets", []):
        bracket_index = align_bracket.get("index", 0)
        reference = align_bracket["reference"]
        merge_entries: list[dict] = []

        # Set 1: aligned_originals + reference
        aligned_originals = align_bracket.get("aligned_originals", [])
        if aligned_originals:
            source_files = [session_dir / reference["relative_path"]]
            source_files += [
                session_dir / entry["relative_path"]
                for entry in aligned_originals
            ]
            merge_entries.extend(
                _merge_bracket_source_set(
                    source_files=source_files,
                    source_set="aligned_originals",
                    output_dir=output_dir,
                    session_dir=session_dir,
                    settings=settings,
                    styles=styles,
                    style_configs=style_configs,
                    log=log,
                )
            )

        # Set 2: noghost images + reference
        rc_bracket = (
            rc_brackets[bracket_index]
            if bracket_index < len(rc_brackets)
            else None
        )
        if rc_bracket is not None:
            ref_shot = next(
                (s for s in rc_bracket.get("shots", []) if s.get("reference_shot")),
                None,
            )
            noghost_entries = rc_bracket.get("noghost", [])
            ref_path = (
                session_dir / ref_shot["relative_path"]
                if ref_shot
                else session_dir / reference["relative_path"]
            )
            if noghost_entries:
                noghost_files = [ref_path]
                noghost_files += [
                    session_dir / entry["relative_path"]
                    for entry in noghost_entries
                ]
                merge_entries.extend(
                    _merge_bracket_source_set(
                        source_files=noghost_files,
                        source_set="noghost",
                        output_dir=output_dir,
                        session_dir=session_dir,
                        settings=settings,
                        styles=styles,
                        style_configs=style_configs,
                        log=log,
                    )
                )

        if merge_entries:
            bracket_payloads.append(
                build_bracket_payload(bracket_index, reference, merge_entries)
            )

    return bracket_payloads


# ---------------------------------------------------------------------------
# Source B — JPEG-only groups
# ---------------------------------------------------------------------------


def _process_jpeg_group(
    group_id: str,
    groups_group: dict,
    input_dir: Path,
    output_dir: Path,
    settings: PhotomatixSettings,
    styles: list[str],
    style_configs: dict[str, dict],
    log,
) -> list[dict]:
    """Process a group that has only original camera JPEGs."""
    bracket_payloads = []

    for bracket_index, bracket in enumerate(groups_group.get("brackets", [])):
        shots = bracket.get("shots", [])
        if len(shots) < 2:
            log.info(
                "hdr_merge: bracket %d has < 2 shots — skipping", bracket_index
            )
            continue

        source_files = [input_dir / shot["filename"] for shot in shots]

        ref_shot = next(
            (s for s in shots if s.get("reference_shot")), shots[0]
        )
        reference = {
            "filename": ref_shot["filename"],
            "relative_path": str(
                (input_dir / ref_shot["filename"]).relative_to(input_dir)
            ),
        }

        merge_entries = _merge_bracket_source_set(
            source_files=source_files,
            source_set="originals",
            output_dir=output_dir,
            session_dir=input_dir,
            settings=settings,
            styles=styles,
            style_configs=style_configs,
            log=log,
        )

        if merge_entries:
            bracket_payloads.append(
                build_bracket_payload(bracket_index, reference, merge_entries)
            )

    return bracket_payloads


# ---------------------------------------------------------------------------
# Source C — RAW groups without alignment (single-shot developed at multiple EVs)
# ---------------------------------------------------------------------------


def _process_raw_noalign_group(
    group_id: str,
    raw_conversions_group: dict,
    session_dir: Path,
    output_dir: Path,
    settings: PhotomatixSettings,
    styles: list[str],
    style_configs: dict[str, dict],
    log,
) -> list[dict]:
    """Process a group with raw conversions but no alignment data.

    This handles the case where a single RAW file was developed at multiple
    exposure recipes (e.g. -2, 0, +2). The resulting shots are already
    pixel-aligned (same source file), so no alignment step is needed.
    """
    bracket_payloads = []

    for bracket_index, rc_bracket in enumerate(
        raw_conversions_group.get("brackets", [])
    ):
        shots = rc_bracket.get("shots", [])
        if len(shots) < 2:
            log.info(
                "hdr_merge: bracket %d has < 2 shots — skipping", bracket_index
            )
            continue

        merge_entries: list[dict] = []

        ref_shot = next(
            (s for s in shots if s.get("reference_shot")), shots[0]
        )
        reference = {
            "filename": ref_shot["filename"],
            "relative_path": ref_shot["relative_path"],
        }

        # Noghost images + reference (no need for a separate "raw_exposures"
        # set — the shots are all from the same RAW, noghost is sufficient)
        noghost_entries = rc_bracket.get("noghost", [])
        if ref_shot and noghost_entries:
            noghost_files = [session_dir / ref_shot["relative_path"]]
            noghost_files += [
                session_dir / entry["relative_path"]
                for entry in noghost_entries
            ]
            merge_entries.extend(
                _merge_bracket_source_set(
                    source_files=noghost_files,
                    source_set="noghost",
                    output_dir=output_dir,
                    session_dir=session_dir,
                    settings=settings,
                    styles=styles,
                    style_configs=style_configs,
                    log=log,
                )
            )

        if merge_entries:
            bracket_payloads.append(
                build_bracket_payload(bracket_index, reference, merge_entries)
            )

    return bracket_payloads


# ---------------------------------------------------------------------------
# Per-source-set merge execution
# ---------------------------------------------------------------------------


def _merge_bracket_source_set(
    source_files: list[Path],
    source_set: str,
    output_dir: Path,
    session_dir: Path,
    settings: PhotomatixSettings,
    styles: list[str],
    style_configs: dict[str, dict],
    log,
) -> list[dict]:
    """Execute merges for one source set across all configured styles.

    Returns a list of merge entry dicts for the JSON payload.
    """
    merge_entries: list[dict] = []
    log.info(
        "── source_set=%s styles=%s files=%s",
        source_set, styles, [f.name for f in source_files],
    )

    for style in styles:
        output_name = build_output_name(source_files, style, source_set)

        request = MergeRequest(
            source_files=source_files,
            output_dir=output_dir,
            output_name=output_name,
            style=style,
            style_params=style_configs.get(style, {}),
            source_set=source_set,
        )

        output_path = execute_merge(settings, request, log=log)

        try:
            relative = output_path.relative_to(session_dir)
        except ValueError:
            relative = output_path

        source_relatives = []
        for sf in source_files:
            try:
                source_relatives.append(
                    str(sf.relative_to(session_dir)).replace("\\", "/")
                )
            except ValueError:
                source_relatives.append(sf.name)

        merge_entries.append(
            build_merge_entry(
                style=style,
                source_set=source_set,
                source_files=source_relatives,
                output_filename=output_path.name,
                relative_path=relative,
            )
        )

    return merge_entries


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------


def _build_photomatix_settings(merging_cfg: dict, config_dir: Path) -> PhotomatixSettings:
    """Build PhotomatixSettings from the merging config section."""
    exe_path = merging_cfg.get("photomatix_exe", "PhotomatixCL.exe")
    xmp_value = merging_cfg.get("xmp_settings", "") or merging_cfg.get("photographic", {}).get("xmp_settings", "")
    xmp_path = _resolve_path(xmp_value, config_dir) if xmp_value else None

    nr_value = merging_cfg.get("noise_reduction", 1)
    noise_reduction = None if nr_value is False else int(nr_value)

    ev_value = merging_cfg.get("ev_spacing", 2.0)
    ev_spacing = None if ev_value is False else float(ev_value)

    return PhotomatixSettings(
        exe=_resolve_path(exe_path, config_dir),
        reduce_ca=bool(merging_cfg.get("reduce_ca", True)),
        noise_reduction=noise_reduction,
        use_scratch_disk=bool(merging_cfg.get("use_scratch_disk", True)),
        ev_spacing=ev_spacing,
        timeout_sec=int(merging_cfg.get("timeout_sec", 600)),
        xmp_path=xmp_path,
    )


def _resolve_style_configs(merging_cfg: dict, styles: list[str]) -> dict[str, dict]:
    """Extract per-style parameter dicts from config."""
    return {style: merging_cfg.get(style, {}) for style in styles}


def _config_dir(config: dict) -> Path:
    """Extract config directory from the loaded config dict."""
    config_dir = config.get("__config_dir__")
    return Path(config_dir).resolve() if config_dir else Path.cwd()


def _resolve_path(value: str | Path, config_dir: Path) -> Path:
    """Resolve a path that may be relative to config_dir."""
    path = Path(value)
    return path.resolve() if path.is_absolute() else (config_dir / path).resolve()


def _find_group_in_json(json_data: dict | None, group_id: str) -> dict | None:
    """Find a group by id in a loaded JSON payload."""
    if json_data is None:
        return None
    for group in json_data.get("groups", []):
        if group["id"] == group_id:
            return group
    return None
