"""Configuration management for Forge."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    """Configuration for a single agent.
    
    agent_type: The backend type (claude, gemini, copilot)
    name: Display name for this agent variant
    model: Specific model to use (e.g., sonnet, opus, haiku)
    """
    enabled: bool = True
    agent_type: str = ""  # claude, gemini, copilot
    command: str = ""
    model: str | None = None
    max_budget_usd: float | None = None
    fallback_to_api: bool = False
    skip_permissions: bool = False
    extra_args: list[str] = Field(default_factory=list)


class WorkspaceConfig(BaseModel):
    """Configuration for project workspace."""
    default_dir: str = "."
    create_git: bool = True
    projects_root: str | None = None  # e.g. ~/Projects


class BuildConfig(BaseModel):
    """Configuration for the build pipeline."""
    test_commands: list[str] = Field(default_factory=lambda: ["python -m pytest"])
    lint_commands: list[str] = Field(default_factory=lambda: ["python -m ruff check ."])
    watch_patterns: list[str] = Field(default_factory=lambda: ["**/*.py"])


class GlobalConfig(BaseModel):
    """Global Forge configuration."""
    timeout: int = 120
    max_parallel: int = 5
    auto_commit: bool = False
    max_build_iterations: int = 10


class ForgeConfig(BaseModel):
    """Root configuration model."""
    global_: GlobalConfig = Field(default_factory=GlobalConfig, alias="global")
    agents: dict[str, AgentConfig] = Field(default_factory=dict)
    build: BuildConfig = Field(default_factory=BuildConfig)
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)

    model_config = {"populate_by_name": True}


# Default agent configurations — multiple model variants per backend
DEFAULT_AGENTS: dict[str, dict[str, Any]] = {
    "claude-sonnet": {
        "enabled": True,
        "agent_type": "claude",
        "command": "claude",
        "model": "sonnet",
        "max_budget_usd": 1.0,
    },
    "claude-opus": {
        "enabled": True,
        "agent_type": "claude",
        "command": "claude",
        "model": "opus",
        "max_budget_usd": 5.0,
    },
    "claude-haiku": {
        "enabled": True,
        "agent_type": "claude",
        "command": "claude",
        "model": "haiku",
        "max_budget_usd": 0.25,
    },
    "gemini": {
        "enabled": True,
        "agent_type": "gemini",
        "command": "gemini",
        "fallback_to_api": True,
    },
    "copilot": {
        "enabled": True,
        "agent_type": "copilot",
        "command": "gh",
    },
}


def find_config_file(start_dir: str | Path | None = None) -> Path | None:
    """Find forge.yaml by walking up from start_dir."""
    if start_dir is None:
        start_dir = Path.cwd()
    else:
        start_dir = Path(start_dir)

    current = start_dir.resolve()
    while True:
        candidate = current / "forge.yaml"
        if candidate.exists():
            return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent

    # Check home directory
    home_config = Path.home() / ".config" / "forge" / "forge.yaml"
    if home_config.exists():
        return home_config

    return None


def load_config(config_path: str | Path | None = None) -> ForgeConfig:
    """Load configuration from forge.yaml.
    
    Priority: specified path > walking up from cwd > ~/.config/forge/forge.yaml > defaults
    """
    if config_path:
        path = Path(config_path)
    else:
        path = find_config_file()

    if path and path.exists():
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
    else:
        raw = {}

    # Merge with defaults for missing agents
    agents_raw = raw.get("agents", {})
    for agent_name, defaults in DEFAULT_AGENTS.items():
        if agent_name not in agents_raw:
            agents_raw[agent_name] = defaults

    raw["agents"] = agents_raw
    return ForgeConfig.model_validate(raw)


def detect_available_agents(config: ForgeConfig) -> dict[str, bool]:
    """Detect which agents are actually available on the system."""
    availability: dict[str, bool] = {}

    for agent_name, agent_config in config.agents.items():
        if not agent_config.enabled:
            availability[agent_name] = False
            continue

        cmd = agent_config.command
        if agent_name == "copilot":
            # Copilot uses `gh copilot`, check `gh` exists
            availability[agent_name] = shutil.which("gh") is not None
        else:
            availability[agent_name] = shutil.which(cmd) is not None

    return availability
