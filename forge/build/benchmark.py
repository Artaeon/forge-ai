"""Benchmark suite for measuring duo pipeline quality.

Defines standard objectives and runs them to track pipeline
quality over time. Results are stored in .forge-benchmark/.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.table import Table

console = Console()


# ─── Standard Benchmark Objectives ───────────────────────────

BENCHMARKS: dict[str, dict] = {
    "cli-tool": {
        "name": "CLI Todo App",
        "objective": (
            "Build a Python CLI todo app using Click with commands: "
            "add, list, complete, delete. Store todos in a JSON file. "
            "Include proper error handling, help text, and colored output."
        ),
        "expected_files": ["cli.py", "pyproject.toml", "README.md"],
        "expected_patterns": ["click", "json", "def "],
    },
    "rest-api": {
        "name": "REST API",
        "objective": (
            "Build a Flask REST API for a bookmarks service with CRUD endpoints: "
            "GET /bookmarks, POST /bookmarks, PUT /bookmarks/:id, DELETE /bookmarks/:id. "
            "Use SQLite for storage. Include input validation and error responses."
        ),
        "expected_files": ["app.py", "requirements.txt", "README.md"],
        "expected_patterns": ["flask", "sqlite", "@app.route"],
    },
    "library": {
        "name": "Python Library",
        "objective": (
            "Build a Python library called 'textmetrics' that calculates text statistics: "
            "word count, sentence count, reading level (Flesch-Kincaid), "
            "and keyword frequency. Include a clean public API and comprehensive tests."
        ),
        "expected_files": ["pyproject.toml", "README.md"],
        "expected_patterns": ["def ", "class ", "assert"],
    },
    "game": {
        "name": "Terminal Game",
        "objective": (
            "Build a terminal-based number guessing game in Python. "
            "Features: difficulty levels, score tracking, high score persistence, "
            "colored output, and replay option. Clean code with type hints."
        ),
        "expected_files": ["pyproject.toml", "README.md"],
        "expected_patterns": ["input(", "random", "def "],
    },
    "mcp-server": {
        "name": "MCP Server",
        "objective": (
            "Build an MCP (Model Context Protocol) server that provides "
            "filesystem tools: read_file, write_file, list_directory, search_files. "
            "Use the MCP Python SDK. Include proper error handling and tests."
        ),
        "expected_files": ["pyproject.toml", "README.md"],
        "expected_patterns": ["mcp", "def ", "async"],
    },
}


@dataclass
class BenchmarkResult:
    """Result from a single benchmark run."""
    benchmark_id: str
    benchmark_name: str
    quality_score: int
    grade: str
    structure_score: int
    code_score: int
    test_score: int
    docs_score: int
    duration_secs: float
    cost_usd: float
    planner: str
    coder: str
    timestamp: str = ""
    files_created: int = 0
    errors: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


@dataclass
class BenchmarkSuite:
    """Results from a complete benchmark run."""
    results: list[BenchmarkResult] = field(default_factory=list)
    avg_score: float = 0.0
    avg_duration: float = 0.0
    total_cost: float = 0.0

    def compute_stats(self) -> None:
        if not self.results:
            return
        self.avg_score = sum(r.quality_score for r in self.results) / len(self.results)
        self.avg_duration = sum(r.duration_secs for r in self.results) / len(self.results)
        self.total_cost = sum(r.cost_usd for r in self.results)


def list_benchmarks() -> list[dict]:
    """List available benchmark objectives."""
    return [
        {"id": k, "name": v["name"], "objective": v["objective"][:80] + "..."}
        for k, v in BENCHMARKS.items()
    ]


def save_benchmark_results(
    working_dir: str, suite: BenchmarkSuite
) -> Path:
    """Save benchmark results to .forge-benchmark/."""
    bench_dir = Path(working_dir) / ".forge-benchmark"
    bench_dir.mkdir(exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    result_file = bench_dir / f"run_{ts}.json"

    data = {
        "timestamp": ts,
        "avg_score": suite.avg_score,
        "avg_duration": suite.avg_duration,
        "total_cost": suite.total_cost,
        "results": [asdict(r) for r in suite.results],
    }

    result_file.write_text(json.dumps(data, indent=2))
    return result_file


def load_benchmark_history(working_dir: str) -> list[dict]:
    """Load all previous benchmark runs."""
    bench_dir = Path(working_dir) / ".forge-benchmark"
    if not bench_dir.exists():
        return []

    runs = []
    for f in sorted(bench_dir.glob("run_*.json")):
        try:
            runs.append(json.loads(f.read_text()))
        except Exception:
            continue
    return runs


def compare_benchmarks(a: BenchmarkSuite, b: BenchmarkSuite) -> str:
    """Compare two benchmark runs and return a summary."""
    lines = ["# Benchmark Comparison\n"]

    lines.append(f"{'Benchmark':<20} {'Run A':>8} {'Run B':>8} {'Delta':>8}")
    lines.append("-" * 48)

    for ra in a.results:
        rb = next((r for r in b.results if r.benchmark_id == ra.benchmark_id), None)
        if rb:
            delta = rb.quality_score - ra.quality_score
            sign = "+" if delta > 0 else ""
            lines.append(
                f"{ra.benchmark_name:<20} {ra.quality_score:>7}  {rb.quality_score:>7}  {sign}{delta:>7}"
            )

    lines.append("-" * 48)
    lines.append(
        f"{'Average':<20} {a.avg_score:>7.1f}  {b.avg_score:>7.1f}  "
        f"{'+' if b.avg_score > a.avg_score else ''}{b.avg_score - a.avg_score:>7.1f}"
    )

    return "\n".join(lines)


def print_benchmark_results(suite: BenchmarkSuite) -> None:
    """Print benchmark results as a Rich table."""
    table = Table(
        title="🏋️ Forge Benchmark Results",
        show_header=True,
        header_style="bold",
        border_style="bright_black",
    )
    table.add_column("Benchmark", min_width=16)
    table.add_column("Score", justify="right", min_width=6)
    table.add_column("Grade", justify="center", min_width=6)
    table.add_column("Time", justify="right", min_width=8)
    table.add_column("Cost", justify="right", style="yellow", min_width=8)

    for r in suite.results:
        grade_colors = {"A": "green", "B": "green", "C": "yellow", "D": "yellow", "F": "red"}
        color = grade_colors.get(r.grade, "white")
        table.add_row(
            r.benchmark_name,
            f"{r.quality_score}/100",
            f"[{color}]{r.grade}[/]",
            f"{r.duration_secs:.1f}s",
            f"${r.cost_usd:.4f}",
        )

    table.add_section()
    table.add_row(
        "Average",
        f"{suite.avg_score:.0f}/100",
        "",
        f"{suite.avg_duration:.1f}s",
        f"${suite.total_cost:.4f}",
    )

    console.print(table)
