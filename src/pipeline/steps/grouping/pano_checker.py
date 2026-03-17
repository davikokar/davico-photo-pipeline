"""
pano_checker.py — visual overlap verification for panorama grouping.

Determines whether two images are adjacent frames of a panoramic sequence
by estimating their geometric relationship via feature matching and RANSAC
homography analysis.

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
Both images are resized to ANALYSIS_WIDTH pixels wide before any processing.
At 800px, ORB runs in ~20-50ms per image and matching is ~5ms.
Total per-pair cost: ~100ms including I/O. For a 20-bracket session this
adds at most 2-3 seconds to the grouping step.

For HDR brackets, the caller should pass the representative (0-EV) shot
rather than the dark or bright exposure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from pipeline.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Tunable constants
# ---------------------------------------------------------------------------

# Width (px) to which images are resized before feature detection.
# Smaller = faster but fewer features in low-overlap regions.
ANALYSIS_WIDTH = 800

# ORB: max features to detect per image.
ORB_N_FEATURES = 1000

# Lowe ratio test threshold for match filtering.
LOWE_RATIO = 0.75

# Minimum matches to attempt homography estimation.
MIN_MATCHES_FOR_HOMOGRAPHY = 12

# Minimum RANSAC inliers to trust the homography.
MIN_INLIERS = 15

# Acceptable scale range (ratio of image scales between two shots).
# Anything outside this means there was a zoom change — not panoramic.
SCALE_MIN = 0.85
SCALE_MAX = 1.15

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
    is_panoramic_overlap: bool    = False
    direction:            str     = "none"   # horizontal | vertical | none | ambiguous
    overlap_pct:          float   = 0.0      # estimated overlap %
    inliers:              int     = 0
    confidence:           float   = 0.0      # 0-1: how sure we are of the result
    reason:               str     = ""       # human-readable explanation

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
    analysis_width:           int   = ANALYSIS_WIDTH
    orb_n_features:           int   = ORB_N_FEATURES
    lowe_ratio:               float = LOWE_RATIO
    min_matches:              int   = MIN_MATCHES_FOR_HOMOGRAPHY
    min_inliers:              int   = MIN_INLIERS
    scale_min:                float = SCALE_MIN
    scale_max:                float = SCALE_MAX
    max_rotation_deg:         float = MAX_ROTATION_DEG
    direction_dominance:      float = DIRECTION_DOMINANCE
    overlap_min:              float = OVERLAP_MIN
    overlap_max:              float = OVERLAP_MAX
    min_confidence_to_override: float = MIN_CONFIDENCE_TO_OVERRIDE

    @classmethod
    def from_dict(cls, d: dict) -> "PanoCheckConfig":
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in known})


# ---------------------------------------------------------------------------
# Image loading and resizing
# ---------------------------------------------------------------------------

def _load_resized(path: Path, target_width: int) -> Optional[np.ndarray]:
    """
    Load a JPEG and resize to target_width maintaining aspect ratio.
    Returns a greyscale uint8 array, or None on read failure.
    """
    img = cv2.imread(str(path))
    if img is None:
        logger.warning(f"pano_checker: could not read {path.name}")
        return None
    h, w = img.shape[:2]
    scale = target_width / w
    new_h = int(h * scale)
    resized = cv2.resize(img, (target_width, new_h), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)


# ---------------------------------------------------------------------------
# Feature detection and matching
# ---------------------------------------------------------------------------

def _detect_and_match(
    gray_a: np.ndarray,
    gray_b: np.ndarray,
    cfg: PanoCheckConfig,
) -> list[cv2.DMatch]:
    """
    ORB feature detection + BFMatcher with Lowe ratio test.

    ORB is chosen over SIFT for speed and because it is free (no patent
    issues). At ANALYSIS_WIDTH=800 it finds 500-1000 keypoints per image
    in ~20ms, which is more than enough for panorama detection.
    """
    orb = cv2.ORB_create(nfeatures=cfg.orb_n_features)
    kp_a, des_a = orb.detectAndCompute(gray_a, None)
    kp_b, des_b = orb.detectAndCompute(gray_b, None)

    if des_a is None or des_b is None or len(des_a) < 4 or len(des_b) < 4:
        return []

    # BFMatcher with Hamming distance (correct for ORB binary descriptors)
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    # knnMatch returns 2 neighbours per descriptor for the ratio test
    raw = matcher.knnMatch(des_a, des_b, k=2)

    # Lowe ratio test: keep matches where best match is significantly
    # better than second-best (eliminates ambiguous matches)
    good = [m for m, n in raw if m.distance < cfg.lowe_ratio * n.distance]
    return good


# ---------------------------------------------------------------------------
# Homography estimation and analysis
# ---------------------------------------------------------------------------

def _estimate_homography(
    kp_a: list,
    kp_b: list,
    good_matches: list[cv2.DMatch],
    cfg: PanoCheckConfig,
) -> tuple[Optional[np.ndarray], int]:
    """
    RANSAC homography estimation.

    Returns (H, n_inliers) or (None, 0) if estimation fails.
    """
    if len(good_matches) < cfg.min_matches:
        return None, 0

    pts_a = np.float32([kp_a[m.queryIdx].pt for m in good_matches])
    pts_b = np.float32([kp_b[m.trainIdx].pt for m in good_matches])

    H, mask = cv2.findHomography(pts_a, pts_b, cv2.RANSAC, ransacReprojThreshold=5.0)
    if H is None or mask is None:
        return None, 0

    return H, int(mask.sum())


def _analyse_homography(
    H: np.ndarray,
    img_w: int,
    img_h: int,
    cfg: PanoCheckConfig,
) -> tuple[bool, str, float, float, str]:
    """
    Decompose and evaluate the homography to determine if it represents
    a valid panoramic relationship.

    Returns:
        (is_pano, direction, overlap_pct, confidence, reason)

    Decomposition
    -------------
    H (3×3) maps points in image A to image B.
    For a panoramic shot, H should approximate a rigid-body transform
    (translation + small rotation, scale ≈ 1).

    From the top-left 2×2 submatrix:
        scale    = sqrt(h00² + h10²)    (length of first column vector)
        rotation = atan2(h10, h00)      (angle of first column vector)

    Translation in image A coordinates (normalizing by h22 for
    perspective correction):
        tx = h02 / h22
        ty = h12 / h22
    """
    # Normalize (perspective division)
    h22 = H[2, 2]
    if abs(h22) < 1e-6:
        return False, "none", 0.0, 0.9, "degenerate homography (h22≈0)"

    H = H / h22

    # Scale: length of the first column of the rotation-scale submatrix
    scale = float(np.sqrt(H[0, 0] ** 2 + H[1, 0] ** 2))

    # Rotation angle (degrees)
    rotation_deg = float(np.degrees(np.arctan2(H[1, 0], H[0, 0])))

    # Translation vector (in resized-image pixels)
    tx = float(H[0, 2])
    ty = float(H[1, 2])

    reasons = []
    failures = []

    # ── Check scale ──────────────────────────────────────────────────────
    if not (cfg.scale_min <= scale <= cfg.scale_max):
        failures.append(f"scale={scale:.2f} outside [{cfg.scale_min},{cfg.scale_max}]")
    else:
        reasons.append(f"scale={scale:.2f}✓")

    # ── Check rotation ───────────────────────────────────────────────────
    abs_rot = abs(rotation_deg)
    if abs_rot > cfg.max_rotation_deg:
        failures.append(f"rotation={rotation_deg:.1f}° > {cfg.max_rotation_deg}°")
    else:
        reasons.append(f"rot={rotation_deg:.1f}°✓")

    # ── Determine translation direction ──────────────────────────────────
    t_mag = float(np.sqrt(tx ** 2 + ty ** 2))

    if t_mag < 2.0:
        # Essentially no translation → same scene, not a panorama pair
        failures.append(f"translation too small (|t|={t_mag:.1f}px)")
        direction = "none"
        overlap_pct = 100.0
    else:
        h_fraction = abs(tx) / t_mag   # fraction of motion that is horizontal
        v_fraction = abs(ty) / t_mag

        if h_fraction >= cfg.direction_dominance:
            direction = "horizontal"
            # Overlap: fraction of image width NOT displaced
            overlap_pct = max(0.0, (img_w - abs(tx)) / img_w * 100.0)
        elif v_fraction >= cfg.direction_dominance:
            direction = "vertical"
            overlap_pct = max(0.0, (img_h - abs(ty)) / img_h * 100.0)
        else:
            # Diagonal translation: probably not a proper panorama,
            # but could be handheld with a lot of drift — flag as ambiguous
            direction = "ambiguous"
            overlap_pct = max(0.0, (img_w - abs(tx)) / img_w * 100.0)
            failures.append(
                f"diagonal translation (h={h_fraction:.0%}, v={v_fraction:.0%})"
            )

        reasons.append(f"dir={direction} tx={tx:.0f}px ty={ty:.0f}px")

    # ── Check overlap range ───────────────────────────────────────────────
    overlap_frac = overlap_pct / 100.0
    if direction not in ("none",):
        if overlap_frac < cfg.overlap_min:
            failures.append(
                f"overlap={overlap_pct:.0f}% < {cfg.overlap_min*100:.0f}% (frames too far apart)"
            )
        elif overlap_frac > cfg.overlap_max:
            failures.append(
                f"overlap={overlap_pct:.0f}% > {cfg.overlap_max*100:.0f}% (frames too similar)"
            )
        else:
            reasons.append(f"overlap={overlap_pct:.0f}%✓")

    # ── Final decision ────────────────────────────────────────────────────
    is_pano = len(failures) == 0 and direction not in ("none", "ambiguous")

    # Confidence: starts at 1.0, penalised by the severity of each failure
    confidence = 1.0
    for f in failures:
        confidence *= 0.5   # each failure halves confidence
    if direction == "ambiguous":
        confidence *= 0.7

    reason_str = " | ".join(reasons + [f"✗ {f}" for f in failures])
    return is_pano, direction, overlap_pct, min(confidence, 1.0), reason_str


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_panoramic_overlap(
    path_a:   Path,
    path_b:   Path,
    cfg:      PanoCheckConfig | None = None,
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
    gray_a = _load_resized(path_a, cfg.analysis_width)
    gray_b = _load_resized(path_b, cfg.analysis_width)

    if gray_a is None or gray_b is None:
        result.reason = "could not load one or both images"
        result.confidence = 0.0
        return result

    img_h, img_w = gray_a.shape

    # ── Feature detection and matching ────────────────────────────────────
    orb = cv2.ORB_create(nfeatures=cfg.orb_n_features)
    kp_a, des_a = orb.detectAndCompute(gray_a, None)
    kp_b, des_b = orb.detectAndCompute(gray_b, None)

    if des_a is None or des_b is None or len(des_a) < 4 or len(des_b) < 4:
        result.reason = (
            f"too few features: A={len(kp_a) if kp_a else 0} "
            f"B={len(kp_b) if kp_b else 0}"
        )
        result.confidence = 0.1
        return result

    matcher   = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    raw       = matcher.knnMatch(des_a, des_b, k=2)
    good      = [m for m, n in raw if m.distance < cfg.lowe_ratio * n.distance]

    log.debug(
        f"  {path_a.name}↔{path_b.name}: "
        f"{len(kp_a)} kp_A / {len(kp_b)} kp_B / {len(good)} good matches"
    )

    if len(good) < cfg.min_matches:
        result.reason      = f"too few good matches ({len(good)} < {cfg.min_matches})"
        result.confidence  = 0.2
        return result

    # ── RANSAC homography ─────────────────────────────────────────────────
    pts_a = np.float32([kp_a[m.queryIdx].pt for m in good])
    pts_b = np.float32([kp_b[m.trainIdx].pt for m in good])
    H, mask = cv2.findHomography(pts_a, pts_b, cv2.RANSAC, ransacReprojThreshold=5.0)

    if H is None or mask is None:
        result.reason     = f"RANSAC failed ({len(good)} matches)"
        result.confidence = 0.2
        return result

    n_inliers = int(mask.sum())
    result.inliers = n_inliers

    if n_inliers < cfg.min_inliers:
        result.reason     = f"too few inliers ({n_inliers} < {cfg.min_inliers})"
        result.confidence = 0.3
        return result

    # ── Homography analysis ───────────────────────────────────────────────
    is_pano, direction, overlap_pct, confidence, reason = _analyse_homography(
        H, img_w, img_h, cfg
    )

    # Scale confidence by inlier ratio (more inliers = more trustworthy H)
    inlier_ratio = n_inliers / max(len(good), 1)
    confidence   = confidence * (0.5 + 0.5 * inlier_ratio)

    result.is_panoramic_overlap = is_pano
    result.direction            = direction
    result.overlap_pct          = round(overlap_pct, 1)
    result.confidence           = round(confidence, 3)
    result.reason               = reason

    log.debug(f"  → {result}")
    return result
