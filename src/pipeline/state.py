"""
Session state management for photo pipeline.

Tracks processing status of every group and step.
Enables resume of interrupted sessions and selective reprocessing.
"""

import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pipeline.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class StepStatus(str, Enum):
    PENDING  = "pending"
    RUNNING  = "running"
    DONE     = "done"
    FAILED   = "failed"
    SKIPPED  = "skipped"


class GroupType(str, Enum):
    SINGLE      = "single"       # single shot, no HDR, no panorama
    HDR         = "hdr"          # HDR bracketing, no panorama
    PANORAMA    = "panorama"     # panorama, no HDR
    HDR_PANORAMA = "hdr+panorama"


# ---------------------------------------------------------------------------
# Data model (plain dicts stored as JSON — simple and transparent)
# ---------------------------------------------------------------------------

PIPELINE_STEPS = [
    "grouping",
    "hdr_merge",
    "stitch",
    "geometry",
    "crop",
    "optics",
    "color",
    "cleanup",
]


def _empty_steps() -> dict:
    return {
        step: {"status": StepStatus.PENDING, "output": None, "error": None, "ts": None}
        for step in PIPELINE_STEPS
    }


def new_group(group_id: str, files: list[str], group_type: GroupType) -> dict:
    return {
        "id":       group_id,
        "type":     group_type,
        "files":    files,
        "steps":    _empty_steps(),
        "notes":    [],        # AI review notes or manual annotations
    }


def new_session(session_id: str, input_dir: str) -> dict:
    return {
        "session":    session_id,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "input_dir":  input_dir,
        "groups":     {},
        "finished":   False,
    }


# ---------------------------------------------------------------------------
# State class
# ---------------------------------------------------------------------------

class SessionState:
    """
    Loads, mutates, and persists the session state JSON.

    All mutations call save() automatically to keep the file in sync.
    """

    def __init__(self, workspace: Path, session_id: str | None = None, input_dir: str = ""):
        self.workspace  = Path(workspace)
        self.session_id = session_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_dir = self.workspace / self.session_id
        self.state_file  = self.session_dir / "state.json"
        self.log_dir     = self.session_dir / "logs"
        self.intermediates_dir = self.session_dir / "intermediates"

        # Create directories
        for d in [self.session_dir, self.log_dir, self.intermediates_dir]:
            d.mkdir(parents=True, exist_ok=True)

        # Load or create state
        if self.state_file.exists():
            self._state = self._load()
            logger.info(f"Resumed session {self.session_id} from {self.state_file}")
        else:
            self._state = new_session(self.session_id, input_dir)
            self.save()
            logger.info(f"Created new session {self.session_id}")

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> dict:
        with open(self.state_file, encoding="utf-8") as f:
            return json.load(f)

    def save(self):
        self._state["updated_at"] = datetime.now().isoformat()
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(self._state, f, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Groups
    # ------------------------------------------------------------------

    def add_group(self, group_id: str, files: list[str], group_type: GroupType):
        self._state["groups"][group_id] = new_group(group_id, files, group_type)
        self.save()
        logger.debug(f"Added group {group_id} ({group_type}, {len(files)} files)")

    def get_group(self, group_id: str) -> dict | None:
        return self._state["groups"].get(group_id)

    def all_groups(self) -> list[dict]:
        return list(self._state["groups"].values())

    def groups_needing_step(self, step: str) -> list[dict]:
        """Return groups where `step` is pending or failed."""
        return [
            g for g in self.all_groups()
            if g["steps"][step]["status"] in (StepStatus.PENDING, StepStatus.FAILED)
        ]

    # ------------------------------------------------------------------
    # Step transitions
    # ------------------------------------------------------------------

    def step_start(self, group_id: str, step: str):
        self._set_step(group_id, step, status=StepStatus.RUNNING, ts=datetime.now().isoformat())

    def step_done(self, group_id: str, step: str, output: str | None = None):
        self._set_step(group_id, step, status=StepStatus.DONE, output=output, ts=datetime.now().isoformat())

    def step_failed(self, group_id: str, step: str, error: str):
        self._set_step(group_id, step, status=StepStatus.FAILED, error=error, ts=datetime.now().isoformat())
        logger.error(f"Step {step} failed for {group_id}: {error}")

    def step_skip(self, group_id: str, step: str, reason: str = ""):
        self._set_step(group_id, step, status=StepStatus.SKIPPED, error=reason, ts=datetime.now().isoformat())

    def _set_step(self, group_id: str, step: str, **kwargs):
        self._state["groups"][group_id]["steps"][step].update(kwargs)
        self.save()

    def get_step_status(self, group_id: str, step: str) -> StepStatus:
        return self._state["groups"][group_id]["steps"][step]["status"]

    def get_step_output(self, group_id: str, step: str) -> str | None:
        return self._state["groups"][group_id]["steps"][step]["output"]

    # ------------------------------------------------------------------
    # Notes / AI review
    # ------------------------------------------------------------------

    def add_note(self, group_id: str, note: str):
        self._state["groups"][group_id]["notes"].append({
            "ts":   datetime.now().isoformat(),
            "text": note,
        })
        self.save()

    # ------------------------------------------------------------------
    # Session-level
    # ------------------------------------------------------------------

    def mark_finished(self):
        self._state["finished"] = True
        self.save()

    @property
    def is_finished(self) -> bool:
        return self._state.get("finished", False)

    @property
    def session(self) -> dict:
        return self._state

    # ------------------------------------------------------------------
    # Status summary (for CLI `status` command)
    # ------------------------------------------------------------------

    def summary(self) -> str:
        lines = []
        s = self._state
        lines.append(f"\nSession:    {s['session']}")
        lines.append(f"Created:    {s['created_at'][:19]}")
        lines.append(f"Input dir:  {s['input_dir']}")
        lines.append(f"Finished:   {s['finished']}")
        lines.append(f"\nGroups ({len(s['groups'])}):")

        for gid, g in s["groups"].items():
            lines.append(f"\n  {gid}  [{g['type']}]  {len(g['files'])} files")
            for step, info in g["steps"].items():
                status = info["status"]
                icon = {
                    StepStatus.PENDING:  "⬜",
                    StepStatus.RUNNING:  "🔄",
                    StepStatus.DONE:     "✅",
                    StepStatus.FAILED:   "❌",
                    StepStatus.SKIPPED:  "⏭ ",
                }.get(status, "?")
                lines.append(f"    {icon} {step:<20} {status}")
                if info.get("error"):
                    lines.append(f"       ↳ {info['error']}")

        return "\n".join(lines)
