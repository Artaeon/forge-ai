"""Rich TUI panels for real-time agent progress and results display."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from forge.agents.base import AgentResult, AgentStatus
    from forge.config import ForgeConfig
    from forge.orchestrate import OrchestrationResult

console = Console()

# Status icons
STATUS_ICONS = {
    "queued":      "⏳",
    "running":     "🔄",
    "streaming":   "📡",
    "success":     "✅",
    "failed":      "❌",
    "timeout":     "⏰",
    "unavailable": "🚫",
}

AGENT_COLORS: dict[str, str] = {
    "claude-sonnet": "bright_magenta",
    "claude-opus": "magenta",
    "claude-haiku": "orchid1",
    "claude": "bright_magenta",
    "gemini": "bright_cyan",
    "copilot": "bright_green",
}


def _get_color(name: str) -> str:
    """Get color for agent, with fallback."""
    if name in AGENT_COLORS:
        return AGENT_COLORS[name]
    # Check prefix
    for prefix in ("claude", "gemini", "copilot"):
        if name.startswith(prefix):
            return AGENT_COLORS[prefix]
    return "white"


def format_cost(cost: float | None) -> str:
    if cost is None:
        return "—"
    return f"${cost:.4f}"


def format_duration(ms: int) -> str:
    if ms < 1000:
        return f"{ms}ms"
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = seconds / 60
    return f"{minutes:.1f}m"


def format_tokens(input_t: int | None, output_t: int | None) -> str:
    if input_t is None and output_t is None:
        return "—"
    parts = []
    if input_t is not None:
        parts.append(f"↓{input_t:,}")
    if output_t is not None:
        parts.append(f"↑{output_t:,}")
    return " ".join(parts)


def make_agent_panel(result: AgentResult) -> Panel:
    """Create a Rich panel for a single agent result."""
    from forge.agents.base import AgentStatus

    icon = STATUS_ICONS.get(result.status.value, "❓")
    color = _get_color(result.agent_name)

    # Header info
    header = Text()
    header.append(f"{icon} ", style="bold")
    header.append(result.agent_name.upper(), style=f"bold {color}")
    if result.model:
        header.append(f"  [{result.model}]", style="dim")
    header.append(f"  {format_duration(result.duration_ms)}", style="dim")
    if result.cost_usd is not None:
        header.append(f"  {format_cost(result.cost_usd)}", style="yellow")

    # Content
    if result.is_success:
        content = result.output[:3000]
        if len(result.output) > 3000:
            content += f"\n\n... ({len(result.output) - 3000} more chars)"
    elif result.error:
        content = f"[Error] {result.error}"
    else:
        content = "[No output]"

    return Panel(
        content,
        title=header,
        border_style=color if result.is_success else "red",
        padding=(1, 2),
    )


def make_summary_table(results: list[AgentResult]) -> Table:
    """Create a summary comparison table across all agents."""
    table = Table(
        title="⚡ Agent Results",
        show_header=True,
        header_style="bold bright_white",
        border_style="bright_black",
        padding=(0, 1),
    )

    table.add_column("Agent", style="bold", min_width=14)
    table.add_column("Status", justify="center", min_width=8)
    table.add_column("Model", style="dim", min_width=12)
    table.add_column("Time", justify="right", min_width=8)
    table.add_column("Cost", justify="right", style="yellow", min_width=8)
    table.add_column("Tokens", justify="right", style="dim", min_width=14)
    table.add_column("Output", justify="right", min_width=8)

    for r in results:
        icon = STATUS_ICONS.get(r.status.value, "❓")
        color = _get_color(r.agent_name)
        table.add_row(
            Text(r.agent_name.upper(), style=f"bold {color}"),
            f"{icon} {r.status.value}",
            r.model or "—",
            format_duration(r.duration_ms),
            format_cost(r.cost_usd),
            format_tokens(r.input_tokens, r.output_tokens),
            f"{len(r.output):,} chars",
        )

    # Totals row
    total_cost = sum(r.cost_usd or 0 for r in results)
    max_dur = max(r.duration_ms for r in results) if results else 0
    table.add_section()
    table.add_row(
        Text("TOTAL", style="bold white"), "", "",
        format_duration(max_dur),
        format_cost(total_cost) if total_cost > 0 else "—",
        "", "",
    )

    return table


# ─── Display Functions ────────────────────────────────────────


def print_header() -> None:
    """Print the Forge ASCII banner."""
    banner = """
[bold bright_magenta]
  ███████╗ ██████╗ ██████╗  ██████╗ ███████╗
  ██╔════╝██╔═══██╗██╔══██╗██╔════╝ ██╔════╝
  █████╗  ██║   ██║██████╔╝██║  ███╗█████╗
  ██╔══╝  ██║   ██║██╔══██╗██║   ██║██╔══╝
  ██║     ╚██████╔╝██║  ██║╚██████╔╝███████╗
  ╚═╝      ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚══════╝
[/]
[dim]  AI Coding Agent Orchestrator v0.2.0[/]
[dim]  Claude Code • Gemini • Copilot[/]
"""
    console.print(banner)


def print_config_status(available: dict[str, bool], cfg: ForgeConfig | None = None) -> None:
    """Print agent availability status with model info."""
    table = Table(
        title="🔧 Agent Status",
        show_header=True,
        header_style="bold",
        border_style="bright_black",
    )
    table.add_column("Agent", style="bold", min_width=16)
    table.add_column("Type", style="dim", min_width=8)
    table.add_column("Model", style="dim", min_width=10)
    table.add_column("Status", justify="center", min_width=12)
    table.add_column("Budget", justify="right", style="yellow", min_width=8)

    for name, is_avail in available.items():
        color = _get_color(name)
        icon = "✅" if is_avail else "❌"

        agent_type = ""
        model = ""
        budget = ""
        if cfg and name in cfg.agents:
            ac = cfg.agents[name]
            agent_type = ac.agent_type or ""
            model = ac.model or ""
            budget = format_cost(ac.max_budget_usd) if ac.max_budget_usd else ""

        table.add_row(
            Text(name.upper(), style=f"bold {color}"),
            agent_type,
            model,
            f"{icon} {'Ready' if is_avail else 'Not found'}",
            budget,
        )

    n_available = sum(1 for v in available.values() if v)
    console.print(table)
    console.print(f"\n[dim]{n_available}/{len(available)} agents available[/]")
    console.print()


def print_result(result: AgentResult) -> None:
    """Print a single agent result."""
    panel = make_agent_panel(result)
    console.print(panel)


def print_results(results: list[AgentResult]) -> None:
    """Print all agent results with summary table."""
    console.print()
    summary = make_summary_table(results)
    console.print(summary)
    console.print()
    for result in results:
        print_result(result)
        console.print()


def print_best_result(result: AgentResult, label: str = "Best Result") -> None:
    """Print the best result highlighted."""
    color = _get_color(result.agent_name)
    console.print(
        Panel(
            result.output,
            title=f"🏆 {label} — {result.agent_name.upper()}",
            border_style=f"bold {color}",
            padding=(1, 2),
        )
    )


def print_orchestration_result(orch_result: OrchestrationResult) -> None:
    """Print detailed orchestration results showing the full agent interaction flow."""
    from forge.orchestrate import OrchestrateMode

    mode = orch_result.mode

    # Mode header
    mode_labels = {
        OrchestrateMode.SINGLE: "🎯 Single Agent",
        OrchestrateMode.PARALLEL: "⚡ Parallel Dispatch",
        OrchestrateMode.CHAIN: "🔗 Chain Mode",
        OrchestrateMode.REVIEW: "🔍 Review Mode",
        OrchestrateMode.CONSENSUS: "🤝 Consensus Mode",
        OrchestrateMode.SWARM: "🐝 Swarm Mode",
    }

    console.print(
        f"[bold bright_magenta]{mode_labels.get(mode, mode.value)}[/] "
        f"— {orch_result.round_count} round(s), "
        f"{len(set(orch_result.agents_used))} agent(s)\n"
    )

    # Show each round
    for round_ in orch_result.rounds:
        color = _get_color(round_.agent_name)
        icon = "✅" if round_.result.is_success else "❌"

        role_icons = {
            "producer": "✍️",
            "initiator": "🚀",
            "improver": "⬆️",
            "reviewer": "🔍",
            "refiner": "✨",
            "judge": "⚖️",
            "planner": "📋",
            "worker": "⚙️",
        }
        role_icon = role_icons.get(round_.role, "🤖")

        header = Text()
        header.append(f"Round {round_.round_number} ", style="dim")
        header.append(f"{role_icon} {round_.role.upper()} ", style="bold")
        header.append(f"— {round_.agent_name.upper()} ", style=f"bold {color}")
        header.append(f"{icon} ", style="bold")
        if round_.result.cost_usd:
            header.append(f" {format_cost(round_.result.cost_usd)}", style="yellow")
        header.append(f" {format_duration(round_.result.duration_ms)}", style="dim")

        # Truncated output
        output = round_.result.output[:1500] if round_.result.is_success else (round_.result.error or "No output")
        if len(round_.result.output) > 1500:
            output += f"\n\n[dim]... ({len(round_.result.output) - 1500} more chars)[/]"

        console.print(Panel(
            output,
            title=header,
            border_style=color if round_.result.is_success else "red",
            padding=(1, 2),
        ))

    # Final summary
    console.print()
    summary_table = Table(
        title="📊 Orchestration Summary",
        show_header=False,
        border_style="bright_black",
        padding=(0, 2),
    )
    summary_table.add_column("Key", style="bold", min_width=16)
    summary_table.add_column("Value", min_width=30)

    summary_table.add_row("Mode", mode.value.upper())
    summary_table.add_row("Rounds", str(orch_result.round_count))
    summary_table.add_row("Agents Used", ", ".join(set(orch_result.agents_used)))
    summary_table.add_row("Total Cost", format_cost(orch_result.total_cost_usd))
    summary_table.add_row("Total Duration", format_duration(orch_result.total_duration_ms))

    console.print(summary_table)

    # Final output panel
    if orch_result.final_output:
        console.print()
        console.print(Panel(
            orch_result.final_output[:4000],
            title="🏆 Final Output",
            border_style="bold bright_magenta",
            padding=(1, 2),
        ))
