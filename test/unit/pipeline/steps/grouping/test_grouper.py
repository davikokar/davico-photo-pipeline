"""
Test grouper logic with simulated ExifData — no real files needed.

Covers:
  - Single shot
  - HDR bracket (3 shots)
  - HDR bracket (5 shots)
  - Panorama (no HDR)
  - HDR + Panorama (3 positions × 3 shots)
  - Mixed session (all of the above together)
"""

import tempfile
from datetime import datetime
from pathlib import Path

from pipeline.utils.exif import ExifData
from pipeline.utils.logger import get_logger
from pipeline.state import SessionState, GroupType
from pipeline.steps.grouping.grouper import (
    _form_brackets,
    _form_panorama_groups,
    _round_to_third,
    grouping_report,
    EV_VARIATION_THRESHOLD,
    MAX_HDR_GAP,
    MAX_PANO_GAP,
    FOCAL_LENGTH_TOLERANCE,
    PanoramaGroup,
)

logger = get_logger("test_grouper")


# ---------------------------------------------------------------------------
# Helpers to build fake ExifData
# ---------------------------------------------------------------------------

BASE_TIME = datetime(2024, 6, 15, 10, 0, 0).timestamp()

def make_shot(
    name: str,
    t: float,           # seconds offset from BASE_TIME
    ev: float = 0.0,
    focal: float = 24.0,
) -> ExifData:
    ts = datetime.fromtimestamp(BASE_TIME + t)
    return ExifData(
        path          = Path(f"/fake/{name}"),
        timestamp     = ts,
        timestamp_sub = 0.0,
        focal_length  = focal,
        aperture      = 8.0,
        shutter       = 2 ** (-ev),   # reverse-engineer shutter from EV (simplified)
        iso           = 100,
        ev_computed   = ev,
    )


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

def test_single():
    shots = [make_shot("IMG_001.jpg", t=0)]
    brackets = _form_brackets(shots, MAX_HDR_GAP)
    groups   = _form_panorama_groups(brackets, MAX_PANO_GAP, FOCAL_LENGTH_TOLERANCE)
    assert len(groups) == 1
    assert groups[0].group_type == GroupType.SINGLE
    logger.info("✅ test_single passed")


def test_hdr_3():
    shots = [
        make_shot("IMG_001.jpg", t=0.0, ev=-2),
        make_shot("IMG_002.jpg", t=0.4, ev= 0),
        make_shot("IMG_003.jpg", t=0.8, ev=+2),
    ]
    brackets = _form_brackets(shots, MAX_HDR_GAP)
    groups   = _form_panorama_groups(brackets, MAX_PANO_GAP, FOCAL_LENGTH_TOLERANCE)
    assert len(groups) == 1
    assert groups[0].group_type == GroupType.HDR
    assert groups[0].brackets[0].ev_spread >= EV_VARIATION_THRESHOLD
    logger.info("✅ test_hdr_3 passed")


def test_hdr_5():
    shots = [
        make_shot("IMG_001.jpg", t=0.0, ev=-4),
        make_shot("IMG_002.jpg", t=0.4, ev=-2),
        make_shot("IMG_003.jpg", t=0.8, ev= 0),
        make_shot("IMG_004.jpg", t=1.2, ev=+2),
        make_shot("IMG_005.jpg", t=1.6, ev=+4),
    ]
    brackets = _form_brackets(shots, MAX_HDR_GAP)
    groups   = _form_panorama_groups(brackets, MAX_PANO_GAP, FOCAL_LENGTH_TOLERANCE)
    assert len(groups) == 1
    assert groups[0].group_type == GroupType.HDR
    assert len(groups[0].all_shots) == 5
    logger.info("✅ test_hdr_5 passed")


def test_panorama_single_shots():
    """4 single shots forming a panorama (no HDR at each position)."""
    shots = [
        make_shot("IMG_001.jpg", t= 0, ev=0, focal=24),
        make_shot("IMG_002.jpg", t= 5, ev=0, focal=24),
        make_shot("IMG_003.jpg", t=10, ev=0, focal=24),
        make_shot("IMG_004.jpg", t=15, ev=0, focal=24),
    ]
    brackets = _form_brackets(shots, MAX_HDR_GAP)
    groups   = _form_panorama_groups(brackets, MAX_PANO_GAP, FOCAL_LENGTH_TOLERANCE)
    assert len(groups) == 1
    assert groups[0].group_type == GroupType.PANORAMA
    assert len(groups[0].brackets) == 4
    logger.info("✅ test_panorama_single_shots passed")


def test_hdr_panorama():
    """3 panorama positions × 3 HDR shots each."""
    shots = [
        # Position 1
        make_shot("IMG_001.jpg", t= 0.0, ev=-2, focal=24),
        make_shot("IMG_002.jpg", t= 0.4, ev= 0, focal=24),
        make_shot("IMG_003.jpg", t= 0.8, ev=+2, focal=24),
        # Position 2 (gap ~5s)
        make_shot("IMG_004.jpg", t= 6.0, ev=-2, focal=24),
        make_shot("IMG_005.jpg", t= 6.4, ev= 0, focal=24),
        make_shot("IMG_006.jpg", t= 6.8, ev=+2, focal=24),
        # Position 3 (gap ~5s)
        make_shot("IMG_007.jpg", t=12.0, ev=-2, focal=24),
        make_shot("IMG_008.jpg", t=12.4, ev= 0, focal=24),
        make_shot("IMG_009.jpg", t=12.8, ev=+2, focal=24),
    ]
    brackets = _form_brackets(shots, MAX_HDR_GAP)
    groups   = _form_panorama_groups(brackets, MAX_PANO_GAP, FOCAL_LENGTH_TOLERANCE)
    assert len(groups) == 1
    assert groups[0].group_type == GroupType.HDR_PANORAMA
    assert len(groups[0].brackets) == 3
    assert len(groups[0].all_shots) == 9
    logger.info("✅ test_hdr_panorama passed")


def test_focal_length_break():
    """Different focal lengths → separate groups."""
    shots = [
        make_shot("IMG_001.jpg", t= 0, ev=0, focal=24),
        make_shot("IMG_002.jpg", t= 5, ev=0, focal=24),
        make_shot("IMG_003.jpg", t=10, ev=0, focal=50),  # different lens!
        make_shot("IMG_004.jpg", t=15, ev=0, focal=50),
    ]
    brackets = _form_brackets(shots, MAX_HDR_GAP)
    groups   = _form_panorama_groups(brackets, MAX_PANO_GAP, FOCAL_LENGTH_TOLERANCE)
    assert len(groups) == 2, f"Expected 2 groups, got {len(groups)}"
    logger.info("✅ test_focal_length_break passed")


def test_time_break():
    """Long gap → separate groups."""
    shots = [
        make_shot("IMG_001.jpg", t=  0, ev=-2, focal=24),
        make_shot("IMG_002.jpg", t=0.4, ev= 0, focal=24),
        make_shot("IMG_003.jpg", t=0.8, ev=+2, focal=24),
        # 10 minute gap → new scene
        make_shot("IMG_004.jpg", t=600, ev=-2, focal=24),
        make_shot("IMG_005.jpg", t=600.4, ev= 0, focal=24),
        make_shot("IMG_006.jpg", t=600.8, ev=+2, focal=24),
    ]
    brackets = _form_brackets(shots, MAX_HDR_GAP)
    groups   = _form_panorama_groups(brackets, MAX_PANO_GAP, FOCAL_LENGTH_TOLERANCE)
    assert len(groups) == 2
    assert groups[0].group_type == GroupType.HDR
    assert groups[1].group_type == GroupType.HDR
    logger.info("✅ test_time_break passed")


def test_mixed_session():
    """Realistic mixed session: single + HDR + panorama + HDR_panorama."""
    shots = [
        # Single shot
        make_shot("IMG_001.jpg", t=0, ev=0, focal=50),

        # Gap 2 min → HDR 3 shots
        make_shot("IMG_002.jpg", t=120.0, ev=-2, focal=24),
        make_shot("IMG_003.jpg", t=120.4, ev= 0, focal=24),
        make_shot("IMG_004.jpg", t=120.8, ev=+2, focal=24),

        # Gap 5 min → Panorama 3 positions (no HDR)
        make_shot("IMG_005.jpg", t=420, ev=0, focal=24),
        make_shot("IMG_006.jpg", t=425, ev=0, focal=24),
        make_shot("IMG_007.jpg", t=430, ev=0, focal=24),

        # Gap 10 min → HDR Panorama 2 positions × 3 shots
        make_shot("IMG_008.jpg", t=1020.0, ev=-2, focal=24),
        make_shot("IMG_009.jpg", t=1020.4, ev= 0, focal=24),
        make_shot("IMG_010.jpg", t=1020.8, ev=+2, focal=24),
        make_shot("IMG_011.jpg", t=1026.0, ev=-2, focal=24),
        make_shot("IMG_012.jpg", t=1026.4, ev= 0, focal=24),
        make_shot("IMG_013.jpg", t=1026.8, ev=+2, focal=24),
    ]
    brackets = _form_brackets(shots, MAX_HDR_GAP)
    groups   = _form_panorama_groups(brackets, MAX_PANO_GAP, FOCAL_LENGTH_TOLERANCE)

    assert len(groups) == 4, f"Expected 4 groups, got {len(groups)}"
    assert groups[0].group_type == GroupType.SINGLE
    assert groups[1].group_type == GroupType.HDR
    assert groups[2].group_type == GroupType.PANORAMA
    assert groups[3].group_type == GroupType.HDR_PANORAMA

    print(grouping_report(groups))
    logger.info("✅ test_mixed_session passed")


# ---------------------------------------------------------------------------
# Run all tests
# ---------------------------------------------------------------------------

def test_with_state():
    """Verify that groups are correctly registered in SessionState."""
    shots = [
        make_shot("IMG_001.jpg", t=0.0, ev=-2, focal=24),
        make_shot("IMG_002.jpg", t=0.4, ev= 0, focal=24),
        make_shot("IMG_003.jpg", t=0.8, ev=+2, focal=24),
        make_shot("IMG_004.jpg", t=6.0, ev=-2, focal=24),
        make_shot("IMG_005.jpg", t=6.4, ev= 0, focal=24),
        make_shot("IMG_006.jpg", t=6.8, ev=+2, focal=24),
    ]
    brackets = _form_brackets(shots, MAX_HDR_GAP)
    groups   = _form_panorama_groups(brackets, MAX_PANO_GAP, FOCAL_LENGTH_TOLERANCE)

    with tempfile.TemporaryDirectory() as tmp:
        state = SessionState(workspace=Path(tmp), input_dir="/fake")
        for i, pg in enumerate(groups):
            gid = f"group_{i+1:03d}"
            state.add_group(gid, [s.path.name for s in pg.all_shots], pg.group_type)
            state.step_done(gid, "grouping")

        assert len(state.all_groups()) == 1
        g = state.all_groups()[0]
        assert g["type"] == GroupType.HDR_PANORAMA
        assert len(g["files"]) == 6
        print(state.summary())

    logger.info("✅ test_with_state passed")



def test_long_exposure_hdr():
    """
    HDR bracket where the base exposure is 30s (night shot).
    Without exposure-aware gap calculation this would be incorrectly
    split into 3 separate groups instead of 1 HDR bracket.

    Timeline:
      shot 1: starts t=0,    shutter=30s  → ends t=30
      shot 2: starts t=30.5, shutter=1s   → ends t=31.5
      shot 3: starts t=32,   shutter=0.5s → ends t=32.5

    Gap (end→start):  shot1→shot2 = 0.5s  ✓
                      shot2→shot3 = 0.5s  ✓
    """
    from pipeline.utils.exif import ExifData
    from datetime import datetime

    BASE = datetime(2024, 6, 15, 22, 0, 0).timestamp()

    def make_night_shot(name, t, shutter, ev):
        return ExifData(
            path          = Path(f"/fake/{name}"),
            timestamp     = datetime.fromtimestamp(BASE + t),
            timestamp_sub = 0.0,
            focal_length  = 24.0,
            aperture      = 8.0,
            shutter       = shutter,
            iso           = 100,
            ev_computed   = ev,
        )

    shots = [
        make_night_shot("IMG_001.jpg", t=0,    shutter=30.0, ev=-2),
        make_night_shot("IMG_002.jpg", t=30.5, shutter=1.0,  ev= 0),
        make_night_shot("IMG_003.jpg", t=32.0, shutter=0.5,  ev=+2),
    ]

    brackets = _form_brackets(shots, MAX_HDR_GAP)
    groups   = _form_panorama_groups(brackets, MAX_PANO_GAP, FOCAL_LENGTH_TOLERANCE)

    assert len(groups) == 1, (
        f"Expected 1 HDR group (long exposures), got {len(groups)} — "
        "gap calculation is not exposure-aware"
    )
    assert groups[0].group_type == GroupType.HDR
    assert len(groups[0].all_shots) == 3
    logger.info("✅ test_long_exposure_hdr passed")


# ---------------------------------------------------------------------------
# Step offset tests
# ---------------------------------------------------------------------------

def test_round_to_third():
    """_round_to_third rounds to nearest 1/3 stop."""
    assert _round_to_third(0.0) == 0.0
    assert _round_to_third(2.0) == 2.0
    assert _round_to_third(-2.0) == -2.0
    assert _round_to_third(1.0) == 1.0
    assert _round_to_third(0.33) == 0.33
    assert _round_to_third(0.34) == 0.33
    assert _round_to_third(0.5) == 0.67   # 0.5 rounds to 2/3 (banker's rounding)
    assert _round_to_third(0.6) == 0.67
    assert _round_to_third(-0.6) == -0.67
    assert _round_to_third(2.3) == 2.33
    assert _round_to_third(-2.3) == -2.33
    assert _round_to_third(2.7) == 2.67
    logger.info("✅ test_round_to_third passed")


def test_step_offsets_hdr_3():
    """3-shot HDR bracket: -2, 0, +2 → offsets -2.0, 0.0, +2.0."""
    shots = [
        make_shot("IMG_001.jpg", t=0.0, ev=-2),
        make_shot("IMG_002.jpg", t=0.4, ev= 0),
        make_shot("IMG_003.jpg", t=0.8, ev=+2),
    ]
    brackets = _form_brackets(shots, MAX_HDR_GAP)
    assert len(brackets) == 1
    offsets = brackets[0].step_offsets
    assert offsets[0] == {"step_offset":  2.0, "reference_shot": False}
    assert offsets[1] == {"step_offset":  0.0, "reference_shot": True}
    assert offsets[2] == {"step_offset": -2.0, "reference_shot": False}
    logger.info("✅ test_step_offsets_hdr_3 passed")


def test_step_offsets_hdr_5():
    """5-shot HDR bracket: -4, -2, 0, +2, +4 → offsets relative to central."""
    shots = [
        make_shot("IMG_001.jpg", t=0.0, ev=-4),
        make_shot("IMG_002.jpg", t=0.4, ev=-2),
        make_shot("IMG_003.jpg", t=0.8, ev= 0),
        make_shot("IMG_004.jpg", t=1.2, ev=+2),
        make_shot("IMG_005.jpg", t=1.6, ev=+4),
    ]
    brackets = _form_brackets(shots, MAX_HDR_GAP)
    assert len(brackets) == 1
    offsets = brackets[0].step_offsets
    assert offsets[0] == {"step_offset":  4.0, "reference_shot": False}
    assert offsets[1] == {"step_offset":  2.0, "reference_shot": False}
    assert offsets[2] == {"step_offset":  0.0, "reference_shot": True}
    assert offsets[3] == {"step_offset": -2.0, "reference_shot": False}
    assert offsets[4] == {"step_offset": -4.0, "reference_shot": False}
    logger.info("✅ test_step_offsets_hdr_5 passed")


def test_step_offsets_single_shot():
    """Single shot → step_offset=0, reference_shot=True."""
    shots = [make_shot("IMG_001.jpg", t=0.0, ev=0)]
    brackets = _form_brackets(shots, MAX_HDR_GAP)
    offsets = brackets[0].step_offsets
    assert len(offsets) == 1
    assert offsets[0] == {"step_offset": 0.0, "reference_shot": True}
    logger.info("✅ test_step_offsets_single_shot passed")


def test_step_offsets_fractional_ev():
    """HDR with 1/3 stop EV spacing."""
    shots = [
        make_shot("IMG_001.jpg", t=0.0, ev=-1.33),
        make_shot("IMG_002.jpg", t=0.4, ev=-0.67),
        make_shot("IMG_003.jpg", t=0.8, ev= 0.0),
        make_shot("IMG_004.jpg", t=1.2, ev=+0.67),
        make_shot("IMG_005.jpg", t=1.6, ev=+1.33),
    ]
    brackets = _form_brackets(shots, MAX_HDR_GAP)
    offsets = brackets[0].step_offsets
    assert offsets[0]["step_offset"] == 1.33
    assert offsets[1]["step_offset"] == 0.67
    assert offsets[2]["step_offset"] == 0.0
    assert offsets[2]["reference_shot"] is True
    assert offsets[3]["step_offset"] == -0.67
    assert offsets[4]["step_offset"] == -1.33
    logger.info("✅ test_step_offsets_fractional_ev passed")


def test_step_offsets_asymmetric_bracket():
    """Asymmetric bracket: -1, 0, +2 → reference is the median (0)."""
    shots = [
        make_shot("IMG_001.jpg", t=0.0, ev=-1),
        make_shot("IMG_002.jpg", t=0.4, ev= 0),
        make_shot("IMG_003.jpg", t=0.8, ev=+2),
    ]
    brackets = _form_brackets(shots, MAX_HDR_GAP)
    offsets = brackets[0].step_offsets
    # Median EV is 0, so reference is shot at ev=0
    assert offsets[1]["reference_shot"] is True
    assert offsets[0]["step_offset"] == 1.0
    assert offsets[1]["step_offset"] == 0.0
    assert offsets[2]["step_offset"] == -2.0
    logger.info("✅ test_step_offsets_asymmetric_bracket passed")

if __name__ == "__main__":
    tests = [
        test_single,
        test_hdr_3,
        test_hdr_5,
        test_panorama_single_shots,
        test_hdr_panorama,
        test_focal_length_break,
        test_time_break,
        test_mixed_session,
        test_long_exposure_hdr,
        test_with_state,
        test_round_to_third,
        test_step_offsets_hdr_3,
        test_step_offsets_hdr_5,
        test_step_offsets_single_shot,
        test_step_offsets_fractional_ev,
        test_step_offsets_asymmetric_bracket,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            logger.error(f"❌ {t.__name__} FAILED: {e}")
            failed += 1
        except Exception as e:
            logger.error(f"❌ {t.__name__} ERROR: {e}")
            failed += 1

    print(f"\n{'─'*50}")
    print(f"Results: {len(tests)-failed}/{len(tests)} passed")
    if failed:
        sys.exit(1)
