"""Collaborative (duo) build pipeline — two agents iterate toward v1.

Flow:
  1. PLAN:   Planner agent creates README, architecture, and file list
  2. CODE:   Coder agent implements all files based on the plan
  3. REVIEW: Planner reviews the code, lists issues and improvements
  4. FIX:    Coder fixes issues from the review
  5. Repeat steps 3-4 until reviewer approves or max rounds reached
  6. FINAL:  Auto-commit the finished project

Usage:
  forge duo "Build a todo app with user auth" --planner gemini --coder claude-sonnet
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from dataclasses import dataclass, field

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from rich.markdown import Markdown

from forge.agents.base import AgentResult, AgentStatus, TaskContext
from forge.engine import ForgeEngine
from forge.build.compact import gather_compact, build_history_summary

console = Console()


# ─── Phase labels ─────────────────────────────────────────────

PHASE_PLAN = "PLAN"
PHASE_CODE = "CODE"
PHASE_REVIEW = "REVIEW"
PHASE_FIX = "FIX"

PHASE_ICONS = {
    PHASE_PLAN: "📋",
    PHASE_CODE: "⚡",
    PHASE_REVIEW: "🔍",
    PHASE_FIX: "🔧",
}


@dataclass
class DuoRound:
    """A single round in the collaborative build."""
    round_number: int
    phase: str
    agent_name: str
    prompt: str
    output: str
    success: bool
    duration_ms: int = 0
    cost_usd: float | None = None


@dataclass
class DuoResult:
    """Final result of the collaborative build."""
    rounds: list[DuoRound] = field(default_factory=list)
    approved: bool = False
    total_rounds: int = 0
    files_created: list[str] = field(default_factory=list)


class DuoBuildPipeline:
    """Collaborative build: two agents iterate toward a finished product.

    The planner creates the architecture and reviews code.
    The coder implements and fixes based on feedback.
    They iterate until the reviewer approves or max rounds are reached.
    """

    def __init__(
        self,
        engine: ForgeEngine,
        working_dir: str,
        planner_agent: str = "gemini",
        coder_agent: str = "claude-sonnet",
        max_rounds: int = 5,
        auto_commit: bool = True,
        timeout: int = 300,
    ):
        self.engine = engine
        self.working_dir = working_dir
        self.planner = planner_agent
        self.coder = coder_agent
        self.max_rounds = max_rounds
        self.auto_commit = auto_commit
        self.timeout = timeout
        self.rounds: list[DuoRound] = []

    async def run(self, objective: str) -> DuoResult:
        """Execute the full collaborative build loop."""
        result = DuoResult()

        # ── Phase 1: PLAN ─────────────────────────────────────
        self._print_phase(PHASE_PLAN, self.planner, "Creating project plan...")
        plan = await self._plan(objective)
        result.rounds.append(plan)
        self.rounds.append(plan)

        if not plan.success:
            console.print(f"[red]Planning failed: {plan.output[:200]}[/]")
            return result

        self._print_output(plan)

        # ── Phase 2: CODE ─────────────────────────────────────
        self._print_phase(PHASE_CODE, self.coder, "Implementing from plan...")
        code_round = await self._code(objective, plan.output)
        result.rounds.append(code_round)
        self.rounds.append(code_round)

        if not code_round.success:
            console.print(f"[red]Coding failed: {code_round.output[:200]}[/]")
            return result

        self._print_output(code_round)

        # ── Phases 3-4: REVIEW / FIX loop ─────────────────────
        for iteration in range(1, self.max_rounds + 1):
            # Review
            self._print_phase(
                PHASE_REVIEW, self.planner,
                f"Review round {iteration}/{self.max_rounds}..."
            )
            review = await self._review(objective, iteration)
            result.rounds.append(review)
            self.rounds.append(review)
            self._print_output(review)

            # Check if approved
            if self._is_approved(review.output):
                result.approved = True
                console.print(
                    f"\n[bold green]✅ APPROVED by {self.planner} "
                    f"after {iteration} review round(s)[/]\n"
                )
                break

            # Fix
            self._print_phase(
                PHASE_FIX, self.coder,
                f"Fixing issues from review {iteration}..."
            )
            fix_round = await self._fix(objective, review.output, iteration)
            result.rounds.append(fix_round)
            self.rounds.append(fix_round)
            self._print_output(fix_round)

        # ── Finalize ──────────────────────────────────────────
        result.total_rounds = len(result.rounds)
        result.files_created = self._list_project_files()

        if self.auto_commit:
            self._auto_commit(objective)

        self._print_summary(result)
        return result

    # ─── Phase implementations ────────────────────────────────

    async def _plan(self, objective: str) -> DuoRound:
        """Planner creates the project architecture and README."""
        prompt = (
            f"You are a senior software architect designing a production-ready project.\n\n"
            f"OBJECTIVE: {objective}\n\n"
            f"Create a detailed project plan with these sections:\n\n"
            f"## 1. README.md Content\n"
            f"Write the FULL README.md including:\n"
            f"- Project name and one-line description\n"
            f"- Features list (bullet points)\n"
            f"- Installation instructions (exact commands)\n"
            f"- Usage examples with code blocks\n"
            f"- Configuration options (if any)\n\n"
            f"## 2. File Structure\n"
            f"List EVERY file to create with:\n"
            f"- Full relative path\n"
            f"- One-line purpose description\n"
            f"- Key classes/functions it should contain\n\n"
            f"## 3. Tech Stack\n"
            f"- Language and version requirements\n"
            f"- Dependencies with version constraints (e.g. click>=8.0)\n"
            f"- Dev dependencies (pytest, ruff, etc.)\n\n"
            f"## 4. Architecture\n"
            f"- Data flow between modules\n"
            f"- Key design patterns (e.g. factory, strategy, plugin)\n"
            f"- Error handling strategy\n"
            f"- Testing strategy (what to test, how)\n\n"
            f"Be precise with file paths and function signatures. "
            f"Another AI agent will implement this — ambiguity causes poor code."
        )
        return await self._dispatch(PHASE_PLAN, self.planner, prompt)

    async def _code(self, objective: str, plan: str) -> DuoRound:
        """Coder implements the full project from the plan."""
        # Pass the FULL plan — it's the blueprint, don't summarize it
        # Truncate only if extremely long (>8K chars)
        if len(plan) > 8000:
            plan_text = plan[:7500] + "\n\n... (plan truncated for length)"
        else:
            plan_text = plan

        prompt = (
            f"You are a senior software engineer. Implement this project completely.\n\n"
            f"OBJECTIVE: {objective}\n\n"
            f"PROJECT PLAN:\n{plan_text}\n\n"
            f"Working directory: {self.working_dir}\n\n"
            f"QUALITY STANDARDS:\n"
            f"- Create ALL files from the plan — missing files = failed build\n"
            f"- Write COMPLETE code — no TODOs, no placeholders, no 'implement later'\n"
            f"- Include proper type hints, docstrings, and error handling\n"
            f"- Add __init__.py files for all packages\n"
            f"- Create pyproject.toml (or package.json) with all dependencies\n"
            f"- Write at least one test file with real test cases\n"
            f"- Create a proper .gitignore\n"
            f"- The README.md should match what the plan specified\n\n"
            f"Write production-ready code that works out of the box after install."
        )
        return await self._dispatch_agentic(PHASE_CODE, self.coder, prompt)

    async def _review(self, objective: str, iteration: int) -> DuoRound:
        """Reviewer examines the code and produces feedback."""
        ctx = gather_compact(self.working_dir)

        # Build compact history of previous rounds
        history = build_history_summary(
            [{"agent_name": r.agent_name, "phase": r.phase, "output": r.output}
             for r in self.rounds],
            max_total=800,
        )

        # Read key files for the reviewer to actually inspect
        file_samples = self._read_key_files_for_review()

        prompt = (
            f"You are a senior code reviewer performing a thorough quality audit.\n\n"
            f"OBJECTIVE: {objective}\n"
            f"Review round: {iteration}/{self.max_rounds}\n\n"
            f"PROJECT FILES: {ctx.to_prompt()}\n\n"
        )

        if file_samples:
            prompt += f"KEY FILE CONTENTS:\n{file_samples}\n\n"

        if history:
            prompt += f"PREVIOUS ROUNDS:\n{history}\n\n"

        prompt += (
            f"REVIEW CRITERIA (check each):\n"
            f"1. COMPLETENESS — Does the code fully implement the objective?\n"
            f"2. CORRECTNESS — Are there bugs, logic errors, or crashes?\n"
            f"3. STRUCTURE — Is the code well-organized with proper separation?\n"
            f"4. QUALITY — Type hints, docstrings, error handling present?\n"
            f"5. TESTS — Do test files exist with meaningful test cases?\n"
            f"6. PACKAGING — Is there pyproject.toml/package.json with deps?\n"
            f"7. DOCS — Does README have install + usage instructions?\n\n"
            f"RESPONSE FORMAT:\n"
            f"If the project is COMPLETE and PRODUCTION-READY, respond:\n"
            f"APPROVED\n"
            f"[brief summary of what's good]\n\n"
            f"If NOT ready, respond with:\n"
            f"ISSUES:\n"
            f"- [CRITICAL] file.py: description of critical bug\n"
            f"- [MISSING] description of missing feature\n"
            f"- [QUALITY] file.py: quality improvement needed\n\n"
            f"List max 7 issues, prioritized by severity. Be specific with file names."
        )
        return await self._dispatch(PHASE_REVIEW, self.planner, prompt)

    async def _fix(self, objective: str, review_feedback: str, iteration: int) -> DuoRound:
        """Coder fixes issues identified in the review."""
        ctx = gather_compact(self.working_dir)

        # Pass FULL review feedback — the specific issues are critical context
        if len(review_feedback) > 3000:
            feedback_text = review_feedback[:2500] + "\n\n... (truncated)"
        else:
            feedback_text = review_feedback

        prompt = (
            f"You are a senior software engineer fixing issues from a code review.\n\n"
            f"OBJECTIVE: {objective}\n\n"
            f"REVIEW FEEDBACK — fix ALL of these:\n{feedback_text}\n\n"
            f"CURRENT PROJECT: {ctx.to_prompt()}\n"
            f"Working directory: {self.working_dir}\n\n"
            f"INSTRUCTIONS:\n"
            f"- Fix every issue listed in the review\n"
            f"- Create any missing files mentioned\n"
            f"- Don't break existing working code while fixing\n"
            f"- After fixing, verify the project still runs/imports correctly\n\n"
            f"Fix iteration: {iteration}/{self.max_rounds}"
        )
        return await self._dispatch_agentic(PHASE_FIX, self.coder, prompt)

    def _read_key_files_for_review(self, max_total_chars: int = 4000) -> str:
        """Read key project files for the reviewer to inspect.

        Returns a compact representation of important source files.
        Prioritizes: entry points, config files, core modules.
        """
        wd = Path(self.working_dir)
        priority_patterns = [
            "README.md", "pyproject.toml", "package.json", "setup.py",
            "requirements.txt",
        ]
        # Then scan for source files
        source_exts = {".py", ".js", ".ts", ".go", ".rs", ".java"}

        files_to_read: list[Path] = []

        # Priority files first
        for pattern in priority_patterns:
            f = wd / pattern
            if f.exists():
                files_to_read.append(f)

        # Source files (skip tests, sort by size — smaller = more likely core)
        skip = {".git", "__pycache__", "node_modules", ".venv", "venv"}
        source_files = []
        for p in wd.rglob("*"):
            if p.is_file() and p.suffix in source_exts:
                rel = p.relative_to(wd)
                if not any(part in skip or part.startswith(".") for part in rel.parts):
                    source_files.append(p)
        source_files.sort(key=lambda p: p.stat().st_size)
        files_to_read.extend(source_files[:10])

        # Read files with truncation
        parts = []
        total = 0
        for f in files_to_read:
            if total >= max_total_chars:
                break
            try:
                content = f.read_text(errors="replace")
                rel = str(f.relative_to(wd))
                budget = min(len(content), max_total_chars - total, 1500)
                snippet = content[:budget]
                if len(content) > budget:
                    snippet += f"\n... ({len(content) - budget} more chars)"
                parts.append(f"--- {rel} ---\n{snippet}")
                total += len(snippet)
            except Exception:
                continue

        return "\n\n".join(parts)

    # ─── Dispatch helpers ─────────────────────────────────────

    async def _dispatch(self, phase: str, agent: str, prompt: str) -> DuoRound:
        """Dispatch to an agent in read-only mode."""
        ctx = TaskContext(
            working_dir=self.working_dir,
            prompt=prompt,
            timeout=self.timeout,
        )
        result = await self.engine.dispatch_single(agent, ctx)
        return DuoRound(
            round_number=len(self.rounds) + 1,
            phase=phase,
            agent_name=agent,
            prompt=prompt[:200],
            output=result.output,
            success=result.is_success,
            duration_ms=result.duration_ms,
            cost_usd=result.cost_usd,
        )

    async def _dispatch_agentic(self, phase: str, agent: str, prompt: str) -> DuoRound:
        """Dispatch to an agent in agentic mode (can write files).

        If the agent can't natively write files (like Gemini CLI),
        we parse its text output for file blocks and write them ourselves.
        """
        ctx = TaskContext(
            working_dir=self.working_dir,
            prompt=prompt,
            timeout=self.timeout,
        )

        adapter = self.engine.adapters.get(agent)
        if adapter is None:
            return DuoRound(
                round_number=len(self.rounds) + 1,
                phase=phase,
                agent_name=agent,
                prompt=prompt[:200],
                output=f"Agent '{agent}' not found",
                success=False,
            )

        # Count files before execution
        files_before = set(self._list_project_files())

        if hasattr(adapter, "execute_agentic"):
            result = await adapter.execute_agentic(ctx)
        else:
            result = await adapter.execute(ctx)

        # Check if any files were actually created
        files_after = set(self._list_project_files())
        new_files = files_after - files_before

        # Fallback: if no files were created on disk, parse output for file blocks
        if result.is_success and not new_files and result.output:
            extracted = self._extract_files_from_output(result.output)
            if extracted:
                console.print(
                    f"[dim]  📝 Extracted {len(extracted)} file(s) from output[/]"
                )
                result.output = (
                    f"Extracted {len(extracted)} file(s): "
                    + ", ".join(extracted)
                    + "\n\n" + result.output
                )

        return DuoRound(
            round_number=len(self.rounds) + 1,
            phase=phase,
            agent_name=agent,
            prompt=prompt[:200],
            output=result.output,
            success=result.is_success,
            duration_ms=result.duration_ms,
            cost_usd=result.cost_usd,
        )

    def _extract_files_from_output(self, output: str) -> list[str]:
        """Parse file blocks from agent text output and write to disk.

        Fallback for agents that can't write files natively (e.g. Gemini CLI).
        Supports multiple output formats.
        """
        import re

        # Strip noise
        clean_lines = []
        for line in output.split("\n"):
            if any(skip in line for skip in [
                "Error executing tool",
                "Tool execution denied",
                "Hook registry initialized",
                "Loaded cached credentials",
                "Did you mean one of:",
            ]):
                continue
            clean_lines.append(line)
        clean = "\n".join(clean_lines)

        written = []

        # Pattern 1: === FILE: path === ... === END FILE ===
        p1 = r"=== FILE:\s*(.+?)\s*===\n(.*?)(?=\n=== END FILE ===|\n=== FILE:|\Z)"
        matches = re.findall(p1, clean, re.DOTALL)

        # Pattern 2: ```path\n...\n```
        if not matches:
            p2 = r"```(\S+/\S+\.\w+)\n(.*?)```"
            matches = re.findall(p2, clean, re.DOTALL)

        # Pattern 3: --- path ---
        if not matches:
            p3 = r"---\s*(\S+/\S+\.\w+)\s*---\n(.*?)(?=\n---\s|\Z)"
            matches = re.findall(p3, clean, re.DOTALL)

        for filepath, content in matches:
            filepath = filepath.strip()
            content = content.rstrip("\n") + "\n"

            # Security
            if ".." in filepath or filepath.startswith("/"):
                continue
            if "/" not in filepath and "." not in filepath:
                continue

            full_path = Path(self.working_dir) / filepath
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content)
            written.append(filepath)

        return written

    # ─── Approval detection ──────────────────────────────────

    @staticmethod
    def _is_approved(review_output: str) -> bool:
        """Check if the reviewer approved the project."""
        first_line = review_output.strip().split("\n")[0].lower()
        return first_line.startswith("approved") or "approved" in first_line[:80]

    # ─── Project files ────────────────────────────────────────

    def _list_project_files(self) -> list[str]:
        """List files in the project directory."""
        wd = Path(self.working_dir)
        files = []
        for f in sorted(wd.rglob("*")):
            if f.is_file() and ".git" not in f.parts:
                files.append(str(f.relative_to(wd)))
        return files

    # ─── Auto-commit ──────────────────────────────────────────

    def _auto_commit(self, objective: str) -> None:
        """Commit the finished project."""
        try:
            # Init git if needed
            git_dir = Path(self.working_dir) / ".git"
            if not git_dir.exists():
                subprocess.run(
                    ["git", "init"], cwd=self.working_dir,
                    capture_output=True, timeout=10,
                )

            subprocess.run(
                ["git", "add", "-A"], cwd=self.working_dir,
                capture_output=True, timeout=10,
            )

            msg = (
                f"v1.0: {objective[:80]}\n\n"
                f"Built collaboratively:\n"
                f"  Planner/Reviewer: {self.planner}\n"
                f"  Coder: {self.coder}\n"
                f"  Rounds: {len(self.rounds)}"
            )
            subprocess.run(
                ["git", "commit", "-m", msg],
                cwd=self.working_dir, capture_output=True, timeout=10,
            )
            console.print("[dim]  Auto-committed as v1.0[/]")
        except Exception as e:
            console.print(f"[dim]  Auto-commit skipped: {e}[/]")

    # ─── TUI output ──────────────────────────────────────────

    def _print_phase(self, phase: str, agent: str, message: str) -> None:
        """Print a phase header."""
        icon = PHASE_ICONS.get(phase, "🔄")
        agent_upper = agent.upper()

        # Color mapping
        colors = {
            "gemini": "bright_cyan",
            "claude-sonnet": "bright_magenta",
            "claude-opus": "magenta",
            "claude-haiku": "orchid1",
            "antigravity-pro": "bright_blue",
            "antigravity-flash": "dodger_blue2",
        }
        color = colors.get(agent, "white")

        console.print(
            f"\n{icon} [bold]{phase}[/] "
            f"→ [bold {color}]{agent_upper}[/] "
            f"[dim]{message}[/]"
        )

    def _print_output(self, round_: DuoRound) -> None:
        """Print the output of a round."""
        # Color mapping
        colors = {
            "gemini": "bright_cyan",
            "claude-sonnet": "bright_magenta",
            "claude-opus": "magenta",
            "antigravity-pro": "bright_blue",
        }
        color = colors.get(round_.agent_name, "white")

        duration = f"{round_.duration_ms / 1000:.1f}s" if round_.duration_ms else ""
        cost = f" ${round_.cost_usd:.4f}" if round_.cost_usd else ""

        # Truncate output for display
        display_output = round_.output[:2000]
        if len(round_.output) > 2000:
            display_output += f"\n\n... ({len(round_.output) - 2000} more chars)"

        header = Text()
        header.append(f"{PHASE_ICONS.get(round_.phase, '')} ", style="bold")
        header.append(f"{round_.phase} ", style="bold")
        header.append(f"— {round_.agent_name.upper()} ", style=f"bold {color}")
        if duration:
            header.append(f" {duration}", style="dim")
        if cost:
            header.append(f" {cost}", style="yellow")

        console.print(Panel(
            display_output,
            title=header,
            border_style=color if round_.success else "red",
            padding=(1, 2),
        ))

    def _print_summary(self, result: DuoResult) -> None:
        """Print the final build summary."""
        console.print()

        table = Table(
            title="🏗️  Collaborative Build Summary",
            show_header=True,
            header_style="bold",
            border_style="bright_black",
        )
        table.add_column("Round", justify="center", min_width=6)
        table.add_column("Phase", min_width=8)
        table.add_column("Agent", min_width=16)
        table.add_column("Time", justify="right", min_width=8)
        table.add_column("Cost", justify="right", style="yellow", min_width=8)

        total_cost = 0.0
        total_time = 0

        for r in result.rounds:
            colors = {
                "gemini": "bright_cyan",
                "claude-sonnet": "bright_magenta",
                "claude-opus": "magenta",
                "antigravity-pro": "bright_blue",
            }
            color = colors.get(r.agent_name, "white")
            icon = PHASE_ICONS.get(r.phase, "")

            dur = f"{r.duration_ms / 1000:.1f}s" if r.duration_ms else "—"
            cost = f"${r.cost_usd:.4f}" if r.cost_usd else "—"

            total_time += r.duration_ms
            total_cost += r.cost_usd or 0

            table.add_row(
                str(r.round_number),
                f"{icon} {r.phase}",
                Text(r.agent_name.upper(), style=f"bold {color}"),
                dur,
                cost,
            )

        table.add_section()
        table.add_row(
            "", "", Text("TOTAL", style="bold"),
            f"{total_time / 1000:.1f}s",
            f"${total_cost:.4f}" if total_cost > 0 else "—",
        )

        console.print(table)

        # Status
        if result.approved:
            console.print(f"\n[bold green]✅ Project approved and committed as v1.0[/]")
        else:
            console.print(f"\n[bold yellow]⚠  Max review rounds reached — project may need manual review[/]")

        # Files
        if result.files_created:
            console.print(f"\n[dim]📂 {len(result.files_created)} file(s) created:[/]")
            for f in result.files_created[:20]:
                console.print(f"[dim]   {f}[/]")
            if len(result.files_created) > 20:
                console.print(f"[dim]   ... and {len(result.files_created) - 20} more[/]")
