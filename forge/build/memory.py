"""Session memory for build iterations.

Persists iteration history so agents can learn from previous attempts,
avoid repeating failed approaches, and build on what worked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class IterationRecord:
    """Record of a single build iteration."""
    iteration: int
    agent: str
    prompt_summary: str  # First ~200 chars of prompt
    output_summary: str  # First ~500 chars of output
    files_created: list[str]
    files_modified: list[str]
    test_passed: bool
    error_summary: str | None = None
    error_category: str | None = None  # syntax, dependency, logic, architecture
    cost_usd: float = 0.0
    strategy_used: str = ""  # What approach was taken


class BuildMemory:
    """Session memory that tracks what has been tried across iterations.
    
    Provides agents with a summary of previous attempts so they can:
    - Avoid repeating the same failed approach
    - Build on partially successful work
    - Understand the progression of the build
    """

    def __init__(self) -> None:
        self._records: list[IterationRecord] = []
        self._failed_approaches: list[str] = []
        self._successful_files: set[str] = set()
        self._total_cost: float = 0.0

    @property
    def iteration_count(self) -> int:
        return len(self._records)

    @property
    def total_cost(self) -> float:
        return self._total_cost

    @property
    def records(self) -> list[IterationRecord]:
        return self._records.copy()

    @property
    def has_successes(self) -> bool:
        return any(r.test_passed for r in self._records)

    @property
    def consecutive_failures(self) -> int:
        """Count consecutive failures from the end."""
        count = 0
        for r in reversed(self._records):
            if r.test_passed:
                break
            count += 1
        return count

    def record_iteration(
        self,
        iteration: int,
        agent: str,
        prompt: str,
        output: str,
        files_created: list[str],
        files_modified: list[str],
        test_passed: bool,
        error: str | None = None,
        error_category: str | None = None,
        cost_usd: float = 0.0,
    ) -> None:
        """Record the outcome of a build iteration."""
        record = IterationRecord(
            iteration=iteration,
            agent=agent,
            prompt_summary=prompt[:200],
            output_summary=output[:500],
            files_created=files_created,
            files_modified=files_modified,
            test_passed=test_passed,
            error_summary=error[:500] if error else None,
            error_category=error_category,
            cost_usd=cost_usd,
        )
        self._records.append(record)
        self._total_cost += cost_usd

        if test_passed:
            self._successful_files.update(files_created)
            self._successful_files.update(files_modified)
        elif error:
            self._failed_approaches.append(
                f"Iteration {iteration} ({agent}): {error[:200]}"
            )

    def to_prompt_section(self) -> str:
        """Format memory as a prompt section for agents.
        
        Gives the agent a concise history of what happened so far,
        what failed, and what worked.
        """
        if not self._records:
            return ""

        parts = ["BUILD HISTORY:"]

        # Summary line
        total = len(self._records)
        passed = sum(1 for r in self._records if r.test_passed)
        parts.append(
            f"  {total} iteration(s) so far, {passed} passed, "
            f"{total - passed} failed. Total cost: ${self._total_cost:.4f}"
        )

        # Per-iteration summary (last 5 to avoid prompt bloat)
        recent = self._records[-5:]
        for r in recent:
            status = "PASSED" if r.test_passed else "FAILED"
            line = f"  Iteration {r.iteration} ({r.agent}) -- {status}"
            if r.files_created:
                line += f" | Created: {', '.join(r.files_created[:5])}"
            if r.error_summary:
                line += f" | Error: {r.error_summary[:100]}"
            if r.error_category:
                line += f" [{r.error_category}]"
            parts.append(line)

        # Failed approaches warning
        if self._failed_approaches:
            parts.append("\nPREVIOUS FAILED APPROACHES (do NOT repeat these):")
            for approach in self._failed_approaches[-3:]:
                parts.append(f"  - {approach}")

        # Successful files
        if self._successful_files:
            parts.append(
                f"\nFiles that were successfully created/modified: "
                f"{', '.join(sorted(self._successful_files)[:10])}"
            )

        return "\n".join(parts)

    def should_escalate(self, max_failures: int = 3) -> bool:
        """Determine if the agent should be escalated to a stronger model."""
        return self.consecutive_failures >= max_failures

    def get_escalation_reason(self) -> str:
        """Get a human-readable reason for escalation."""
        if not self._records:
            return ""

        recent_errors = [
            r.error_category or "unknown"
            for r in self._records[-3:]
            if not r.test_passed
        ]

        if recent_errors:
            return (
                f"Failed {self.consecutive_failures} consecutive iterations. "
                f"Error types: {', '.join(recent_errors)}"
            )
        return f"Failed {self.consecutive_failures} consecutive iterations."
