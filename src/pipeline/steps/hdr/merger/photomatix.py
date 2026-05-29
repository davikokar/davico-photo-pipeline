"""PhotomatixCL command builder and executor.

Pure worker — knows how to construct PhotomatixCL command lines and execute
them via subprocess. Has no awareness of session state or JSON persistence.
"""

from __future__ import annotations

import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from pipeline.utils.logger import get_logger

logger = get_logger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".tif", ".tiff", ".png", ".hdr", ".exr"}


@dataclass(frozen=True)
class PhotomatixSettings:
    exe: Path
    reduce_ca: bool = True
    noise_reduction: int = 1
    timeout_sec: int = 1800
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

    # Chromatic aberration reduction
    if settings.reduce_ca:
        cmd.append("-ca")

    # Noise reduction
    cmd.append(f"-no{settings.noise_reduction}")

    # Naming: use first image name
    cmd.extend(["-n", "0"])

    # Destination directory (must end with double backslash on Windows)
    dest = str(request.output_dir).rstrip("\\") + "\\\\"
    cmd.extend(["-d", dest])

    # Style-specific flags
    if request.style == "natural":
        # -md (scratch disk) is only supported for Fusion/Natural
        cmd.append("-md")
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
        cmd.append("-md")
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
    elif request.style == "photographic":
        if settings.xmp_path is None:
            raise ValueError("photographic style requires xmp_path in settings")
        cmd.extend(["-3", "-h", "remove", "-t2", "-x2", str(settings.xmp_path)])
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

    Uses temp-file redirection for stdout/stderr instead of pipes to avoid
    deadlocks when PhotomatixCL spawns child processes that inherit handles.

    Detects the output filename by comparing directory contents before and
    after execution.

    :raises RuntimeError: If PhotomatixCL fails with no output produced
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
        "▶ PhotomatixCL START style=%s source_set=%s files=%d",
        request.style, request.source_set, len(request.source_files),
    )
    log.debug("  cmd: %s", " ".join(command))

    t0 = time.perf_counter()
    returncode, stdout_text, stderr_text = _run_with_file_redirect(
        command, timeout_sec=settings.timeout_sec, log=log,
    )
    elapsed = time.perf_counter() - t0

    after = {
        f for f in request.output_dir.iterdir()
        if f.suffix.lower() in IMAGE_EXTENSIONS
    }
    new_files = sorted(after - before)

    if returncode is None:
        if new_files:
            log.warning(
                "✓ PhotomatixCL TIMEOUT after %ds but produced output — "
                "continuing (%.1fs elapsed)",
                settings.timeout_sec, elapsed,
            )
            output_path = new_files[0]
            log.info("  output: %s", output_path.name)
            return output_path
        raise RuntimeError(
            f"PhotomatixCL timed out after {settings.timeout_sec}s "
            f"with no output in {request.output_dir}"
        )

    if returncode != 0:
        if new_files:
            log.warning(
                "✓ PhotomatixCL DONE exit=%d but produced output — "
                "continuing (%.1fs). stdout: %s",
                returncode, elapsed, stdout_text.strip(),
            )
        else:
            raise RuntimeError(
                f"PhotomatixCL failed (exit {returncode}): "
                f"{stdout_text.strip()} {stderr_text.strip()}".strip()
            )

    if not new_files:
        raise FileNotFoundError(
            f"PhotomatixCL produced no output in {request.output_dir}"
        )

    if len(new_files) > 1:
        log.warning(
            "  PhotomatixCL produced %d files, expected 1: %s",
            len(new_files), [f.name for f in new_files],
        )

    output_path = new_files[0]
    log.info(
        "✓ PhotomatixCL DONE style=%s → %s (%.1fs)",
        request.style, output_path.name, elapsed,
    )
    return output_path


def _run_with_file_redirect(
    command: list[str],
    timeout_sec: int,
    log,
) -> tuple[int | None, str, str]:
    """Run a command redirecting stdout/stderr to temp files.

    Returns (returncode, stdout_text, stderr_text).
    returncode is None if the process timed out.
    """
    with (
        tempfile.TemporaryFile(mode="w+", suffix=".stdout.txt") as out_f,
        tempfile.TemporaryFile(mode="w+", suffix=".stderr.txt") as err_f,
    ):
        process = subprocess.Popen(
            command,
            stdout=out_f,
            stderr=err_f,
        )
        try:
            process.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            log.warning("  process PID %d timed out — killing", process.pid)
            process.kill()
            process.wait()
            out_f.seek(0)
            err_f.seek(0)
            return None, out_f.read(), err_f.read()

        out_f.seek(0)
        err_f.seek(0)
        return process.returncode, out_f.read(), err_f.read()
