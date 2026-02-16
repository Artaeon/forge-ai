"""Workspace-aware context gathering for build iterations.

Provides agents with structured information about the project state:
file tree, git status, language/framework detection, and key file contents.
"""

from __future__ import annotations

import os
import logging
import subprocess
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class ProjectInfo:
    """Detected project type and framework information."""
    language: str = "unknown"  # python, javascript, typescript, go, rust
    framework: str | None = None  # flask, fastapi, express, nextjs, etc.
    package_manager: str | None = None  # pip, npm, yarn, cargo, go
    entry_point: str | None = None  # main file
    config_files: list[str] = field(default_factory=list)


@dataclass
class WorkspaceContext:
    """Full workspace context fed to agents during build iterations."""
    working_dir: str
    file_tree: list[str]
    git_status: str
    git_diff: str
    project_info: ProjectInfo
    key_file_contents: dict[str, str]

    def to_prompt_section(self) -> str:
        """Format workspace context as a prompt section for agents."""
        parts = [f"Working directory: {self.working_dir}"]

        # Project info
        pi = self.project_info
        if pi.language != "unknown":
            info = f"Language: {pi.language}"
            if pi.framework:
                info += f", Framework: {pi.framework}"
            if pi.package_manager:
                info += f", Package manager: {pi.package_manager}"
            parts.append(info)

        # File tree
        if self.file_tree:
            tree = "\n".join(f"  {f}" for f in self.file_tree[:50])
            parts.append(f"Project files ({len(self.file_tree)} total):\n{tree}")
            if len(self.file_tree) > 50:
                parts.append(f"  ... and {len(self.file_tree) - 50} more files")

        # Git status
        if self.git_status.strip():
            parts.append(f"Git status:\n{self.git_status}")

        # Git diff (truncated)
        if self.git_diff.strip():
            diff = self.git_diff[:2000]
            parts.append(f"Recent changes:\n{diff}")
            if len(self.git_diff) > 2000:
                parts.append(f"  ... diff truncated ({len(self.git_diff)} chars total)")

        # Key file contents
        for name, content in self.key_file_contents.items():
            truncated = content[:1500]
            parts.append(f"--- {name} ---\n{truncated}")
            if len(content) > 1500:
                parts.append(f"  ... truncated ({len(content)} chars total)")

        return "\n\n".join(parts)


def gather_context(working_dir: str) -> WorkspaceContext:
    """Gather full workspace context from the project directory."""
    wd = Path(working_dir)

    file_tree = _list_files(wd)
    git_status = _run_git(wd, ["git", "status", "--short"])
    git_diff = _run_git(wd, ["git", "diff", "--stat"])
    project_info = _detect_project(wd, file_tree)
    key_files = _read_key_files(wd, project_info)

    return WorkspaceContext(
        working_dir=working_dir,
        file_tree=file_tree,
        git_status=git_status,
        git_diff=git_diff,
        project_info=project_info,
        key_file_contents=key_files,
    )


def _list_files(wd: Path) -> list[str]:
    """List project files, excluding hidden dirs and noise."""
    result = []
    if not wd.exists():
        return result

    skip_dirs = {".git", "__pycache__", "node_modules", ".venv", "venv", ".tox", ".mypy_cache"}

    for p in sorted(wd.rglob("*")):
        rel = p.relative_to(wd)
        parts = rel.parts
        if any(part in skip_dirs or part.startswith(".") for part in parts):
            continue
        if p.is_file():
            result.append(str(rel))

    return result


def _run_git(wd: Path, cmd: list[str]) -> str:
    """Run a git command and return output, or empty string on failure."""
    try:
        result = subprocess.run(
            cmd, cwd=str(wd), capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


# Language detection patterns
_LANGUAGE_MARKERS: dict[str, list[str]] = {
    "python": ["pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile"],
    "javascript": ["package.json"],
    "typescript": ["tsconfig.json"],
    "go": ["go.mod"],
    "rust": ["Cargo.toml"],
    "java": ["pom.xml", "build.gradle"],
    "ruby": ["Gemfile"],
}

# Framework detection patterns
_FRAMEWORK_MARKERS: dict[str, dict[str, str]] = {
    "python": {
        "flask": "flask",
        "fastapi": "fastapi",
        "django": "django",
        "pytest": "pytest",
    },
    "javascript": {
        "next": "next",
        "express": "express",
        "react": "react",
        "vue": "vue",
    },
    "typescript": {
        "next": "next",
        "express": "express",
        "react": "react",
        "angular": "@angular",
    },
}


def _detect_project(wd: Path, file_tree: list[str]) -> ProjectInfo:
    """Detect language, framework, and package manager."""
    info = ProjectInfo()
    file_set = set(file_tree)

    # Detect language
    for lang, markers in _LANGUAGE_MARKERS.items():
        for marker in markers:
            if marker in file_set:
                info.language = lang
                info.config_files.append(marker)
                break
        if info.language != "unknown":
            break

    # Fallback: detect by file extensions
    if info.language == "unknown":
        ext_counts: dict[str, int] = {}
        for f in file_tree:
            ext = Path(f).suffix.lower()
            if ext:
                ext_counts[ext] = ext_counts.get(ext, 0) + 1

        ext_to_lang = {
            ".py": "python", ".js": "javascript", ".ts": "typescript",
            ".go": "go", ".rs": "rust", ".java": "java", ".rb": "ruby",
        }
        if ext_counts:
            top_ext = max(ext_counts, key=ext_counts.get)  # type: ignore[arg-type]
            info.language = ext_to_lang.get(top_ext, "unknown")

    # Package manager
    pm_map = {
        "python": "pip", "javascript": "npm", "typescript": "npm",
        "go": "go", "rust": "cargo", "java": "maven", "ruby": "bundler",
    }
    info.package_manager = pm_map.get(info.language)

    # Framework detection from dependency files
    framework_markers = _FRAMEWORK_MARKERS.get(info.language, {})
    if framework_markers:
        dep_content = _read_dep_file(wd, info.language)
        for framework, marker in framework_markers.items():
            if marker in dep_content.lower():
                info.framework = framework
                break

    # Entry point detection
    entry_candidates = {
        "python": ["app.py", "main.py", "server.py", "__main__.py", "manage.py"],
        "javascript": ["index.js", "app.js", "server.js", "src/index.js"],
        "typescript": ["index.ts", "app.ts", "server.ts", "src/index.ts"],
        "go": ["main.go", "cmd/main.go"],
        "rust": ["src/main.rs"],
    }
    for candidate in entry_candidates.get(info.language, []):
        if candidate in file_set:
            info.entry_point = candidate
            break

    return info


def _read_dep_file(wd: Path, language: str) -> str:
    """Read the primary dependency file for framework detection."""
    dep_files = {
        "python": ["requirements.txt", "pyproject.toml", "Pipfile"],
        "javascript": ["package.json"],
        "typescript": ["package.json"],
    }
    for fname in dep_files.get(language, []):
        fpath = wd / fname
        if fpath.exists():
            try:
                return fpath.read_text(errors="replace")[:5000]
            except (OSError, PermissionError):
                pass
    return ""


def _read_key_files(wd: Path, project_info: ProjectInfo) -> dict[str, str]:
    """Read key project files that provide important context."""
    key_files: dict[str, str] = {}

    # Always include config files
    for cf in project_info.config_files:
        fpath = wd / cf
        if fpath.exists():
            try:
                key_files[cf] = fpath.read_text(errors="replace")[:3000]
            except (OSError, PermissionError):
                pass

    # Include entry point
    if project_info.entry_point:
        fpath = wd / project_info.entry_point
        if fpath.exists():
            try:
                key_files[project_info.entry_point] = fpath.read_text(errors="replace")[:3000]
            except (OSError, PermissionError):
                pass

    # Include README if present
    for readme in ["README.md", "README.rst", "README.txt"]:
        fpath = wd / readme
        if fpath.exists():
            try:
                key_files[readme] = fpath.read_text(errors="replace")[:2000]
            except (OSError, PermissionError):
                pass
            break

    return key_files
