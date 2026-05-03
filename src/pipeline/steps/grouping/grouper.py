"""
Grouper — clusters individual shots into logical groups.

A group represents one final image and can be:
  - SINGLE:        one shot, no HDR, no panorama
  - HDR:           2-9 shots with different EV, same scene
  - PANORAMA:      multiple shots of adjacent angles, same EV
  - HDR_PANORAMA:  panorama where each position is an HDR bracket

Algorithm overview
------------------
1. Sort shots by timestamp (done by exif.read_folder)
2. Form HDR brackets: consecutive shots within MAX_HDR_GAP seconds
   that show EV variation above EV_VARIATION_THRESHOLD
3. Group brackets into panorama sequences: consecutive brackets
   within MAX_PANO_GAP seconds with the same focal length
4. Classify each final group and register it in SessionState
"""

import statistics
from pathlib import Path

from pipeline.state import GroupType
from pipeline.utils.exif import ExifData, read_folder
from pipeline.utils.logger import get_logger

logger = get_logger(__name__)


def _round_to_third(value: float) -> float:
    """Round a float to the nearest 1/3 stop.

    Valid results: 0, ±0.33, ±0.67, ±1, ±1.33, … etc.
    Uses banker's-style rounding to the closest third.

    :param float value: Raw EV offset
    :return: Value rounded to nearest 1/3
    :rtype: float
    """
    thirds = round(value * 3)
    return round(thirds / 3, 2)


# ---------------------------------------------------------------------------
# Tunable thresholds — exposed here so they can be overridden via config
# ---------------------------------------------------------------------------

# Max seconds between shots of the same HDR bracket
MAX_HDR_GAP: float = 2.0

# Max seconds between brackets (or single shots) of the same panorama
MAX_PANO_GAP: float = 30.0

# Min EV spread within a cluster to consider it an HDR bracket
# (e.g. 3 shots at -2, 0, +2 → spread = 4.0)
EV_VARIATION_THRESHOLD: float = 0.8

# Tolerance when comparing focal lengths across panorama positions (mm)
FOCAL_LENGTH_TOLERANCE: float = 1.0

# Enable visual overlap check (ORB + RANSAC) to confirm panorama pairs.
# When True, time+focal are pre-filters; the visual check is the final arbiter.
# When False, behaviour is identical to the original (time+focal only).
PANO_VISUAL_CHECK: bool = True


# ---------------------------------------------------------------------------
# Internal data structures
# ---------------------------------------------------------------------------


class Bracket:
    """
    One HDR bracket: a set of shots taken in rapid succession.
    May also be a single shot with no EV variation.
    """

    def __init__(self, shots: list[ExifData]):
        self.shots = shots

    @property
    def start_time(self) -> float:
        return self.shots[0].timestamp_float

    @property
    def end_time(self) -> float:
        """End of bracket = when the last exposure finished (start + shutter)."""
        return self.shots[-1].end_time_float

    @property
    def focal_length(self) -> float | None:
        focals = [s.focal_length for s in self.shots if s.focal_length]
        return statistics.median(focals) if focals else None

    @property
    def ev_spread(self) -> float:
        evs = [s.ev for s in self.shots if s.ev is not None]
        if len(evs) < 2:
            return 0.0
        return max(evs) - min(evs)

    @property
    def reference_shot(self) -> ExifData:
        """
        Return the shot closest to median EV (the central exposure).

        For single shots: the only shot.
        The middle exposure has the most detail in both shadows and highlights,
        giving feature detectors the most to work with.
        """
        return self.shots[self.reference_shot_index]

    @property
    def reference_shot_index(self) -> int:
        """Index of the shot closest to median EV (the central exposure)."""
        if len(self.shots) == 1:
            return 0
        evs = [s.ev for s in self.shots]
        if all(e is None for e in evs):
            return len(self.shots) // 2
        median_ev = statistics.median(e for e in evs if e is not None)
        _, idx = min(
            (abs((s.ev or 0.0) - median_ev), i) for i, s in enumerate(self.shots)
        )
        return idx

    @property
    def step_offsets(self) -> list[dict]:
        """Compute per-shot step offsets from the reference (central) exposure.

        Each entry is a dict with:
          - ``step_offset``: float rounded to nearest 1/3 stop (0, ±0.33, ±0.67, ±1, …)
          - ``reference_shot``: True only for the central exposure

        For non-HDR brackets (single shot or no EV variation) all shots
        get step_offset=0.0 and the first shot is marked as reference.

        :return: list of dicts, one per shot, in the same order as self.shots
        :rtype: list[dict]
        """
        ref_idx = self.reference_shot_index
        ref_ev = self.shots[ref_idx].ev

        result = []
        for i, shot in enumerate(self.shots):
            is_ref = i == ref_idx
            if not self.is_hdr or ref_ev is None or shot.ev is None:
                result.append({"step_offset": 0.0, "reference_shot": is_ref})
            else:
                raw_offset = ref_ev - shot.ev
                result.append(
                    {
                        "step_offset": _round_to_third(raw_offset),
                        "reference_shot": is_ref,
                    }
                )
        return result

    @property
    def is_hdr(self) -> bool:
        return self.ev_spread >= EV_VARIATION_THRESHOLD

    def __repr__(self):
        return (
            f"Bracket({len(self.shots)} shots, "
            f"ev_spread={self.ev_spread:.1f}, "
            f"fl={self.focal_length}mm)"
        )


class PanoramaGroup:
    """One or more brackets forming a panorama sequence (or a single image)."""

    def __init__(self, brackets: list[Bracket]):
        self.brackets = brackets

    @property
    def all_shots(self) -> list[ExifData]:
        return [s for b in self.brackets for s in b.shots]

    @property
    def is_panorama(self) -> bool:
        return len(self.brackets) > 1

    @property
    def is_hdr(self) -> bool:
        return any(b.is_hdr for b in self.brackets)

    @property
    def group_type(self) -> GroupType:
        if self.is_panorama and self.is_hdr:
            return GroupType.HDR_PANORAMA
        if self.is_panorama:
            return GroupType.PANORAMA
        if self.is_hdr:
            return GroupType.HDR
        return GroupType.SINGLE


# ---------------------------------------------------------------------------
# Step 1 — form HDR brackets from raw shot list
# ---------------------------------------------------------------------------


def _form_brackets(shots: list[ExifData], max_hdr_gap: float) -> list[Bracket]:
    """
    Split shot list into brackets based on time gaps.

    Gap is measured from the END of the previous exposure to the START of
    the next one: gap = curr.timestamp_float - prev.end_time_float

    This correctly handles long exposures (e.g. 30s night shots) where the
    camera timestamp marks the start of exposure, not the end. Without this
    correction a 30s base exposure followed by a 0.3s shot would appear to
    have a 30s gap and would be split into separate brackets incorrectly.

    Any gap > max_hdr_gap seconds after the exposure ends starts a new bracket.
    """
    if not shots:
        return []

    brackets: list[Bracket] = []
    current: list[ExifData] = [shots[0]]

    for prev, curr in zip(shots, shots[1:]):
        # Time between end of previous exposure and start of next
        gap = curr.timestamp_float - prev.end_time_float

        if gap <= max_hdr_gap:
            current.append(curr)
        else:
            brackets.append(Bracket(current))
            current = [curr]

    brackets.append(Bracket(current))
    return brackets


# ---------------------------------------------------------------------------
# Step 2 — group brackets into panorama sequences
# ---------------------------------------------------------------------------


def _same_focal(a: Bracket, b: Bracket, tol: float) -> bool:
    if a.focal_length is None or b.focal_length is None:
        return True  # can't disprove — assume same
    return abs(a.focal_length - b.focal_length) <= tol


def _form_panorama_groups(
    brackets: list[Bracket],
    max_pano_gap: float,
    focal_tol: float,
    pano_cfg=None,  # PanoCheckConfig | None
    log=None,
) -> list[PanoramaGroup]:
    """
    Cluster brackets into panorama groups.

    A new group starts when any of the following conditions is met:
      1. Time gap between brackets exceeds max_pano_gap, OR
      2. Focal length changes significantly, OR
      3. (if PANO_VISUAL_CHECK enabled) Visual overlap check confidently
         determines the two representative shots are NOT a panoramic pair.

    Condition 3 uses ORB feature matching + RANSAC homography to verify
    that consecutive brackets have a valid horizontal or vertical overlap.
    It only overrides the grouping decision when confidence exceeds
    PanoCheckConfig.min_confidence_to_override — otherwise the time+focal
    result stands (graceful fallback for low-texture scenes).
    """
    if not brackets:
        return []

    if log is None:
        log = logger

    # Import here to avoid circular dependency at module load time
    visual_check_enabled = PANO_VISUAL_CHECK and pano_cfg is not None
    if visual_check_enabled:
        from pipeline.steps.grouping.pano_checker import (
            check_panoramic_overlap,
        )

    groups: list[PanoramaGroup] = []
    current: list[Bracket] = [brackets[0]]

    for prev, curr in zip(brackets, brackets[1:]):
        gap = curr.start_time - prev.end_time
        same_fl = _same_focal(prev, curr, focal_tol)

        # ── Pre-filter: time and focal length ──────────────────────────
        if gap > max_pano_gap or not same_fl:
            reason = (
                f"gap={gap:.1f}s>{max_pano_gap}s"
                if gap > max_pano_gap
                else "focal change"
            )
            log.debug(f"  New group: {reason}")
            groups.append(PanoramaGroup(current))
            current = [curr]
            continue

        # ── Visual check (optional) ────────────────────────────────────
        if visual_check_enabled:
            img_a = prev.reference_shot.path
            img_b = curr.reference_shot.path
            vc = check_panoramic_overlap(img_a, img_b, cfg=pano_cfg, log=log)

            if vc.confidence >= pano_cfg.min_confidence_to_override:
                if not vc.is_panoramic_overlap:
                    log.debug(
                        f"  New group (visual ✗): {prev.reference_shot.path.name}"
                        f" ↔ {curr.reference_shot.path.name} — {vc.reason}"
                    )
                    groups.append(PanoramaGroup(current))
                    current = [curr]
                    continue
                else:
                    log.debug(
                        f"  Same group (visual ✓): {vc.direction} "
                        f"overlap={vc.overlap_pct:.0f}%"
                    )
            else:
                # Low confidence → treat as NOT panoramic (conservative choice)
                log.debug(
                    f"  New group (visual low-conf {vc.confidence:.2f}): "
                    f"{prev.reference_shot.path.name} ↔ "
                    f"{curr.reference_shot.path.name} — {vc.reason}"
                )
                groups.append(PanoramaGroup(current))
                current = [curr]
                continue

        current.append(curr)

    groups.append(PanoramaGroup(current))
    return groups


# ---------------------------------------------------------------------------
# Main grouper function
# ---------------------------------------------------------------------------


def run_grouper(
    input_dir: Path,
    config: dict | None = None,
    log=None,
) -> list[PanoramaGroup]:
    """Scan input_dir, detect groups, and return them.

    :param Path input_dir: Folder containing source JPEG files
    :param dict config: Optional full pipeline config (only ``grouper`` section is read)
    :param log: Optional logger
    :return: List of detected PanoramaGroup objects
    :rtype: list[PanoramaGroup]
    """
    log = log or logger
    input_dir = Path(input_dir)

    cfg = (config or {}).get("grouper", {})
    max_hdr_gap = float(cfg.get("max_hdr_gap", MAX_HDR_GAP))
    max_pano_gap = float(cfg.get("max_pano_gap", MAX_PANO_GAP))
    focal_tol = float(cfg.get("focal_length_tolerance", FOCAL_LENGTH_TOLERANCE))

    # Build PanoCheckConfig from the 'grouper.pano_check' sub-section.
    # If the key is absent or pano_visual_check=false, visual checking is disabled.
    pano_cfg = None
    if cfg.get("pano_visual_check", PANO_VISUAL_CHECK):
        from pipeline.steps.grouping.pano_checker import PanoCheckConfig

        pc_dict = cfg.get("pano_check", {})
        pano_cfg = PanoCheckConfig.from_dict(pc_dict)
        logger.info("Panorama visual check: ENABLED")
    else:
        logger.info("Panorama visual check: disabled (pano_visual_check=false)")

    # 1. Read all EXIF
    shots = read_folder(Path(input_dir), config=config)
    if not shots:
        logger.warning(f"No JPEG files found in {input_dir}")
        return []

    logger.info(f"Read {len(shots)} shots")

    # Warn about shots with no timestamp (will be grouped last)
    no_ts = [s for s in shots if s.timestamp is None]
    if no_ts:
        logger.warning(
            f"{len(no_ts)} shot(s) have no timestamp and will be grouped by filename: "
            + ", ".join(s.path.name for s in no_ts)
        )

    # 2. Form HDR brackets
    brackets = _form_brackets(shots, max_hdr_gap)
    logger.info(f"Formed {len(brackets)} bracket(s)")
    for b in brackets:
        logger.debug(f"  {b}")

    # 3. Group into panorama sequences
    pano_groups = _form_panorama_groups(
        brackets, max_pano_gap, focal_tol, pano_cfg=pano_cfg, log=logger
    )
    logger.info(f"Formed {len(pano_groups)} group(s)")

    for i, pg in enumerate(pano_groups):
        log.info(
            f"  group_{i + 1:03d}: {pg.group_type.value} — "
            f"{len(pg.brackets)} bracket(s), {len(pg.all_shots)} shot(s)"
        )
        if pg.is_hdr:
            for j, b in enumerate(pg.brackets):
                evs = [f"{s.ev:+.1f}" for s in b.shots if s.ev is not None]
                log.debug(f"    bracket {j + 1}: EV [{', '.join(evs)}]")

    return pano_groups


# ---------------------------------------------------------------------------
# Diagnostic helpers (useful during development / review)
# ---------------------------------------------------------------------------


def grouping_report(pano_groups: list[PanoramaGroup]) -> str:
    """Return a human-readable summary of detected groups."""
    lines = [f"\nGrouping report — {len(pano_groups)} group(s)\n" + "─" * 50]

    for i, pg in enumerate(pano_groups):
        group_id = f"group_{i + 1:03d}"
        lines.append(f"\n{group_id}  [{pg.group_type.value}]")
        lines.append(f"  Brackets : {len(pg.brackets)}")
        lines.append(f"  Total shots: {len(pg.all_shots)}")

        for j, b in enumerate(pg.brackets):
            evs = [f"{s.ev:+.1f}" for s in b.shots if s.ev is not None]
            ts = (
                b.shots[0].timestamp.strftime("%H:%M:%S")
                if b.shots[0].timestamp
                else "??:??:??"
            )
            lines.append(
                f"  [{j + 1}] {ts}  {len(b.shots)} shots"
                + (f"  EV=[{', '.join(evs)}]" if evs else "")
                + ("  ← HDR" if b.is_hdr else "")
            )
            for s in b.shots:
                lines.append(f"       · {s.path.name}")

    return "\n".join(lines)
