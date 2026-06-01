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
    noise_reduction: int | None = 1
    use_scratch_disk: bool = True
    ev_spacing: float | None = 2.0
    timeout_sec: int = 1800
    xmp_path: Path | None = None


@dataclass(frozen=True)
class MergeRequest:
    source_files: list[Path]
    output_dir: Path
    output_name: str
    style: str
    style_params: dict
    source_set: str


def build_output_name(source_files: list[Path], style: str, source_set: str) -> str:
    """Build the output filename (without extension) for a PhotomatixCL merge.

    For aligned_originals/originals: ref_prefix + diff parts + "hdr" + Style
    For noghost: ref_prefix + "noghost" + Style
    """
    def _prefix(path: Path) -> str:
        stem = path.stem
        return stem.split("_")[0] if "_" in stem else stem

    ref_prefix = _prefix(source_files[0])

    if source_set == "noghost":
        return f"{ref_prefix}_noghost_{style.capitalize()}"

    parts = [ref_prefix]
    for f in source_files[1:]:
        f_prefix = _prefix(f)
        common = 0
        for a, b in zip(ref_prefix, f_prefix):
            if a != b:
                break
            common += 1
        diff = f_prefix[common:]
        parts.append(diff if diff else f_prefix)

    parts.append("hdr")
    parts.append(style.capitalize())
    return "_".join(parts)


def build_photomatix_command(
    settings: PhotomatixSettings,
    request: MergeRequest,
) -> list[str]:
    """Build the full PhotomatixCL command-line argument list."""
    cmd: list[str] = [str(settings.exe)]

    if settings.reduce_ca:
        cmd.append("-ca")

    if settings.noise_reduction is not None:
        cmd.append(f"-no{settings.noise_reduction}")

    if settings.use_scratch_disk:
        cmd.append("-md")

    dest = str(request.output_dir).rstrip("\\") + "\\\\"
    cmd.extend(["-d", dest])

    if settings.ev_spacing is not None:
        cmd.extend(["-e", str(settings.ev_spacing)])

    cmd.extend(["-o", request.output_name])

    # Style-specific flags
    if request.style in ("natural", "realistic"):
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
