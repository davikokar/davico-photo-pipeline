"""
source_classifier — determines if a group is aerial or terrestrial.

Auto-classifies based on camera make metadata:
  - DJI → aerial (drone, no RAW files available)
  - Canon → terrestrial (DSLR/mirrorless, full RAW pipeline)

Unknown makers default to terrestrial (conservative — full pipeline).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pipeline.state import CaptureSource
from pipeline.utils.logger import get_logger

if TYPE_CHECKING:
    from pipeline.utils.exif import ExifData

logger = get_logger(__name__)

MAKE_TO_SOURCE: dict[str, CaptureSource] = {
    "dji": CaptureSource.AERIAL,
    "canon": CaptureSource.TERRESTRIAL,
}


def classify_shot(exif: ExifData) -> CaptureSource:
    """Classify a single shot based on its camera make."""
    make = (exif.camera_make or "").strip().lower()
    for key, source in MAKE_TO_SOURCE.items():
        if key in make:
            return source
    return CaptureSource.TERRESTRIAL


def classify_group(shots: list[ExifData]) -> CaptureSource:
    """Classify a group by majority vote across all shots.

    Returns TERRESTRIAL for empty groups or unknown makers.
    Logs a warning if shots disagree.
    """
    if not shots:
        return CaptureSource.TERRESTRIAL

    classifications = [classify_shot(s) for s in shots]
    aerial_count = sum(1 for c in classifications if c == CaptureSource.AERIAL)
    terrestrial_count = len(classifications) - aerial_count

    if aerial_count > 0 and terrestrial_count > 0:
        logger.warning(
            f"Mixed-maker group ({aerial_count} aerial, {terrestrial_count} terrestrial) "
            f"— using majority vote"
        )

    if aerial_count > terrestrial_count:
        return CaptureSource.AERIAL
    return CaptureSource.TERRESTRIAL
