"""
test_ghost_detector.py — unit test for ghost_detector.

Usage
-----
Run as-is to use synthetic test images generated on the fly:
    python test/unit/pipeline/steps/hdr/test_ghost_detector.py

To test against your own bracket, set the three paths below:
    SHOT_DARK   = Path(r"C:\foto\IMG_001.jpg")   # -2 EV
    SHOT_MID    = Path(r"C:\foto\IMG_002.jpg")   # 0 EV
    SHOT_BRIGHT = Path(r"C:\foto\IMG_003.jpg")   # +2 EV

Output files (mask + heatmap) are written next to this script in
a folder called  ghost_test_output/ so you can inspect them visually.
"""

import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

# ── Adjust this to point to your bracket if you want to test on real photos ──
SHOT_DARK   = Path(r"C:\temp\pipeline_tests\0H8A4871.JPG")   # e.g. Path(r"C:\foto\IMG_001.jpg")
SHOT_MID    = Path(r"C:\temp\pipeline_tests\0H8A4870.JPG")   # e.g. Path(r"C:\foto\IMG_002.jpg")
SHOT_BRIGHT = Path(r"C:\temp\pipeline_tests\0H8A4872.JPG")   # e.g. Path(r"C:\foto\IMG_003.jpg")
# ─────────────────────────────────────────────────────────────────────────────

sys.path.insert(0, str(Path(__file__).parents[5] / "src"))

from pipeline.steps.hdr.ghost_detector import detect_ghosts, GhostReport, GHOST_AREA_MIN_PCT


OUTPUT_DIR = Path(__file__).parent / "ghost_test_output"


# ---------------------------------------------------------------------------
# Synthetic image builders
# ---------------------------------------------------------------------------

def _make_static_bracket(folder: Path) -> tuple[list[Path], list[float]]:
    """
    Three perfectly static frames at -2 / 0 / +2 EV.
    Same content, just different brightness. Ghost area should be ~0%.

    Greyscale scene (identical value on all BGR channels) avoids partial
    per-channel clipping where e.g. R clips but G/B don't, which would
    produce pixels with corrupted EV-normalization that look like ghosts.

    Brightness ceiling: 62/channel. At +2 EV (×4) max pixel = 248 —
    no pixel clips in any frame, so validity mask covers 100% of the image.
    """
    h, w = 200, 300
    np.random.seed(7)
    # max base value: 248 / 4 = 62  →  at +2 EV (×4) max = 248, never clips
    grey  = np.random.randint(10, 62, (h, w), dtype=np.uint8)
    scene = np.stack([grey, grey, grey], axis=2)   # identical on all channels

    paths, evs = [], [-2.0, 0.0, 2.0]
    for ev in evs:
        scale  = 2.0 ** ev
        bright = np.clip(scene.astype(np.float32) * scale, 0, 255).astype(np.uint8)
        p      = folder / f"static_ev{ev:+.0f}.png"
        cv2.imwrite(str(p), bright)   # PNG: lossless, no compression artifacts
        paths.append(p)

    return paths, evs


def _make_ghost_bracket(folder: Path, ghost_size: int = 40) -> tuple[list[Path], list[float]]:
    """
    Three frames with a moving rectangular object.
    The object shifts 50px right between shots — clear ghost region.
    Ghost area should be well above GHOST_AREA_MIN_PCT.

    Greyscale scene (identical BGR channels), clipping-safe range:
      background: 25-45/ch.  mover: 55/ch.
      At +2 EV (×4): background max = 180, mover = 220.
      All values stay within [8, 248] → entire image valid → clean test.
    """
    h, w = 200, 300
    np.random.seed(42)
    bg    = np.random.randint(25, 45, (h, w), dtype=np.uint8)
    scene = np.stack([bg, bg, bg], axis=2)

    offsets = [20, 70, 120]
    paths, evs = [], [-2.0, 0.0, 2.0]
    for ev, x_off in zip(evs, offsets):
        frame = scene.copy()
        frame[80:80+ghost_size, x_off:x_off+ghost_size] = 55   # mover, safe at +2 EV
        scale  = 2.0 ** ev
        bright = np.clip(frame.astype(np.float32) * scale, 0, 255).astype(np.uint8)
        p      = folder / f"ghost_ev{ev:+.0f}.png"
        cv2.imwrite(str(p), bright)   # PNG: lossless
        paths.append(p)

    return paths, evs


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

def test_no_false_positive_on_static_scene(work_dir: Path):
    print("\n── Test 1: static scene (no ghost expected) ──")
    paths, evs = _make_static_bracket(work_dir)
    out_dir    = OUTPUT_DIR / "test1_static"

    report = detect_ghosts(paths, ev_values=evs, mask_dir=out_dir)
    print(f"  ghost_area_pct : {report.ghost_area_pct:.2f}%")
    print(f"  severity       : {report.severity}")
    print(f"  has_ghosts     : {report.has_ghosts}")
    print(f"  notes          : {report.notes}")

    assert not report.has_ghosts, (
        f"False positive: static scene flagged as ghost "
        f"({report.ghost_area_pct:.2f}% > {GHOST_AREA_MIN_PCT}%)"
    )
    assert report.mask_path    is not None and report.mask_path.exists()
    assert report.heatmap_path is not None and report.heatmap_path.exists()
    print(f"  mask     → {report.mask_path}")
    print(f"  heatmap  → {report.heatmap_path}")
    print("  PASS")


def test_ghost_detected_on_moving_object(work_dir: Path):
    print("\n── Test 2: moving object (ghost expected) ──")
    paths, evs = _make_ghost_bracket(work_dir)
    out_dir    = OUTPUT_DIR / "test2_ghost"

    report = detect_ghosts(paths, ev_values=evs, mask_dir=out_dir)
    print(f"  ghost_area_pct : {report.ghost_area_pct:.2f}%")
    print(f"  severity       : {report.severity}")
    print(f"  has_ghosts     : {report.has_ghosts}")
    print(f"  notes          : {report.notes}")

    assert report.has_ghosts, (
        f"Ghost not detected on moving object "
        f"({report.ghost_area_pct:.2f}%)"
    )
    assert report.mask_path    is not None and report.mask_path.exists()
    assert report.heatmap_path is not None and report.heatmap_path.exists()
    print(f"  mask     → {report.mask_path}")
    print(f"  heatmap  → {report.heatmap_path}")
    print("  PASS")


def test_ev_normalization_vs_median_fallback(work_dir: Path):
    """
    Both strategies should agree on a clean static bracket.
    EV-based normalization should produce lower ghost_area_pct
    because the scaling is physically correct, not statistical.
    """
    print("\n── Test 3: EV-based vs median normalization on static scene ──")
    paths, evs = _make_static_bracket(work_dir)

    report_ev  = detect_ghosts(paths, ev_values=evs,  mask_dir=None)
    report_med = detect_ghosts(paths, ev_values=None,  mask_dir=None)

    print(f"  EV-based  ghost_area_pct : {report_ev.ghost_area_pct:.2f}%")
    print(f"  Median    ghost_area_pct : {report_med.ghost_area_pct:.2f}%")

    assert not report_ev.has_ghosts,  "EV-based: false positive on static scene"
    assert not report_med.has_ghosts, "Median:   false positive on static scene"
    # EV normalization should be at least as good as median on synthetic data
    assert report_ev.ghost_area_pct <= report_med.ghost_area_pct + 0.5, (
        "EV-based normalization unexpectedly worse than median fallback "
        f"({report_ev.ghost_area_pct:.2f}% vs {report_med.ghost_area_pct:.2f}%)"
    )
    print("  PASS")


def test_mask_is_binary(work_dir: Path):
    print("\n── Test 4: mask file is binary (only 0 and 255) ──")
    paths, evs = _make_ghost_bracket(work_dir)
    out_dir    = OUTPUT_DIR / "test4_binary"

    report = detect_ghosts(paths, ev_values=evs, mask_dir=out_dir)
    mask   = cv2.imread(str(report.mask_path), cv2.IMREAD_GRAYSCALE)

    unique = set(mask.flatten().tolist())
    assert unique <= {0, 255}, f"Mask has unexpected values: {unique - {0, 255}}"
    print(f"  unique pixel values: {sorted(unique)}")
    print("  PASS")


def test_on_real_photos():
    """
    Run against real bracket photos.
    Skipped automatically when the hardcoded paths are None.
    """
    print("\n── Test 5: real bracket photos ──")
    if SHOT_DARK is None or SHOT_MID is None or SHOT_BRIGHT is None:
        print("  SKIPPED (set SHOT_DARK / SHOT_MID / SHOT_BRIGHT at top of file)")
        return

    paths = [SHOT_DARK, SHOT_MID, SHOT_BRIGHT]
    evs   = [-2.0, 0.0, 2.0]

    for p in paths:
        assert Path(p).exists(), f"File not found: {p}"

    out_dir = OUTPUT_DIR / "test5_real"
    report  = detect_ghosts(paths, ev_values=evs, mask_dir=out_dir)

    print(f"  ghost_area_pct : {report.ghost_area_pct:.2f}%")
    print(f"  severity       : {report.severity}")
    print(f"  has_ghosts     : {report.has_ghosts}")
    print(f"  mask     → {report.mask_path}")
    print(f"  heatmap  → {report.heatmap_path}")
    print("  PASS (no assertion — inspect outputs visually)")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        test_no_false_positive_on_static_scene(work)
        test_ghost_detected_on_moving_object(work)
        test_ev_normalization_vs_median_fallback(work)
        test_mask_is_binary(work)
        test_on_real_photos()

    print(f"\nAll tests passed.  Output images → {OUTPUT_DIR.resolve()}")
