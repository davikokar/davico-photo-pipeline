"""
ghost_detector.py — detects ghosting in HDR brackets.

Uses per-pixel variance across aligned frames: pixels where luminance
varies anomalously between exposures (after normalizing for EV difference)
are flagged as ghost candidates.

Normalization strategy
----------------------
When EV values are available (from EXIF), each frame is scaled by
2^(ev_ref - ev_shot) so that a static pixel has the same value across
all frames regardless of exposure difference. ev_ref is the median EV
of the bracket (usually the 0 EV frame).

When EV values are NOT available, falls back to dividing each frame by
its own median luminance (the original heuristic). This is less precise
on scenes with large bright or dark areas but still works in most cases.

Output files (written to mask_dir if provided)
----------------------------------------------
  ghost_mask.png      — binary mask: white = ghost area, black = clean
  ghost_heatmap.png   — false-colour variance map (blue→red) overlaid
                        at 50% opacity on the middle exposure frame,
                        with ghost mask boundary drawn in yellow.

Returns a GhostReport with:
  - has_ghosts:     bool
  - ghost_area_pct: float  (% of image area affected)
  - severity:       'none' | 'low' | 'medium' | 'high'
  - mask_path:      Path | None  (ghost_mask.png)
  - heatmap_path:   Path | None  (ghost_heatmap.png)
  - notes:          list[str]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from pipeline.steps.hdr.image_aligner import align_images_ecc
from pipeline.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

GHOST_AREA_MIN_PCT  = 0.3   # % of image area to consider "has ghosts"
VARIANCE_PERCENTILE = 96.0  # variance percentile used as threshold
DILATE_PX           = 12    # dilation radius to close gaps in mask

# Minimum absolute variance for a pixel to qualify as a ghost candidate.
# The percentile threshold is relative and always flags the top X% of pixels
# even when all variances are tiny (pure JPEG quantization noise).
# This floor ensures a pixel must have genuinely anomalous variance.
# Derivation: 8-bit quant error ~±0.5 LSB; at -2 EV (x4 normalization)
# this becomes ~±2 LSB -> variance ~4-8. Real motion produces variance > 50.
# A floor of 20 safely separates quantization noise from real ghosts.
ABS_VARIANCE_FLOOR  = 80.0


# ---------------------------------------------------------------------------
# Report dataclass
# ---------------------------------------------------------------------------

@dataclass
class GhostReport:
    has_ghosts:     bool            = False
    ghost_area_pct: float           = 0.0
    severity:       str             = "none"   # none | low | medium | high
    mask_path:      Optional[Path]  = None     # ghost_mask.png
    heatmap_path:   Optional[Path]  = None     # ghost_heatmap.png
    notes:          list[str]       = field(default_factory=list)

    def __str__(self):
        if not self.has_ghosts:
            return "No ghost detected"
        return (f"Ghost detected — severity={self.severity}, "
                f"area={self.ghost_area_pct:.1f}%"
                + (f", mask={self.mask_path.name}" if self.mask_path else ""))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _severity(pct: float) -> str:
    if pct < GHOST_AREA_MIN_PCT: return "none"
    if pct < 2.0:                return "low"
    if pct < 6.0:                return "medium"
    return "high"



# Pixels outside this range are excluded from variance computation.
# Below: noise / sensor black level. Above: clipped highlights.
# HDR brackets by design have clipped highlights in the bright frame
# and crushed shadows in the dark frame — these are not ghosts.
_VALID_MIN = 8
_VALID_MAX = 248


def _normalize_ev(
    grays:     list[np.ndarray],          # float32 greyscale, already aligned
    ev_values: list[float] | None,
    color_imgs: list[np.ndarray] | None,  # original BGR imgs (for validity check)
) -> tuple[list[np.ndarray], np.ndarray]:
    """
    Scale each frame to a common reference exposure and return a
    validity mask of pixels usable for ghost detection.

    Why clipping is critical for HDR brackets
    ------------------------------------------
    A +2 EV frame exposes shadows but clips highlights (values → 255).
    After scaling back to the reference exposure, those clipped pixels
    would read as ~64 instead of their true value (e.g. 200), creating
    huge apparent variance — a false ghost covering all highlights.

    Why validity must be checked on COLOR channels, not greyscale
    --------------------------------------------------------------
    A pixel with R=255 (clipped) + G=100 + B=50 can have greyscale ≈ 180
    (well within the valid range), but its EV-normalized greyscale will be
    wrong because the R channel was truncated. Checking validity on the
    greyscale alone misses these partially-saturated pixels.
    Solution: require ALL color channels in ALL frames to be in [_VALID_MIN,
    _VALID_MAX]. This is stricter but physically correct.

    Strategy A — EV from EXIF (preferred):
        scale = 2^(ev_ref - ev_shot), ev_ref = median EV of the bracket.
        Physically correct; approximate on JPEG (gamma-encoded) but much
        better than median for skewed scenes (night sky, snow, backlit).

    Strategy B — median fallback (no EV data):
        Normalize by per-frame median of valid pixels.

    Returns:
        (normalized_grays, valid_mask)
        valid_mask: bool array (H, W), True where all channels in all frames
                    are in [_VALID_MIN, _VALID_MAX].
    """
    # Build validity mask on COLOR channels (not greyscale).
    # This catches partially-saturated pixels that look fine in greyscale
    # but have corrupted EV normalization due to per-channel clipping.
    if color_imgs and len(color_imgs) == len(grays):
        valid = np.ones(grays[0].shape, dtype=bool)
        for img in color_imgs:
            # img is BGR uint8 or float32; check all 3 channels
            valid &= np.all(img >= _VALID_MIN, axis=2)
            valid &= np.all(img <= _VALID_MAX, axis=2)
    else:
        # Fallback: greyscale validity (less precise but always available)
        valid = np.ones(grays[0].shape, dtype=bool)
        for gray in grays:
            valid &= (gray >= _VALID_MIN) & (gray <= _VALID_MAX)

    if ev_values and len(ev_values) == len(grays):
        ev_ref = float(np.median(ev_values))
        normalized = [gray * (2.0 ** (ev_ref - ev)) for gray, ev in zip(grays, ev_values)]
        return normalized, valid

    # Fallback: per-frame median on valid pixels only
    result = []
    for gray in grays:
        valid_px = gray[valid]
        median   = float(np.median(valid_px)) if valid_px.size > 0 else 1.0
        result.append(gray / median if median > 0 else gray)
    return result, valid


def _save_outputs(
    mask:       np.ndarray,    # binary uint8 (0/1)
    variance:   np.ndarray,    # float32 (H, W)
    middle_img: np.ndarray,    # BGR reference frame
    mask_dir:   Path,
    middle_img_name: str | None = None,
    log = None,
) -> tuple[Path, Path]:
    """
    Write ghost_mask.png and ghost_heatmap.png.

    ghost_mask.png
        Pure binary image (white = ghost, black = clean).
        Can be used directly as an alpha/compositing mask.

    ghost_heatmap.png
        Human-readable diagnostic:
        - Middle exposure converted to greyscale, dimmed to 55%
        - Variance map coloured with JET colormap (blue→red),
          blended at 60% opacity only on ghost-flagged pixels
          (non-ghost areas remain greyscale so the eye goes straight
          to the hot zones)
        - Ghost contours drawn in yellow for crisp boundary visibility
    """
    mask_dir = Path(mask_dir)
    mask_dir.mkdir(parents=True, exist_ok=True)

    mask_255 = (mask * 255).astype(np.uint8)

    # Build filenames with optional prefix from middle EV filename
    mask_filename = "ghost_mask.png"
    heatmap_filename = "ghost_heatmap.png"
    if middle_img_name:
        mask_filename = f"{middle_img_name}_ghost_mask.png"
        heatmap_filename = f"{middle_img_name}_ghost_heatmap.png"

    # ── ghost_mask.png ───────────────────────────────────────────────────
    mask_path = mask_dir / mask_filename
    cv2.imwrite(str(mask_path), mask_255)
    log.info(f"Ghost mask    → {mask_path}")

    # ── ghost_heatmap.png ────────────────────────────────────────────────
    # Base: greyscale version of middle frame at reduced brightness
    gray_bgr = cv2.cvtColor(
        cv2.cvtColor(middle_img, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR
    )
    base = (gray_bgr * 0.55).astype(np.uint8)

    # Variance layer: normalise WITHIN GHOST PIXELS ONLY so the full
    # JET palette is used for the ghost region (low variance = blue = minor
    # ghost, high variance = red = strong ghost). Pixels outside the ghost
    # mask are set to 0 (blue) but they won't be blended anyway.
    ghost_variance = variance[mask_255 > 0]
    if ghost_variance.size > 0:
        v_min = float(ghost_variance.min())
        v_max = float(ghost_variance.max())
    else:
        v_min, v_max = 0.0, 1.0

    v_norm = np.zeros_like(variance, dtype=np.uint8)
    if v_max > v_min:
        inside = mask_255 > 0
        v_norm[inside] = (
            (variance[inside] - v_min) / (v_max - v_min) * 255
        ).clip(0, 255).astype(np.uint8)

    heat = cv2.applyColorMap(v_norm, cv2.COLORMAP_JET)

    # Blend: colour only the ghost-flagged pixels, leave rest greyscale
    alpha   = mask_255[:, :, np.newaxis].astype(np.float32) / 255.0
    blended = (
        base.astype(np.float32) * (1 - alpha * 0.6)
        + heat.astype(np.float32) * alpha * 0.6
    ).astype(np.uint8)

    # Contour in pure yellow: BGR = (0, 255, 255)
    contours, _ = cv2.findContours(mask_255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(blended, contours, -1, (0, 255, 255), 2)

    heatmap_path = mask_dir / heatmap_filename
    cv2.imwrite(str(heatmap_path), blended)
    log.info(f"Ghost heatmap → {heatmap_path}")

    return mask_path, heatmap_path


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def detect_ghosts(
    jpg_paths:  list[Path],
    ev_values:  list[float] | None = None,
    mask_dir:   Path | None        = None,
    log=None,
) -> GhostReport:
    """
    Detect ghost regions across a set of bracketed JPEG files.

    Algorithm:
      1. Load images and align with AlignMTB
      2. Convert to float32 greyscale
      3. Normalize for EV (EXIF-based if available, median fallback otherwise)
      4. Per-pixel variance across the normalized stack
      5. Threshold at VARIANCE_PERCENTILE → binary ghost mask
      6. Morphological dilation to close small gaps
      7. Optionally save mask + heatmap to mask_dir

    Args:
        jpg_paths:  Bracketed JPEG paths (any order).
        ev_values:  EV value per file, same order as jpg_paths.
                    Use ExifData.ev (computed from aperture/shutter/ISO).
                    Pass None to use median-based fallback.
        mask_dir:   Directory for ghost_mask.png and ghost_heatmap.png.
                    Pass None to skip file output.
        log:        Logger adapter.

    Returns:
        GhostReport
    """
    if log is None:
        log = logger

    if len(jpg_paths) < 2:
        return GhostReport()

    # ── Load ─────────────────────────────────────────────────────────────
    imgs, valid_evs, loaded_paths = [], [], []
    for i, p in enumerate(jpg_paths):
        img = cv2.imread(str(p))
        if img is None:
            log.warning(f"Could not read {p.name} for ghost detection")
            continue
        imgs.append(img)
        loaded_paths.append(p)
        if ev_values and i < len(ev_values):
            valid_evs.append(ev_values[i])

    if len(imgs) < 2:
        return GhostReport(notes=["Not enough readable images"])

    ev_for_norm = valid_evs if len(valid_evs) == len(imgs) else None
    norm_note   = "EV-based normalization" if ev_for_norm else "median fallback (no EV data)"
    log.debug(f"Ghost detection using {norm_note}")

    # Find the jpg_path corresponding to the middle EV value
    middle_filename = None
    if ev_for_norm and len(ev_for_norm) == len(imgs):
        middle_ev = float(np.median(ev_for_norm))
        closest_idx = int(np.argmin(np.abs(np.array(ev_for_norm) - middle_ev)))
        middle_filename = loaded_paths[closest_idx].stem

    # ── Align ─────────────────────────────────────────────────────────────
    # aligned = list(imgs)
    # cv2.createAlignMTB(max_bits=6, exclude_range=4).process(imgs, aligned)

    aligned = align_images_ecc(imgs)

    # ── Greyscale + normalize ─────────────────────────────────────────────
    grays             = [cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32) for img in aligned]
    norm_grays, valid = _normalize_ev(grays, ev_for_norm, color_imgs=aligned)

    # ── Pre-variance blur ─────────────────────────────────────────────────
    # Suppresses JPEG block-DCT noise without smearing real ghost edges.
    norm_grays = [cv2.GaussianBlur(g, (0, 0), sigmaX=1.5) for g in norm_grays]

    # ── Per-pixel variance, restricted to valid range ─────────────────────
    # Zero out clipped/black pixels so they can never exceed the threshold.
    # np.percentile is computed on ALL pixels so zeroing is conservative —
    # it only ensures that clipped zones are never misread as ghost areas.
    stack    = np.stack(norm_grays, axis=0)
    variance = np.where(valid, np.var(stack, axis=0), 0.0)

    # Compute threshold on VALID pixels only.
    # If computed on all pixels (many of which are 0 due to the valid mask),
    # the percentile lands at 0 and flags every non-zero valid pixel —
    # a false positive on any scene with wide EV spread.
    valid_variance = variance[valid]
    if valid_variance.size > 0:
        threshold = np.percentile(valid_variance, VARIANCE_PERCENTILE)
    else:
        threshold = np.inf   # no valid pixels → no ghosts

    # Double gate: top percentile AND above absolute floor.
    # Prevents flagging pure quantization noise when the whole scene is static.
    mask = ((variance > threshold) & (variance > ABS_VARIANCE_FLOOR) & valid).astype(np.uint8)

    if DILATE_PX > 0:
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (DILATE_PX * 2 + 1, DILATE_PX * 2 + 1)
        )
        mask = cv2.dilate(mask, kernel)

    ghost_pct = float(np.count_nonzero(mask)) / mask.size * 100
    severity  = _severity(ghost_pct)

    report = GhostReport(
        has_ghosts     = ghost_pct >= GHOST_AREA_MIN_PCT,
        ghost_area_pct = round(ghost_pct, 2),
        severity       = severity,
        notes          = [norm_note],
    )
    if report.has_ghosts:
        report.notes.append(f"Ghost area: {ghost_pct:.1f}% (severity={severity})")

    # ── Save outputs ──────────────────────────────────────────────────────
    if mask_dir is not None:
        middle_idx = len(aligned) // 2
        report.mask_path, report.heatmap_path = _save_outputs(
            mask       = mask,
            variance   = variance,
            middle_img = aligned[middle_idx],
            mask_dir   = mask_dir,
            middle_img_name = middle_filename,
            log        = log,
        )

    log.info(str(report))
    return report
