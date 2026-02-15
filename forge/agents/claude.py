"""Claude Code CLI adapter.

Supports two modes:
- PRINT mode (default for `forge run`): `claude --print` for text-only Q&A
- AGENTIC mode (for `forge build`): `claude` without --print, can write/edit files
"""

from __future__ import annotations

import asyncio
import json
import shutil
from typing import AsyncIterator

from forge.agents.base import AgentResult, AgentStatus, BaseAdapter, TaskContext


class ClaudeAdapter(BaseAdapter):
    """Adapter for Claude Code CLI.
    
    In print mode: `claude --print --output-format json` (Q&A, no file changes)
    In agentic mode: `claude -p` (can create/edit/delete files autonomously)
    """

    name = "claude"
    display_name = "Claude Code"

    def __init__(
        self,
        model: str | None = "sonnet",
        max_budget_usd: float | None = None,
        skip_permissions: bool = False,
        extra_args: list[str] | None = None,
    ):
        self.model = model
        self.max_budget_usd = max_budget_usd
        self.skip_permissions = skip_permissions
        self.extra_args = extra_args or []

    def is_available(self) -> bool:
        return shutil.which("claude") is not None

    def _build_command(self, ctx: TaskContext, agentic: bool = False) -> list[str]:
        """Build the claude command.
        
        agentic=False: claude --print (read-only, returns text)
        agentic=True:  claude -p --dangerously-skip-permissions (writes files)
        """
        if agentic:
            # Agentic mode — Claude can create, edit, delete files
            cmd = [
                "claude",
                "-p",  # print mode but with full capabilities
                "--output-format", "json",
                "--dangerously-skip-permissions",  # Required for autonomous operation
            ]
        else:
            # Print mode — text-only Q&A
            cmd = [
                "claude",
                "--print",
                "--output-format", "json",
            ]

        if self.model:
            cmd.extend(["--model", self.model])

        budget = ctx.max_budget_usd or self.max_budget_usd
        if budget:
            cmd.extend(["--max-budget-usd", str(budget)])

        if not agentic and self.skip_permissions:
            cmd.append("--dangerously-skip-permissions")

        if ctx.system_prompt:
            cmd.extend(["--system-prompt", ctx.system_prompt])

        cmd.extend(self.extra_args)
        cmd.append(ctx.prompt)

        return cmd

    async def execute(self, ctx: TaskContext) -> AgentResult:
        """Execute in print mode (text-only, no file changes)."""
        return await self._run(ctx, agentic=False)

    async def execute_agentic(self, ctx: TaskContext) -> AgentResult:
        """Execute in agentic mode — Claude can create, edit, and delete files.
        
        This is used by the build pipeline where Claude needs to actually
        write code to the filesystem.
        """
        return await self._run(ctx, agentic=True)

    async def _run(self, ctx: TaskContext, agentic: bool = False) -> AgentResult:
        """Core execution logic used by both modes."""
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
                error=f"Claude timed out after {ctx.timeout}s",
            )
        except Exception as e:
            return self._make_error_result(str(e), self._now_ms() - start)

        elapsed = self._now_ms() - start

        if proc.returncode != 0:
            return self._make_error_result(
                stderr.decode(errors="replace").strip() or f"Exit code {proc.returncode}",
                elapsed,
            )

        # Parse response
        raw_text = stdout.decode(errors="replace").strip()
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            # Not JSON — treat as plain text
            return AgentResult(
                agent_name=self.name,
                output=raw_text,
                status=AgentStatus.SUCCESS,
                duration_ms=elapsed,
            )

        # Extract structured data
        usage = data.get("usage", {})
        result_text = data.get("result", raw_text)
        is_error = data.get("is_error", False)

        return AgentResult(
            agent_name=self.name,
            output=result_text,
            status=AgentStatus.SUCCESS if not is_error else AgentStatus.FAILED,
            duration_ms=data.get("duration_ms", elapsed),
            cost_usd=data.get("total_cost_usd"),
            model=self._extract_model(data),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            error=result_text if is_error else None,
            raw_response=data,
        )

    async def stream(self, ctx: TaskContext) -> AsyncIterator[str]:
        if not self.is_available():
            yield f"[error] {self.display_name} is not available"
            return

        cmd = [
            "claude",
            "--print",
            "--output-format", "stream-json",
        ]

        if self.model:
            cmd.extend(["--model", self.model])

        budget = ctx.max_budget_usd or self.max_budget_usd
        if budget:
            cmd.extend(["--max-budget-usd", str(budget)])

        if self.skip_permissions:
            cmd.append("--dangerously-skip-permissions")

        if ctx.system_prompt:
            cmd.extend(["--system-prompt", ctx.system_prompt])

        cmd.extend(self.extra_args)
        cmd.append(ctx.prompt)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=ctx.working_dir,
        )

        assert proc.stdout is not None
        async for line in proc.stdout:
            text = line.decode(errors="replace").strip()
            if not text:
                continue
            try:
                event = json.loads(text)
                event_type = event.get("type", "")
                if event_type == "assistant" and "message" in event:
                    msg = event["message"]
                    if isinstance(msg, dict):
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    yield block.get("text", "")
                        elif isinstance(content, str):
                            yield content
                    elif isinstance(msg, str):
                        yield msg
                elif event_type == "result":
                    yield event.get("result", "")
            except json.JSONDecodeError:
                yield text

        await proc.wait()

    @staticmethod
    def _extract_model(data: dict) -> str | None:
        model_usage = data.get("modelUsage", {})
        if model_usage:
            return next(iter(model_usage.keys()), None)
        return None
