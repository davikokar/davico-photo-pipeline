"""
pano_checker.py — visual overlap verification for panorama grouping.

Determines whether two images are adjacent frames of a panoramic sequence
by estimating their geometric relationship via LoFTR feature matching and
MAGSAC++ homography analysis.

Why LoFTR over ORB
------------------
ORB (binary descriptor, Hamming matching) is fast but fails on terrestrial
scenes with 3D parallax, repetitive structures, and low-texture overlap
zones. LoFTR is a learned dense matcher that handles these cases robustly.

Why homography analysis rather than just inlier count
------------------------------------------------------
A high inlier count alone is not sufficient: two similar-looking shots of
the same static scene (e.g. bracketing repeats, or almost identical
reframings) can produce hundreds of inliers without being a genuine panorama.
What matters is *what the homography looks like*:

  - Scale ≈ 1.0          (no zoom between shots)
  - Rotation small       (< ~20°, handheld tolerance)
  - Translation is primarily horizontal OR primarily vertical
    (not diagonal, not near-zero, not excessively large)
  - Estimated overlap in [OVERLAP_MIN, OVERLAP_MAX]

All four conditions must hold for `is_panoramic_overlap = True`.

Speed
-----
Both images are resized to ANALYSIS_WIDTH pixels wide before LoFTR inference.
At 840px, LoFTR runs in ~200ms per pair on CPU, ~50ms on GPU.
For a 20-bracket session this adds 4s (CPU) or 1s (GPU) total — acceptable
for a one-time grouping step.

For HDR brackets, the caller should pass the representative (0-EV) shot
rather than the dark or bright exposure.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Optional

import cv2
import numpy as np
import torch

from pipeline.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Tunable constants
# ---------------------------------------------------------------------------

# Width (px) to which images are resized before LoFTR inference.
# Must be divisible by 8 for LoFTR architecture constraints.
ANALYSIS_WIDTH = 840

# Minimum LoFTR correspondences to attempt homography estimation.
MIN_CORRESPONDENCES = 20

# Minimum MAGSAC++ inliers to trust the homography.
MIN_INLIERS = 15

# Maximum absolute rotation between shots (degrees).
MAX_ROTATION_DEG = 20.0

# A translation axis must account for at least this fraction of the total
# translation magnitude to be considered "directional" (H or V).
# e.g. 0.6 means tx must be ≥ 60% of sqrt(tx²+ty²) for "horizontal".
DIRECTION_DOMINANCE = 0.60

# Overlap range [min, max] as fraction of the image dimension in the
# direction of the translation. Outside this range → not a panorama pair:
#   < OVERLAP_MIN : missed frames, or completely different scene
#   > OVERLAP_MAX : almost identical shots (burst, repeat, slight reframe)
OVERLAP_MIN = 0.15
OVERLAP_MAX = 0.85

# If the visual check cannot reach this confidence level (0-1),
# the grouper falls back to the time+focal criteria instead of
# using the visual result.
MIN_CONFIDENCE_TO_OVERRIDE = 0.55


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class PanoCheckResult:
    is_panoramic_overlap: bool = False
    direction: str = "none"  # horizontal | vertical | none | ambiguous
    overlap_pct: float = 0.0  # estimated overlap %
    inliers: int = 0
    confidence: float = 0.0  # 0-1: how sure we are of the result
    reason: str = ""  # human-readable explanation

    def __str__(self):
        return (
            f"PanoCheck({'✓' if self.is_panoramic_overlap else '✗'}) "
            f"dir={self.direction} overlap={self.overlap_pct:.0f}% "
            f"inliers={self.inliers} conf={self.confidence:.2f} "
            f"[{self.reason}]"
        )


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


@dataclass
class PanoCheckConfig:
    analysis_width: int = ANALYSIS_WIDTH
    min_correspondences: int = MIN_CORRESPONDENCES
    min_inliers: int = MIN_INLIERS
    max_rotation_deg: float = MAX_ROTATION_DEG
    direction_dominance: float = DIRECTION_DOMINANCE
    overlap_min: float = OVERLAP_MIN
    overlap_max: float = OVERLAP_MAX
    min_confidence_to_override: float = MIN_CONFIDENCE_TO_OVERRIDE

    @classmethod
    def from_dict(cls, d: dict) -> "PanoCheckConfig":
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in known})


# ---------------------------------------------------------------------------
# LoFTR singleton (lazy-loaded on first use)
# ---------------------------------------------------------------------------

_loftr_matcher = None
_loftr_device = None


def _get_loftr() -> tuple:
    """Return (matcher, device) — loads model on first call, reuses thereafter."""
    global _loftr_matcher, _loftr_device
    if _loftr_matcher is None:
        import kornia as K

        _loftr_device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        _loftr_matcher = K.feature.LoFTR(pretrained="outdoor").to(_loftr_device).eval()
        logger.info(f"LoFTR loaded on {_loftr_device}")
    return _loftr_matcher, _loftr_device


# ---------------------------------------------------------------------------
# Image loading and LoFTR matching
# ---------------------------------------------------------------------------


def _load_gray(path: Path, target_width: int) -> Optional[tuple[np.ndarray, float]]:
    """
    Load a JPEG, resize to target_width maintaining aspect ratio.
    Returns (greyscale uint8 array, scale_factor) or None on failure.
    """
    img = cv2.imread(str(path))
    if img is None:
        logger.warning(f"pano_checker: could not read {path.name}")
        return None
    h, w = img.shape[:2]
    scale = target_width / w
    new_h = int(h * scale)
    # Round height to multiple of 8 for LoFTR
    new_h = (new_h // 8) * 8
    resized = cv2.resize(img, (target_width, new_h), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    return gray, scale


def _to_loftr_tensor(gray: np.ndarray, device: torch.device) -> torch.Tensor:
    """Convert grayscale numpy array to LoFTR-compatible [1, 1, H, W] tensor."""
    t = torch.from_numpy(gray).float() / 255.0
    return t.unsqueeze(0).unsqueeze(0).to(device)


def _match_loftr(
    gray_a: np.ndarray,
    gray_b: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Run LoFTR matching on two grayscale images.

    Returns (keypoints_a, keypoints_b) as Nx2 float32 arrays in resized-image
    coordinates. Returns empty arrays if matching fails.
    """
    matcher, device = _get_loftr()

    t_a = _to_loftr_tensor(gray_a, device)
    t_b = _to_loftr_tensor(gray_b, device)

    with torch.inference_mode():
        correspondences = matcher({"image0": t_a, "image1": t_b})

    kpts_a = correspondences["keypoints0"].cpu().numpy()
    kpts_b = correspondences["keypoints1"].cpu().numpy()
    return kpts_a, kpts_b


# ---------------------------------------------------------------------------
# Homography estimation and analysis
# ---------------------------------------------------------------------------


def _analyse_homography(
    H: np.ndarray,
    img_w: int,
    img_h: int,
    cfg: PanoCheckConfig,
) -> tuple[bool, str, float, float, str]:
    """
    Evaluate the homography by warping image A's corners into image B's
    coordinate system and measuring the geometric relationship.

    Returns:
        (is_pano, direction, overlap_pct, confidence, reason)

    Unlike affine decomposition (which breaks on perspective homographies),
    this approach works correctly for real panoramic rotations where the
    perspective terms in H are significant.
    """
    # Normalize
    h22 = H[2, 2]
    if abs(h22) < 1e-6:
        return False, "none", 0.0, 0.9, "degenerate homography (h22≈0)"
    H = H / h22

    # Warp corners of image A into image B's coordinate system
    corners_a = np.float32([[0, 0], [img_w, 0], [img_w, img_h], [0, img_h]])
    corners_warped = cv2.perspectiveTransform(
        corners_a.reshape(-1, 1, 2), H
    ).reshape(-1, 2)

    # Check the warped quad is convex (degenerate H can produce bowties)
    if not cv2.isContourConvex(corners_warped.astype(np.float32)):
        return False, "none", 0.0, 0.8, "degenerate homography (non-convex warp)"

    original_area = float(img_w * img_h)

    reasons = []
    failures = []

    # NOTE: No scale check here. For a panoramic rotation (camera rotating
    # in place), the perspective projection of image A into B's plane
    # naturally covers a larger area — this is NOT zoom. Zoom is already
    # detected by the focal length comparison in the grouper pre-filter.

    # ── Check rotation via top edge angle ────────────────────────────────
    top_edge = corners_warped[1] - corners_warped[0]  # top-right minus top-left
    rotation_deg = float(np.degrees(np.arctan2(top_edge[1], top_edge[0])))
    abs_rot = abs(rotation_deg)

    if abs_rot > cfg.max_rotation_deg:
        failures.append(f"rotation={rotation_deg:.1f}° > {cfg.max_rotation_deg}°")
    else:
        reasons.append(f"rot={rotation_deg:.1f}°✓")

    # ── Determine direction from center displacement ─────────────────────
    center_b = np.array([img_w / 2.0, img_h / 2.0])
    center_warped = np.mean(corners_warped, axis=0)
    displacement = center_warped - center_b
    tx, ty = float(displacement[0]), float(displacement[1])
    t_mag = float(np.sqrt(tx**2 + ty**2))

    if t_mag < 2.0:
        failures.append(f"translation too small (|t|={t_mag:.1f}px)")
        direction = "none"
        overlap_pct = 100.0
    else:
        h_fraction = abs(tx) / t_mag
        v_fraction = abs(ty) / t_mag

        if h_fraction >= cfg.direction_dominance:
            direction = "horizontal"
        elif v_fraction >= cfg.direction_dominance:
            direction = "vertical"
        else:
            direction = "ambiguous"
            failures.append(
                f"diagonal translation (h={h_fraction:.0%}, v={v_fraction:.0%})"
            )

        reasons.append(f"dir={direction} tx={tx:.0f}px ty={ty:.0f}px")

        # ── Compute overlap via polygon intersection ─────────────────────
        rect_b = np.float32([[0, 0], [img_w, 0], [img_w, img_h], [0, img_h]])
        intersection_area, _ = cv2.intersectConvexConvex(
            corners_warped.astype(np.float32),
            rect_b,
        )
        overlap_pct = float(intersection_area) / original_area * 100.0

    # ── Check overlap range ───────────────────────────────────────────────
    overlap_frac = overlap_pct / 100.0
    if direction not in ("none",):
        if overlap_frac < cfg.overlap_min:
            failures.append(
                f"overlap={overlap_pct:.0f}% < {cfg.overlap_min * 100:.0f}% (frames too far apart)"
            )
        elif overlap_frac > cfg.overlap_max:
            failures.append(
                f"overlap={overlap_pct:.0f}% > {cfg.overlap_max * 100:.0f}% (frames too similar)"
            )
        else:
            reasons.append(f"overlap={overlap_pct:.0f}%✓")

    # ── Final decision ────────────────────────────────────────────────────
    is_pano = len(failures) == 0 and direction not in ("none", "ambiguous")

    confidence = 1.0
    for f in failures:
        confidence *= 0.5
    if direction == "ambiguous":
        confidence *= 0.7

    reason_str = " | ".join(reasons + [f"✗ {f}" for f in failures])
    return is_pano, direction, overlap_pct, min(confidence, 1.0), reason_str


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_panoramic_overlap(
    path_a: Path,
    path_b: Path,
    cfg: PanoCheckConfig | None = None,
    log=None,
) -> PanoCheckResult:
    """
    Determine whether two images have a panoramic overlap relationship.

    Args:
        path_a:  Path to the first image (left/top in the panorama).
        path_b:  Path to the second image (right/bottom).
        cfg:     PanoCheckConfig (uses module defaults if None).
        log:     Logger adapter.

    Returns:
        PanoCheckResult with full diagnostic information.
    """
    if cfg is None:
        cfg = PanoCheckConfig()
    if log is None:
        log = logger

    result = PanoCheckResult()

    # ── Load and resize ───────────────────────────────────────────────────
    loaded_a = _load_gray(path_a, cfg.analysis_width)
    loaded_b = _load_gray(path_b, cfg.analysis_width)

    if loaded_a is None or loaded_b is None:
        result.reason = "could not load one or both images"
        result.confidence = 0.0
        return result

    gray_a, scale_a = loaded_a
    gray_b, scale_b = loaded_b

    img_h, img_w = gray_a.shape

    # ── LoFTR feature matching ────────────────────────────────────────────
    kpts_a, kpts_b = _match_loftr(gray_a, gray_b)

    n_correspondences = len(kpts_a)
    log.debug(
        f"  {path_a.name}↔{path_b.name}: "
        f"{n_correspondences} LoFTR correspondences"
    )

    if n_correspondences < cfg.min_correspondences:
        result.reason = (
            f"too few correspondences ({n_correspondences} < {cfg.min_correspondences})"
        )
        result.confidence = 0.2
        return result

    # ── MAGSAC++ homography ───────────────────────────────────────────────
    H, mask = cv2.findHomography(
        kpts_a,
        kpts_b,
        method=cv2.USAC_MAGSAC,
        ransacReprojThreshold=3.0,
        maxIters=20000,
        confidence=0.999,
    )

    if H is None or mask is None:
        result.reason = f"MAGSAC++ failed ({n_correspondences} correspondences)"
        result.confidence = 0.2
        return result

    n_inliers = int(mask.sum())
    result.inliers = n_inliers

    if n_inliers < cfg.min_inliers:
        result.reason = f"too few inliers ({n_inliers} < {cfg.min_inliers})"
        result.confidence = 0.3
        return result

    # ── Homography analysis ───────────────────────────────────────────────
    is_pano, direction, overlap_pct, confidence, reason = _analyse_homography(
        H, img_w, img_h, cfg
    )

    # Scale confidence by inlier ratio (more inliers = more trustworthy H)
    inlier_ratio = n_inliers / max(n_correspondences, 1)
    confidence = confidence * (0.5 + 0.5 * inlier_ratio)

    result.is_panoramic_overlap = is_pano
    result.direction = direction
    result.overlap_pct = round(overlap_pct, 1)
    result.confidence = round(confidence, 3)
    result.reason = reason

    log.debug(f"  → {result}")
    return result

# ══════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════
#
# Example usage:
#   python -m pipeline.steps.grouping.pano_checker '/mnt/c/Temp/pipeline_tests/canon/original/0H8A4482.JPG' '/mnt/c/Temp/pipeline_tests/canon/original/0H8A4485.JPG'

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(
            "Usage: python pano_checker.py path_to_image_A path_to_image_B"
        )
        sys.exit(1)

    path_a = sys.argv[1]
    path_b = sys.argv[2]
    
    configuration = PanoCheckConfig(
        analysis_width=ANALYSIS_WIDTH,
        min_correspondences=MIN_CORRESPONDENCES,
        min_inliers=MIN_INLIERS,
        scale_min=SCALE_MIN,
        scale_max=SCALE_MAX,
        max_rotation_deg=MAX_ROTATION_DEG,
        direction_dominance=DIRECTION_DOMINANCE,
        overlap_min=OVERLAP_MIN,
        overlap_max=OVERLAP_MAX,
        min_confidence_to_override=MIN_CONFIDENCE_TO_OVERRIDE,
    )


    panocheck_result = check_panoramic_overlap(
        Path(path_a),
        Path(path_b),
        cfg=configuration,
    )

    print(panocheck_result)
