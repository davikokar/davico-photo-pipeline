"""
Structured logger for photo pipeline.
Outputs to both console (colored) and per-session log file.
"""

import logging
import sys
from pathlib import Path
from datetime import datetime


# ANSI color codes for console output
COLORS = {
    "DEBUG": "\033[36m",  # cyan
    "INFO": "\033[32m",  # green
    "WARNING": "\033[33m",  # yellow
    "ERROR": "\033[31m",  # red
    "CRITICAL": "\033[35m",  # magenta
    "RESET": "\033[0m",
    "BOLD": "\033[1m",
    "DIM": "\033[2m",
}


class ColoredFormatter(logging.Formatter):
    """Console formatter with colors and clean layout."""

    FORMAT = "{color}{bold}[{levelname:<8}]{reset} {dim}{asctime}{reset}  {msg}"

    def format(self, record):
        color = COLORS.get(record.levelname, "")
        reset = COLORS["RESET"]
        bold = COLORS["BOLD"]
        dim = COLORS["DIM"]

        # Step/group context injected via 'extra'
        context = ""
        if hasattr(record, "step"):
            context += f" [{record.step}]"
        if hasattr(record, "group"):
            context += f" [{record.group}]"

        msg = f"{record.getMessage()}{context}"

        asctime = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")

        return (
            f"{color}{bold}[{record.levelname:<8}]{reset} {dim}{asctime}{reset}  {msg}"
        )


class PlainFormatter(logging.Formatter):
    """File formatter — no colors, full timestamp."""

    def format(self, record):
        context = ""
        if hasattr(record, "step"):
            context += f" [{record.step}]"
        if hasattr(record, "group"):
            context += f" [{record.group}]"

        asctime = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        return f"[{record.levelname:<8}] {asctime}  {record.getMessage()}{context}"


def get_logger(
    name: str, log_file: Path | None = None, level: int = logging.DEBUG
) -> logging.Logger:
    """
    Build and return a logger with console + optional file handler.

    Args:
        name:     Logger name (usually __name__ of calling module).
        log_file: Path to log file. If None, logs only to console.
        level:    Minimum log level.

    Returns:
        Configured Logger instance.
    """
    logger = logging.getLogger(name)

    # Avoid adding duplicate handlers if logger already exists
    if logger.handlers:
        return logger

    logger.setLevel(level)

    # --- Console handler ---
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(ColoredFormatter())
    logger.addHandler(console)

    # --- File handler (optional) ---
    if log_file is not None:
        log_file = Path(log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)  # always verbose on file
        file_handler.setFormatter(PlainFormatter())
        logger.addHandler(file_handler)

    return logger


def step_logger(logger: logging.Logger, step: str, group: str | None = None):
    """
    Return a LoggerAdapter that automatically injects step/group context.

    Usage:
        log = step_logger(logger, step="hdr_merge", group="group_001")
        log.info("Merging 3 exposures")
        # → [INFO    ] 14:23:01  Merging 3 exposures [hdr_merge] [group_001]
    """
    extra = {"step": step}
    if group:
        extra["group"] = group
    return logging.LoggerAdapter(logger, extra)
