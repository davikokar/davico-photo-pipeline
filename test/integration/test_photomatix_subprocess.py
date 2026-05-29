"""Integration test: run PhotomatixCL realistic merge via subprocess.

Purpose: isolate whether the subprocess mechanism itself causes hangs
with the -5 (Fusion/Realistic) method and/or -md flag.
"""

import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from run import DEFAULT_CONFIG, load_config

PHOTOMATIX_EXE = None


def _get_exe() -> str:
    global PHOTOMATIX_EXE
    if PHOTOMATIX_EXE is None:
        config = load_config(DEFAULT_CONFIG)
        PHOTOMATIX_EXE = config["steps"]["hdr"]["merging"]["photomatix_exe"]
    return PHOTOMATIX_EXE


@pytest.mark.parametrize("use_md", [False, True], ids=["no_md", "with_md"])
@pytest.mark.parametrize(
    "output_dir",
    [r"C:\Temp\pipeline_tests\output\20260520_144910\merged_hdrs\group_001"],
)
@pytest.mark.parametrize(
    "source_files",
    [
        [
            r"C:\Temp\pipeline_tests\output\20260520_144910\aligned\group_001\0H8A4390_reference.JPG",
            r"C:\Temp\pipeline_tests\output\20260520_144910\aligned\group_001\0H8A4391_original_aligned.JPG",
            r"C:\Temp\pipeline_tests\output\20260520_144910\aligned\group_001\0H8A4392_original_aligned.JPG",
        ],
    ],
)
def test_photomatix_realistic_subprocess(source_files, output_dir, use_md):
    """Run PhotomatixCL -5 (realistic) via subprocess and check it completes."""
    exe = _get_exe()
    assert Path(exe).exists(), f"PhotomatixCL not found: {exe}"

    for f in source_files:
        assert Path(f).exists(), f"Source file not found: {f}"

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    output_dir_arg = output_dir.rstrip("\\") + "\\\\"

    cmd = [exe, "-ca", "-no1"]
    if use_md:
        cmd.append("-md")
    cmd.extend(["-n", "0", "-d", output_dir_arg])
    cmd.extend(["-5", "-5a", "0.0", "-5c", "0.0", "-5h", "2.0"])
    cmd.extend(source_files)

    print(f"\nuse_md={use_md}")
    print(f"cmd: {' '.join(cmd)}")

    timeout = 1800

    with (
        tempfile.TemporaryFile(mode="w+", suffix=".stdout") as out_f,
        tempfile.TemporaryFile(mode="w+", suffix=".stderr") as err_f,
    ):
        t0 = time.perf_counter()
        process = subprocess.Popen(cmd, stdout=out_f, stderr=err_f)

        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            elapsed = time.perf_counter() - t0
            process.kill()
            process.wait()
            out_f.seek(0)
            err_f.seek(0)
            pytest.fail(
                f"PhotomatixCL TIMED OUT after {elapsed:.1f}s (limit {timeout}s)\n"
                f"stdout: {out_f.read()}\n"
                f"stderr: {err_f.read()}"
            )

        elapsed = time.perf_counter() - t0
        out_f.seek(0)
        err_f.seek(0)
        stdout_text = out_f.read()
        stderr_text = err_f.read()

    print(f"returncode: {process.returncode}")
    print(f"elapsed: {elapsed:.1f}s ({elapsed/60:.1f}min)")
    print(f"stdout: {stdout_text}")
    if stderr_text.strip():
        print(f"stderr: {stderr_text}")

    output_files = list(Path(output_dir).glob("*"))
    print(f"output files: {[f.name for f in output_files]}")

    assert output_files, "PhotomatixCL produced no output file"
