"""Orchestration engine — dispatches tasks to agents and manages execution."""

from __future__ import annotations

import asyncio
from typing import Callable

from forge.agents.base import AgentResult, AgentStatus, BaseAdapter, TaskContext
from forge.config import ForgeConfig, AgentConfig


class ForgeEngine:
    """Core orchestrator that manages agent lifecycle and task dispatch."""

    def __init__(self, config: ForgeConfig):
        self.config = config
        self._adapters: dict[str, BaseAdapter] = {}
        self._init_adapters()

    def _init_adapters(self) -> None:
        """Initialize adapters based on configuration.
        
        Supports multiple model variants per backend type via agent_type field.
        E.g., claude-sonnet, claude-opus both map to ClaudeAdapter with different models.
        """
        from forge.agents.claude import ClaudeAdapter
        from forge.agents.gemini import GeminiAdapter
        from forge.agents.copilot import CopilotAdapter

        adapter_classes: dict[str, type] = {
            "claude": ClaudeAdapter,
            "gemini": GeminiAdapter,
            "copilot": CopilotAdapter,
        }

        for name, agent_cfg in self.config.agents.items():
            if not agent_cfg.enabled:
                continue
            # Use agent_type to find adapter class, fallback to name
            agent_type = agent_cfg.agent_type or name
            cls = adapter_classes.get(agent_type)
            if cls is None:
                continue
            adapter = self._create_adapter(cls, agent_cfg)
            # Override the adapter's name to match the config key
            adapter.name = name
            adapter.display_name = f"{agent_type.capitalize()} ({agent_cfg.model})" if agent_cfg.model else agent_type.capitalize()
            self._adapters[name] = adapter

    @staticmethod
    def _create_adapter(cls: type, cfg: AgentConfig) -> BaseAdapter:
        """Create an adapter instance from config."""
        from forge.agents.claude import ClaudeAdapter
        from forge.agents.gemini import GeminiAdapter
        from forge.agents.copilot import CopilotAdapter

        if cls is ClaudeAdapter:
            return ClaudeAdapter(
                model=cfg.model,
                max_budget_usd=cfg.max_budget_usd,
                skip_permissions=cfg.skip_permissions,
                extra_args=cfg.extra_args,
            )
        elif cls is GeminiAdapter:
            return GeminiAdapter(
                fallback_to_api=cfg.fallback_to_api,
                extra_args=cfg.extra_args,
            )
        elif cls is CopilotAdapter:
            return CopilotAdapter(extra_args=cfg.extra_args)
        else:
            raise ValueError(f"Unknown adapter class: {cls}")

    @property
    def adapters(self) -> dict[str, BaseAdapter]:
        return self._adapters

    def get_available_agents(self) -> dict[str, bool]:
        """Check which agents are currently available."""
        return {
            name: adapter.is_available()
            for name, adapter in self._adapters.items()
        }

    async def dispatch_single(
        self,
        agent_name: str,
        ctx: TaskContext,
        on_progress: Callable[[str, str], None] | None = None,
    ) -> AgentResult:
        """Dispatch a task to a single agent."""
        adapter = self._adapters.get(agent_name)
        if adapter is None:
            return AgentResult(
                agent_name=agent_name,
                output="",
                status=AgentStatus.UNAVAILABLE,
                error=f"Agent '{agent_name}' is not configured",
            )

        if on_progress:
            on_progress(agent_name, "running")

        result = await adapter.execute(ctx)

        if on_progress:
            status = "success" if result.is_success else "failed"
            on_progress(agent_name, status)

        return result

    async def dispatch_all(
        self,
        ctx: TaskContext,
        on_progress: Callable[[str, str], None] | None = None,
    ) -> list[AgentResult]:
        """Dispatch a task to ALL available agents in parallel."""
        available = {
            name: adapter
            for name, adapter in self._adapters.items()
            if adapter.is_available()
        }

        if not available:
            return [
                AgentResult(
                    agent_name="forge",
                    output="",
                    status=AgentStatus.FAILED,
                    error="No agents are available. Check installation and configuration.",
                )
            ]

        # Fan out to all agents concurrently
        semaphore = asyncio.Semaphore(self.config.global_.max_parallel)

        async def run_with_semaphore(name: str, adapter: BaseAdapter) -> AgentResult:
            async with semaphore:
                if on_progress:
                    on_progress(name, "running")
                result = await adapter.execute(ctx)
                if on_progress:
                    status = "success" if result.is_success else "failed"
                    on_progress(name, status)
                return result

        tasks = [
            run_with_semaphore(name, adapter)
            for name, adapter in available.items()
        ]

        return list(await asyncio.gather(*tasks, return_exceptions=False))

    async def dispatch_agents(
        self,
        agent_names: list[str],
        ctx: TaskContext,
        on_progress: Callable[[str, str], None] | None = None,
    ) -> list[AgentResult]:
        """Dispatch to specific named agents in parallel."""
        semaphore = asyncio.Semaphore(self.config.global_.max_parallel)

        async def run_agent(name: str) -> AgentResult:
            async with semaphore:
                return await self.dispatch_single(name, ctx, on_progress)

        tasks = [run_agent(name) for name in agent_names]
        return list(await asyncio.gather(*tasks, return_exceptions=False))
