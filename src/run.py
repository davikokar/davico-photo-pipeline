"""
Photo Pipeline — CLI entry point.

Usage:
    python run.py process <input_dir> [--config config/pipeline.yaml]
    python run.py resume  <session_dir>
    python run.py rerun   <session_dir> --group <group_id> --step <step>
    python run.py status  <session_dir>
"""

import argparse
import sys
import yaml
from pathlib import Path

from pipeline.state import SessionState
from pipeline.orchestrator import Orchestrator
from pipeline.utils.logger import get_logger

logger = get_logger("run")

DEFAULT_CONFIG = Path(__file__).parent / "config" / "pipeline.yaml"


def load_config(path: Path) -> dict:
    if not path.exists():
        logger.warning(f"Config file not found: {path} — using defaults")
        return {}
    with open(path) as f:
        config = yaml.safe_load(f) or {}
    config["__config_dir__"] = str(path.parent.resolve())
    return config


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_process(args):
    input_dir = Path(args.input_dir).resolve()
    if not input_dir.exists():
        logger.error(f"Input directory not found: {input_dir}")
        sys.exit(1)

    raw_dir = Path(args.raw_dir).resolve()
    if not raw_dir.exists():
        raw_dir = ""

    config = load_config(Path(args.config))
    workspace = Path(config.get("pipeline", {}).get("workspace", "./workspace"))
    output    = Path(config.get("pipeline", {}).get("output",    "./output"))

    state = SessionState(workspace=workspace, input_dir=str(input_dir), raw_dir=str(raw_dir))
    orchestrator = Orchestrator(state=state, config=config, output_dir=output)

    logger.info(f"Processing: {input_dir}")
    orchestrator.run()


def cmd_resume(args):
    session_dir = Path(args.session_dir).resolve()
    if not session_dir.exists():
        logger.error(f"Session directory not found: {session_dir}")
        sys.exit(1)

    config    = load_config(Path(args.config))
    workspace = session_dir.parent
    session_id = session_dir.name
    output    = Path(config.get("pipeline", {}).get("output", "./output"))

    state = SessionState(workspace=workspace, session_id=session_id)
    orchestrator = Orchestrator(state=state, config=config, output_dir=output)

    logger.info(f"Resuming session: {session_id}")
    orchestrator.run()


def cmd_rerun(args):
    session_dir = Path(args.session_dir).resolve()
    config      = load_config(Path(args.config))
    workspace   = session_dir.parent
    session_id  = session_dir.name
    output      = Path(config.get("pipeline", {}).get("output", "./output"))

    state = SessionState(workspace=workspace, session_id=session_id)
    orchestrator = Orchestrator(state=state, config=config, output_dir=output)

    logger.info(f"Re-running step '{args.step}' for group '{args.group}'")
    orchestrator.rerun_step(group_id=args.group, step=args.step)


def cmd_status(args):
    session_dir = Path(args.session_dir).resolve()
    workspace   = session_dir.parent
    session_id  = session_dir.name

    state = SessionState(workspace=workspace, session_id=session_id)
    print(state.summary())


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="photo-pipeline",
        description="Automated photo post-processing pipeline",
    )
    parser.add_argument(
        "--config", default=str(DEFAULT_CONFIG),
        help="Path to pipeline.yaml config file",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # process
    p_process = sub.add_parser("process", help="Start a new pipeline session")
    p_process.add_argument("input_dir", help="Folder containing source photos")
    p_process.add_argument("raw_dir", help="Folder containing raw photos")

    # resume
    p_resume = sub.add_parser("resume", help="Resume an interrupted session")
    p_resume.add_argument("session_dir", help="Path to session workspace folder")

    # rerun
    p_rerun = sub.add_parser("rerun", help="Re-run a single step for a group")
    p_rerun.add_argument("session_dir", help="Path to session workspace folder")
    p_rerun.add_argument("--group", required=True, help="Group ID (e.g. group_001)")
    p_rerun.add_argument("--step",  required=True, help="Step name (e.g. color)")

    # status
    p_status = sub.add_parser("status", help="Show session status")
    p_status.add_argument("session_dir", help="Path to session workspace folder")

    return parser


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()

    commands = {
        "process": cmd_process,
        "resume":  cmd_resume,
        "rerun":   cmd_rerun,
        "status":  cmd_status,
    }
    commands[args.command](args)
