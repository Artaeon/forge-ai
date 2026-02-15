"""GitHub Copilot CLI adapter."""

from __future__ import annotations

import asyncio
import shutil
from typing import AsyncIterator

from forge.agents.base import AgentResult, AgentStatus, BaseAdapter, TaskContext


class CopilotAdapter(BaseAdapter):
    """Adapter for GitHub Copilot CLI (gh copilot).

    Uses `gh copilot suggest` for code/command generation
    and `gh copilot explain` for explanations.
    """

    name = "copilot"
    display_name = "GitHub Copilot"

    def __init__(self, extra_args: list[str] | None = None):
        self.extra_args = extra_args or []

    def is_available(self) -> bool:
        """Check if gh CLI is installed and copilot extension is available."""
        if not shutil.which("gh"):
            return False
        # Quick check if copilot subcommand exists
        try:
            import subprocess
            result = subprocess.run(
                ["gh", "copilot", "--help"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def _classify_prompt(self, prompt: str) -> str:
        """Classify if the prompt is asking for explanation or suggestion."""
        explain_keywords = [
            "explain", "what does", "how does", "why does",
            "what is", "describe", "understand", "meaning",
        ]
        prompt_lower = prompt.lower()
        for keyword in explain_keywords:
            if keyword in prompt_lower:
                return "explain"
        return "suggest"

    def _build_command(self, ctx: TaskContext, mode: str) -> list[str]:
        cmd = ["gh", "copilot"]

        if mode == "explain":
            cmd.append("explain")
        else:
            cmd.extend(["suggest", "-t", "shell"])

        cmd.extend(self.extra_args)
        cmd.append(ctx.prompt)
        return cmd

    async def execute(self, ctx: TaskContext) -> AgentResult:
        if not self.is_available():
            return self._make_unavailable_result()

        start = self._now_ms()
        mode = self._classify_prompt(ctx.prompt)
        cmd = self._build_command(ctx, mode)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=ctx.working_dir,
                env=self._get_env(),
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=ctx.timeout,
            )
        except asyncio.TimeoutError:
            return AgentResult(
                agent_name=self.name,
                output="",
                status=AgentStatus.TIMEOUT,
                duration_ms=self._now_ms() - start,
                error=f"Copilot timed out after {ctx.timeout}s",
            )
        except Exception as e:
            return self._make_error_result(str(e), self._now_ms() - start)

        elapsed = self._now_ms() - start
        output_text = stdout.decode(errors="replace").strip()
        error_text = stderr.decode(errors="replace").strip()

        if proc.returncode != 0:
            return self._make_error_result(
                error_text or f"Exit code {proc.returncode}",
                elapsed,
            )

        # Strip ANSI and clean up Copilot's interactive output
        clean_output = self._strip_ansi(output_text)

        return AgentResult(
            agent_name=self.name,
            output=clean_output,
            status=AgentStatus.SUCCESS,
            duration_ms=elapsed,
            model="copilot",
        )

    async def stream(self, ctx: TaskContext) -> AsyncIterator[str]:
        if not self.is_available():
            yield f"[error] {self.display_name} is not available"
            return

        mode = self._classify_prompt(ctx.prompt)
        cmd = self._build_command(ctx, mode)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=ctx.working_dir,
            env=self._get_env(),
        )

        assert proc.stdout is not None
        async for line in proc.stdout:
            text = line.decode(errors="replace").rstrip()
            if text:
                yield self._strip_ansi(text)

        await proc.wait()

    @staticmethod
    def _strip_ansi(text: str) -> str:
        """Remove ANSI escape codes from text."""
        import re
        return re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)

    @staticmethod
    def _get_env() -> dict[str, str] | None:
        """Get environment with non-interactive settings."""
        import os
        env = os.environ.copy()
        env["GH_PROMPT_DISABLED"] = "1"
        env["NO_COLOR"] = "1"
        return env
