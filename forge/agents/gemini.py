"""Gemini CLI adapter."""

from __future__ import annotations

import asyncio
import json
import shutil
from typing import AsyncIterator

from forge.agents.base import AgentResult, AgentStatus, BaseAdapter, TaskContext


class GeminiAdapter(BaseAdapter):
    """Adapter for Gemini CLI (@google/gemini-cli).

    The Gemini CLI supports non-interactive mode via stdin piping.
    Falls back to basic prompt mode when advanced features are unavailable.
    """

    name = "gemini"
    display_name = "Gemini"

    def __init__(
        self,
        fallback_to_api: bool = True,
        extra_args: list[str] | None = None,
    ):
        self.fallback_to_api = fallback_to_api
        self.extra_args = extra_args or []

    def is_available(self) -> bool:
        return shutil.which("gemini") is not None

    def _build_command(self, ctx: TaskContext) -> list[str]:
        """Build the gemini CLI command.

        Gemini CLI accepts prompts via stdin in non-interactive mode.
        Using -y flag for non-interactive (auto-accept) mode.
        """
        cmd = ["gemini"]
        cmd.extend(self.extra_args)
        return cmd

    async def execute(self, ctx: TaskContext) -> AgentResult:
        if not self.is_available():
            return self._make_unavailable_result()

        start = self._now_ms()

        # Gemini CLI: pipe prompt via stdin with non-interactive flags
        # Using echo to pipe the prompt, then EOF to signal end
        cmd = self._build_command(ctx)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=ctx.working_dir,
                env=self._get_env(),
            )

            # Send the prompt via stdin and close it to signal EOF
            prompt_bytes = ctx.prompt.encode()
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=prompt_bytes),
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

        # Try to parse as JSON (some gemini CLI versions output structured data)
        try:
            data = json.loads(output_text)
            result_text = data.get("response", data.get("text", output_text))
            return AgentResult(
                agent_name=self.name,
                output=result_text,
                status=AgentStatus.SUCCESS,
                duration_ms=elapsed,
                model=data.get("model"),
                raw_response=data,
            )
        except (json.JSONDecodeError, TypeError):
            # Plain text output — strip ANSI escape codes
            clean_output = self._strip_ansi(output_text)
            return AgentResult(
                agent_name=self.name,
                output=clean_output,
                status=AgentStatus.SUCCESS,
                duration_ms=elapsed,
            )

    async def stream(self, ctx: TaskContext) -> AsyncIterator[str]:
        if not self.is_available():
            yield f"[error] {self.display_name} is not available"
            return

        cmd = self._build_command(ctx)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=ctx.working_dir,
            env=self._get_env(),
        )

        # Send prompt
        assert proc.stdin is not None
        proc.stdin.write(ctx.prompt.encode())
        proc.stdin.close()

        # Stream stdout
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
        """Get environment with any needed modifications."""
        import os
        env = os.environ.copy()
        # Ensure non-interactive mode
        env["TERM"] = "dumb"
        env["NO_COLOR"] = "1"
        return env
