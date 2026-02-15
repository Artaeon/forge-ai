"""Advanced orchestration modes — chain, review, consensus, and swarm.

This module implements inter-agent communication patterns:

- CHAIN:     Agent A → Agent B → Agent C (output feeds forward)
- REVIEW:    Agent A produces, Agent B reviews & improves
- CONSENSUS: All agents produce, then critique each other, best wins
- SWARM:     Break task into subtasks, assign to best-fit agents
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from rich.console import Console

from forge.agents.base import AgentResult, AgentStatus, TaskContext
from forge.engine import ForgeEngine
from forge.aggregator import ResultAggregator

console = Console()


class OrchestrateMode(Enum):
    """Available orchestration patterns."""
    SINGLE = "single"       # One agent, one shot
    PARALLEL = "parallel"   # All agents, same prompt, pick best
    CHAIN = "chain"         # Sequential: output feeds into next agent
    REVIEW = "review"       # One produces, another reviews & improves
    CONSENSUS = "consensus" # All produce, then cross-critique
    SWARM = "swarm"         # Break into subtasks, assign to best agents


@dataclass
class OrchestrationResult:
    """Result from an orchestrated multi-agent session."""
    mode: OrchestrateMode
    rounds: list[OrchestrationRound]
    final_output: str
    total_cost_usd: float = 0.0
    total_duration_ms: int = 0
    agents_used: list[str] = field(default_factory=list)

    @property
    def round_count(self) -> int:
        return len(self.rounds)


@dataclass
class OrchestrationRound:
    """A single round in the orchestration process."""
    round_number: int
    agent_name: str
    role: str  # "producer", "reviewer", "critic", etc.
    prompt: str
    result: AgentResult
    

class Orchestrator:
    """Advanced multi-agent orchestration engine.
    
    Implements inter-agent communication patterns where agents
    talk to each other, critique each other's work, and build
    on each other's output.
    """

    def __init__(self, engine: ForgeEngine):
        self.engine = engine

    async def run(
        self,
        mode: OrchestrateMode,
        prompt: str,
        working_dir: str,
        agents: list[str] | None = None,
        timeout: int = 120,
        max_budget_usd: float | None = None,
        on_progress: Callable[[str, str, str], None] | None = None,
    ) -> OrchestrationResult:
        """Run an orchestration session with the specified mode."""
        available = self.engine.get_available_agents()
        agent_list = agents or [n for n, a in available.items() if a]

        if not agent_list:
            return OrchestrationResult(
                mode=mode,
                rounds=[],
                final_output="No agents available.",
            )

        dispatch = {
            OrchestrateMode.SINGLE: self._run_single,
            OrchestrateMode.PARALLEL: self._run_parallel,
            OrchestrateMode.CHAIN: self._run_chain,
            OrchestrateMode.REVIEW: self._run_review,
            OrchestrateMode.CONSENSUS: self._run_consensus,
            OrchestrateMode.SWARM: self._run_swarm,
        }

        handler = dispatch[mode]
        return await handler(prompt, working_dir, agent_list, timeout, max_budget_usd, on_progress)

    # ─── SINGLE MODE ─────────────────────────────────────────────

    async def _run_single(
        self, prompt: str, working_dir: str, agents: list[str],
        timeout: int, budget: float | None, on_progress: Callable | None,
    ) -> OrchestrationResult:
        agent = agents[0]
        ctx = TaskContext(working_dir=working_dir, prompt=prompt, timeout=timeout, max_budget_usd=budget)
        
        if on_progress:
            on_progress(agent, "running", "Executing task...")

        result = await self.engine.dispatch_single(agent, ctx)

        return OrchestrationResult(
            mode=OrchestrateMode.SINGLE,
            rounds=[OrchestrationRound(1, agent, "producer", prompt, result)],
            final_output=result.output,
            total_cost_usd=result.cost_usd or 0,
            total_duration_ms=result.duration_ms,
            agents_used=[agent],
        )

    # ─── PARALLEL MODE ───────────────────────────────────────────

    async def _run_parallel(
        self, prompt: str, working_dir: str, agents: list[str],
        timeout: int, budget: float | None, on_progress: Callable | None,
    ) -> OrchestrationResult:
        ctx = TaskContext(working_dir=working_dir, prompt=prompt, timeout=timeout, max_budget_usd=budget)

        if on_progress:
            for a in agents:
                on_progress(a, "queued", "Waiting to start...")

        results = await self.engine.dispatch_agents(agents, ctx)
        agg = ResultAggregator(results)
        best = agg.best

        rounds = [
            OrchestrationRound(1, r.agent_name, "producer", prompt, r)
            for r in results
        ]

        return OrchestrationResult(
            mode=OrchestrateMode.PARALLEL,
            rounds=rounds,
            final_output=best.output if best else "",
            total_cost_usd=agg.total_cost_usd,
            total_duration_ms=agg.total_duration_ms,
            agents_used=[r.agent_name for r in results],
        )

    # ─── CHAIN MODE ──────────────────────────────────────────────
    # Agent A produces → output fed to Agent B → output fed to Agent C
    # Each agent builds on the previous agent's work

    async def _run_chain(
        self, prompt: str, working_dir: str, agents: list[str],
        timeout: int, budget: float | None, on_progress: Callable | None,
    ) -> OrchestrationResult:
        rounds: list[OrchestrationRound] = []
        current_output = ""
        total_cost = 0.0
        total_duration = 0

        for i, agent in enumerate(agents):
            if i == 0:
                # First agent gets the original prompt
                agent_prompt = prompt
                role = "initiator"
            else:
                # Subsequent agents get the previous output + context
                agent_prompt = (
                    f"You are agent #{i+1} in a chain of {len(agents)} AI agents "
                    f"working together on a task.\n\n"
                    f"ORIGINAL TASK: {prompt}\n\n"
                    f"PREVIOUS AGENT ({agents[i-1].upper()}) OUTPUT:\n"
                    f"```\n{current_output[-4000:]}\n```\n\n"
                    f"Your job: Review the previous agent's work, improve it, "
                    f"fix any issues, and add anything that was missed. "
                    f"Produce the final improved version."
                )
                role = "improver"

            if on_progress:
                on_progress(agent, "running", f"Chain step {i+1}/{len(agents)}")

            ctx = TaskContext(
                working_dir=working_dir,
                prompt=agent_prompt,
                timeout=timeout,
                max_budget_usd=budget,
            )
            result = await self.engine.dispatch_single(agent, ctx)
            
            rounds.append(OrchestrationRound(i + 1, agent, role, agent_prompt, result))
            
            if result.is_success:
                current_output = result.output
            total_cost += result.cost_usd or 0
            total_duration += result.duration_ms

            if on_progress:
                status = "done" if result.is_success else "failed"
                on_progress(agent, status, f"Chain step {i+1} complete")

        return OrchestrationResult(
            mode=OrchestrateMode.CHAIN,
            rounds=rounds,
            final_output=current_output,
            total_cost_usd=total_cost,
            total_duration_ms=total_duration,
            agents_used=agents,
        )

    # ─── REVIEW MODE ─────────────────────────────────────────────
    # Agent A produces code → Agent B reviews & improves → Agent A refines

    async def _run_review(
        self, prompt: str, working_dir: str, agents: list[str],
        timeout: int, budget: float | None, on_progress: Callable | None,
    ) -> OrchestrationResult:
        if len(agents) < 2:
            # Fallback to single if only one agent
            return await self._run_single(prompt, working_dir, agents, timeout, budget, on_progress)

        producer = agents[0]
        reviewer = agents[1]
        rounds: list[OrchestrationRound] = []
        total_cost = 0.0
        total_duration = 0

        # Round 1: Producer creates initial work
        if on_progress:
            on_progress(producer, "running", "Producing initial work...")

        ctx1 = TaskContext(working_dir=working_dir, prompt=prompt, timeout=timeout, max_budget_usd=budget)
        result1 = await self.engine.dispatch_single(producer, ctx1)
        rounds.append(OrchestrationRound(1, producer, "producer", prompt, result1))
        total_cost += result1.cost_usd or 0
        total_duration += result1.duration_ms

        if not result1.is_success:
            return OrchestrationResult(
                mode=OrchestrateMode.REVIEW,
                rounds=rounds,
                final_output=result1.output,
                total_cost_usd=total_cost,
                total_duration_ms=total_duration,
                agents_used=[producer],
            )

        # Round 2: Reviewer critiques and improves
        if on_progress:
            on_progress(reviewer, "running", "Reviewing and improving...")

        review_prompt = (
            f"You are a senior code reviewer. Another AI agent ({producer.upper()}) "
            f"produced the following work.\n\n"
            f"ORIGINAL TASK: {prompt}\n\n"
            f"PRODUCED CODE/OUTPUT:\n```\n{result1.output[-4000:]}\n```\n\n"
            f"Please:\n"
            f"1. Identify any bugs, issues, or improvements\n"
            f"2. Produce an IMPROVED version that fixes all issues\n"
            f"3. Explain what you changed and why\n\n"
            f"Output the complete improved version."
        )

        ctx2 = TaskContext(working_dir=working_dir, prompt=review_prompt, timeout=timeout, max_budget_usd=budget)
        result2 = await self.engine.dispatch_single(reviewer, ctx2)
        rounds.append(OrchestrationRound(2, reviewer, "reviewer", review_prompt, result2))
        total_cost += result2.cost_usd or 0
        total_duration += result2.duration_ms

        # Round 3: Producer refines based on review (if we have 3+ agents or cycle back)
        if len(agents) >= 3:
            refiner = agents[2]
        else:
            refiner = producer

        if on_progress:
            on_progress(refiner, "running", "Final refinement...")

        refine_prompt = (
            f"You are doing final refinement. Here is the task and two previous iterations.\n\n"
            f"ORIGINAL TASK: {prompt}\n\n"
            f"FIRST VERSION ({producer.upper()}):\n```\n{result1.output[-2000:]}\n```\n\n"
            f"REVIEWED VERSION ({reviewer.upper()}):\n```\n{result2.output[-2000:]}\n```\n\n"
            f"Produce the FINAL, polished version incorporating the best of both. "
            f"Focus on correctness, clean code, and completeness."
        )

        ctx3 = TaskContext(working_dir=working_dir, prompt=refine_prompt, timeout=timeout, max_budget_usd=budget)
        result3 = await self.engine.dispatch_single(refiner, ctx3)
        rounds.append(OrchestrationRound(3, refiner, "refiner", refine_prompt, result3))
        total_cost += result3.cost_usd or 0
        total_duration += result3.duration_ms

        final_output = result3.output if result3.is_success else result2.output

        return OrchestrationResult(
            mode=OrchestrateMode.REVIEW,
            rounds=rounds,
            final_output=final_output,
            total_cost_usd=total_cost,
            total_duration_ms=total_duration,
            agents_used=list(set([producer, reviewer, refiner])),
        )

    # ─── CONSENSUS MODE ──────────────────────────────────────────
    # All agents produce independently → each critiques the others → best wins

    async def _run_consensus(
        self, prompt: str, working_dir: str, agents: list[str],
        timeout: int, budget: float | None, on_progress: Callable | None,
    ) -> OrchestrationResult:
        rounds: list[OrchestrationRound] = []
        total_cost = 0.0
        total_duration = 0

        # Phase 1: All agents produce independently (parallel)
        if on_progress:
            for a in agents:
                on_progress(a, "running", "Phase 1: Independent production")

        ctx = TaskContext(working_dir=working_dir, prompt=prompt, timeout=timeout, max_budget_usd=budget)
        initial_results = await self.engine.dispatch_agents(agents, ctx)

        for r in initial_results:
            rounds.append(OrchestrationRound(1, r.agent_name, "producer", prompt, r))
            total_cost += r.cost_usd or 0
            total_duration += r.duration_ms

        successful = [r for r in initial_results if r.is_success]
        if len(successful) <= 1:
            # Not enough for consensus, return best of what we have
            best = successful[0] if successful else initial_results[0]
            return OrchestrationResult(
                mode=OrchestrateMode.CONSENSUS,
                rounds=rounds,
                final_output=best.output,
                total_cost_usd=total_cost,
                total_duration_ms=total_duration,
                agents_used=[r.agent_name for r in initial_results],
            )

        # Phase 2: Each agent critiques ALL other outputs
        # Use the first available agent as the judge to save cost
        judge = successful[0].agent_name

        if on_progress:
            on_progress(judge, "running", "Phase 2: Cross-critique & selection")

        # Build comparison prompt
        outputs_text = ""
        for i, r in enumerate(successful):
            outputs_text += (
                f"\n--- SOLUTION {i+1} (by {r.agent_name.upper()}) ---\n"
                f"{r.output[-2000:]}\n"
            )

        judge_prompt = (
            f"You are judging multiple AI-generated solutions to a task.\n\n"
            f"ORIGINAL TASK: {prompt}\n\n"
            f"SOLUTIONS:{outputs_text}\n\n"
            f"Please:\n"
            f"1. Analyze each solution's strengths and weaknesses\n"
            f"2. Pick the best elements from each\n"
            f"3. Produce a FINAL SYNTHESIZED solution that combines the best parts\n\n"
            f"Output only the final synthesized solution."
        )

        ctx_judge = TaskContext(
            working_dir=working_dir,
            prompt=judge_prompt,
            timeout=timeout,
            max_budget_usd=budget,
        )
        judge_result = await self.engine.dispatch_single(judge, ctx_judge)
        rounds.append(OrchestrationRound(2, judge, "judge", judge_prompt, judge_result))
        total_cost += judge_result.cost_usd or 0
        total_duration += judge_result.duration_ms

        final_output = judge_result.output if judge_result.is_success else successful[0].output

        return OrchestrationResult(
            mode=OrchestrateMode.CONSENSUS,
            rounds=rounds,
            final_output=final_output,
            total_cost_usd=total_cost,
            total_duration_ms=total_duration,
            agents_used=[r.agent_name for r in initial_results] + [judge],
        )

    # ─── SWARM MODE ──────────────────────────────────────────────
    # Break the task into subtasks, assign each to the best-fit agent

    async def _run_swarm(
        self, prompt: str, working_dir: str, agents: list[str],
        timeout: int, budget: float | None, on_progress: Callable | None,
    ) -> OrchestrationResult:
        rounds: list[OrchestrationRound] = []
        total_cost = 0.0
        total_duration = 0

        # Phase 1: Use first agent as planner to break task into subtasks
        planner = agents[0]

        if on_progress:
            on_progress(planner, "running", "Phase 1: Planning subtasks")

        plan_prompt = (
            f"Break this task into subtasks. Each subtask should be independent.\n"
            f"Available agents: {', '.join(a.upper() for a in agents)}\n\n"
            f"Agent strengths:\n"
            f"- CLAUDE: Best at code generation, debugging, architecture, complex logic\n"
            f"- GEMINI: Best at explanation, documentation, web search, code review\n"
            f"- COPILOT: Best at shell commands, git operations, quick scripts\n\n"
            f"TASK: {prompt}\n\n"
            f"Output a JSON array of subtasks, each with 'agent' and 'task' fields:\n"
            f'[{{"agent": "claude", "task": "..."}}, {{"agent": "gemini", "task": "..."}}]\n\n'
            f"Output ONLY the JSON array, no other text."
        )

        ctx_plan = TaskContext(
            working_dir=working_dir,
            prompt=plan_prompt,
            timeout=timeout,
            max_budget_usd=budget,
        )
        plan_result = await self.engine.dispatch_single(planner, ctx_plan)
        rounds.append(OrchestrationRound(1, planner, "planner", plan_prompt, plan_result))
        total_cost += plan_result.cost_usd or 0
        total_duration += plan_result.duration_ms

        # Parse subtasks from planner output
        subtasks = self._parse_subtasks(plan_result.output, agents)

        if not subtasks:
            # Fallback: just run the whole thing on the first agent
            return await self._run_single(prompt, working_dir, agents, timeout, budget, on_progress)

        # Phase 2: Execute subtasks (parallel where possible)
        if on_progress:
            for st in subtasks:
                on_progress(st["agent"], "queued", f"Subtask: {st['task'][:50]}...")

        async def run_subtask(st: dict, idx: int) -> AgentResult:
            if on_progress:
                on_progress(st["agent"], "running", f"Subtask {idx+1}")
            ctx = TaskContext(
                working_dir=working_dir,
                prompt=st["task"],
                timeout=timeout,
                max_budget_usd=budget,
            )
            return await self.engine.dispatch_single(st["agent"], ctx)

        subtask_results = await asyncio.gather(
            *[run_subtask(st, i) for i, st in enumerate(subtasks)]
        )

        for i, (st, result) in enumerate(zip(subtasks, subtask_results)):
            rounds.append(OrchestrationRound(2, st["agent"], "worker", st["task"], result))
            total_cost += result.cost_usd or 0
            total_duration += result.duration_ms

        # Phase 3: Combine all subtask results
        combined = "\n\n".join(
            f"=== Subtask: {st['task'][:80]} ({st['agent'].upper()}) ===\n{r.output}"
            for st, r in zip(subtasks, subtask_results)
            if r.is_success
        )

        return OrchestrationResult(
            mode=OrchestrateMode.SWARM,
            rounds=rounds,
            final_output=combined,
            total_cost_usd=total_cost,
            total_duration_ms=total_duration,
            agents_used=list(set(st["agent"] for st in subtasks)),
        )

    @staticmethod
    def _parse_subtasks(output: str, available_agents: list[str]) -> list[dict]:
        """Parse JSON subtask list from planner output."""
        import json
        import re

        # Try to find JSON array in the output
        # Look for [...] pattern
        match = re.search(r'\[.*\]', output, re.DOTALL)
        if not match:
            return []

        try:
            tasks = json.loads(match.group())
            if not isinstance(tasks, list):
                return []

            valid = []
            for t in tasks:
                if isinstance(t, dict) and "agent" in t and "task" in t:
                    agent = t["agent"].lower()
                    if agent in available_agents:
                        valid.append({"agent": agent, "task": t["task"]})
                    else:
                        # Assign to first available agent
                        valid.append({"agent": available_agents[0], "task": t["task"]})
            return valid
        except (json.JSONDecodeError, KeyError):
            return []
