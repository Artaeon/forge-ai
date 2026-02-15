"""Gemini CLI adapter.

Uses `gemini -p "prompt"` for non-interactive (headless) mode.
Supports agentic mode where Gemini can create/edit files via sandbox.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from typing import AsyncIterator

from forge.agents.base import AgentResult, AgentStatus, BaseAdapter, TaskContext


class GeminiAdapter(BaseAdapter):
    """Adapter for Gemini CLI (@google/gemini-cli).

    Uses the `-p` flag for non-interactive prompt execution.
    In agentic mode, allows file creation via output parsing.
    """

    name = "gemini"
    display_name = "Gemini"

    def __init__(
        self,
        model: str | None = None,
        fallback_to_api: bool = True,
        extra_args: list[str] | None = None,
    ):
        self.model = model
        self.fallback_to_api = fallback_to_api
        self.extra_args = extra_args or []

    def is_available(self) -> bool:
        return shutil.which("gemini") is not None

    def _build_command(self, ctx: TaskContext, agentic: bool = False) -> list[str]:
        """Build the gemini CLI command.

        Uses `-p` for headless (non-interactive) mode.
        agentic=False: standard prompt, text output
        agentic=True:  prompt with file-writing instructions
        """
        cmd = ["gemini"]

        # Model selection
        if self.model:
            cmd.extend(["-m", self.model])

        # Sandbox mode for agentic (allows file operations)
        if agentic:
            cmd.extend(["-s", "true"])

        cmd.extend(self.extra_args)

        # The prompt via -p flag (headless mode)
        cmd.extend(["-p", ctx.prompt])

        return cmd

    async def execute(self, ctx: TaskContext) -> AgentResult:
        """Execute in standard mode (text Q&A)."""
        return await self._run(ctx, agentic=False)

    async def execute_agentic(self, ctx: TaskContext) -> AgentResult:
        """Execute in agentic mode — Gemini can create/edit files.

        Enhances the prompt with file-writing instructions and parses
        the output to write files to disk.
        """
        # Enhance prompt for file generation
        agentic_prompt = (
            "You are an autonomous coding agent working in the directory: "
            f"{ctx.working_dir}\n\n"
            "Your task is to create or modify files to accomplish the objective below.\n\n"
            "For EACH file you create or modify, output it in this exact format:\n\n"
            "=== FILE: <relative/path/to/file> ===\n"
            "<complete file contents>\n"
            "=== END FILE ===\n\n"
            "Output ALL files needed with complete contents.\n\n"
            f"OBJECTIVE: {ctx.prompt}"
        )

        modified_ctx = TaskContext(
            working_dir=ctx.working_dir,
            prompt=agentic_prompt,
            files=ctx.files,
            system_prompt=ctx.system_prompt,
            previous_results=ctx.previous_results,
            max_budget_usd=ctx.max_budget_usd,
            timeout=ctx.timeout,
        )

        result = await self._run(modified_ctx, agentic=True)

        if result.is_success and result.output:
            files_written = self._write_files_from_output(result.output, ctx.working_dir)
            if files_written:
                result.output = (
                    f"Created/modified {len(files_written)} file(s):\n"
                    + "\n".join(f"  - {f}" for f in files_written)
                    + "\n\n" + result.output
                )

        return result

    async def _run(self, ctx: TaskContext, agentic: bool = False) -> AgentResult:
        """Core execution: shell out to `gemini -p "prompt"`."""
        if not self.is_available():
            return self._make_unavailable_result()

        start = self._now_ms()
        cmd = self._build_command(ctx, agentic=agentic)

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
                error=f"Gemini timed out after {ctx.timeout}s",
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

        # Clean output — strip ANSI codes and control chars
        clean_output = self._strip_ansi(output_text)

        # Try to parse as JSON (some gemini CLI modes output structured data)
        try:
            data = json.loads(clean_output)
            result_text = data.get("response", data.get("text", clean_output))
            return AgentResult(
                agent_name=self.name,
                output=result_text,
                status=AgentStatus.SUCCESS,
                duration_ms=elapsed,
                model=data.get("model"),
                raw_response=data,
            )
        except (json.JSONDecodeError, TypeError):
            return AgentResult(
                agent_name=self.name,
                output=clean_output,
                status=AgentStatus.SUCCESS,
                duration_ms=elapsed,
                model=self.model or "gemini",
            )

    async def stream(self, ctx: TaskContext) -> AsyncIterator[str]:
        """Stream output from Gemini CLI."""
        if not self.is_available():
            yield f"[error] {self.display_name} is not available"
            return

        cmd = self._build_command(ctx)

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

    def _write_files_from_output(self, output: str, working_dir: str) -> list[str]:
        """Parse file blocks from agent output and write them to disk."""
        from pathlib import Path

        pattern = r"=== FILE: (.+?) ===\n(.*?)(?==== END FILE ===|=== FILE:|\Z)"
        matches = re.findall(pattern, output, re.DOTALL)
        written = []

        for filepath, content in matches:
            filepath = filepath.strip()
            content = content.rstrip("\n") + "\n"

            # Security: prevent path traversal
            if ".." in filepath or filepath.startswith("/"):
                continue

            full_path = Path(working_dir) / filepath
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content)
            written.append(filepath)

        return written

    @staticmethod
    def _strip_ansi(text: str) -> str:
        """Remove ANSI escape codes from text."""
        return re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)

    @staticmethod
    def _get_env() -> dict[str, str] | None:
        """Get environment with non-interactive settings."""
        env = os.environ.copy()
        env["TERM"] = "dumb"
        env["NO_COLOR"] = "1"
        return env
