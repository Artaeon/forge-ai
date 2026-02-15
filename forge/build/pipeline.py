"""Autonomous build pipeline — iterative agent-driven project generation.

Key design: Uses Claude's agentic mode (not --print) so it can actually
create, edit, and delete files on disk. The pipeline: 
  1. Dispatch objective to agent in agentic mode (writes files)
  2. Check files were created
  3. Install dependencies if requirements found
  4. Run verification commands
  5. Feed errors back and retry
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from dataclasses import dataclass, field

from rich.console import Console

from forge.agents.base import AgentResult, AgentStatus, TaskContext
from forge.engine import ForgeEngine

console = Console()


@dataclass
class BuildStep:
    """A single step in the build pipeline."""
    iteration: int
    prompt: str
    agent_results: list[AgentResult] = field(default_factory=list)
    build_output: str = ""
    build_success: bool = False
    test_output: str = ""
    test_success: bool = False
    files_created: list[str] = field(default_factory=list)


class BuildPipeline:
    """Autonomous build pipeline that iteratively generates, builds, and fixes code.

    Uses Claude's agentic mode where it can actually create and edit files,
    then verifies the result with build/test commands.
    """

    def __init__(
        self,
        engine: ForgeEngine,
        working_dir: str,
        primary_agent: str = "claude-sonnet",
        max_iterations: int = 10,
        test_commands: list[str] | None = None,
        auto_commit: bool = False,
    ):
        self.engine = engine
        self.working_dir = working_dir
        self.primary_agent = primary_agent
        self.max_iterations = max_iterations
        self.test_commands = test_commands or []
        self.auto_commit = auto_commit
        self.steps: list[BuildStep] = []

    async def run(self, objective: str) -> list[BuildStep]:
        """Execute the autonomous build loop."""
        console.print(f"\n[bold bright_magenta]🔨 Autonomous Build Mode[/]")
        console.print(f"[dim]Objective: {objective}[/]")
        console.print(f"[dim]Max iterations: {self.max_iterations}[/]")
        console.print(f"[dim]Primary agent: {self.primary_agent}[/]")
        console.print(f"[dim]Working dir: {self.working_dir}[/]\n")

        # Snapshot files before we start
        files_before = self._list_project_files()
        error_context = ""

        for iteration in range(1, self.max_iterations + 1):
            console.print(f"[bold]━━━ Iteration {iteration}/{self.max_iterations} ━━━[/]\n")

            # Build the prompt
            if error_context:
                prompt = self._build_fix_prompt(objective, error_context, iteration)
            else:
                prompt = self._build_initial_prompt(objective, iteration)

            # Create task context — longer timeout for agentic builds
            ctx = TaskContext(
                working_dir=self.working_dir,
                prompt=prompt,
                timeout=300,  # 5 minutes for agentic builds
            )

            # Dispatch in AGENTIC mode — Claude will write files
            console.print(f"[dim]  → Dispatching to {self.primary_agent} (agentic mode)...[/]")
            result = await self._dispatch_agentic(ctx)

            step = BuildStep(
                iteration=iteration,
                prompt=prompt,
                agent_results=[result],
            )

            if not result.is_success:
                console.print(f"[red]  ✗ Agent failed: {result.error}[/]")
                self.steps.append(step)
                # Don't break — retry with the error
                error_context = result.error or "Agent execution failed"
                continue

            console.print(f"[green]  ✓ Agent responded ({len(result.output)} chars)[/]")

            # Check what files were created/modified
            files_after = self._list_project_files()
            new_files = [f for f in files_after if f not in files_before]
            step.files_created = new_files

            if new_files:
                console.print(f"[green]  📁 Files created/modified: {len(new_files)}[/]")
                for f in new_files[:10]:
                    console.print(f"[dim]     + {f}[/]")
                if len(new_files) > 10:
                    console.print(f"[dim]     ... and {len(new_files) - 10} more[/]")
            
            # Update files_before for next iteration
            files_before = files_after

            # Auto-install dependencies if we detect requirements
            self._auto_install_deps()

            # Run verification
            if self.test_commands:
                console.print(f"[dim]  → Running verification commands...[/]")
                test_success, test_output = self._run_verification()
                step.test_output = test_output
                step.test_success = test_success

                if test_success:
                    console.print(f"[green]  ✓ All checks passed![/]")
                    step.build_success = True
                    self.steps.append(step)

                    if self.auto_commit:
                        self._git_commit(f"forge: build iteration {iteration}")

                    console.print(
                        f"\n[bold green]🎉 Build completed successfully "
                        f"in {iteration} iteration(s)![/]\n"
                    )
                    return self.steps
                else:
                    console.print(f"[yellow]  ⚠ Checks failed, will retry...[/]")
                    # Show what failed
                    for line in test_output.split('\n')[:5]:
                        if line.strip():
                            console.print(f"[dim]     {line.strip()[:120]}[/]")
                    error_context = test_output
            else:
                # No test commands — check if files exist as success criteria
                if new_files or (iteration > 1 and files_after):
                    step.build_success = True
                    self.steps.append(step)

                    if self.auto_commit:
                        self._git_commit(f"forge: build iteration {iteration}")

                    console.print(f"\n[bold green]🎉 Build completed! Files created in {self.working_dir}[/]\n")
                    return self.steps
                else:
                    console.print(f"[yellow]  ⚠ No files were created, retrying...[/]")
                    error_context = (
                        "No files were created in the working directory. "
                        "Please create the actual files. "
                        f"Working directory: {self.working_dir}"
                    )

            self.steps.append(step)

        console.print(
            f"\n[bold yellow]⚠ Reached max iterations ({self.max_iterations}) "
            f"without fully passing.[/]\n"
        )

        # Show what we ended up with
        final_files = self._list_project_files()
        if final_files:
            console.print(f"[dim]Files in project ({len(final_files)}):[/]")
            for f in final_files[:20]:
                console.print(f"[dim]  📄 {f}[/]")

        return self.steps

    async def _dispatch_agentic(self, ctx: TaskContext) -> AgentResult:
        """Dispatch to agent using agentic mode (file write capability)."""
        adapter = self.engine.adapters.get(self.primary_agent)
        if adapter is None:
            return AgentResult(
                agent_name=self.primary_agent,
                output="",
                status=AgentStatus.FAILED,
                error=f"Agent '{self.primary_agent}' not found",
            )

        # Use agentic mode if the adapter supports it (ClaudeAdapter does)
        if hasattr(adapter, 'execute_agentic'):
            return await adapter.execute_agentic(ctx)
        else:
            # Fallback to regular execute for non-Claude agents
            return await adapter.execute(ctx)

    def _build_initial_prompt(self, objective: str, iteration: int) -> str:
        project_files = self._list_project_files()
        files_info = ""
        if project_files:
            files_info = f"\nExisting files in the project:\n"
            for f in project_files[:30]:
                files_info += f"  - {f}\n"

        return (
            f"OBJECTIVE: {objective}\n\n"
            f"Working directory: {self.working_dir}\n"
            f"{files_info}\n"
            f"Please create all necessary files to complete this objective. "
            f"Create the actual files in the working directory — do not just describe them. "
            f"Make sure the code is complete, functional, and ready to run."
        )

    def _build_fix_prompt(self, objective: str, errors: str, iteration: int) -> str:
        project_files = self._list_project_files()
        files_info = ""
        if project_files:
            files_info = f"\nCurrent files in the project:\n"
            for f in project_files[:30]:
                files_info += f"  - {f}\n"

        return (
            f"OBJECTIVE: {objective}\n\n"
            f"Working directory: {self.working_dir}\n"
            f"{files_info}\n"
            f"The previous iteration had the following errors:\n\n"
            f"```\n{errors[-3000:]}\n```\n\n"
            f"Please fix these errors by modifying the actual files in the working directory. "
            f"Do not just describe the fix — actually edit the files."
        )

    def _list_project_files(self) -> list[str]:
        """List all project files (excluding hidden dirs like .git)."""
        result = []
        wd = Path(self.working_dir)
        if not wd.exists():
            return result
        
        for p in sorted(wd.rglob("*")):
            # Skip hidden directories and common noise
            parts = p.relative_to(wd).parts
            if any(part.startswith('.') for part in parts):
                continue
            if any(part in ('__pycache__', 'node_modules', '.venv', 'venv') for part in parts):
                continue
            if p.is_file():
                result.append(str(p.relative_to(wd)))
        
        return result

    def _auto_install_deps(self) -> None:
        """Auto-detect and install dependencies."""
        wd = Path(self.working_dir)
        
        # Python: requirements.txt
        req_file = wd / "requirements.txt"
        if req_file.exists():
            console.print(f"[dim]  → Installing Python dependencies...[/]")
            try:
                # Try pip install in the project context
                result = subprocess.run(
                    ["pip", "install", "-r", "requirements.txt", "-q"],
                    cwd=self.working_dir,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if result.returncode == 0:
                    console.print(f"[green]  ✓ Dependencies installed[/]")
                else:
                    # Try with python -m pip
                    result = subprocess.run(
                        ["python3", "-m", "pip", "install", "-r", "requirements.txt", "-q"],
                        cwd=self.working_dir,
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    if result.returncode == 0:
                        console.print(f"[green]  ✓ Dependencies installed (via python3 -m pip)[/]")
            except Exception:
                pass

        # Node: package.json
        pkg_file = wd / "package.json"
        if pkg_file.exists() and not (wd / "node_modules").exists():
            console.print(f"[dim]  → Installing Node dependencies...[/]")
            try:
                result = subprocess.run(
                    ["npm", "install"],
                    cwd=self.working_dir,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if result.returncode == 0:
                    console.print(f"[green]  ✓ Node dependencies installed[/]")
            except Exception:
                pass

    def _run_verification(self) -> tuple[bool, str]:
        """Run verification commands and return (success, output)."""
        all_output = []
        all_passed = True

        for cmd in self.test_commands:
            try:
                result = subprocess.run(
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    cwd=self.working_dir,
                    timeout=60,
                )
                output = f"$ {cmd}\n{result.stdout}\n{result.stderr}"
                all_output.append(output)
                if result.returncode != 0:
                    all_passed = False
            except subprocess.TimeoutExpired:
                all_output.append(f"$ {cmd}\n[TIMEOUT after 60s]")
                all_passed = False
            except Exception as e:
                all_output.append(f"$ {cmd}\n[ERROR: {e}]")
                all_passed = False

        return all_passed, "\n\n".join(all_output)

    def _git_commit(self, message: str) -> None:
        """Auto-commit changes."""
        try:
            subprocess.run(
                ["git", "add", "-A"],
                cwd=self.working_dir,
                capture_output=True,
                timeout=10,
            )
            subprocess.run(
                ["git", "commit", "-m", message],
                cwd=self.working_dir,
                capture_output=True,
                timeout=10,
            )
            console.print(f"[dim]  📝 Committed: {message}[/]")
        except Exception:
            pass
