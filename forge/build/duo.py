"""Collaborative (duo) build pipeline — two agents iterate toward v1.

Flow:
  1. SCAFFOLD: Auto-detect project type, create skeleton files
  2. PLAN:     Planner agent creates README, architecture, and file list
  3. CODE:     Coder agent implements all files based on the plan
  4. VERIFY:   Run build + tests, capture errors
  5. REVIEW:   Planner reviews the code with verification results
  6. FIX:      Coder fixes issues (gets real error output)
  7. Repeat steps 4-6 until reviewer approves or max rounds reached
  8. FINAL:    Auto-commit the finished project

Usage:
  forge duo "Build a todo app with user auth" --planner gemini --coder claude-sonnet
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from dataclasses import dataclass, field

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.table import Table

from forge.agents.base import TaskContext
from forge.engine import ForgeEngine
from forge.build.compact import gather_compact, build_history_summary
from forge.build.validate import validate_project
from forge.build.templates import detect_template, scaffold_template
from forge.build.testing import detect_verification_suite
from forge.build.resume import save_state, load_state, clear_state
from forge.build.depfix import resolve_missing_deps
from forge.build.scoring import score_project

# Phase modules — extracted from this file for maintainability
from forge.build.phases.plan import run_plan
from forge.build.phases.code import run_code
from forge.build.phases.verify import run_verify
from forge.build.phases.review import run_review, run_fix

console = Console()
logger = logging.getLogger(__name__)


# ─── Phase labels ─────────────────────────────────────────────

PHASE_PLAN = "PLAN"
PHASE_CODE = "CODE"
PHASE_VERIFY = "VERIFY"
PHASE_REVIEW = "REVIEW"
PHASE_FIX = "FIX"

PHASE_ICONS: dict[str, str] = {
    PHASE_PLAN: "📋",
    PHASE_CODE: "💻",
    PHASE_VERIFY: "🔨",
    PHASE_REVIEW: "🔍",
    PHASE_FIX: "🔧",
}


# ─── Data models ──────────────────────────────────────────────


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
    errors: str = ""


@dataclass
class DuoResult:
    """Final result of the collaborative build."""
    rounds: list[DuoRound] = field(default_factory=list)
    approved: bool = False
    total_rounds: int = 0
    files_created: list[str] = field(default_factory=list)


# ─── Pipeline ─────────────────────────────────────────────────


class DuoBuildPipeline:
    """Collaborative build: two agents iterate toward a finished product.

    The planner creates the architecture and reviews code.
    The coder implements and fixes based on feedback.
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
        self.interactive = False
        self.resume = False
        self.rounds: list[DuoRound] = []
        self._running_cost: float = 0.0
        self._running_time: int = 0

        # Feature integrations
        self._plugin_registry = None
        self._persistent_memory = None

    async def run(self, objective: str) -> DuoResult:
        """Execute the full collaborative build loop."""
        result = DuoResult()

        # ── Initialize feature integrations ────────────────────
        self._init_plugins()
        self._init_persistent_memory()

        # ── Plugin: on_start ───────────────────────────────────
        if self._plugin_registry:
            self._plugin_registry.dispatch("on_start", objective=objective)

        # ── Agent validation ──────────────────────────────────
        self._validate_agents()

        # ── Resume from saved state ───────────────────────────
        plan_output = ""
        skip_to_review = False

        if self.resume:
            saved = load_state(self.working_dir)
            if saved:
                plan_output = saved.get("plan_output", "")
                last_phase = saved.get("last_phase", "")
                num_rounds = len(saved.get("rounds", []))
                console.print(
                    f"[bold cyan]↩ Resuming from {last_phase} "
                    f"({num_rounds} rounds completed)[/]"
                )
                if last_phase in ("CODE", "VERIFY", "FIX"):
                    skip_to_review = True

        # ── Phase 0: SCAFFOLD ─────────────────────────────────
        if not skip_to_review:
            self._scaffold_if_needed(objective)

        # ── Phase 1: PLAN ─────────────────────────────────────
        if not skip_to_review:
            # Inject persistent memory into planning prompt
            memory_prefix = ""
            if self._persistent_memory:
                memory_section = self._persistent_memory.to_prompt_section(objective)
                if memory_section:
                    memory_prefix = memory_section + "\n\n"

            self._print_phase(PHASE_PLAN, self.planner, "Creating project plan...")

            if self._plugin_registry:
                self._plugin_registry.dispatch("on_phase_start", phase=PHASE_PLAN)

            plan = await run_plan(self, objective)
            self._track_round(result, plan)

            if not plan.success:
                console.print(f"[red]Planning failed: {plan.output[:200]}[/]")
                return result

            self._print_output(plan)
            plan_output = plan.output
            self._save_pipeline_state(objective, "PLAN", plan_output)

        # Interactive: pause after plan for user review
        if not skip_to_review and self.interactive:
            action = self._interactive_pause(
                "Review the plan above. Continue to coding?",
                allow_feedback=True,
            )
            if action == "abort":
                console.print("[yellow]Build aborted by user.[/]")
                return result
            elif action and action != "continue":
                plan_output += f"\n\nUSER FEEDBACK: {action}"

        # ── Phase 2: CODE ─────────────────────────────────────
        if not skip_to_review:
            self._print_phase(PHASE_CODE, self.coder, "Implementing from plan...")

            if self._plugin_registry:
                self._plugin_registry.dispatch("on_phase_start", phase=PHASE_CODE)

            code_round = await run_code(self, objective, plan_output)
            self._track_round(result, code_round)

            if not code_round.success:
                console.print(f"[red]Coding failed: {code_round.output[:200]}[/]")
                return result

            self._print_output(code_round)
            self._save_pipeline_state(objective, "CODE", plan_output)

        # ── Phase 2.5: Install deps + VERIFY ──────────────────
        self._install_deps()

        if self._plugin_registry:
            self._plugin_registry.dispatch("on_phase_start", phase=PHASE_VERIFY)

        verify_result = await run_verify(self, objective)
        self._track_round(result, verify_result)
        self._print_output(verify_result)

        # Auto-resolve missing deps if errors
        if verify_result.errors:
            self._auto_resolve_deps(verify_result.errors)

        # ── Phases 3-5: REVIEW / FIX / VERIFY loop ────────────
        for iteration in range(1, self.max_rounds + 1):
            # Validation gate
            validation = validate_project(self.working_dir)
            validation_text = validation.to_prompt()

            # Review — gets verification errors + validation
            self._print_phase(
                PHASE_REVIEW, self.planner,
                f"Review round {iteration}/{self.max_rounds}..."
            )

            if self._plugin_registry:
                self._plugin_registry.dispatch("on_phase_start", phase=PHASE_REVIEW)

            review = await run_review(
                self, objective, iteration,
                verify_errors=verify_result.errors,
                validation_text=validation_text,
            )
            self._track_round(result, review)
            self._print_output(review)

            # Check if approved
            if self._is_approved(review.output):
                result.approved = True
                console.print(
                    f"\n[bold green]✅ APPROVED by {self.planner} "
                    f"after {iteration} review round(s)[/]\n"
                )
                break

            # Interactive: pause after review for user feedback
            if self.interactive:
                action = self._interactive_pause(
                    "Review the feedback above. Continue fixing?",
                    allow_feedback=True,
                )
                if action == "abort":
                    console.print("[yellow]Build aborted by user.[/]")
                    break
                elif action and action != "continue":
                    review.output += f"\n\nADDITIONAL USER FEEDBACK: {action}"

            # Fix — gets real errors, not just review comments
            self._print_phase(
                PHASE_FIX, self.coder,
                f"Fixing issues from review {iteration}..."
            )

            if self._plugin_registry:
                self._plugin_registry.dispatch("on_phase_start", phase=PHASE_FIX)

            fix_round = await run_fix(
                self, objective, review.output, iteration,
                verify_errors=verify_result.errors,
            )
            self._track_round(result, fix_round)
            self._print_output(fix_round)

            # Re-install deps + re-verify after fix
            self._install_deps()
            verify_result = await run_verify(self, objective)
            self._track_round(result, verify_result)
            self._print_output(verify_result)
            self._save_pipeline_state(objective, "VERIFY", plan_output)

            # Auto-resolve missing deps
            if verify_result.errors:
                self._auto_resolve_deps(verify_result.errors)

        # ── Finalize ──────────────────────────────────────────
        result.total_rounds = len(result.rounds)
        result.files_created = self._list_project_files()

        if self.auto_commit:
            self._auto_commit(objective)

        # Clear state file on successful completion
        clear_state(self.working_dir)

        # ── Plugin: on_end ────────────────────────────────────
        if self._plugin_registry:
            self._plugin_registry.dispatch("on_end", result=result)

        # ── Save to dashboard history ─────────────────────────
        self._save_run_record(objective, result)

        # ── Persistent memory: learn from this run ────────────
        self._learn_from_run(objective, result)

        self._print_summary(result)
        return result

    # ─── Feature integration helpers ──────────────────────────

    def _init_plugins(self) -> None:
        """Initialize plugin registry if plugins are available."""
        try:
            from forge.build.plugins import PluginRegistry
            self._plugin_registry = PluginRegistry()
            self._plugin_registry.load_from_directory(
                str(Path(self.working_dir) / ".forge" / "plugins")
            )
            if self._plugin_registry.plugins:
                names = [p.name for p in self._plugin_registry.plugins]
                console.print(f"[dim]  🔌 Plugins loaded: {', '.join(names)}[/]")
        except ImportError:
            logger.debug("Plugin system not available")

    def _init_persistent_memory(self) -> None:
        """Load persistent memory from previous runs."""
        try:
            from forge.build.memory import PersistentMemory
            self._persistent_memory = PersistentMemory(self.working_dir)
            if self._persistent_memory.count > 0:
                console.print(
                    f"[dim]  🧠 Loaded {self._persistent_memory.count} "
                    f"learnings from previous runs[/]"
                )
        except (ImportError, FileNotFoundError):
            logger.debug("Persistent memory not available")

    def _save_run_record(self, objective: str, result: DuoResult) -> None:
        """Save run to dashboard history."""
        try:
            from forge.build.dashboard import RunRecord, save_run

            total_time = sum(r.duration_ms for r in result.rounds) / 1000
            total_cost = sum(r.cost_usd or 0 for r in result.rounds)
            score = score_project(self.working_dir)

            record = RunRecord(
                objective=objective[:80],
                planner=self.planner,
                coder=self.coder,
                quality_score=score.total,
                grade=score.grade,
                duration_sec=total_time,
                cost_usd=total_cost,
                rounds=result.total_rounds,
                approved=result.approved,
            )
            save_run(self.working_dir, record)
        except ImportError:
            logger.debug("Dashboard not available")

    def _learn_from_run(self, objective: str, result: DuoResult) -> None:
        """Extract learnings from this run for future runs."""
        if not self._persistent_memory:
            return

        if result.approved:
            self._persistent_memory.add_learning(
                f"Successfully built: {objective[:60]}",
                kind="success",
                objective=objective,
                agent=self.coder,
            )

        # Learn from errors
        for r in result.rounds:
            if r.phase == PHASE_VERIFY and r.errors:
                # Extract first error line as a learning
                first_error = r.errors.split("\n")[0][:100]
                self._persistent_memory.add_learning(
                    f"Build error encountered: {first_error}",
                    kind="failure",
                    objective=objective,
                    agent="system",
                )
                break  # Only record first error per run

    # ─── State management ─────────────────────────────────────

    def _save_pipeline_state(self, objective: str, phase: str, plan_output: str) -> None:
        """Save current pipeline state for resume capability."""
        round_data = [
            {
                "round_number": r.round_number,
                "phase": r.phase,
                "agent_name": r.agent_name,
                "success": r.success,
                "duration_ms": r.duration_ms,
                "cost_usd": r.cost_usd,
            }
            for r in self.rounds
        ]
        save_state(
            working_dir=self.working_dir,
            objective=objective,
            rounds=round_data,
            last_phase=phase,
            plan_output=plan_output,
            planner=self.planner,
            coder=self.coder,
        )

    def _track_round(self, result: DuoResult, round_: DuoRound) -> None:
        """Track a round and update running totals."""
        result.rounds.append(round_)
        self.rounds.append(round_)
        self._running_cost += round_.cost_usd or 0
        self._running_time += round_.duration_ms

        # Print running cost after each round
        cost_str = f"${self._running_cost:.4f}" if self._running_cost > 0 else "—"
        time_str = f"{self._running_time / 1000:.1f}s"
        console.print(f"[dim]    ⏱  {time_str}  💰 {cost_str}[/]")

    # ─── Interactive Mode ─────────────────────────────────────

    def _interactive_pause(
        self, message: str, allow_feedback: bool = False,
    ) -> str:
        """Pause for user input in interactive mode.

        Returns:
            "continue" — user approved
            "abort" — user wants to stop
            str — user-provided feedback (if allow_feedback=True)
        """
        console.print(f"\n[bold yellow]⏸  {message}[/]")

        if allow_feedback:
            console.print(
                "[dim]  Enter: continue  |  n: abort  |  "
                "Type feedback to add notes[/]"
            )
        else:
            console.print("[dim]  Enter: continue  |  n: abort[/]")

        try:
            response = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            return "abort"

        if not response or response.lower() in ("y", "yes", ""):
            return "continue"
        if response.lower() in ("n", "no", "abort", "quit", "q"):
            return "abort"

        if allow_feedback:
            return response

        return "continue"

    # ─── Agent Validation ─────────────────────────────────────

    def _validate_agents(self) -> None:
        """Warn about suboptimal agent configurations."""
        planner_adapter = self.engine.adapters.get(self.planner)
        coder_adapter = self.engine.adapters.get(self.coder)

        if planner_adapter is None:
            console.print(f"[bold red]⚠ Planner agent '{self.planner}' not found![/]")
        if coder_adapter is None:
            console.print(f"[bold red]⚠ Coder agent '{self.coder}' not found![/]")

        if self.planner == self.coder:
            console.print(
                f"[yellow]💡 Tip: Using different agents for planner and coder "
                f"often produces better results (e.g., --planner gemini --coder claude-sonnet)[/]"
            )

        if coder_adapter and not hasattr(coder_adapter, "execute_agentic"):
            console.print(
                f"[yellow]⚠ Coder '{self.coder}' has no agentic mode — "
                f"files may not be created on disk[/]"
            )

    # ─── Dependency Install ────────────────────────────────────

    def _install_deps(self) -> None:
        """Auto-install project dependencies before verification.

        Detects project type and runs the appropriate install command:
        - Python: pip install -e . (if pyproject.toml/setup.py) or pip install -r requirements.txt
        - Node.js: npm install (if package.json)
        """
        wd = Path(self.working_dir)
        installed = False

        # Python projects
        if (wd / "pyproject.toml").exists() or (wd / "setup.py").exists():
            console.print("[dim]  📦 Installing Python deps (pip install -e .)...[/]")
            try:
                result = subprocess.run(
                    ["pip", "install", "-e", ".", "-q"],
                    cwd=self.working_dir, capture_output=True, text=True, timeout=120,
                )
                if result.returncode == 0:
                    console.print("[dim]  ✅ Python deps installed[/]")
                    installed = True
                else:
                    err = (result.stderr or result.stdout)[:300]
                    console.print(f"[dim]  ⚠ pip install failed: {err}[/]")
            except subprocess.TimeoutExpired:
                console.print("[dim]  ⚠ pip install timed out[/]")
            except FileNotFoundError:
                console.print("[dim]  ⚠ pip not found[/]")
            except OSError as e:
                console.print(f"[dim]  ⚠ pip install error: {e}[/]")
        elif (wd / "requirements.txt").exists():
            console.print("[dim]  📦 Installing Python deps (pip install -r)...[/]")
            try:
                result = subprocess.run(
                    ["pip", "install", "-r", "requirements.txt", "-q"],
                    cwd=self.working_dir, capture_output=True, text=True, timeout=120,
                )
                if result.returncode == 0:
                    console.print("[dim]  ✅ Python deps installed[/]")
                    installed = True
                else:
                    err = (result.stderr or result.stdout)[:300]
                    console.print(f"[dim]  ⚠ pip install failed: {err}[/]")
            except subprocess.TimeoutExpired:
                console.print("[dim]  ⚠ pip install timed out[/]")
            except FileNotFoundError:
                console.print("[dim]  ⚠ pip not found[/]")
            except OSError as e:
                console.print(f"[dim]  ⚠ pip install error: {e}[/]")

        # Node.js projects
        if (wd / "package.json").exists() and not (wd / "node_modules").exists():
            console.print("[dim]  📦 Installing Node deps (npm install)...[/]")
            try:
                result = subprocess.run(
                    ["npm", "install", "--silent"],
                    cwd=self.working_dir, capture_output=True, text=True, timeout=120,
                )
                if result.returncode == 0:
                    console.print("[dim]  ✅ Node deps installed[/]")
                    installed = True
                else:
                    err = (result.stderr or result.stdout)[:300]
                    console.print(f"[dim]  ⚠ npm install failed: {err}[/]")
            except subprocess.TimeoutExpired:
                console.print("[dim]  ⚠ npm install timed out[/]")
            except FileNotFoundError:
                console.print("[dim]  ⚠ npm not found[/]")
            except OSError as e:
                console.print(f"[dim]  ⚠ npm install error: {e}[/]")

        if not installed:
            if (wd / "go.mod").exists():
                try:
                    subprocess.run(
                        ["go", "mod", "download"],
                        cwd=self.working_dir, capture_output=True, timeout=60,
                    )
                    console.print("[dim]  ✅ Go deps downloaded[/]")
                except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                    logger.debug("Go dep download failed")
            elif (wd / "Cargo.toml").exists():
                try:
                    subprocess.run(
                        ["cargo", "fetch", "-q"],
                        cwd=self.working_dir, capture_output=True, timeout=60,
                    )
                    console.print("[dim]  ✅ Rust deps fetched[/]")
                except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                    logger.debug("Cargo fetch failed")

    # ─── Scaffolding ──────────────────────────────────────────

    def _auto_resolve_deps(self, error_text: str) -> None:
        """Auto-detect and install missing dependencies from error output."""
        installed = resolve_missing_deps(self.working_dir, error_text)
        if installed:
            console.print(
                f"[dim]  🔧 Auto-installed missing deps: "
                f"{', '.join(installed)}[/]"
            )

    def _scaffold_if_needed(self, objective: str) -> None:
        """Auto-scaffold project based on objective keywords."""
        wd = Path(self.working_dir)

        existing = [f for f in wd.iterdir()
                    if f.name not in {".git", ".gitignore", "__pycache__", ".venv"}]
        if existing:
            return

        template = detect_template(objective)
        if template:
            try:
                files = scaffold_template(template, self.working_dir)
                console.print(
                    f"[dim]  🏗️  Scaffolded '{template}' template: "
                    f"{', '.join(files[:5])}"
                    + (f" +{len(files)-5} more" if len(files) > 5 else "")
                    + "[/]"
                )
                self._git_init()
            except (OSError, ValueError) as e:
                console.print(f"[dim]  ⚠ Scaffold failed: {e}[/]")

    def _git_init(self) -> None:
        """Initialize git repo if not exists (for diff tracking)."""
        git_dir = Path(self.working_dir) / ".git"
        if not git_dir.exists():
            try:
                subprocess.run(
                    ["git", "init", "-q"],
                    cwd=self.working_dir, capture_output=True, timeout=5,
                )
                subprocess.run(
                    ["git", "add", "-A"],
                    cwd=self.working_dir, capture_output=True, timeout=5,
                )
                subprocess.run(
                    ["git", "commit", "-q", "-m", "scaffold", "--allow-empty"],
                    cwd=self.working_dir, capture_output=True, timeout=5,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
                logger.debug("Git init failed: %s", e)

    # ─── Git helpers ──────────────────────────────────────────

    def _commit_round(self, phase: str) -> None:
        """Lightweight commit after each CODE/FIX for diff tracking."""
        try:
            subprocess.run(
                ["git", "add", "-A"],
                cwd=self.working_dir, capture_output=True, timeout=5,
            )
            subprocess.run(
                ["git", "commit", "-q", "-m", f"duo-{phase.lower()}", "--allow-empty"],
                cwd=self.working_dir, capture_output=True, timeout=5,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.debug("Commit round failed: %s", e)

    # ─── Approval detection ──────────────────────────────────

    @staticmethod
    def _is_approved(review_output: str) -> bool:
        """Check if the reviewer approved the project."""
        if not review_output:
            return False
        first_100 = review_output[:100].upper()
        return "APPROVED" in first_100

    # ─── Project file helpers ────────────────────────────────

    def _list_project_files(self) -> list[str]:
        """List files in the project directory."""
        wd = Path(self.working_dir)
        files = []
        for f in sorted(wd.rglob("*")):
            if f.is_file() and ".git" not in f.parts:
                files.append(str(f.relative_to(wd)))
        return files

    # ─── Auto-commit ─────────────────────────────────────────

    def _auto_commit(self, objective: str) -> None:
        """Commit the finished project."""
        wd = Path(self.working_dir)
        git_dir = wd / ".git"

        if not git_dir.exists():
            try:
                subprocess.run(
                    ["git", "init", "-q"],
                    cwd=self.working_dir, capture_output=True, timeout=10,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
                logger.warning("Git init failed during auto-commit: %s", e)
                return

        try:
            subprocess.run(
                ["git", "add", "-A"],
                cwd=self.working_dir, capture_output=True, timeout=10,
            )

            short_obj = objective[:60].replace('"', '\\"')
            message = f"feat: {short_obj}\n\nBuilt by Forge duo pipeline (v1.0)"

            subprocess.run(
                ["git", "commit", "--allow-empty", "-m", message],
                cwd=self.working_dir, capture_output=True, timeout=10,
            )
            console.print("[green]📦 Auto-committed project[/]")
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            console.print(f"[dim]⚠ Auto-commit failed: {e}[/]")

    # ─── File reading for review ──────────────────────────────

    def _read_key_files_for_review(self, max_total_chars: int = 4000) -> str:
        """Read key project files for the reviewer to inspect."""
        wd = Path(self.working_dir)
        priority_patterns = [
            "README.md", "pyproject.toml", "package.json", "setup.py",
            "requirements.txt",
        ]
        source_exts = {".py", ".js", ".ts", ".go", ".rs", ".java"}

        files_to_read: list[Path] = []

        for pattern in priority_patterns:
            f = wd / pattern
            if f.exists():
                files_to_read.append(f)

        skip = {".git", "__pycache__", "node_modules", ".venv", "venv"}
        source_files = []
        for p in wd.rglob("*"):
            if p.is_file() and p.suffix in source_exts:
                rel = p.relative_to(wd)
                if not any(part in skip or part.startswith(".") for part in rel.parts):
                    source_files.append(p)
        source_files.sort(key=lambda p: p.stat().st_size)
        files_to_read.extend(source_files[:10])

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
            except OSError:
                continue

        return "\n\n".join(parts)

    def _get_round_diff(self, max_chars: int = 2000) -> str:
        """Get git diff since last commit (shows what changed this round)."""
        try:
            result = subprocess.run(
                ["git", "diff", "--stat"],
                cwd=self.working_dir, capture_output=True, text=True, timeout=5,
            )
            stat = result.stdout.strip()

            result2 = subprocess.run(
                ["git", "diff"],
                cwd=self.working_dir, capture_output=True, text=True, timeout=5,
            )
            diff = result2.stdout.strip()

            if not diff:
                return ""

            combined = f"{stat}\n\n{diff}"
            if len(combined) > max_chars:
                combined = combined[:max_chars] + "\n... (diff truncated)"
            return combined
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return ""

    # ─── TUI ──────────────────────────────────────────────────

    def _print_phase(self, phase: str, agent: str, message: str) -> None:
        """Print a phase header."""
        icon = PHASE_ICONS.get(phase, "")

        colors = {
            "gemini": "bright_cyan",
            "claude-sonnet": "bright_magenta",
            "claude-opus": "magenta",
            "antigravity-pro": "bright_blue",
            "antigravity-flash": "bright_blue",
            "system": "bright_green",
        }
        color = colors.get(agent, "white")

        console.print(
            f"\n{icon} [bold]{phase}[/] → "
            f"[bold {color}]{agent.upper()}[/] "
            f"[dim]{message}[/]"
        )

    def _print_output(self, round_: DuoRound) -> None:
        """Print the output of a round."""
        if not round_.output:
            return

        output = round_.output
        max_display = 3000
        truncated = len(output) > max_display
        if truncated:
            display_output = output[:max_display]
        else:
            display_output = output

        dur = f"{round_.duration_ms / 1000:.1f}s" if round_.duration_ms else ""
        cost = f"  ${round_.cost_usd:.4f}" if round_.cost_usd else ""

        icon = PHASE_ICONS.get(round_.phase, "")
        title = (
            f"{icon} {round_.phase} — "
            f"{round_.agent_name.upper()}"
            f"  {dur}{cost}"
        )

        border_style = {
            PHASE_PLAN: "blue",
            PHASE_CODE: "green",
            PHASE_VERIFY: "yellow",
            PHASE_REVIEW: "cyan",
            PHASE_FIX: "magenta",
        }.get(round_.phase, "white")

        panel = Panel(
            display_output + ("\n\n... (truncated)" if truncated else ""),
            title=title,
            border_style=border_style,
            padding=(1, 2),
        )
        console.print(panel)

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
        table.add_column("Status", min_width=6)

        total_cost = 0.0
        total_time = 0

        for r in result.rounds:
            colors = {
                "gemini": "bright_cyan",
                "claude-sonnet": "bright_magenta",
                "claude-opus": "magenta",
                "antigravity-pro": "bright_blue",
                "system": "bright_green",
            }
            color = colors.get(r.agent_name, "white")
            icon = PHASE_ICONS.get(r.phase, "")

            dur = f"{r.duration_ms / 1000:.1f}s" if r.duration_ms else "—"
            cost = f"${r.cost_usd:.4f}" if r.cost_usd else "—"
            status = "✅" if r.success else "❌"

            total_time += r.duration_ms
            total_cost += r.cost_usd or 0

            table.add_row(
                str(r.round_number),
                f"{icon} {r.phase}",
                Text(r.agent_name.upper(), style=f"bold {color}"),
                dur,
                cost,
                status,
            )

        table.add_section()
        table.add_row(
            "", "", Text("TOTAL", style="bold"),
            f"{total_time / 1000:.1f}s",
            f"${total_cost:.4f}" if total_cost > 0 else "—",
            "",
        )

        console.print(table)

        if result.approved:
            console.print(f"\n[bold green]✅ Project approved and committed as v1.0[/]")
        else:
            console.print(f"\n[bold yellow]⚠  Max review rounds reached — project may need manual review[/]")

        if result.files_created:
            console.print(f"\n[dim]📂 {len(result.files_created)} file(s) created:[/]")
            for f in result.files_created[:20]:
                console.print(f"[dim]   {f}[/]")
            if len(result.files_created) > 20:
                console.print(f"[dim]   ... and {len(result.files_created) - 20} more[/]")

        # Quality Score
        score = score_project(self.working_dir)
        grade_colors = {"A": "bold green", "B": "green", "C": "yellow", "D": "yellow", "F": "red"}
        color = grade_colors.get(score.grade, "white")

        console.print(
            f"\n[{color}]{score.emoji} Quality Score: {score.total}/100 "
            f"(Grade: {score.grade})[/]"
        )
        console.print(
            f"[dim]  Structure: {score.structure}/25  │  "
            f"Code: {score.code}/25  │  "
            f"Tests: {score.tests}/25  │  "
            f"Docs: {score.docs}/25[/]"
        )
        for detail in score.details:
            console.print(f"[dim]  {detail}[/]")
