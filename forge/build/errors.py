"""Error classification and routing for build failures.

Categorizes errors by type and severity to determine the best
retry strategy: simple fix, dependency install, model escalation,
or architectural re-planning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class ErrorCategory(Enum):
    """Classification of build/test errors."""
    SYNTAX = "syntax"           # Parse errors, indentation, missing brackets
    DEPENDENCY = "dependency"   # Missing modules, import errors, version conflicts
    LOGIC = "logic"             # Test failures, assertion errors, wrong output
    ARCHITECTURE = "architecture"  # Design flaws, missing interfaces, wrong patterns
    RUNTIME = "runtime"         # Crashes, exceptions at runtime
    CONFIGURATION = "configuration"  # Missing config, wrong paths, env vars
    UNKNOWN = "unknown"


class ErrorSeverity(Enum):
    """How severe the error is -- determines retry strategy."""
    LOW = "low"       # Auto-fixable (dep install, syntax fix)
    MEDIUM = "medium"  # Needs agent retry with same model
    HIGH = "high"      # Needs model escalation
    CRITICAL = "critical"  # Needs re-planning


@dataclass
class ClassifiedError:
    """A classified build error with routing metadata."""
    category: ErrorCategory
    severity: ErrorSeverity
    summary: str  # One-line summary
    raw_output: str
    suggested_action: str  # Human-readable recommendation
    auto_fixable: bool = False  # Can be fixed without agent help

    @property
    def should_escalate(self) -> bool:
        return self.severity in (ErrorSeverity.HIGH, ErrorSeverity.CRITICAL)


# Pattern-based classification rules
_SYNTAX_PATTERNS = [
    r"SyntaxError",
    r"IndentationError",
    r"unexpected EOF",
    r"invalid syntax",
    r"expected.*['\"]",
    r"unterminated string",
    r"TabError",
]

_DEPENDENCY_PATTERNS = [
    r"ModuleNotFoundError",
    r"ImportError",
    r"No module named",
    r"Could not find a version",
    r"pip install",
    r"npm ERR!.*not found",
    r"package.*not found",
    r"Cannot find module",
    r"Module not found",
    r"error\[E0432\]",  # Rust unresolved import
    r"cannot find package",  # Go
]

_LOGIC_PATTERNS = [
    r"AssertionError",
    r"FAILED.*assert",
    r"Expected.*but got",
    r"Test failed",
    r"test.*FAILED",
    r"FAIL:",
    r"failures=\d+",
    r"errors=\d+",
]

_RUNTIME_PATTERNS = [
    r"TypeError",
    r"ValueError",
    r"KeyError",
    r"IndexError",
    r"AttributeError",
    r"RuntimeError",
    r"ZeroDivisionError",
    r"FileNotFoundError",
    r"PermissionError",
    r"OSError",
    r"Traceback \(most recent call last\)",
    r"panic:",  # Go/Rust
    r"Segmentation fault",
]

_CONFIG_PATTERNS = [
    r"FileNotFoundError.*config",
    r"No such file or directory",
    r"environment variable.*not set",
    r"missing.*configuration",
    r"ENOENT",
]


class ErrorClassifier:
    """Classifies build errors and determines retry strategy."""

    def classify(self, error_output: str) -> ClassifiedError:
        """Classify an error from build/test output."""
        output_lower = error_output.lower()

        # Check patterns in priority order
        if self._matches_any(error_output, _SYNTAX_PATTERNS):
            return ClassifiedError(
                category=ErrorCategory.SYNTAX,
                severity=ErrorSeverity.LOW,
                summary=self._extract_summary(error_output, _SYNTAX_PATTERNS),
                raw_output=error_output,
                suggested_action="Fix syntax error -- simple correction, retry with same agent.",
                auto_fixable=False,
            )

        if self._matches_any(error_output, _DEPENDENCY_PATTERNS):
            return ClassifiedError(
                category=ErrorCategory.DEPENDENCY,
                severity=ErrorSeverity.LOW,
                summary=self._extract_summary(error_output, _DEPENDENCY_PATTERNS),
                raw_output=error_output,
                suggested_action="Install missing dependency, then retry.",
                auto_fixable=True,
            )

        if self._matches_any(error_output, _CONFIG_PATTERNS):
            return ClassifiedError(
                category=ErrorCategory.CONFIGURATION,
                severity=ErrorSeverity.MEDIUM,
                summary=self._extract_summary(error_output, _CONFIG_PATTERNS),
                raw_output=error_output,
                suggested_action="Fix configuration -- create missing file or set variable.",
            )

        if self._matches_any(error_output, _LOGIC_PATTERNS):
            return ClassifiedError(
                category=ErrorCategory.LOGIC,
                severity=ErrorSeverity.MEDIUM,
                summary=self._extract_summary(error_output, _LOGIC_PATTERNS),
                raw_output=error_output,
                suggested_action="Fix logic error -- review test expectations and implementation.",
            )

        if self._matches_any(error_output, _RUNTIME_PATTERNS):
            return ClassifiedError(
                category=ErrorCategory.RUNTIME,
                severity=ErrorSeverity.MEDIUM,
                summary=self._extract_summary(error_output, _RUNTIME_PATTERNS),
                raw_output=error_output,
                suggested_action="Fix runtime error -- check types and edge cases.",
            )

        # If we can't classify, assume medium severity
        return ClassifiedError(
            category=ErrorCategory.UNKNOWN,
            severity=ErrorSeverity.MEDIUM,
            summary=error_output[:200].strip(),
            raw_output=error_output,
            suggested_action="Review error output and fix accordingly.",
        )

    def classify_repeated_failures(
        self, errors: list[ClassifiedError],
    ) -> ClassifiedError:
        """Analyze a pattern of repeated failures to detect architecture issues."""
        if len(errors) < 3:
            if errors:
                return errors[-1]
            return ClassifiedError(
                category=ErrorCategory.UNKNOWN,
                severity=ErrorSeverity.MEDIUM,
                summary="No errors to classify",
                raw_output="",
                suggested_action="No action needed.",
            )

        # If same category keeps repeating, escalate
        categories = [e.category for e in errors[-3:]]
        if len(set(categories)) == 1:
            return ClassifiedError(
                category=ErrorCategory.ARCHITECTURE,
                severity=ErrorSeverity.HIGH,
                summary=f"Persistent {categories[0].value} errors across 3+ iterations.",
                raw_output=errors[-1].raw_output,
                suggested_action=(
                    "Escalate to stronger model. The current approach has a "
                    f"fundamental {categories[0].value} issue that needs re-thinking."
                ),
            )

        return errors[-1]

    @staticmethod
    def _matches_any(text: str, patterns: list[str]) -> bool:
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False

    @staticmethod
    def _extract_summary(text: str, patterns: list[str]) -> str:
        """Extract the most relevant line matching the patterns."""
        for line in text.split("\n"):
            for pattern in patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    return line.strip()[:200]
        return text[:200].strip()
