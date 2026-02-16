"""Dispatch helpers — execute agent calls with spinners and retries.

Extracted from DuoBuildPipeline to reduce god-class complexity.
All functions accept the pipeline instance as the first argument.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console

from forge.agents.base import TaskContext

if TYPE_CHECKING:
    from forge.build.duo import DuoBuildPipeline, DuoRound

console = Console()
logger = logging.getLogger(__name__)


async def execute_with_spinner(
    pipeline: DuoBuildPipeline,
    execute_fn,
    ctx: TaskContext,
    phase: str,
    agent: str,
    max_retries: int = 1,
):
    """Execute an agent function with a live progress spinner and auto-retry."""
    from forge.build.duo import PHASE_ICONS

    icon = PHASE_ICONS.get(phase, "")

    for attempt in range(max_retries + 1):
        start = time.monotonic()

        try:
            task = asyncio.create_task(execute_fn(ctx))

            retry_label = f" (retry {attempt})" if attempt > 0 else ""
            with console.status(
                f"[bold]{icon} {agent.upper()}[/] working{retry_label}...",
                spinner="dots",
            ) as status:
                while not task.done():
                    elapsed = time.monotonic() - start
                    status.update(
                        f"[bold]{icon} {agent.upper()}[/] working{retry_label}... "
                        f"[dim]({elapsed:.0f}s)[/]"
                    )
                    await asyncio.sleep(1.0)

            result = task.result()

            if not result.is_success and attempt < max_retries:
                console.print(
                    f"[yellow]  ⚠ {agent.upper()} failed — retrying in 3s...[/]"
                )
                await asyncio.sleep(3.0)
                continue

            return result

        except (asyncio.TimeoutError, TimeoutError, OSError) as e:
            logger.warning("Agent %s error on attempt %d: %s", agent, attempt, e)
            if attempt < max_retries:
                console.print(
                    f"[yellow]  ⚠ {agent.upper()} error: {e} — retrying in 3s...[/]"
                )
                await asyncio.sleep(3.0)
                continue
            raise

    # Should not reach here, but just in case
    return task.result()  # type: ignore[possibly-undefined]


async def dispatch(
    pipeline: DuoBuildPipeline, phase: str, agent: str, prompt: str,
) -> DuoRound:
    """Dispatch to an agent in read-only mode."""
    from forge.build.duo import DuoRound

    ctx = TaskContext(
        working_dir=pipeline.working_dir,
        prompt=prompt,
        timeout=pipeline.timeout,
    )

    adapter = pipeline.engine.adapters.get(agent)
    if adapter is None:
        return DuoRound(
            round_number=len(pipeline.rounds) + 1,
            phase=phase,
            agent_name=agent,
            prompt=prompt[:200],
            output=f"Agent '{agent}' not found",
            success=False,
        )

    result = await execute_with_spinner(pipeline, adapter.execute, ctx, phase, agent)

    return DuoRound(
        round_number=len(pipeline.rounds) + 1,
        phase=phase,
        agent_name=agent,
        prompt=prompt[:200],
        output=result.output,
        success=result.is_success,
        duration_ms=result.duration_ms,
        cost_usd=result.cost_usd,
    )


async def dispatch_agentic(
    pipeline: DuoBuildPipeline, phase: str, agent: str, prompt: str,
) -> DuoRound:
    """Dispatch to an agent in agentic mode (can write files).

    If the agent can't natively write files (like Gemini CLI),
    we parse its text output for file blocks and write them ourselves.
    """
    from forge.build.duo import DuoRound

    ctx = TaskContext(
        working_dir=pipeline.working_dir,
        prompt=prompt,
        timeout=pipeline.timeout,
    )

    adapter = pipeline.engine.adapters.get(agent)
    if adapter is None:
        return DuoRound(
            round_number=len(pipeline.rounds) + 1,
            phase=phase,
            agent_name=agent,
            prompt=prompt[:200],
            output=f"Agent '{agent}' not found",
            success=False,
        )

    # Count files before execution
    files_before = set(pipeline._list_project_files())

    if hasattr(adapter, "execute_agentic"):
        result = await execute_with_spinner(
            pipeline, adapter.execute_agentic, ctx, phase, agent
        )
    else:
        result = await execute_with_spinner(
            pipeline, adapter.execute, ctx, phase, agent
        )

    # Check if any files were actually created
    files_after = set(pipeline._list_project_files())
    new_files = files_after - files_before

    # Fallback: if no files were created on disk, parse output for file blocks
    if result.is_success and not new_files and result.output:
        extracted = extract_files_from_output(pipeline, result.output)
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
    pipeline._commit_round(phase)

    return DuoRound(
        round_number=len(pipeline.rounds) + 1,
        phase=phase,
        agent_name=agent,
        prompt=prompt[:200],
        output=result.output,
        success=result.is_success,
        duration_ms=result.duration_ms,
        cost_usd=result.cost_usd,
    )


def extract_files_from_output(
    pipeline: DuoBuildPipeline, output: str,
) -> list[str]:
    """Parse file blocks from agent text output and write to disk.

    Fallback for agents that can't write files natively (e.g. Gemini CLI).
    Supports multiple output formats.
    """
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

    written: list[str] = []

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

        full_path = Path(pipeline.working_dir) / filepath
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content)
        written.append(filepath)

    return written
