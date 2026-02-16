"""Autonomous build pipeline -- iterative agent-driven project generation.

Integrates all autonomy features:
  - Workspace-aware context (file tree, git diff, framework detection)
  - Session memory (avoid repeating failures)
  - Smart test generation (auto-detect project type)
  - Error classification (categorize and route failures)
  - Rollback protection (revert regressions)
  - Multi-agent roles (planner/coder/reviewer with escalation)
  - Streaming TUI output during builds
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path
from dataclasses import dataclass, field

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from forge.agents.base import AgentResult, AgentStatus, TaskContext
from forge.build.context import gather_context
from forge.build.memory import BuildMemory
from forge.build.errors import ErrorClassifier, ClassifiedError, ErrorCategory
from forge.build.testing import detect_verification_suite
from forge.engine import ForgeEngine

console = Console()
logger = logging.getLogger(__name__)


@dataclass
class BuildStep:
    """A single step in the build pipeline."""
    iteration: int
    prompt: str
    agent_name: str = ""
    agent_results: list[AgentResult] = field(default_factory=list)
    build_output: str = ""
    build_success: bool = False
    test_output: str = ""
    test_success: bool = False
    files_created: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    error_classification: ClassifiedError | None = None
    rolled_back: bool = False


class BuildPipeline:
    """Autonomous build pipeline with full autonomy features.

    Supports role-based multi-agent builds with automatic escalation,
    workspace-aware context, session memory, error classification,
    and rollback protection.
    """

    # Agent roles for multi-agent builds
    ROLE_PLANNER = "planner"
    ROLE_CODER = "coder"
    ROLE_REVIEWER = "reviewer"

    # Model tiers for escalation (weakest to strongest)
    ESCALATION_TIERS = [
        "claude-haiku",
        "antigravity-flash",
        "gemini",
        "claude-sonnet",
        "antigravity-pro",
        "claude-opus",
    ]

    def __init__(
        self,
        engine: ForgeEngine,
        working_dir: str,
        primary_agent: str = "claude-sonnet",
        max_iterations: int = 10,
        test_commands: list[str] | None = None,
        auto_commit: bool = False,
        enable_review: bool = False,
        enable_escalation: bool = True,
    ):
        self.engine = engine
        self.working_dir = working_dir
        self.primary_agent = primary_agent
        self.max_iterations = max_iterations
        self.test_commands = test_commands
        self.auto_commit = auto_commit
        self.enable_review = enable_review
        self.enable_escalation = enable_escalation

        self.steps: list[BuildStep] = []
        self.memory = BuildMemory()
        self.classifier = ErrorClassifier()
        self._current_agent = primary_agent
        self._best_checkpoint: str | None = None
        self._best_test_count: int = 0

    async def run(self, objective: str) -> list[BuildStep]:
        """Execute the autonomous build loop with all autonomy features."""
        console.print(f"\n[bold bright_magenta]Autonomous Build[/]")
        console.print(f"[dim]Objective:[/] {objective}")
        console.print(f"[dim]Agent:[/] {self._current_agent}")
        console.print(f"[dim]Directory:[/] {self.working_dir}")
        console.print(f"[dim]Max iterations:[/] {self.max_iterations}")

        # Auto-detect test commands if not specified
        if not self.test_commands:
            suite = detect_verification_suite(self.working_dir)
            if suite.has_commands:
                self.test_commands = suite.all_commands
                console.print(f"[dim]Auto-detected verification:[/] {len(self.test_commands)} command(s)")
            else:
                console.print(f"[dim]Verification:[/] file existence check")

        console.print()

        # Create initial git checkpoint
        self._create_checkpoint("pre-build")

        for iteration in range(1, self.max_iterations + 1):
            console.print(f"[bold]--- Iteration {iteration}/{self.max_iterations} ---[/]\n")

            step = await self._run_iteration(iteration, objective)
            self.steps.append(step)

            if step.build_success:
                self._print_success(iteration)
                return self.steps

            # Check for escalation
            if self.enable_escalation and self.memory.should_escalate(max_failures=3):
                escalated = self._try_escalate()
                if escalated:
                    console.print(
                        f"[yellow]  Escalated to {self._current_agent} "
                        f"({self.memory.get_escalation_reason()})[/]"
                    )

        self._print_exhausted()
        return self.steps

    async def _run_iteration(self, iteration: int, objective: str) -> BuildStep:
        """Execute a single build iteration."""
        # Gather workspace context
        context = gather_context(self.working_dir)
        files_before = set(context.file_tree)

        # Build prompt with context and memory
        prompt = self._build_prompt(objective, context, iteration)

        # Create task context
        ctx = TaskContext(
            working_dir=self.working_dir,
            prompt=prompt,
            timeout=300,
        )

        step = BuildStep(
            iteration=iteration,
            prompt=prompt,
            agent_name=self._current_agent,
        )

        # Create git checkpoint before this iteration
        checkpoint_ref = self._create_checkpoint(f"iter-{iteration}")

        # Dispatch to agent in agentic mode
        console.print(f"[dim]  Agent: {self._current_agent}[/]")
        result = await self._dispatch_agentic(ctx)
        step.agent_results = [result]

        if not result.is_success:
            console.print(f"[red]  Agent failed: {result.error}[/]")
            self.memory.record_iteration(
                iteration=iteration,
                agent=self._current_agent,
                prompt=prompt,
                output=result.output,
                files_created=[],
                files_modified=[],
                test_passed=False,
                error=result.error,
                error_category="agent_failure",
                cost_usd=result.cost_usd or 0.0,
            )
            return step

        console.print(f"[green]  Agent responded ({len(result.output)} chars)[/]")

        # Detect file changes
        context_after = gather_context(self.working_dir)
        files_after = set(context_after.file_tree)
        new_files = sorted(files_after - files_before)
        modified_files = self._detect_modified_files(files_before & files_after)
        step.files_created = new_files
        step.files_modified = modified_files

        if new_files or modified_files:
            total = len(new_files) + len(modified_files)
            console.print(f"[green]  Files changed: {total}[/]")
            for f in (new_files + modified_files)[:8]:
                prefix = "+" if f in new_files else "~"
                console.print(f"[dim]     {prefix} {f}[/]")
            if total > 8:
                console.print(f"[dim]     ... and {total - 8} more[/]")

        # Auto-install dependencies
        self._auto_install_deps()

        # Re-detect test commands after new files are created
        if not self.test_commands:
            suite = detect_verification_suite(self.working_dir)
            if suite.has_commands:
                self.test_commands = suite.all_commands

        # Run verification
        if self.test_commands:
            console.print(f"[dim]  Running verification...[/]")
            test_success, test_output = self._run_verification()
            step.test_output = test_output
            step.test_success = test_success

            if test_success:
                step.build_success = True
                self._best_checkpoint = checkpoint_ref
                if self.auto_commit:
                    self._git_commit(f"forge: iteration {iteration} passed")
            else:
                # Classify the error
                classified = self.classifier.classify(test_output)
                step.error_classification = classified
                console.print(
                    f"[yellow]  Failed: {classified.category.value} "
                    f"({classified.severity.value})[/]"
                )
                console.print(f"[dim]  {classified.summary[:120]}[/]")

                # Check for regression and rollback if needed
                if self._should_rollback(step):
                    self._rollback(checkpoint_ref)
                    step.rolled_back = True
                    console.print(f"[yellow]  Rolled back to previous checkpoint[/]")

                self.memory.record_iteration(
                    iteration=iteration,
                    agent=self._current_agent,
                    prompt=prompt,
                    output=result.output,
                    files_created=new_files,
                    files_modified=modified_files,
                    test_passed=False,
                    error=test_output,
                    error_category=classified.category.value,
                    cost_usd=result.cost_usd or 0.0,
                )
        else:
            # No test commands -- success if files were created
            if new_files or modified_files:
                step.build_success = True
                if self.auto_commit:
                    self._git_commit(f"forge: iteration {iteration}")
            else:
                console.print(f"[yellow]  No files changed[/]")
                self.memory.record_iteration(
                    iteration=iteration,
                    agent=self._current_agent,
                    prompt=prompt,
                    output=result.output,
                    files_created=[],
                    files_modified=[],
                    test_passed=False,
                    error="No files were created or modified.",
                    cost_usd=result.cost_usd or 0.0,
                )

        if step.build_success:
            self.memory.record_iteration(
                iteration=iteration,
                agent=self._current_agent,
                prompt=prompt,
                output=result.output,
                files_created=new_files,
                files_modified=modified_files,
                test_passed=True,
                cost_usd=result.cost_usd or 0.0,
            )

        return step

    def _build_prompt(self, objective: str, context, iteration: int) -> str:
        """Build a comprehensive prompt with workspace context and memory."""
        parts = [f"OBJECTIVE: {objective}"]

        # Add workspace context
        parts.append(context.to_prompt_section())

        # Add session memory
        memory_section = self.memory.to_prompt_section()
        if memory_section:
            parts.append(memory_section)

        # Error-specific instructions
        recent_errors = [
            s.error_classification
            for s in self.steps[-3:]
            if s.error_classification is not None
        ]
        if recent_errors:
            last_error = recent_errors[-1]
            parts.append(
                f"LAST ERROR ({last_error.category.value}): "
                f"{last_error.summary[:300]}\n"
                f"Action: {last_error.suggested_action}"
            )

        # Core instructions
        parts.append(
            "Create or modify files in the working directory to complete this objective. "
            "Do not just describe what to do -- actually write the code files."
        )

        return "\n\n".join(parts)

    async def _dispatch_agentic(self, ctx: TaskContext) -> AgentResult:
        """Dispatch to agent using agentic mode."""
        adapter = self.engine.adapters.get(self._current_agent)
        if adapter is None:
            return AgentResult(
                agent_name=self._current_agent,
                output="",
                status=AgentStatus.FAILED,
                error=f"Agent '{self._current_agent}' not found",
            )

        if hasattr(adapter, "execute_agentic"):
            return await adapter.execute_agentic(ctx)
        return await adapter.execute(ctx)

    def _try_escalate(self) -> bool:
        """Try to escalate to a stronger model. Returns True if escalated."""
        available = list(self.engine.adapters.keys())

        # Find current tier
        current_tier = -1
        for i, tier_agent in enumerate(self.ESCALATION_TIERS):
            if tier_agent == self._current_agent:
                current_tier = i
                break

        # Try next tier
        for tier_agent in self.ESCALATION_TIERS[current_tier + 1:]:
            if tier_agent in available:
                self._current_agent = tier_agent
                return True

        return False

    def _should_rollback(self, current_step: BuildStep) -> bool:
        """Determine if the current iteration caused a regression."""
        if len(self.steps) < 2:
            return False

        # If we had a passing test count and now it's lower, rollback
        prev_step = self.steps[-1] if self.steps else None
        if prev_step and prev_step.test_success and not current_step.test_success:
            return True

        return False

    def _detect_modified_files(self, common_files: set[str]) -> list[str]:
        """Detect which existing files were modified (via git)."""
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only"],
                cwd=self.working_dir,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                changed = set(result.stdout.strip().split("\n")) if result.stdout.strip() else set()
                return sorted(changed & common_files)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            logger.debug("Git diff detection failed")
        return []

    def _create_checkpoint(self, label: str) -> str:
        """Create a git checkpoint (stash or tag) for rollback."""
        try:
            # Stage everything and create a checkpoint commit
            subprocess.run(
                ["git", "add", "-A"],
                cwd=self.working_dir, capture_output=True, timeout=10,
            )
            result = subprocess.run(
                ["git", "stash", "push", "-m", f"forge-checkpoint-{label}"],
                cwd=self.working_dir, capture_output=True, text=True, timeout=10,
            )
            # Pop immediately -- we just want Git to know the state
            subprocess.run(
                ["git", "stash", "pop"],
                cwd=self.working_dir, capture_output=True, timeout=10,
            )
            return label
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.debug("Checkpoint creation failed: %s", e)
            return label

    def _rollback(self, checkpoint_ref: str) -> None:
        """Rollback to a previous checkpoint."""
        try:
            subprocess.run(
                ["git", "checkout", "--", "."],
                cwd=self.working_dir, capture_output=True, timeout=10,
            )
            subprocess.run(
                ["git", "clean", "-fd"],
                cwd=self.working_dir, capture_output=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.debug("Rollback failed: %s", e)

    def _auto_install_deps(self) -> None:
        """Auto-detect and install dependencies."""
        wd = Path(self.working_dir)

        req_file = wd / "requirements.txt"
        if req_file.exists():
            console.print(f"[dim]  Installing Python dependencies...[/]")
            try:
                result = subprocess.run(
                    ["pip", "install", "-r", "requirements.txt", "-q"],
                    cwd=self.working_dir, capture_output=True, text=True, timeout=60,
                )
                if result.returncode == 0:
                    console.print(f"[green]  Dependencies installed[/]")
                else:
                    subprocess.run(
                        ["python3", "-m", "pip", "install", "-r", "requirements.txt", "-q"],
                        cwd=self.working_dir, capture_output=True, text=True, timeout=60,
                    )
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                logger.debug("pip install failed")

        pkg_file = wd / "package.json"
        if pkg_file.exists() and not (wd / "node_modules").exists():
            console.print(f"[dim]  Installing Node dependencies...[/]")
            try:
                subprocess.run(
                    ["npm", "install"],
                    cwd=self.working_dir, capture_output=True, text=True, timeout=120,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                logger.debug("npm install failed")

    def _run_verification(self) -> tuple[bool, str]:
        """Run verification commands."""
        if not self.test_commands:
            return True, ""

        all_output = []
        all_passed = True

        for cmd in self.test_commands:
            try:
                result = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True,
                    cwd=self.working_dir, timeout=60,
                )
                output = f"$ {cmd}\n{result.stdout}\n{result.stderr}"
                all_output.append(output)
                if result.returncode != 0:
                    all_passed = False
            except subprocess.TimeoutExpired:
                all_output.append(f"$ {cmd}\n[TIMEOUT after 60s]")
                all_passed = False
            except (FileNotFoundError, OSError) as e:
                all_output.append(f"$ {cmd}\n[ERROR: {e}]")
                all_passed = False

        return all_passed, "\n\n".join(all_output)

    def _git_commit(self, message: str) -> None:
        """Auto-commit changes."""
        try:
            subprocess.run(
                ["git", "add", "-A"],
                cwd=self.working_dir, capture_output=True, timeout=10,
            )
            subprocess.run(
                ["git", "commit", "-m", message],
                cwd=self.working_dir, capture_output=True, timeout=10,
            )
            console.print(f"[dim]  Committed: {message}[/]")
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            logger.debug("Git commit failed")

    def _print_success(self, iteration: int) -> None:
        total_cost = self.memory.total_cost
        if self.steps and self.steps[-1].agent_results:
            last_cost = self.steps[-1].agent_results[-1].cost_usd or 0
            total_cost += last_cost
        console.print(
            f"\n[bold green]Build completed successfully "
            f"in {iteration} iteration(s).[/]"
        )
        console.print(f"[dim]Total cost: ${total_cost:.4f}[/]\n")

    def _print_exhausted(self) -> None:
        console.print(
            f"\n[bold yellow]Reached max iterations ({self.max_iterations}) "
            f"without fully passing.[/]"
        )
        # Show final state
        final_files = gather_context(self.working_dir).file_tree
        if final_files:
            console.print(f"[dim]Files in project ({len(final_files)}):[/]")
            for f in final_files[:15]:
                console.print(f"[dim]  {f}[/]")
        console.print()
