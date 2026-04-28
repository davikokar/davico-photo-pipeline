"""RAW to JPG conversion using groups JSON and dpp4cli.

This step consumes the latest groups JSON produced by the grouper,
derives the exact set of JPEG conversions required for a single group,
executes the conversions in recipe-based batches through dpp4cli, and
updates an aggregate JSON file in the session folder with the generated
HDR helper images.
"""

from __future__ import annotations

import json
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from pipeline.steps.grouping.groups_io import load_latest_groups_json
from pipeline.utils.logger import get_logger

logger = get_logger(__name__)

RAW_CONVERSIONS_VERSION = 1
RAW_CONVERSIONS_FILENAME = "raw_conversions.json"
DEFAULT_RAW_EXTENSIONS = (".cr2", ".cr3", ".crw", ".crf")


@dataclass(frozen=True)
class ConversionRequest:
	"""One RAW to JPEG conversion request.

	:param Path raw_path: RAW input file
	:param str recipe_key: Logical recipe identifier, e.g. ``0`` or ``+2``
	:param Path recipe_path: Recipe file passed to dpp4cli
	:param str suffix: Output suffix passed to dpp4cli
	:param Path output_dir: Directory where dpp4cli writes the JPEG
	:param str collection: Output collection name in JSON: ``hdr``, ``noghost``, or ``normalized``
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


def convert_group_from_groups_json(session_dir: Path, group_id: str, config: dict, log=None) -> Path:
	"""Convert one group using the latest groups JSON.

	:param Path session_dir: Session directory containing groups JSON files
	:param str group_id: Group identifier to convert
	:param dict config: Full pipeline configuration
	:param log: Optional logger or logger adapter
	:return: Path to the aggregate raw conversion JSON
	:rtype: Path
	:raises FileNotFoundError: If RAW conversion is disabled or the group has no RAW files
	:raises ValueError: If required recipes are missing or the group is not present
	"""
	log = log or logger
	session_dir = Path(session_dir)

	groups_payload = load_latest_groups_json(session_dir)
	if groups_payload is None:
		raise ValueError("no groups JSON found in session directory")

	hdr_config = _get_hdr_config(config)
	config_dir = _get_config_dir(config)
	raw_dir = _resolve_raw_dir(hdr_config, config_dir)
	if raw_dir is None:
		raise FileNotFoundError("raw_to_jpg skipped: steps.hdr.raw_dir is not configured")
	if not raw_dir.exists():
		raise FileNotFoundError(f"raw_to_jpg skipped: raw_dir not found: {raw_dir}")

	group = _find_group(groups_payload["groups"], group_id)
	raw_index = build_raw_index(raw_dir, _get_raw_extensions(hdr_config))
	requests, group_payload = plan_group_conversions(group, raw_index, hdr_config, session_dir)

	if not requests:
		raise FileNotFoundError(f"raw_to_jpg skipped: no RAW files found for {group_id}")

	_ensure_output_dirs(requests)
	execute_conversion_plan(requests, hdr_config, log)
	_verify_outputs(requests)

	aggregate_path = session_dir / RAW_CONVERSIONS_FILENAME
	write_group_conversion_json(
		aggregate_path=aggregate_path,
		source_groups_payload=groups_payload,
		raw_dir=raw_dir,
		group_payload=group_payload,
	)
	log.info("RAW conversions written to %s", aggregate_path)
	return aggregate_path


def plan_group_conversions(
	group: dict,
	raw_index: dict[str, Path],
	hdr_config: dict,
	session_dir: Path,
) -> tuple[list[ConversionRequest], dict]:
	"""Build all conversion requests and JSON payload for one group.

	:param dict group: Group entry from groups JSON
	:param dict raw_index: Mapping of JPEG stem to RAW file path
	:param dict hdr_config: ``steps.hdr`` configuration section
	:return: Tuple of conversion requests and group payload ready for JSON serialisation
	:rtype: tuple[list[ConversionRequest], dict]
	"""
	recipe_paths = parse_recipe_paths(hdr_config)
	requests: list[ConversionRequest] = []
	bracket_payloads: list[dict] = []
	output_root = Path(session_dir) / "intermediates" / "raw_to_jpg" / group["id"]

	for bracket_index, bracket in enumerate(group.get("brackets", []), start=1):
		bracket_dir = output_root / f"bracket_{bracket_index:03d}"
		normalized_shots = normalize_bracket_shots(bracket)
		bracket_requests = build_bracket_requests(
			normalized_shots=normalized_shots,
			bracket_dir=bracket_dir,
			raw_index=raw_index,
			recipe_paths=recipe_paths,
			bracket_index=bracket_index - 1,
		)
		requests.extend(bracket_requests)
		bracket_payloads.append(build_bracket_payload(normalized_shots, bracket_requests, session_dir))

	group_payload = {
		"id": group["id"],
		"type": group["type"],
		"brackets": bracket_payloads,
	}
	return requests, group_payload


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
			raise ValueError(f"duplicate RAW stem found for {candidate.stem}: {index[stem_key]} and {candidate}")
		index[stem_key] = candidate

	return index


def parse_recipe_paths(hdr_config: dict) -> dict[str, Path]:
	"""Parse configured recipe paths.

	:param dict hdr_config: ``steps.hdr`` configuration section
	:return: Mapping from normalised recipe key to absolute path
	:rtype: dict[str, Path]
	:raises ValueError: If the recipe mapping is missing or empty
	"""
	recipe_config = hdr_config.get("raw_to_jpg", {}).get("recipes", {})
	if not recipe_config:
		raise ValueError("steps.hdr.raw_to_jpg.recipes is required")

	config_dir = _get_config_dir_from_hdr(hdr_config)

	parsed: dict[str, Path] = {}
	for raw_key, recipe_path in recipe_config.items():
		key = normalize_recipe_key(raw_key)
		parsed[key] = _resolve_config_path(recipe_path, config_dir)

	return parsed


def normalize_bracket_shots(bracket: dict) -> list[dict]:
	"""Ensure bracket shots expose ``step_offset`` and ``reference_shot``.

	:param dict bracket: Bracket object from groups JSON
	:return: Normalised shot dicts
	:rtype: list[dict]
	"""
	shots = [dict(shot) for shot in bracket.get("shots", [])]
	if not shots:
		return []

	reference_index = _resolve_reference_index(shots)
	reference_ev = shots[reference_index].get("ev")

	for index, shot in enumerate(shots):
		shot["reference_shot"] = index == reference_index
		if shot.get("step_offset") is None:
			shot["step_offset"] = _derive_step_offset(shot.get("ev"), reference_ev)

	return shots


def build_bracket_requests(
	normalized_shots: list[dict],
	bracket_dir: Path,
	raw_index: dict[str, Path],
	recipe_paths: dict[str, Path],
	bracket_index: int,
) -> list[ConversionRequest]:
	"""Create conversion requests for one bracket.

	:param list[dict] normalized_shots: Normalised bracket shots
	:param Path bracket_dir: Relative output directory for the bracket
	:param dict raw_index: RAW lookup by stem
	:param dict recipe_paths: Available configured recipes
	:param int bracket_index: Zero-based bracket index within the group
	:return: Conversion requests
	:rtype: list[ConversionRequest]
	"""
	if not normalized_shots:
		return []

	resolved = [_attach_raw_path(shot, raw_index) for shot in normalized_shots]
	is_hdr_bracket = len(resolved) > 1 and any(abs(shot["step_offset"]) > 0 for shot in resolved)

	if not is_hdr_bracket:
		return build_single_shot_requests(resolved[0], bracket_dir, recipe_paths, bracket_index)

	required_recipe_keys = {"0"}
	for shot in resolved:
		shot_key = normalize_recipe_key(shot["step_offset"])
		required_recipe_keys.add(shot_key)
		required_recipe_keys.add(normalize_recipe_key(-shot["step_offset"]))

	missing_keys = sorted(key for key in required_recipe_keys if key not in recipe_paths)
	if missing_keys:
		raise ValueError(f"missing recipes for HDR bracket: {', '.join(missing_keys)}")

	requests: list[ConversionRequest] = []
	reference_shot = next(shot for shot in resolved if shot["reference_shot"])
	unique_offsets = sorted(
		{normalize_recipe_key(shot["step_offset"]) for shot in resolved if not shot["reference_shot"]},
		key=lambda value: float(value),
	)

	for shot in resolved:
		requests.append(
			make_request(
				shot=shot,
				recipe_key="0",
				recipe_paths=recipe_paths,
				output_dir=bracket_dir,
				collection="hdr",
				bracket_index=bracket_index,
			)
		)

	requests.append(
		make_request(
			shot=reference_shot,
			recipe_key="0",
			recipe_paths=recipe_paths,
			output_dir=bracket_dir,
			collection="noghost",
			bracket_index=bracket_index,
		)
	)

	for offset_key in unique_offsets:
		requests.append(
			make_request(
				shot=reference_shot,
				recipe_key=offset_key,
				recipe_paths=recipe_paths,
				output_dir=bracket_dir,
				collection="noghost",
				bracket_index=bracket_index,
			)
		)

	for shot in resolved:
		if shot["reference_shot"]:
			continue
		opposite_key = normalize_recipe_key(-shot["step_offset"])
		requests.append(
			make_request(
				shot=shot,
				recipe_key=opposite_key,
				recipe_paths=recipe_paths,
				output_dir=bracket_dir,
				collection="normalized",
				bracket_index=bracket_index,
			)
		)

	return deduplicate_requests(requests)


def build_single_shot_requests(
	shot: dict,
	bracket_dir: Path,
	recipe_paths: dict[str, Path],
	bracket_index: int,
) -> list[ConversionRequest]:
	"""Create pseudo-bracket conversions for a non-HDR shot.

	:param dict shot: Normalised single shot
	:param Path bracket_dir: Relative output directory for the bracket
	:param dict recipe_paths: Available recipes
	:param int bracket_index: Zero-based bracket index within the group
	:return: Conversion requests
	:rtype: list[ConversionRequest]
	"""
	required_keys = ["-2", "0", "+2"]
	missing_keys = [key for key in required_keys if key not in recipe_paths]
	if missing_keys:
		raise ValueError(f"missing recipes for single-shot conversion: {', '.join(missing_keys)}")

	requests = [
		make_request(shot, key, recipe_paths, bracket_dir, "hdr", bracket_index)
		for key in required_keys
	]
	requests.extend(
		make_request(shot, key, recipe_paths, bracket_dir, "noghost", bracket_index)
		for key in required_keys
	)
	return deduplicate_requests(requests)


def make_request(
	shot: dict,
	recipe_key: str,
	recipe_paths: dict[str, Path],
	output_dir: Path,
	collection: str,
	bracket_index: int,
) -> ConversionRequest:
	"""Build one conversion request.

	:param dict shot: Source shot metadata
	:param str recipe_key: Logical recipe key
	:param dict recipe_paths: Available recipes
	:param Path output_dir: Relative output directory
	:param str collection: Output collection name
	:param int bracket_index: Zero-based bracket index
	:return: Conversion request
	:rtype: ConversionRequest
	"""
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


def deduplicate_requests(requests: list[ConversionRequest]) -> list[ConversionRequest]:
	"""Drop duplicate requests that produce the same file for the same collection.

	:param list[ConversionRequest] requests: Requests to deduplicate
	:return: Deduplicated requests
	:rtype: list[ConversionRequest]
	"""
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


def execute_conversion_plan(requests: list[ConversionRequest], hdr_config: dict, log=None) -> None:
	"""Execute conversion requests grouped by recipe and output parameters.

	:param list[ConversionRequest] requests: Conversion requests
	:param dict hdr_config: ``steps.hdr`` configuration section
	:param log: Optional logger or logger adapter
	"""
	log = log or logger
	dpp4cli_exe = _resolve_dpp4cli_path(hdr_config)
	quality = int(hdr_config.get("raw_to_jpg", {}).get("jpeg_quality", 100))
	output_format = hdr_config.get("raw_to_jpg", {}).get("format", "jpg")
	dpp4dir = hdr_config.get("raw_to_jpg", {}).get("dpp4dir", "")
	verbose = bool(hdr_config.get("raw_to_jpg", {}).get("verbose", False))
	timeout_sec = hdr_config.get("raw_to_jpg", {}).get("subprocess_timeout_sec")
	timeout = int(timeout_sec) if timeout_sec else None

	grouped: dict[tuple[Path, str, Path, str], list[ConversionRequest]] = defaultdict(list)
	for request in requests:
		grouped[(request.recipe_path, request.suffix, request.output_dir, request.recipe_key)].append(request)

	for (recipe_path, suffix, output_dir, recipe_key), batch in grouped.items():
		command = [
			str(dpp4cli_exe),
			"--recipe",
			str(recipe_path),
			"--outputdir",
			str(output_dir),
			"--quality",
			str(quality),
			"--format",
			output_format,
		]
		if suffix:
			command.extend(["--suffix", suffix])
		if dpp4dir:
			command.extend(["--dpp4dir", dpp4dir])
		if verbose:
			command.append("--verbose")
		command.extend(str(request.raw_path) for request in batch)

		log.info("dpp4cli recipe %s for %d file(s)", recipe_key, len(batch))
		result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
		if result.returncode != 0:
			raise RuntimeError(
				f"dpp4cli failed for recipe {recipe_key}: {result.stdout.strip()} {result.stderr.strip()}".strip()
			)


def build_bracket_payload(normalized_shots: list[dict], requests: list[ConversionRequest], session_dir: Path) -> dict:
	"""Create JSON payload for one bracket.

	:param list[dict] normalized_shots: Source shots
	:param list[ConversionRequest] requests: Executed conversion requests for the bracket
	:param Path output_root: Relative output root for the group
	:return: Serialisable bracket payload
	:rtype: dict
	"""
	collections = {"hdr": [], "noghost": [], "normalized": []}
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
				"raw_filename": shot["raw_path"].name,
				"ev": shot.get("ev"),
				"shutter": shot.get("shutter"),
				"step_offset": shot["step_offset"],
				"reference_shot": shot["reference_shot"],
			}
			for shot in normalized_shots
		],
		"hdr": collections["hdr"],
		"noghost": collections["noghost"],
		"normalized": collections["normalized"],
	}


def write_group_conversion_json(
	aggregate_path: Path,
	source_groups_payload: dict,
	raw_dir: Path,
	group_payload: dict,
) -> None:
	"""Upsert one group into the aggregate RAW conversion JSON.

	:param Path aggregate_path: Aggregate JSON path
	:param dict source_groups_payload: Latest groups JSON payload
	:param Path raw_dir: RAW directory used for lookup
	:param dict group_payload: Serialisable group payload
	"""
	aggregate_path = Path(aggregate_path)
	if aggregate_path.exists():
		with open(aggregate_path, encoding="utf-8") as handle:
			payload = json.load(handle)
	else:
		payload = {
			"version": RAW_CONVERSIONS_VERSION,
			"generated_at": datetime.now().isoformat(),
			"session_id": source_groups_payload.get("session_id"),
			"input_dir": source_groups_payload.get("input_dir"),
			"raw_dir": str(raw_dir),
			"groups": [],
		}

	payload["generated_at"] = datetime.now().isoformat()
	payload["raw_dir"] = str(raw_dir)

	groups_by_id = {group["id"]: group for group in payload.get("groups", [])}
	groups_by_id[group_payload["id"]] = group_payload
	payload["groups"] = sorted(groups_by_id.values(), key=lambda item: item["id"])

	with open(aggregate_path, "w", encoding="utf-8") as handle:
		json.dump(payload, handle, indent=2, ensure_ascii=False)


def normalize_recipe_key(value: str | int | float) -> str:
	"""Normalise recipe keys for config lookup and filenames.

	:param value: Numeric or string recipe key
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

	:param str recipe_key: Canonical recipe key
	:return: Output suffix, empty for the ``0`` recipe
	:rtype: str
	"""
	return "" if normalize_recipe_key(recipe_key) == "0" else f"_{normalize_recipe_key(recipe_key)}"


def _find_group(groups: list[dict], group_id: str) -> dict:
	for group in groups:
		if group.get("id") == group_id:
			return group
	raise ValueError(f"group not found in groups JSON: {group_id}")


def _get_hdr_config(config: dict) -> dict:
	hdr_config = dict(config.get("steps", {}).get("hdr", {}))
	hdr_config["__config_dir__"] = config.get("__config_dir__")
	return hdr_config


def _get_config_dir(config: dict) -> Path:
	config_dir = config.get("__config_dir__")
	return Path(config_dir).resolve() if config_dir else Path.cwd()


def _get_config_dir_from_hdr(hdr_config: dict) -> Path:
	config_dir = hdr_config.get("__config_dir__")
	return Path(config_dir).resolve() if config_dir else Path.cwd()


def _get_raw_extensions(hdr_config: dict) -> tuple[str, ...]:
	configured = hdr_config.get("raw_to_jpg", {}).get("raw_extensions")
	if not configured:
		return DEFAULT_RAW_EXTENSIONS
	return tuple(str(value).lower() for value in configured)


def _resolve_raw_dir(hdr_config: dict, config_dir: Path) -> Path | None:
	raw_dir = hdr_config.get("raw_dir", "")
	if not raw_dir:
		return None
	return _resolve_config_path(raw_dir, config_dir)


def _resolve_dpp4cli_path(hdr_config: dict) -> Path:
	config_dir = _get_config_dir_from_hdr(hdr_config)
	dpp4cli_exe = hdr_config.get("raw_to_jpg", {}).get("dpp4cli_exe", "")
	if not dpp4cli_exe:
		raise ValueError("steps.hdr.raw_to_jpg.dpp4cli_exe is required")
	path = _resolve_config_path(dpp4cli_exe, config_dir)
	if not path.exists():
		raise ValueError(f"dpp4cli executable not found: {path}")
	return path


def _resolve_config_path(path_value: str | Path, config_dir: Path) -> Path:
	path = Path(path_value)
	if path.is_absolute():
		return path.resolve()
	return (config_dir / path).resolve()


def _resolve_reference_index(shots: list[dict]) -> int:
	for index, shot in enumerate(shots):
		if shot.get("reference_shot") is True:
			return index

	offsets = [shot.get("step_offset") for shot in shots]
	for index, offset in enumerate(offsets):
		if offset is not None and abs(float(offset)) < 1e-9:
			return index

	evs = [shot.get("ev") for shot in shots]
	if all(ev is not None for ev in evs):
		ordered = sorted((float(ev), index) for index, ev in enumerate(evs))
		return ordered[len(ordered) // 2][1]

	return len(shots) // 2


def _derive_step_offset(shot_ev: float | None, reference_ev: float | None) -> float:
	if shot_ev is None or reference_ev is None:
		return 0.0
	offset = float(reference_ev) - float(shot_ev)
	return round(offset, 2)


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


def _ensure_output_dirs(requests: list[ConversionRequest]) -> None:
	for request in requests:
		request.output_path.parent.mkdir(parents=True, exist_ok=True)


def _verify_outputs(requests: list[ConversionRequest]) -> None:
	missing = [str(request.output_path) for request in requests if not request.output_path.exists()]
	if missing:
		raise RuntimeError(f"expected converted files were not produced: {missing}")
