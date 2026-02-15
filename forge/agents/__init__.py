"""Forge agent adapters."""

from forge.agents.base import AgentAdapter, AgentResult, TaskContext
from forge.agents.claude import ClaudeAdapter
from forge.agents.gemini import GeminiAdapter
from forge.agents.copilot import CopilotAdapter

__all__ = [
    "AgentAdapter",
    "AgentResult",
    "TaskContext",
    "ClaudeAdapter",
    "GeminiAdapter",
    "CopilotAdapter",
]
