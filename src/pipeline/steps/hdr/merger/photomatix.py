"""PhotomatixCL command builder and executor.

Pure worker — knows how to construct PhotomatixCL command lines and execute
them via subprocess. Has no awareness of session state or JSON persistence.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from pipeline.utils.logger import get_logger

logger = get_logger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".tif", ".tiff", ".png", ".hdr", ".exr"}


@dataclass(frozen=True)
class PhotomatixSettings:
    exe: Path
    align: bool = True
    reduce_ca: bool = True
    noise_reduction: int = 1
    timeout_sec: int = 600
    xmp_path: Path | None = None


@dataclass(frozen=True)
class MergeRequest:
    source_files: list[Path]
    output_dir: Path
    style: str
    style_params: dict
    source_set: str


def build_photomatix_command(
    settings: PhotomatixSettings,
    request: MergeRequest,
) -> list[str]:
    """Build the full PhotomatixCL command-line argument list.

    :param PhotomatixSettings settings: Global PhotomatixCL settings
    :param MergeRequest request: Parameters for this specific merge
    :return: Command arguments suitable for subprocess.run
    """
    cmd: list[str] = [str(settings.exe)]

    # Alignment
    if settings.align:
        cmd.append("-a2")

    # Chromatic aberration reduction
    if settings.reduce_ca:
        cmd.append("-ca")

    # Noise reduction
    cmd.append(f"-no{settings.noise_reduction}")

    # Scratch disk mode
    cmd.append("-md")

    # Naming: use first image name
    cmd.extend(["-n", "0"])

    # Destination directory (must end with backslash on Windows)
    dest = str(request.output_dir)
    if not dest.endswith("\\"):
        dest += "\\"
    cmd.extend(["-d", dest])

    # Style-specific flags
    if request.style == "natural":
        p = request.style_params
        cmd.extend([
            "-2",
            "-2a", str(p["accentuation"]),
            "-2b", str(p["blending_point"]),
            "-2c", str(p["color_saturation"]),
            "-2h", str(p["sharpness"]),
            "-2k", str(p["black_point"]),
            "-2m", str(p["midtone"]),
            "-2s", str(p["shadows"]),
            "-2w", str(p["white_point"]),
        ])
    elif request.style == "realistic":
        p = request.style_params
        cmd.extend([
            "-5",
            "-5a", str(p["strength"]),
            "-5c", str(p["color_saturation"]),
            "-5h", str(p["sharpness"]),
        ])
    elif request.style == "photographic":
        if settings.xmp_path is None:
            raise ValueError("photographic style requires xmp_path in settings")
        cmd.extend(["-3", "-t2", "-x2", str(settings.xmp_path)])
    else:
        raise ValueError(f"Unknown merge style: {request.style}")

    # Source images
    cmd.extend(str(f) for f in request.source_files)

    return cmd


def execute_merge(
    settings: PhotomatixSettings,
    request: MergeRequest,
    log=None,
) -> Path:
    """Execute a single PhotomatixCL merge and return the output file path.

    Detects the output filename by comparing directory contents before and
    after execution.

    :raises RuntimeError: If PhotomatixCL returns a non-zero exit code
    :raises FileNotFoundError: If no output file is detected after execution
    """
    log = log or logger
    request.output_dir.mkdir(parents=True, exist_ok=True)

    before = {
        f for f in request.output_dir.iterdir()
        if f.suffix.lower() in IMAGE_EXTENSIONS
    }

    command = build_photomatix_command(settings, request)
    log.info(
        "PhotomatixCL: style=%s source_set=%s files=%d",
        request.style, request.source_set, len(request.source_files),
    )
    log.debug("PhotomatixCL cmd: %s", " ".join(command))

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=settings.timeout_sec,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"PhotomatixCL failed (exit {result.returncode}): "
            f"{result.stdout.strip()} {result.stderr.strip()}".strip()
        )

    after = {
        f for f in request.output_dir.iterdir()
        if f.suffix.lower() in IMAGE_EXTENSIONS
    }
    new_files = sorted(after - before)

    if not new_files:
        raise FileNotFoundError(
            f"PhotomatixCL produced no output in {request.output_dir}"
        )

    if len(new_files) > 1:
        log.warning(
            "PhotomatixCL produced %d files, expected 1: %s",
            len(new_files), [f.name for f in new_files],
        )

    output_path = new_files[0]
    log.info("PhotomatixCL output: %s", output_path.name)
    return output_path
