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


# ─── Cross-Run Persistent Memory ──────────────────────────────


import json
from pathlib import Path


MEMORY_FILE = ".forge-memory.json"


@dataclass
class LearningEntry:
    """A pattern learned from a previous run."""
    pattern: str          # What was learned
    category: str         # "success", "failure", "strategy"
    objective_hint: str   # Keywords from the objective
    agent: str            # Which agent discovered this
    confidence: float     # 0.0-1.0 how confident we are
    uses: int = 0         # How many times this was used


class PersistentMemory:
    """Cross-run memory that persists learnings across pipeline runs.

    Stores patterns like:
    - "Flask apps need requirements.txt with flask>=3.0"
    - "CLI tools should use click or argparse, not sys.argv"
    - "Tests should be in tests/ not alongside source"
    """

    def __init__(self, working_dir: str) -> None:
        self.working_dir = working_dir
        self._mem_file = Path(working_dir) / MEMORY_FILE
        self._entries: list[LearningEntry] = []
        self._load()

    def _load(self) -> None:
        if not self._mem_file.exists():
            return
        try:
            data = json.loads(self._mem_file.read_text())
            for item in data.get("learnings", []):
                self._entries.append(LearningEntry(**item))
        except (json.JSONDecodeError, OSError, KeyError, TypeError):
            pass

    def save(self) -> None:
        data = {
            "version": 1,
            "learnings": [
                {
                    "pattern": e.pattern,
                    "category": e.category,
                    "objective_hint": e.objective_hint,
                    "agent": e.agent,
                    "confidence": e.confidence,
                    "uses": e.uses,
                }
                for e in self._entries
            ],
        }
        self._mem_file.write_text(json.dumps(data, indent=2))

    def add_learning(
        self,
        pattern: str,
        category: str,
        objective_hint: str,
        agent: str,
        confidence: float = 0.7,
    ) -> None:
        """Add a new learning entry."""
        # Deduplicate
        for e in self._entries:
            if e.pattern == pattern:
                e.confidence = min(1.0, e.confidence + 0.1)
                e.uses += 1
                self.save()
                return

        self._entries.append(LearningEntry(
            pattern=pattern,
            category=category,
            objective_hint=objective_hint,
            agent=agent,
            confidence=confidence,
        ))
        self.save()

    def learn_from_run(self, memory: BuildMemory, objective: str) -> None:
        """Extract learnings from a completed session memory."""
        hint = " ".join(objective.lower().split()[:5])

        for record in memory.records:
            if record.test_passed and record.files_created:
                # Successful pattern
                self.add_learning(
                    pattern=f"Created {', '.join(record.files_created[:5])} successfully",
                    category="success",
                    objective_hint=hint,
                    agent=record.agent,
                )
            elif record.error_category:
                # Failure pattern
                self.add_learning(
                    pattern=f"Avoid: {record.error_category} — {(record.error_summary or '')[:100]}",
                    category="failure",
                    objective_hint=hint,
                    agent=record.agent,
                    confidence=0.8,
                )

    def get_relevant(self, objective: str, max_entries: int = 10) -> list[LearningEntry]:
        """Get entries relevant to the given objective."""
        words = set(objective.lower().split())

        scored: list[tuple[float, LearningEntry]] = []
        for e in self._entries:
            hint_words = set(e.objective_hint.split())
            overlap = len(words & hint_words)
            score = overlap * 0.5 + e.confidence * 0.3 + (e.uses * 0.05)
            scored.append((score, e))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[:max_entries]]

    def to_prompt_section(self, objective: str) -> str:
        """Format relevant memories as a prompt section."""
        relevant = self.get_relevant(objective, max_entries=8)
        if not relevant:
            return ""

        parts = ["LEARNINGS FROM PREVIOUS RUNS:"]
        for e in relevant:
            icon = {"success": "✅", "failure": "❌", "strategy": "💡"}.get(e.category, "📝")
            parts.append(f"  {icon} [{e.category}] {e.pattern}")

        return "\n".join(parts)

    @property
    def count(self) -> int:
        return len(self._entries)

