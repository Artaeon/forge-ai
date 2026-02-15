"""Result aggregation and comparison for multi-agent execution."""

from __future__ import annotations

from forge.agents.base import AgentResult


class ResultAggregator:
    """Aggregates and compares results from multiple agents."""

    def __init__(self, results: list[AgentResult]):
        self.results = results
        self.successful = [r for r in results if r.is_success]
        self.failed = [r for r in results if not r.is_success]

    @property
    def total_cost_usd(self) -> float:
        """Total cost across all agents."""
        return sum(r.cost_usd or 0.0 for r in self.results)

    @property
    def total_duration_ms(self) -> int:
        """Longest duration (since they run in parallel)."""
        if not self.results:
            return 0
        return max(r.duration_ms for r in self.results)

    @property
    def fastest(self) -> AgentResult | None:
        """Fastest successful result."""
        if not self.successful:
            return None
        return min(self.successful, key=lambda r: r.duration_ms)

    @property
    def cheapest(self) -> AgentResult | None:
        """Cheapest successful result (by cost)."""
        with_cost = [r for r in self.successful if r.cost_usd is not None]
        if not with_cost:
            return self.fastest  # fallback
        return min(with_cost, key=lambda r: r.cost_usd or float("inf"))

    @property
    def best(self) -> AgentResult | None:
        """Best result by heuristic scoring.
        
        Score = output_length * 0.4 + speed_score * 0.3 + cost_score * 0.3
        """
        if not self.successful:
            return None
        if len(self.successful) == 1:
            return self.successful[0]

        max_len = max(len(r.output) for r in self.successful) or 1
        max_dur = max(r.duration_ms for r in self.successful) or 1
        max_cost = max(r.cost_usd or 0.01 for r in self.successful) or 0.01

        def score(r: AgentResult) -> float:
            length_score = len(r.output) / max_len
            speed_score = 1.0 - (r.duration_ms / max_dur)
            cost_score = 1.0 - ((r.cost_usd or 0) / max_cost)
            return length_score * 0.4 + speed_score * 0.3 + cost_score * 0.3

        return max(self.successful, key=score)

    def summary_dict(self) -> dict:
        """Return a summary suitable for display."""
        return {
            "total_agents": len(self.results),
            "successful": len(self.successful),
            "failed": len(self.failed),
            "total_cost_usd": self.total_cost_usd,
            "total_duration_ms": self.total_duration_ms,
            "fastest_agent": self.fastest.agent_name if self.fastest else None,
            "cheapest_agent": self.cheapest.agent_name if self.cheapest else None,
            "best_agent": self.best.agent_name if self.best else None,
        }
