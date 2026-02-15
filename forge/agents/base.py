"""Base agent protocol and shared data models."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator, Protocol, runtime_checkable


class AgentStatus(Enum):
    """Current status of an agent execution."""
    QUEUED = "queued"
    RUNNING = "running"
    STREAMING = "streaming"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"


@dataclass
class TaskContext:
    """Context passed to agents for task execution."""
    working_dir: str
    prompt: str
    files: list[str] = field(default_factory=list)
    system_prompt: str | None = None
    previous_results: list[AgentResult] = field(default_factory=list)
    max_budget_usd: float | None = None
    timeout: int = 120


@dataclass
class AgentResult:
    """Result from an agent execution."""
    agent_name: str
    output: str
    status: AgentStatus
    duration_ms: int = 0
    cost_usd: float | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None
    raw_response: dict | None = None

    @property
    def is_success(self) -> bool:
        return self.status == AgentStatus.SUCCESS

    @property
    def duration_seconds(self) -> float:
        return self.duration_ms / 1000


@runtime_checkable
class AgentAdapter(Protocol):
    """Protocol that all agent adapters must implement."""

    name: str
    display_name: str

    async def execute(self, ctx: TaskContext) -> AgentResult:
        """Execute a task and return the result."""
        ...

    async def stream(self, ctx: TaskContext) -> AsyncIterator[str]:
        """Stream output from the agent in real-time."""
        ...

    def is_available(self) -> bool:
        """Check if this agent is available (CLI installed, authenticated, etc.)."""
        ...


class BaseAdapter:
    """Base class with shared utilities for agent adapters."""

    name: str = "base"
    display_name: str = "Base"

    def _make_error_result(self, error: str, duration_ms: int = 0) -> AgentResult:
        return AgentResult(
            agent_name=self.name,
            output="",
            status=AgentStatus.FAILED,
            duration_ms=duration_ms,
            error=error,
        )

    def _make_unavailable_result(self) -> AgentResult:
        return AgentResult(
            agent_name=self.name,
            output="",
            status=AgentStatus.UNAVAILABLE,
            error=f"{self.display_name} is not available. Check installation and authentication.",
        )

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)
