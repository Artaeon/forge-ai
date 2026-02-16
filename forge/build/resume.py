"""Pipeline state persistence for resume capability.

Saves and loads DuoBuildPipeline state to/from a JSON file,
enabling --resume after crashes or interruptions.
"""

from __future__ import annotations

import json
from pathlib import Path
from dataclasses import asdict
from typing import Any


STATE_FILENAME = ".forge-duo-state.json"


def save_state(
    working_dir: str,
    objective: str,
    rounds: list[dict[str, Any]],
    last_phase: str,
    plan_output: str = "",
    planner: str = "",
    coder: str = "",
) -> str:
    """Save pipeline state to disk for resume.

    Returns the path to the state file.
    """
    state = {
        "version": 1,
        "objective": objective,
        "planner": planner,
        "coder": coder,
        "last_phase": last_phase,
        "plan_output": plan_output,
        "rounds": rounds,
    }

    state_path = Path(working_dir) / STATE_FILENAME
    state_path.write_text(json.dumps(state, indent=2, default=str))
    return str(state_path)


def load_state(working_dir: str) -> dict[str, Any] | None:
    """Load pipeline state from disk.

    Returns None if no state file exists or it's corrupted.
    """
    state_path = Path(working_dir) / STATE_FILENAME
    if not state_path.exists():
        return None

    try:
        state = json.loads(state_path.read_text())
        if state.get("version") != 1:
            return None
        return state
    except (json.JSONDecodeError, KeyError):
        return None


def clear_state(working_dir: str) -> None:
    """Remove the state file after successful completion."""
    state_path = Path(working_dir) / STATE_FILENAME
    if state_path.exists():
        state_path.unlink()
