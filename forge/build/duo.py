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
from forge.build.validate import validate_project
from forge.build.templates import detect_template, scaffold_template
from forge.build.testing import detect_verification_suite

console = Console()


# ─── Phase labels ─────────────────────────────────────────────

PHASE_PLAN = "PLAN"
PHASE_CODE = "CODE"
PHASE_VERIFY = "VERIFY"
PHASE_REVIEW = "REVIEW"
PHASE_FIX = "FIX"

PHASE_ICONS = {
    PHASE_PLAN: "📋",
    PHASE_CODE: "⚡",
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
    prompt: str  # truncated for display
    output: str
    success: bool
    duration_ms: int = 0
    cost_usd: float | None = None
    errors: str = ""  # verification errors (stack traces, build failures)


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
        self.rounds: list[DuoRound] = []
        self._running_cost: float = 0.0
        self._running_time: int = 0

    async def run(self, objective: str) -> DuoResult:
        """Execute the full collaborative build loop."""
        result = DuoResult()

        # ── Agent validation ──────────────────────────────────
        self._validate_agents()

        # ── Phase 0: SCAFFOLD ─────────────────────────────────
        self._scaffold_if_needed(objective)

        # ── Phase 1: PLAN ─────────────────────────────────────
        self._print_phase(PHASE_PLAN, self.planner, "Creating project plan...")
        plan = await self._plan(objective)
        self._track_round(result, plan)

        if not plan.success:
            console.print(f"[red]Planning failed: {plan.output[:200]}[/]")
            return result

        self._print_output(plan)

        # ── Phase 2: CODE ─────────────────────────────────────
        self._print_phase(PHASE_CODE, self.coder, "Implementing from plan...")
        code_round = await self._code(objective, plan.output)
        self._track_round(result, code_round)

        if not code_round.success:
            console.print(f"[red]Coding failed: {code_round.output[:200]}[/]")
            return result

        self._print_output(code_round)

        # ── Phase 2.5: Initial VERIFY ─────────────────────────
        verify_result = await self._verify(objective)
        self._track_round(result, verify_result)
        self._print_output(verify_result)

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
            review = await self._review(
                objective, iteration,
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

            # Fix — gets real errors, not just review comments
            self._print_phase(
                PHASE_FIX, self.coder,
                f"Fixing issues from review {iteration}..."
            )
            fix_round = await self._fix(
                objective, review.output, iteration,
                verify_errors=verify_result.errors,
            )
            self._track_round(result, fix_round)
            self._print_output(fix_round)

            # Re-verify after fix
            verify_result = await self._verify(objective)
            self._track_round(result, verify_result)
            self._print_output(verify_result)

        # ── Finalize ──────────────────────────────────────────
        result.total_rounds = len(result.rounds)
        result.files_created = self._list_project_files()

        if self.auto_commit:
            self._auto_commit(objective)

        self._print_summary(result)
        return result

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

    # ─── Agent Validation ─────────────────────────────────────

    def _validate_agents(self) -> None:
        """Warn about suboptimal agent configurations."""
        planner_adapter = self.engine.adapters.get(self.planner)
        coder_adapter = self.engine.adapters.get(self.coder)

        if planner_adapter is None:
            console.print(f"[bold red]⚠ Planner agent '{self.planner}' not found![/]")
        if coder_adapter is None:
            console.print(f"[bold red]⚠ Coder agent '{self.coder}' not found![/]")

        # Warn about suboptimal combos
        if self.planner == self.coder:
            console.print(
                f"[yellow]💡 Tip: Using different agents for planner and coder "
                f"often produces better results (e.g., --planner gemini --coder claude-sonnet)[/]"
            )

        # Check if coder has agentic capability
        if coder_adapter and not hasattr(coder_adapter, "execute_agentic"):
            console.print(
                f"[yellow]⚠ Coder '{self.coder}' has no agentic mode — "
                f"files may not be created on disk[/]"
            )

    # ─── Scaffolding ──────────────────────────────────────────

    def _scaffold_if_needed(self, objective: str) -> None:
        """Auto-scaffold project based on objective keywords."""
        wd = Path(self.working_dir)

        # Don't scaffold if project already has files
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

                # Init git for diff tracking
                self._git_init()
            except Exception as e:
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
            except Exception:
                pass

    # ─── Phase: PLAN ──────────────────────────────────────────

    async def _plan(self, objective: str) -> DuoRound:
        """Planner creates the project architecture and README."""
        # Show existing scaffold files if any
        existing = self._list_project_files()
        scaffold_note = ""
        if existing:
            scaffold_note = (
                f"\n\nNOTE: The project already has a scaffold with these files: "
                f"{', '.join(existing[:10])}\n"
                f"Build on this foundation. Don't recreate files that already exist — extend them."
            )

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
            f"{scaffold_note}"
        )
        return await self._dispatch(PHASE_PLAN, self.planner, prompt)

    # ─── Phase: CODE ──────────────────────────────────────────

    async def _code(self, objective: str, plan: str) -> DuoRound:
        """Coder implements the full project from the plan."""
        # Pass the FULL plan — it's the blueprint, don't summarize it
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

    # ─── Phase: VERIFY ────────────────────────────────────────

    async def _verify(self, objective: str) -> DuoRound:
        """Run build + tests and capture real errors."""
        self._print_phase(PHASE_VERIFY, "system", "Running build & tests...")

        suite = detect_verification_suite(self.working_dir)
        errors: list[str] = []
        output_parts: list[str] = []

        if not suite.has_commands:
            output_parts.append("No verification commands detected for this project type.")
        else:
            for cmd in suite.all_commands:
                try:
                    result = subprocess.run(
                        cmd, shell=True, capture_output=True, text=True,
                        cwd=self.working_dir, timeout=60,
                    )
                    stdout = result.stdout.strip()
                    stderr = result.stderr.strip()
                    combined = (stdout + "\n" + stderr).strip()

                    if result.returncode != 0:
                        errors.append(f"$ {cmd}\nExit code: {result.returncode}\n{combined}")
                        output_parts.append(f"❌ {cmd} → FAILED\n{combined[:500]}")
                    else:
                        output_parts.append(f"✅ {cmd} → OK")
                        if combined:
                            output_parts.append(f"   {combined[:200]}")
                except subprocess.TimeoutExpired:
                    errors.append(f"$ {cmd}\nTIMEOUT after 60s")
                    output_parts.append(f"⏰ {cmd} → TIMEOUT")
                except Exception as e:
                    errors.append(f"$ {cmd}\nERROR: {e}")
                    output_parts.append(f"❌ {cmd} → ERROR: {e}")

        # Also run validation gate
        validation = validate_project(self.working_dir)
        if not validation.passed:
            output_parts.append(f"\n{validation.to_prompt()}")
            for issue in validation.issues:
                if issue.severity.value == "CRITICAL":
                    errors.append(f"VALIDATION: {issue.message}" +
                                  (f" ({issue.file})" if issue.file else ""))

        error_text = "\n\n".join(errors) if errors else ""
        success = len(errors) == 0

        if success:
            output_parts.insert(0, "🟢 All checks passed")
        else:
            output_parts.insert(0, f"🔴 {len(errors)} check(s) failed")

        return DuoRound(
            round_number=len(self.rounds) + 1,
            phase=PHASE_VERIFY,
            agent_name="system",
            prompt="verify build & tests",
            output="\n".join(output_parts),
            success=success,
            errors=error_text,
        )

    # ─── Phase: REVIEW ────────────────────────────────────────

    async def _review(
        self, objective: str, iteration: int,
        verify_errors: str = "", validation_text: str = "",
    ) -> DuoRound:
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

        # Git diff for rounds 2+
        diff_text = ""
        if iteration > 1:
            diff_text = self._get_round_diff()

        prompt = (
            f"You are a senior code reviewer performing a thorough quality audit.\n\n"
            f"OBJECTIVE: {objective}\n"
            f"Review round: {iteration}/{self.max_rounds}\n\n"
            f"PROJECT FILES: {ctx.to_prompt()}\n\n"
        )

        if file_samples:
            prompt += f"KEY FILE CONTENTS:\n{file_samples}\n\n"

        # Show verification errors (real stack traces!)
        if verify_errors:
            prompt += (
                f"🔴 BUILD/TEST ERRORS (these are REAL errors from running the code):\n"
                f"{verify_errors[:2000]}\n\n"
            )

        if validation_text:
            prompt += f"{validation_text}\n\n"

        if diff_text and iteration > 1:
            prompt += f"CHANGES SINCE LAST ROUND:\n{diff_text}\n\n"

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

    # ─── Phase: FIX ───────────────────────────────────────────

    async def _fix(
        self, objective: str, review_feedback: str, iteration: int,
        verify_errors: str = "",
    ) -> DuoRound:
        """Coder fixes issues identified in the review."""
        ctx = gather_compact(self.working_dir)

        # Pass FULL review feedback
        if len(review_feedback) > 3000:
            feedback_text = review_feedback[:2500] + "\n\n... (truncated)"
        else:
            feedback_text = review_feedback

        prompt = (
            f"You are a senior software engineer fixing issues from a code review.\n\n"
            f"OBJECTIVE: {objective}\n\n"
            f"REVIEW FEEDBACK — fix ALL of these:\n{feedback_text}\n\n"
        )

        # Include real errors from verification (stack traces!)
        if verify_errors:
            prompt += (
                f"🔴 ACTUAL BUILD/TEST ERRORS (fix these first!):\n"
                f"{verify_errors[:2000]}\n\n"
            )

        prompt += (
            f"CURRENT PROJECT: {ctx.to_prompt()}\n"
            f"Working directory: {self.working_dir}\n\n"
            f"INSTRUCTIONS:\n"
            f"- Fix every issue listed in the review\n"
            f"- Fix ALL build/test errors shown above\n"
            f"- Create any missing files mentioned\n"
            f"- Do NOT rewrite files that are already working correctly\n"
            f"- Only modify files that have issues\n"
            f"- After fixing, verify the project still runs/imports correctly\n\n"
            f"Fix iteration: {iteration}/{self.max_rounds}"
        )
        return await self._dispatch_agentic(PHASE_FIX, self.coder, prompt)

    # ─── File reading helpers ─────────────────────────────────

    def _read_key_files_for_review(self, max_total_chars: int = 4000) -> str:
        """Read key project files for the reviewer to inspect."""
        wd = Path(self.working_dir)
        priority_patterns = [
            "README.md", "pyproject.toml", "package.json", "setup.py",
            "requirements.txt",
        ]
        source_exts = {".py", ".js", ".ts", ".go", ".rs", ".java"}

        files_to_read: list[Path] = []

        # Priority files first
        for pattern in priority_patterns:
            f = wd / pattern
            if f.exists():
                files_to_read.append(f)

        # Source files (skip tests, sort by size)
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
        except Exception:
            return ""

    # ─── Dispatch helpers ─────────────────────────────────────

    async def _dispatch(self, phase: str, agent: str, prompt: str) -> DuoRound:
        """Dispatch to an agent in read-only mode."""
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

        # Commit round for diff tracking
        self._commit_round(phase)

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
        except Exception:
            pass

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
            except Exception:
                return

        try:
            subprocess.run(
                ["git", "add", "-A"],
                cwd=self.working_dir, capture_output=True, timeout=10,
            )

            # Create a meaningful commit message
            short_obj = objective[:60].replace('"', '\\"')
            message = f"feat: {short_obj}\n\nBuilt by Forge duo pipeline (v1.0)"

            subprocess.run(
                ["git", "commit", "--allow-empty", "-m", message],
                cwd=self.working_dir, capture_output=True, timeout=10,
            )
            console.print("[green]📦 Auto-committed project[/]")
        except Exception as e:
            console.print(f"[dim]⚠ Auto-commit failed: {e}[/]")

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

        # Truncate for display
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

        # Choose border style based on phase
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
