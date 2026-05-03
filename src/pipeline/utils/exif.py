"""
EXIF utility — reads and normalizes metadata from JPEG files.

Provides a consistent ExifData dataclass regardless of how the camera
wrote the original tags. EV is always computed from first principles
(aperture + shutter + ISO) to avoid relying on ExposureBiasValue,
which is often missing or unreliable.
"""

import math
import subprocess
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pipeline.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ExifData:
    path: Path
    timestamp: datetime | None = None  # DateTimeOriginal
    timestamp_sub: float = 0.0  # sub-second offset (0.0 - 0.999)
    focal_length: float | None = None  # mm
    aperture: float | None = None  # f-number
    shutter: float | None = None  # seconds
    iso: int | None = None
    ev_bias: float | None = None  # ExposureBiasValue from EXIF (optional)
    ev_computed: float | None = None  # computed from aperture/shutter/ISO
    width: int | None = None
    height: int | None = None
    camera_make: str = ""
    camera_model: str = ""
    raw: dict = field(default_factory=dict)  # full exiftool output

    @property
    def timestamp_float(self) -> float:
        """Timestamp as Unix float including sub-second precision."""
        if self.timestamp is None:
            return 0.0
        return self.timestamp.timestamp() + self.timestamp_sub

    @property
    def end_time_float(self) -> float:
        """
        Time when the exposure ended: timestamp_float + shutter duration.

        For a 1/1000s shot this is virtually identical to timestamp_float.
        For a 30s night exposure it differs by 30 seconds — which matters
        when determining whether the next shot belongs to the same HDR bracket.
        Falls back to timestamp_float if shutter is unknown.
        """
        shutter = self.shutter or 0.0
        return self.timestamp_float + shutter

    @property
    def ev(self) -> float | None:
        """Best available EV estimate: prefer computed, fallback to bias."""
        return self.ev_computed if self.ev_computed is not None else self.ev_bias


# ---------------------------------------------------------------------------
# EV computation
# ---------------------------------------------------------------------------


def compute_ev(aperture: float, shutter: float, iso: int) -> float:
    """
    Compute Exposure Value (EV at ISO 100) from camera settings.

    Formula: EV100 = log2(aperture² / shutter) - log2(ISO / 100)

    Args:
        aperture: f-number (e.g. 8.0)
        shutter:  exposure time in seconds (e.g. 0.001)
        iso:      ISO value (e.g. 400)

    Returns:
        EV100 as float.
    """
    if shutter <= 0 or aperture <= 0 or iso <= 0:
        return 0.0
    ev100 = math.log2(aperture**2 / shutter) - math.log2(iso / 100)
    return round(ev100, 2)


# ---------------------------------------------------------------------------
# EXIF reader — uses exiftool via subprocess
# ---------------------------------------------------------------------------


def _get_exiftool_path(config: dict | None = None) -> str:
    """
    Get the exiftool executable path from config or fallback to PATH.

    Args:
        config: Pipeline configuration dict. If provided, looks for
                config['grouper']['exitool_exe'] (note the typo in config key).

    Returns:
        Path string to exiftool executable, or "exiftool" to use PATH.
    """
    if config and "grouper" in config:
        exiftool_path = config.get("grouper", {}).get("exitool_exe")
        if exiftool_path:
            return exiftool_path
    return "exiftool"


def read_exif(path: Path, config: dict | None = None) -> ExifData | None:
    """
    Read EXIF metadata from a JPEG file using exiftool.

    Falls back gracefully if exiftool is not installed (returns ExifData
    with only path filled in — the grouper will treat it as unknown).

    Args:
        path: Path to JPEG file.
        config: Optional pipeline configuration dict with exiftool path.

    Returns:
        ExifData instance, or None if file cannot be read.
    """
    path = Path(path)
    if not path.exists():
        logger.error(f"File not found: {path}")
        return None

    try:
        exiftool_exe = _get_exiftool_path(config)
        result = subprocess.run(
            [
                exiftool_exe,
                "-json",
                "-n",  # -n: numeric output (no units)
                "-DateTimeOriginal",
                "-SubSecTimeOriginal",
                "-FocalLength",
                "-FNumber",
                "-ExposureTime",
                "-ISO",
                "-ExposureBiasValue",
                "-ImageWidth",
                "-ImageHeight",
                "-Make",
                "-Model",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            logger.warning(f"exiftool error for {path.name}: {result.stderr.strip()}")
            return ExifData(path=path)

        data = json.loads(result.stdout)[0]

    except FileNotFoundError:
        # exiftool not installed — use Pillow as fallback
        logger.warning(
            "exiftool not found, falling back to Pillow (limited EXIF support)"
        )
        return _read_exif_pillow(path)
    except Exception as e:
        logger.warning(f"Failed to read EXIF from {path.name}: {e}")
        return ExifData(path=path)

    # --- Parse timestamp ---
    timestamp = None
    ts_str = data.get("DateTimeOriginal")
    if ts_str:
        try:
            timestamp = datetime.strptime(str(ts_str), "%Y:%m:%d %H:%M:%S")
        except ValueError:
            pass

    sub_sec = 0.0
    sub_str = data.get("SubSecTimeOriginal")
    if sub_str:
        try:
            sub_sec = float(f"0.{str(sub_str).strip()}")
        except ValueError:
            pass

    # --- Numeric fields ---
    focal = _to_float(data.get("FocalLength"))
    fnum = _to_float(data.get("FNumber"))
    shutter = _to_float(data.get("ExposureTime"))
    iso = _to_int(data.get("ISO"))
    ev_bias = _to_float(data.get("ExposureBiasValue"))

    # --- Compute EV from first principles ---
    ev_computed = None
    if fnum and shutter and iso:
        ev_computed = compute_ev(fnum, shutter, iso)

    return ExifData(
        path=path,
        timestamp=timestamp,
        timestamp_sub=sub_sec,
        focal_length=focal,
        aperture=fnum,
        shutter=shutter,
        iso=iso,
        ev_bias=ev_bias,
        ev_computed=ev_computed,
        width=_to_int(data.get("ImageWidth")),
        height=_to_int(data.get("ImageHeight")),
        camera_make=data.get("Make", ""),
        camera_model=data.get("Model", ""),
        raw=data,
    )


def _read_exif_pillow(path: Path) -> ExifData:
    """Minimal EXIF fallback using Pillow (no sub-second, limited tags)."""
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS

        img = Image.open(path)
        info = img._getexif() or {}
        tag = {TAGS.get(k, k): v for k, v in info.items()}

        ts = None
        ts_str = tag.get("DateTimeOriginal")
        if ts_str:
            try:
                ts = datetime.strptime(ts_str, "%Y:%m:%d %H:%M:%S")
            except ValueError:
                pass

        fnum = _ratio_to_float(tag.get("FNumber"))
        shutter = _ratio_to_float(tag.get("ExposureTime"))
        iso = tag.get("ISOSpeedRatings")
        if isinstance(iso, tuple):
            iso = iso[0]

        ev_computed = None
        if fnum and shutter and iso:
            ev_computed = compute_ev(fnum, float(shutter), int(iso))

        return ExifData(
            path=path,
            timestamp=ts,
            focal_length=_ratio_to_float(tag.get("FocalLength")),
            aperture=fnum,
            shutter=shutter,
            iso=int(iso) if iso else None,
            ev_computed=ev_computed,
            width=tag.get("ExifImageWidth") or img.width,
            height=tag.get("ExifImageHeight") or img.height,
            camera_make=tag.get("Make", ""),
            camera_model=tag.get("Model", ""),
        )
    except Exception as e:
        logger.warning(f"Pillow EXIF fallback failed for {path.name}: {e}")
        return ExifData(path=path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _ratio_to_float(v) -> float | None:
    """Handle IFDRational or tuple (num, den) from Pillow."""
    if v is None:
        return None
    if hasattr(v, "numerator"):
        return float(v)
    if isinstance(v, tuple) and len(v) == 2 and v[1] != 0:
        return v[0] / v[1]
    return _to_float(v)


def read_folder(
    folder: Path, extensions: tuple = (".jpg", ".jpeg"), config: dict | None = None
) -> list[ExifData]:
    """
    Read EXIF for all matching files in a folder, sorted by timestamp.

    Files with no timestamp are sorted to the end by filename.

    Args:
        folder: Directory containing JPEG files.
        extensions: Tuple of file extensions to match (default: .jpg, .jpeg).
        config: Optional pipeline configuration dict with exiftool path.
    """
    folder = Path(folder)
    files = sorted(f for f in folder.iterdir() if f.suffix.lower() in extensions)

    logger.info(f"Reading EXIF from {len(files)} files in {folder}")
    results = []
    for f in files:
        exif = read_exif(f, config=config)
        if exif:
            results.append(exif)

    # Sort: files with timestamp first (by timestamp), then by filename
    results.sort(
        key=lambda e: (
            e.timestamp is None,
            e.timestamp_float if e.timestamp else 0,
            e.path.name,
        )
    )

    return results
