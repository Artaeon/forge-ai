"""Prompt A/B testing for duo pipeline.

Run the same objective with two prompt variants and compare
quality scores to find which prompts produce better output.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.table import Table

console = Console()


@dataclass
class PromptVariant:
    """A named prompt variant for A/B testing."""
    name: str
    description: str
    # Prompt template overrides (injected into planning/coding prompts)
    plan_prefix: str = ""
    code_prefix: str = ""
    review_prefix: str = ""


@dataclass
class ABResult:
    """Result from running one variant."""
    variant_name: str
    quality_score: int
    grade: str
    structure_score: int
    code_score: int
    test_score: int
    docs_score: int
    duration_secs: float
    cost_usd: float


@dataclass
class ABTestResult:
    """Complete A/B test result."""
    objective: str
    variant_a: ABResult
    variant_b: ABResult
    winner: str  # "A", "B", or "TIE"
    score_delta: int
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


# ─── Built-in Prompt Variants ────────────────────────────────

VARIANTS: dict[str, PromptVariant] = {
    "default": PromptVariant(
        name="default",
        description="Standard duo pipeline prompts (baseline)",
    ),
    "strict": PromptVariant(
        name="strict",
        description="Emphasize strict typing and error handling",
        code_prefix=(
            "IMPORTANT: Use strict type hints everywhere. "
            "Every function must have type annotations. "
            "Handle all edge cases with explicit error handling. "
            "Never use 'Any' type.\n\n"
        ),
    ),
    "tdd": PromptVariant(
        name="tdd",
        description="Test-driven development emphasis",
        plan_prefix=(
            "Use a TDD approach: plan tests FIRST, then implementation. "
            "Every public function must have at least 2 test cases.\n\n"
        ),
        code_prefix=(
            "Write tests BEFORE implementation code. "
            "Use pytest. Every public function needs at least 2 tests. "
            "Aim for >80% code coverage.\n\n"
        ),
    ),
    "minimal": PromptVariant(
        name="minimal",
        description="Minimal, lean code with no extras",
        code_prefix=(
            "Write the MINIMUM code needed. No unnecessary abstractions. "
            "No unused imports. No comments unless essential. "
            "Keep functions under 20 lines.\n\n"
        ),
    ),
    "production": PromptVariant(
        name="production",
        description="Production-ready with logging, configs, and docs",
        code_prefix=(
            "Write production-grade code: "
            "structured logging (not print), configuration via env vars, "
            "comprehensive docstrings, proper error classes, "
            "and a detailed README with install/usage/API sections.\n\n"
        ),
    ),
}


def list_variants() -> list[dict]:
    """List available prompt variants."""
    return [
        {"name": v.name, "description": v.description}
        for v in VARIANTS.values()
    ]


def save_ab_result(working_dir: str, result: ABTestResult) -> Path:
    """Save A/B test result to .forge-ab-results.json."""
    results_file = Path(working_dir) / ".forge-ab-results.json"

    existing = []
    if results_file.exists():
        try:
            existing = json.loads(results_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    existing.append(asdict(result))
    results_file.write_text(json.dumps(existing, indent=2))
    return results_file


def load_ab_results(working_dir: str) -> list[dict]:
    """Load previous A/B test results."""
    results_file = Path(working_dir) / ".forge-ab-results.json"
    if not results_file.exists():
        return []
    try:
        return json.loads(results_file.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def determine_winner(a: ABResult, b: ABResult) -> tuple[str, int]:
    """Determine winner based on quality score."""
    delta = b.quality_score - a.quality_score
    if abs(delta) < 3:  # Within noise margin
        return "TIE", delta
    elif delta > 0:
        return "B", delta
    else:
        return "A", delta


def print_ab_results(result: ABTestResult) -> None:
    """Print A/B test results as a Rich table."""
    table = Table(
        title="🔬 Prompt A/B Test Results",
        show_header=True,
        header_style="bold",
        border_style="bright_black",
    )
    table.add_column("Metric", min_width=12)
    table.add_column(f"A: {result.variant_a.variant_name}", justify="right", min_width=10)
    table.add_column(f"B: {result.variant_b.variant_name}", justify="right", min_width=10)

    a, b = result.variant_a, result.variant_b

    table.add_row("Quality", f"{a.quality_score}/100", f"{b.quality_score}/100")
    table.add_row("Grade", a.grade, b.grade)
    table.add_row("Structure", f"{a.structure_score}/25", f"{b.structure_score}/25")
    table.add_row("Code", f"{a.code_score}/25", f"{b.code_score}/25")
    table.add_row("Tests", f"{a.test_score}/25", f"{b.test_score}/25")
    table.add_row("Docs", f"{a.docs_score}/25", f"{b.docs_score}/25")
    table.add_row("Time", f"{a.duration_secs:.1f}s", f"{b.duration_secs:.1f}s")
    table.add_row("Cost", f"${a.cost_usd:.4f}", f"${b.cost_usd:.4f}")

    console.print(table)

    winner_color = {"A": "green", "B": "cyan", "TIE": "yellow"}.get(result.winner, "white")
    console.print(
        f"\n[{winner_color}]🏆 Winner: {result.winner} "
        f"(Δ{result.score_delta:+d} points)[/]"
    )
