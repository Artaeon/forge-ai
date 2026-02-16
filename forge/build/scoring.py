"""Quality scoring for generated projects.

Scores a project 0-100 based on structural completeness,
code quality signals, test coverage, and documentation.
"""

from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass


@dataclass
class QualityScore:
    """Breakdown of project quality score."""
    total: int  # 0-100
    structure: int  # 0-25
    code: int  # 0-25
    tests: int  # 0-25
    docs: int  # 0-25
    details: list[str]  # Human-readable breakdown

    @property
    def grade(self) -> str:
        """Letter grade for the score."""
        if self.total >= 90:
            return "A"
        elif self.total >= 80:
            return "B"
        elif self.total >= 70:
            return "C"
        elif self.total >= 60:
            return "D"
        return "F"

    @property
    def emoji(self) -> str:
        """Emoji for the grade."""
        return {"A": "🏆", "B": "✅", "C": "⚠️", "D": "😐", "F": "❌"}.get(
            self.grade, "❓"
        )


def score_project(working_dir: str) -> QualityScore:
    """Score a project's quality from 0-100.

    Categories (25 pts each):
    - Structure: manifest, .gitignore, proper layout
    - Code: source files exist, non-trivial, no placeholders
    - Tests: test files exist, have assertions
    - Docs: README exists, has content
    """
    wd = Path(working_dir)
    details: list[str] = []
    skip = {".git", "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache"}

    all_files = []
    for f in wd.rglob("*"):
        if f.is_file() and not any(p in skip for p in f.parts):
            all_files.append(f)

    src_exts = {".py", ".js", ".ts", ".go", ".rs", ".java", ".rb"}
    source_files = [f for f in all_files if f.suffix in src_exts]
    test_files = [
        f for f in source_files
        if "test" in f.stem.lower() or f.parent.name in ("tests", "test", "__tests__")
    ]

    # ─── Structure (25 pts) ───────────────────────────────────
    structure = 0

    # Has manifest file (8 pts)
    manifests = ["pyproject.toml", "package.json", "Cargo.toml", "go.mod", "setup.py"]
    if any((wd / m).exists() for m in manifests):
        structure += 8
        details.append("✅ Package manifest found")
    else:
        details.append("❌ No package manifest (pyproject.toml, package.json, etc.)")

    # Has .gitignore (4 pts)
    if (wd / ".gitignore").exists():
        structure += 4
        details.append("✅ .gitignore present")
    else:
        details.append("❌ Missing .gitignore")

    # Has proper directory structure (8 pts)
    dirs = {f.parent.relative_to(wd) for f in source_files if f.parent != wd}
    if len(dirs) >= 1:
        structure += 4
        details.append(f"✅ Organized in {len(dirs)} directory(ies)")
    if len(dirs) >= 2:
        structure += 4

    # Has __init__.py or index file (5 pts)
    has_init = any(f.name in ("__init__.py", "index.js", "index.ts", "mod.rs", "main.go")
                   for f in source_files)
    if has_init:
        structure += 5
        details.append("✅ Entry point/init file found")

    # ─── Code Quality (25 pts) ────────────────────────────────
    code = 0

    # Source files exist (10 pts, scaled by count)
    if source_files:
        pts = min(10, len(source_files) * 2)
        code += pts
        details.append(f"✅ {len(source_files)} source file(s)")
    else:
        details.append("❌ No source files found")

    # Non-trivial code (10 pts)
    total_lines = 0
    for f in source_files[:20]:
        try:
            lines = f.read_text(errors="replace").count("\n")
            total_lines += lines
        except Exception:
            pass

    if total_lines > 200:
        code += 10
        details.append(f"✅ {total_lines} lines of code")
    elif total_lines > 50:
        code += 5
        details.append(f"⚠️  Only {total_lines} lines of code")
    elif total_lines > 0:
        code += 2
        details.append(f"⚠️  Very little code ({total_lines} lines)")

    # No placeholder content (5 pts)
    placeholder_count = 0
    for f in source_files[:10]:
        try:
            content = f.read_text(errors="replace").lower()
            if "todo" in content or "pass  # placeholder" in content:
                placeholder_count += 1
        except Exception:
            pass

    if placeholder_count == 0:
        code += 5
        details.append("✅ No TODO/placeholder code")
    else:
        details.append(f"⚠️  {placeholder_count} file(s) with TODO/placeholders")

    # ─── Tests (25 pts) ───────────────────────────────────────
    tests = 0

    # Test files exist (10 pts)
    if test_files:
        pts = min(10, len(test_files) * 5)
        tests += pts
        details.append(f"✅ {len(test_files)} test file(s)")
    else:
        details.append("❌ No test files found")

    # Tests have assertions (10 pts)
    has_assertions = False
    for f in test_files[:5]:
        try:
            content = f.read_text(errors="replace")
            if "assert" in content or "expect(" in content or "should" in content:
                has_assertions = True
                break
        except Exception:
            pass
    if has_assertions:
        tests += 10
        details.append("✅ Tests contain assertions")
    elif test_files:
        details.append("⚠️  Test files exist but no assertions found")

    # Test to source ratio (5 pts)
    non_test_sources = [f for f in source_files if f not in test_files]
    if non_test_sources and test_files:
        ratio = len(test_files) / len(non_test_sources)
        if ratio >= 0.3:
            tests += 5
            details.append(f"✅ Good test ratio ({ratio:.0%})")
        elif ratio >= 0.1:
            tests += 2
            details.append(f"⚠️  Low test ratio ({ratio:.0%})")

    # ─── Documentation (25 pts) ───────────────────────────────
    docs = 0

    # README exists (10 pts)
    readme = wd / "README.md"
    if readme.exists():
        docs += 5
        readme_content = readme.read_text(errors="replace")
        readme_lines = readme_content.count("\n")

        if readme_lines >= 20:
            docs += 5
            details.append(f"✅ README.md ({readme_lines} lines)")
        else:
            details.append(f"⚠️  README.md is short ({readme_lines} lines)")

        # Has install instructions (5 pts)
        readme_lower = readme_content.lower()
        if "install" in readme_lower or "pip install" in readme_lower or "npm install" in readme_lower:
            docs += 5
            details.append("✅ Install instructions in README")

        # Has usage examples (5 pts)
        if "```" in readme_content or "usage" in readme_lower:
            docs += 5
            details.append("✅ Usage examples in README")
    else:
        details.append("❌ No README.md")

    # Has docstrings/comments (5 pts)
    has_docstrings = False
    for f in source_files[:5]:
        try:
            content = f.read_text(errors="replace")
            if '"""' in content or "'''" in content or "/**" in content:
                has_docstrings = True
                break
        except Exception:
            pass

    if has_docstrings:
        docs += 5
        details.append("✅ Code has docstrings/comments")

    total = structure + code + tests + docs

    return QualityScore(
        total=total,
        structure=structure,
        code=code,
        tests=tests,
        docs=docs,
        details=details,
    )
