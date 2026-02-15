"""Workspace state management and file operations."""

from __future__ import annotations

import subprocess
from pathlib import Path


class Workspace:
    """Manages workspace state for the build pipeline."""

    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()

    @property
    def is_git_repo(self) -> bool:
        return (self.path / ".git").is_dir()

    def get_file_tree(self, max_depth: int = 3) -> str:
        """Get a tree representation of the workspace."""
        try:
            result = subprocess.run(
                ["find", ".", "-maxdepth", str(max_depth), "-not", "-path", "./.git/*"],
                capture_output=True,
                text=True,
                cwd=str(self.path),
                timeout=5,
            )
            return result.stdout.strip()
        except Exception:
            return str(self.path)

    def get_git_status(self) -> str:
        """Get current git status."""
        if not self.is_git_repo:
            return "Not a git repository"
        try:
            result = subprocess.run(
                ["git", "status", "--short"],
                capture_output=True,
                text=True,
                cwd=str(self.path),
                timeout=5,
            )
            return result.stdout.strip() or "Clean"
        except Exception:
            return "Unable to get git status"

    def get_git_diff(self) -> str:
        """Get current uncommitted changes."""
        if not self.is_git_repo:
            return ""
        try:
            result = subprocess.run(
                ["git", "diff", "--stat"],
                capture_output=True,
                text=True,
                cwd=str(self.path),
                timeout=5,
            )
            return result.stdout.strip()
        except Exception:
            return ""
