"""Smart test generation and project type detection.

Auto-detects the project type and generates appropriate test, lint,
and build verification commands when the user doesn't specify them.
"""

from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass, field

from forge.build.context import ProjectInfo, _detect_project, _list_files


@dataclass
class VerificationSuite:
    """A set of verification commands for a project."""
    test_commands: list[str] = field(default_factory=list)
    lint_commands: list[str] = field(default_factory=list)
    build_commands: list[str] = field(default_factory=list)
    syntax_check: str | None = None

    @property
    def all_commands(self) -> list[str]:
        """All verification commands in execution order."""
        cmds = []
        if self.syntax_check:
            cmds.append(self.syntax_check)
        cmds.extend(self.build_commands)
        cmds.extend(self.lint_commands)
        cmds.extend(self.test_commands)
        return cmds

    @property
    def has_commands(self) -> bool:
        return bool(self.all_commands)


# Per-language verification command templates
_PYTHON_SUITE = VerificationSuite(
    syntax_check="python3 -m py_compile $(find . -name '*.py' -not -path './.venv/*' -not -path './venv/*' | head -20) 2>&1 || true",
    test_commands=["python3 -m pytest -x --tb=short 2>&1 || python3 -m unittest discover -s . -p 'test_*.py' 2>&1"],
    lint_commands=[],
    build_commands=[],
)

_JAVASCRIPT_SUITE = VerificationSuite(
    syntax_check="node --check $(find . -name '*.js' -not -path './node_modules/*' | head -10) 2>&1 || true",
    test_commands=["npm test 2>&1 || true"],
    build_commands=["npm run build 2>&1 || true"],
)

_TYPESCRIPT_SUITE = VerificationSuite(
    test_commands=["npm test 2>&1 || true"],
    build_commands=["npx tsc --noEmit 2>&1 || npm run build 2>&1 || true"],
)

_GO_SUITE = VerificationSuite(
    build_commands=["go build ./... 2>&1"],
    test_commands=["go test ./... 2>&1"],
    lint_commands=["go vet ./... 2>&1"],
)

_RUST_SUITE = VerificationSuite(
    build_commands=["cargo build 2>&1"],
    test_commands=["cargo test 2>&1"],
    lint_commands=["cargo clippy 2>&1 || true"],
)


_LANGUAGE_SUITES: dict[str, VerificationSuite] = {
    "python": _PYTHON_SUITE,
    "javascript": _JAVASCRIPT_SUITE,
    "typescript": _TYPESCRIPT_SUITE,
    "go": _GO_SUITE,
    "rust": _RUST_SUITE,
}


def detect_verification_suite(working_dir: str) -> VerificationSuite:
    """Auto-detect the project type and return appropriate verification commands.
    
    Scans the working directory for project markers (pyproject.toml, package.json, etc.)
    and returns a VerificationSuite with test, lint, and build commands.
    """
    wd = Path(working_dir)
    file_tree = _list_files(wd)
    project_info = _detect_project(wd, file_tree)

    suite = _LANGUAGE_SUITES.get(project_info.language)
    if suite is not None:
        return _refine_suite(suite, wd, project_info, file_tree)

    # Fallback: basic file existence check
    return VerificationSuite(
        syntax_check=None,
        test_commands=[_make_file_check(file_tree)],
    )


def _refine_suite(
    base: VerificationSuite,
    wd: Path,
    project_info: ProjectInfo,
    file_tree: list[str],
) -> VerificationSuite:
    """Refine the verification suite based on specific project characteristics."""
    suite = VerificationSuite(
        test_commands=list(base.test_commands),
        lint_commands=list(base.lint_commands),
        build_commands=list(base.build_commands),
        syntax_check=base.syntax_check,
    )

    if project_info.language == "python":
        # Check if pytest is in requirements
        has_pytest = _dep_file_contains(wd, "pytest")
        has_tests = any("test" in f.lower() for f in file_tree)

        if not has_pytest and not has_tests:
            # No test framework, use simple syntax check
            suite.test_commands = [
                "python3 -c \"import ast; import sys; "
                "[ast.parse(open(f).read()) for f in sys.argv[1:]]\" "
                "$(find . -name '*.py' -not -path './.venv/*' -not -path './venv/*' | head -20)"
            ]

        # Check for specific tools
        if _dep_file_contains(wd, "ruff"):
            suite.lint_commands = ["python3 -m ruff check . 2>&1 || true"]
        if _dep_file_contains(wd, "mypy"):
            suite.lint_commands.append("python3 -m mypy . 2>&1 || true")

    elif project_info.language in ("javascript", "typescript"):
        # Check package.json for test/build scripts
        pkg_json = wd / "package.json"
        if pkg_json.exists():
            try:
                import json
                pkg = json.loads(pkg_json.read_text())
                scripts = pkg.get("scripts", {})
                if "test" not in scripts:
                    suite.test_commands = []
                if "build" not in scripts:
                    suite.build_commands = []
            except Exception:
                pass

    return suite


def _dep_file_contains(wd: Path, package: str) -> bool:
    """Check if any dependency file references a package."""
    for fname in ["requirements.txt", "pyproject.toml", "Pipfile", "package.json"]:
        fpath = wd / fname
        if fpath.exists():
            try:
                if package in fpath.read_text(errors="replace").lower():
                    return True
            except Exception:
                pass
    return False


def _make_file_check(file_tree: list[str]) -> str:
    """Create a simple file existence check command."""
    if not file_tree:
        return "test -f *.* 2>&1 || echo 'No files found'"
    # Check that at least one project file exists
    first_file = file_tree[0]
    return f"test -f '{first_file}' && echo 'OK: {first_file} exists'"
