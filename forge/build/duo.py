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
from forge.build.compact import gather_compact, summarize_round, build_history_summary

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
            f"ROLE: Project architect\n"
            f"OBJECTIVE: {objective}\n\n"
            f"Create a concise project plan:\n"
            f"1. Tech stack and dependencies\n"
            f"2. File list with one-line descriptions\n"
            f"3. Key architecture decisions\n\n"
            f"Be specific about file paths. Keep it concise — this goes to a coder agent."
        )
        return await self._dispatch(PHASE_PLAN, self.planner, prompt)

    async def _code(self, objective: str, plan: str) -> DuoRound:
        """Coder implements the full project from the plan."""
        # Summarize the plan to avoid passing 5K+ raw tokens
        compact_plan = summarize_round(self.planner, PHASE_PLAN, plan, max_chars=2000)
        prompt = (
            f"ROLE: Software engineer — implement this project.\n"
            f"OBJECTIVE: {objective}\n\n"
            f"PLAN:\n{compact_plan}\n\n"
            f"Dir: {self.working_dir}\n\n"
            f"Create ALL files. Write complete production code, no placeholders."
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

        prompt = (
            f"ROLE: Senior code reviewer\n"
            f"OBJECTIVE: {objective}\n"
            f"Review {iteration}/{self.max_rounds}\n\n"
            f"PROJECT: {ctx.to_prompt()}\n\n"
        )

        if history:
            prompt += f"HISTORY:\n{history}\n\n"

        prompt += (
            f"Review for: bugs, missing features, error handling, tests, structure.\n"
            f"If COMPLETE and PRODUCTION-READY, start with APPROVED.\n"
            f"If NOT, list issues concisely (max 5 bullet points)."
        )
        return await self._dispatch(PHASE_REVIEW, self.planner, prompt)

    async def _fix(self, objective: str, review_feedback: str, iteration: int) -> DuoRound:
        """Coder fixes issues identified in the review."""
        # Extract only the actionable issues from review
        compact_feedback = summarize_round(
            self.planner, PHASE_REVIEW, review_feedback, max_chars=1000
        )
        ctx = gather_compact(self.working_dir)

        prompt = (
            f"ROLE: Software engineer — fix review issues.\n"
            f"OBJECTIVE: {objective}\n\n"
            f"ISSUES TO FIX:\n{compact_feedback}\n\n"
            f"PROJECT: {ctx.to_prompt()}\n\n"
            f"Dir: {self.working_dir}\n"
            f"Fix iteration {iteration}. Address every issue. Make changes directly."
        )
        return await self._dispatch_agentic(PHASE_FIX, self.coder, prompt)

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
        """Dispatch to an agent in agentic mode (can write files)."""
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

        if hasattr(adapter, "execute_agentic"):
            result = await adapter.execute_agentic(ctx)
        else:
            result = await adapter.execute(ctx)

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
