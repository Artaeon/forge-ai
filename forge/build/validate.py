"""Project output validation gate.

Checks that essential project artifacts exist and are well-formed
after CODE or FIX phases. Failures are injected into the review context
automatically, giving the reviewer (and fixer) concrete issues.
"""

from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    CRITICAL = "CRITICAL"   # Missing essential files, broken structure
    WARNING = "WARNING"     # Suboptimal but functional
    INFO = "INFO"           # Nice to have


@dataclass
class ValidationIssue:
    severity: Severity
    message: str
    file: str | None = None


@dataclass
class ValidationResult:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(i.severity == Severity.CRITICAL for i in self.issues)

    @property
    def critical_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == Severity.CRITICAL)

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == Severity.WARNING)

    def to_prompt(self) -> str:
        """Format as text for injection into prompts."""
        if not self.issues:
            return "✅ All validation checks passed."

        lines = [f"VALIDATION: {self.critical_count} critical, {self.warning_count} warnings"]
        for issue in self.issues:
            prefix = f"[{issue.severity.value}]"
            if issue.file:
                lines.append(f"- {prefix} {issue.file}: {issue.message}")
            else:
                lines.append(f"- {prefix} {issue.message}")
        return "\n".join(lines)


def validate_project(working_dir: str) -> ValidationResult:
    """Validate that a project has essential structure and files.

    Checks:
    1. Package manifest exists (pyproject.toml, package.json, etc.)
    2. README.md exists and has content
    3. Source files exist (not just config)
    4. Test files exist
    5. No empty source files
    6. .gitignore exists
    """
    wd = Path(working_dir)
    result = ValidationResult()

    if not wd.exists():
        result.issues.append(ValidationIssue(
            Severity.CRITICAL, "Working directory does not exist"
        ))
        return result

    # Collect all files
    skip = {".git", "__pycache__", "node_modules", ".venv", "venv",
            ".tox", ".mypy_cache", ".pytest_cache"}
    all_files: list[str] = []
    for p in sorted(wd.rglob("*")):
        if p.is_file():
            rel = p.relative_to(wd)
            if not any(part in skip or part.startswith(".") for part in rel.parts):
                all_files.append(str(rel))

    if not all_files:
        result.issues.append(ValidationIssue(
            Severity.CRITICAL, "No files found in project directory"
        ))
        return result

    # 1. Package manifest
    manifests = ["pyproject.toml", "setup.py", "setup.cfg", "package.json",
                 "Cargo.toml", "go.mod", "requirements.txt"]
    has_manifest = any((wd / m).exists() for m in manifests)
    if not has_manifest:
        result.issues.append(ValidationIssue(
            Severity.CRITICAL,
            "No package manifest (pyproject.toml, package.json, requirements.txt, etc.)"
        ))

    # 2. README
    readme = wd / "README.md"
    if not readme.exists():
        result.issues.append(ValidationIssue(
            Severity.CRITICAL, "README.md is missing", "README.md"
        ))
    elif readme.stat().st_size < 50:
        result.issues.append(ValidationIssue(
            Severity.WARNING, "README.md exists but is too short (< 50 bytes)",
            "README.md"
        ))

    # 3. Source files
    source_exts = {".py", ".js", ".ts", ".go", ".rs", ".java", ".rb", ".php"}
    source_files = [f for f in all_files if Path(f).suffix in source_exts]
    if not source_files:
        result.issues.append(ValidationIssue(
            Severity.CRITICAL, "No source code files found"
        ))

    # 4. Test files
    test_files = [f for f in all_files
                  if "test" in f.lower() and Path(f).suffix in source_exts]
    if not test_files:
        result.issues.append(ValidationIssue(
            Severity.WARNING, "No test files found (files containing 'test')"
        ))

    # 5. Empty source files (excluding __init__.py)
    for f in source_files:
        fp = wd / f
        if fp.stat().st_size == 0 and not f.endswith("__init__.py"):
            result.issues.append(ValidationIssue(
                Severity.WARNING, "File is empty", f
            ))

    # 6. .gitignore
    if not (wd / ".gitignore").exists():
        result.issues.append(ValidationIssue(
            Severity.INFO, ".gitignore is missing"
        ))

    return result
