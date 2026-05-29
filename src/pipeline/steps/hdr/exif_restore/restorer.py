"""EXIF restoration worker — copies metadata from source images to targets via exiftool."""

from __future__ import annotations

import subprocess
from pathlib import Path

from pipeline.utils.logger import get_logger

logger = get_logger(__name__)


def copy_exif_tags(
    source: Path,
    target: Path,
    exiftool_exe: str,
    log=None,
) -> bool:
    """Copy all EXIF/metadata tags from *source* to *target* using exiftool.

    :param Path source: Image whose EXIF metadata should be copied
    :param Path target: Image that will receive the metadata
    :param str exiftool_exe: Path to exiftool executable
    :param log: Optional logger
    :return: ``True`` if the copy succeeded
    """
    log = log or logger
    try:
        result = subprocess.run(
            [
                exiftool_exe,
                "-TagsFromFile", str(source),
                "-all:all",
                # OpenCV bakes EXIF rotation into the pixel data, so the
                # aligned file's actual dimensions differ from the original's
                # EXIF dimension fields. Force Orientation=Normal and exclude
                # dimension tags so they aren't overwritten with wrong values.
                "-Orientation=1",
                "--ExifImageWidth",
                "--ExifImageHeight",
                "--RelatedImageWidth",
                "--RelatedImageHeight",
                "-overwrite_original",
                str(target),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            log.warning(
                "exif_restore: copy failed %s -> %s: %s",
                source.name, target.name, result.stderr.strip(),
            )
            return False

        log.debug("exif_restore: copied %s -> %s", source.name, target.name)
        return True

    except FileNotFoundError:
        log.error(
            "exif_restore: exiftool not found at '%s'", exiftool_exe,
        )
        return False
    except Exception as exc:
        log.warning(
            "exif_restore: error %s -> %s: %s",
            source.name, target.name, exc,
        )
        return False
