"""RAW to JPG conversion — pure worker.

Plans and executes dpp4cli conversions for one group at a time.
Knows nothing about session JSON files or the SessionState; takes a
group dict, a RAW index, recipe paths, and execution settings, and
returns the produced ConversionRequest objects.
"""

from __future__ import annotations

import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from pipeline.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_RAW_EXTENSIONS = (".cr2", ".cr3", ".crw", ".crf")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConversionRequest:
    """One RAW to JPEG conversion request.

    :param Path raw_path: RAW input file
    :param str recipe_key: Logical recipe identifier, e.g. ``0`` or ``+2``
    :param Path recipe_path: Recipe file passed to dpp4cli
    :param str suffix: Output suffix passed to dpp4cli
    :param Path output_dir: Directory where dpp4cli writes the JPEG
    :param str collection: Output collection name in JSON: ``shots``, ``noghost``, or ``normalized``
    :param int bracket_index: Zero-based bracket index within the group
    :param bool reference_shot: Whether the source shot is the bracket reference shot
    :param float step_offset: EV offset relative to the reference shot
    """

    raw_path: Path
    recipe_key: str
    recipe_path: Path
    suffix: str
    output_dir: Path
    collection: str
    bracket_index: int
    reference_shot: bool
    step_offset: float

    @property
    def output_filename(self) -> str:
        return f"{self.raw_path.stem}{self.suffix}.jpg"

    @property
    def output_path(self) -> Path:
        return self.output_dir / self.output_filename


@dataclass(frozen=True)
class Dpp4Settings:
    """Resolved dpp4cli execution settings.

    :param Path exe: Path to dpp4cli executable
    :param int jpeg_quality: JPEG quality (1-100)
    :param str output_format: Output image format (e.g. ``jpg``)
    :param str dpp4dir: Optional Canon DPP4 install dir override
    :param bool verbose: Pass --verbose to dpp4cli
    :param int timeout_sec_per_img: Per-image subprocess timeout (multiplied by batch size)
    """

    exe: Path
    jpeg_quality: int = 100
    output_format: str = "jpg"
    dpp4dir: str = ""
    verbose: bool = False
    timeout_sec_per_img: int = 180


# ---------------------------------------------------------------------------
# Indexing and recipe resolution
# ---------------------------------------------------------------------------


def build_raw_index(raw_dir: Path, raw_extensions: Iterable[str]) -> dict[str, Path]:
    """Index RAW files by stem.

    :param Path raw_dir: Directory containing RAW files
    :param Iterable[str] raw_extensions: Allowed RAW extensions
    :return: Mapping from stem to RAW file path
    :rtype: dict[str, Path]
    :raises ValueError: If multiple RAW files share the same stem
    """
    raw_dir = Path(raw_dir)
    extensions = {ext.lower() for ext in raw_extensions}
    index: dict[str, Path] = {}

    for candidate in raw_dir.rglob("*"):
        if not candidate.is_file() or candidate.suffix.lower() not in extensions:
            continue
        stem_key = candidate.stem.lower()
        if stem_key in index and index[stem_key] != candidate:
            raise ValueError(
                f"duplicate RAW stem found for {candidate.stem}: "
                f"{index[stem_key]} and {candidate}"
            )
        index[stem_key] = candidate

    return index


def normalize_recipe_key(value: str | int | float) -> str:
    """Normalise recipe keys for config lookup and filenames.

    :return: Canonical string representation, e.g. ``+2`` or ``-1.33``
    :rtype: str
    """
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("+"):
            stripped = stripped[1:]
        numeric = float(stripped)
    else:
        numeric = float(value)

    if numeric == 0:
        return "0"

    rendered = f"{numeric:.2f}".rstrip("0").rstrip(".")
    return rendered if rendered.startswith("-") else f"+{rendered}"


def build_output_suffix(recipe_key: str) -> str:
    """Convert a recipe key into the dpp4cli suffix.

    :return: Output suffix, empty for the ``0`` recipe
    :rtype: str
    """
    return (
        ""
        if normalize_recipe_key(recipe_key) == "0"
        else f"_{normalize_recipe_key(recipe_key)}"
    )


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def plan_group_conversions(
    group: dict,
    raw_index: dict[str, Path],
    recipe_paths: dict[str, Path],
    output_dir: Path,
) -> list[ConversionRequest]:
    """Build all conversion requests for one group.

    :param dict group: Group entry from groups JSON
    :param dict raw_index: Mapping of JPEG stem to RAW file path
    :param dict recipe_paths: Available recipes (canonical key → path)
    :param Path output_dir: Directory where converted JPEGs will be written
    :return: Conversion requests for the whole group
    :rtype: list[ConversionRequest]
    """
    output_dir = Path(output_dir)
    requests: list[ConversionRequest] = []

    for bracket_index, bracket in enumerate(group.get("brackets", [])):
        shots = [dict(shot) for shot in bracket.get("shots", [])]
        requests.extend(
            _build_bracket_requests(
                normalized_shots=shots,
                bracket_dir=output_dir,
                raw_index=raw_index,
                recipe_paths=recipe_paths,
                bracket_index=bracket_index,
            )
        )

    return requests


def _build_bracket_requests(
    normalized_shots: list[dict],
    bracket_dir: Path,
    raw_index: dict[str, Path],
    recipe_paths: dict[str, Path],
    bracket_index: int,
) -> list[ConversionRequest]:
    """Create conversion requests for one bracket."""
    if not normalized_shots:
        return []

    resolved = [_attach_raw_path(shot, raw_index) for shot in normalized_shots]
    is_hdr_bracket = len(resolved) > 1 and any(
        abs(shot["step_offset"]) > 0 for shot in resolved
    )

    if not is_hdr_bracket:
        return _build_single_shot_requests(
            resolved[0], bracket_dir, recipe_paths, bracket_index
        )

    required_recipe_keys = {"0"}
    for shot in resolved:
        required_recipe_keys.add(normalize_recipe_key(shot["step_offset"]))
        required_recipe_keys.add(normalize_recipe_key(-shot["step_offset"]))

    missing_keys = sorted(
        key for key in required_recipe_keys if key not in recipe_paths
    )
    if missing_keys:
        raise ValueError(f"missing recipes for HDR bracket: {', '.join(missing_keys)}")

    requests: list[ConversionRequest] = []
    reference_shot = next(shot for shot in resolved if shot["reference_shot"])
    unique_offsets = sorted(
        {
            normalize_recipe_key(shot["step_offset"])
            for shot in resolved
            if not shot["reference_shot"]
        },
        key=lambda value: float(value),
    )

    # All shots with recipe 0 → "shots" collection
    for shot in resolved:
        requests.append(
            _make_request(shot, "0", recipe_paths, bracket_dir, "shots", bracket_index)
        )

    # Reference shot with each non-zero recipe → "noghost" collection
    for offset_key in unique_offsets:
        requests.append(
            _make_request(
                reference_shot,
                offset_key,
                recipe_paths,
                bracket_dir,
                "noghost",
                bracket_index,
            )
        )

    # Each non-reference shot with the opposite recipe → "normalized"
    for shot in resolved:
        if shot["reference_shot"]:
            continue
        opposite_key = normalize_recipe_key(-shot["step_offset"])
        requests.append(
            _make_request(
                shot,
                opposite_key,
                recipe_paths,
                bracket_dir,
                "normalized",
                bracket_index,
            )
        )

    return _deduplicate_requests(requests)


def _build_single_shot_requests(
    shot: dict,
    bracket_dir: Path,
    recipe_paths: dict[str, Path],
    bracket_index: int,
) -> list[ConversionRequest]:
    """Create pseudo-bracket conversions for a non-HDR shot."""
    required_keys = ["-2", "0", "+2"]
    missing_keys = [key for key in required_keys if key not in recipe_paths]
    if missing_keys:
        raise ValueError(
            f"missing recipes for single-shot conversion: {', '.join(missing_keys)}"
        )

    requests = [
        _make_request(shot, key, recipe_paths, bracket_dir, "shots", bracket_index)
        for key in required_keys
    ]
    requests.extend(
        _make_request(shot, key, recipe_paths, bracket_dir, "noghost", bracket_index)
        for key in required_keys
    )
    return _deduplicate_requests(requests)


def _make_request(
    shot: dict,
    recipe_key: str,
    recipe_paths: dict[str, Path],
    output_dir: Path,
    collection: str,
    bracket_index: int,
) -> ConversionRequest:
    normalized_key = normalize_recipe_key(recipe_key)
    return ConversionRequest(
        raw_path=shot["raw_path"],
        recipe_key=normalized_key,
        recipe_path=recipe_paths[normalized_key],
        suffix=build_output_suffix(normalized_key),
        output_dir=Path(output_dir),
        collection=collection,
        bracket_index=bracket_index,
        reference_shot=bool(shot["reference_shot"]),
        step_offset=float(shot["step_offset"]),
    )


def _deduplicate_requests(requests: list[ConversionRequest]) -> list[ConversionRequest]:
    """Drop duplicate requests that produce the same file for the same collection."""
    seen: set[tuple[str, str, str, str]] = set()
    unique: list[ConversionRequest] = []
    for request in requests:
        key = (
            str(request.raw_path),
            request.recipe_key,
            str(request.output_dir),
            request.collection,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(request)
    return unique


def _attach_raw_path(shot: dict, raw_index: dict[str, Path]) -> dict:
    stem_key = Path(shot["filename"]).stem.lower()
    raw_path = raw_index.get(stem_key)
    if raw_path is None:
        raise FileNotFoundError(f"RAW file not found for {shot['filename']}")

    enriched = dict(shot)
    enriched["raw_path"] = raw_path
    enriched["step_offset"] = float(enriched.get("step_offset", 0.0) or 0.0)
    enriched["reference_shot"] = bool(enriched.get("reference_shot", False))
    return enriched


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def execute_conversion_plan(
    requests: list[ConversionRequest],
    settings: Dpp4Settings,
    log=None,
) -> None:
    """Execute conversion requests grouped by recipe and output parameters.

    :param list[ConversionRequest] requests: Conversion requests
    :param Dpp4Settings settings: Resolved dpp4cli settings
    :param log: Optional logger
    """
    log = log or logger
    ensure_output_dirs(requests)

    grouped: dict[tuple[Path, str, Path, str], list[ConversionRequest]] = defaultdict(
        list
    )
    for request in requests:
        grouped[
            (
                request.recipe_path,
                request.suffix,
                request.output_dir,
                request.recipe_key,
            )
        ].append(request)

    for (recipe_path, suffix, output_dir, recipe_key), batch in grouped.items():
        command = [
            str(settings.exe),
            "--recipe",
            str(recipe_path),
            "--outputdir",
            str(output_dir),
            "--quality",
            str(settings.jpeg_quality),
            "--format",
            settings.output_format,
        ]
        if suffix:
            command.extend(["--suffix", suffix])
        if settings.dpp4dir:
            command.extend(["--dpp4dir", settings.dpp4dir])
        if settings.verbose:
            command.append("--verbose")
        command.extend(str(request.raw_path) for request in batch)

        batch_timeout = settings.timeout_sec_per_img * len(batch)
        log.info("dpp4cli recipe %s for %d file(s)", recipe_key, len(batch))

        result = subprocess.run(
            command, capture_output=True, text=True, timeout=batch_timeout, check=False
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"dpp4cli failed for recipe {recipe_key}: "
                f"{result.stdout.strip()} {result.stderr.strip()}".strip()
            )

    verify_outputs(requests)


def ensure_output_dirs(requests: list[ConversionRequest]) -> None:
    for request in requests:
        request.output_path.parent.mkdir(parents=True, exist_ok=True)


def verify_outputs(requests: list[ConversionRequest]) -> None:
    missing = [
        str(request.output_path)
        for request in requests
        if not request.output_path.exists()
    ]
    if missing:
        raise RuntimeError(f"expected converted files were not produced: {missing}")
