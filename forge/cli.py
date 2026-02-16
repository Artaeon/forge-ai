"""Forge CLI — main entry point."""

from __future__ import annotations

import asyncio
import os
import sys

import click
from rich.console import Console
from rich.table import Table
from rich.text import Text

from forge import __version__
from forge.config import load_config, detect_available_agents
from forge.engine import ForgeEngine
from forge.agents.base import AgentResult, TaskContext
from forge.aggregator import ResultAggregator
from forge.orchestrate import OrchestrateMode, Orchestrator
from forge.tui.panels import (
    print_header,
    print_config_status,
    print_results,
    print_best_result,
    print_result,
    print_orchestration_result,
    console as tui_console,
)

console = Console()

# ─── CLI Group ────────────────────────────────────────────────


@click.group(invoke_without_command=True)
@click.option("--version", is_flag=True, help="Show version")
@click.pass_context
def main(ctx: click.Context, version: bool) -> None:
    """⚡ Forge — AI Coding Agent Orchestrator

    Unifies Claude Code, Gemini, and GitHub Copilot into a single
    console for autonomous builds.

    \b
    Orchestration Modes:
      single    — One agent, one shot (default)
      parallel  — All agents, same prompt, pick best
      chain     — Sequential: output feeds into next agent
      review    — One produces, another reviews & improves
      consensus — All produce, then cross-critique to synthesize
      swarm     — Break into subtasks, assign to best agents
    """
    if version:
        click.echo(f"forge v{__version__}")
        return

    if ctx.invoked_subcommand is None:
        print_header()
        click.echo(ctx.get_help())


# ─── CONFIG ───────────────────────────────────────────────────


@main.command()
@click.option("--config", "-c", "config_path", help="Path to forge.yaml")
def config(config_path: str | None) -> None:
    """Show current configuration and agent status."""
    print_header()

    cfg = load_config(config_path)
    engine = ForgeEngine(cfg)
    available = engine.get_available_agents()

    print_config_status(available, cfg)

    # Workspace info
    ws = cfg.workspace
    console.print(f"[dim]📂 Projects root: {ws.projects_root or 'current directory'}[/]")
    console.print(
        f"[dim]⚙️  Timeout: {cfg.global_.timeout}s | "
        f"Max parallel: {cfg.global_.max_parallel}[/]"
    )


# ─── RUN ──────────────────────────────────────────────────────


@main.command()
@click.argument("prompt")
@click.option("--agent", "-a", "agent_names", multiple=True,
              help="Agent(s) to use (e.g. -a claude-sonnet -a claude-opus)")
@click.option("--all", "-A", "use_all", is_flag=True, help="Use ALL available agents")
@click.option("--mode", "-m", "mode",
              type=click.Choice(["single", "parallel", "chain", "review", "consensus", "swarm"]),
              default="single", help="Orchestration mode")
@click.option("--config", "-c", "config_path", help="Path to forge.yaml")
@click.option("--dir", "-d", "working_dir", help="Working directory", default=".")
@click.option("--budget", "-b", type=float, help="Max budget in USD per agent")
@click.option("--timeout", "-t", type=int, help="Timeout in seconds")
@click.option("--system-prompt", "-s", help="Custom system prompt")
def run(
    prompt: str,
    agent_names: tuple[str, ...],
    use_all: bool,
    mode: str,
    config_path: str | None,
    working_dir: str,
    budget: float | None,
    timeout: int | None,
    system_prompt: str | None,
) -> None:
    """Run a prompt through AI agents with orchestration.

    \b
    Examples:
      forge run "Write a fibonacci function"
      forge run -a claude-sonnet "Fix this bug"
      forge run -a claude-opus -a claude-haiku --mode chain "Design a REST API"
      forge run --all --mode consensus "Write a sorting algorithm"
      forge run --all --mode review "Refactor this module"
      forge run --all --mode swarm "Build a full CRUD app"
    """
    print_header()

    cfg = load_config(config_path)
    engine = ForgeEngine(cfg)
    wd = os.path.abspath(working_dir)

    # Determine which agents to use
    available = engine.get_available_agents()
    available_names = [n for n, a in available.items() if a]

    if not available_names:
        console.print("[bold red]❌ No agents available![/]")
        console.print("[dim]Install at least one: claude, gemini, or gh copilot[/]")
        sys.exit(1)

    if use_all:
        agents = available_names
    elif agent_names:
        agents = list(agent_names)
        # Validate
        for a in agents:
            if a not in available:
                console.print(f"[red]Agent '{a}' not found. Available: {list(available.keys())}[/]")
                sys.exit(1)
    else:
        # Default: first available
        agents = [available_names[0]]

    # Resolve mode
    orchestrate_mode = OrchestrateMode(mode)

    # Auto-upgrade mode if needed
    if len(agents) == 1 and orchestrate_mode in (
        OrchestrateMode.CHAIN, OrchestrateMode.REVIEW,
        OrchestrateMode.CONSENSUS, OrchestrateMode.SWARM,
    ):
        console.print(
            f"[yellow]⚠ Mode '{mode}' needs multiple agents but only '{agents[0]}' selected. "
            f"Falling back to single mode.[/]\n"
        )
        orchestrate_mode = OrchestrateMode.SINGLE

    # Display info
    console.print(f"[dim]📂 Working dir: {wd}[/]")
    console.print(f"[dim]💬 Prompt: {prompt[:100]}{'...' if len(prompt) > 100 else ''}[/]")
    console.print(f"[dim]🎯 Mode: {orchestrate_mode.value}[/]")
    console.print(f"[dim]🤖 Agents: {', '.join(agents)}[/]\n")

    # Run orchestration
    orchestrator = Orchestrator(engine)

    def on_progress(agent: str, status: str, detail: str) -> None:
        icon = {"running": "🔄", "queued": "⏳", "done": "✅", "failed": "❌"}.get(status, "❓")
        console.print(f"  {icon} [bold]{agent}[/] — {detail}")

    result = asyncio.run(
        orchestrator.run(
            mode=orchestrate_mode,
            prompt=prompt,
            working_dir=wd,
            agents=agents,
            timeout=timeout or cfg.global_.timeout,
            max_budget_usd=budget,
            on_progress=on_progress,
        )
    )

    # Display results
    console.print()
    print_orchestration_result(result)


# ─── BUILD ────────────────────────────────────────────────────


@main.command()
@click.argument("objective")
@click.option("--agent", "-a", "agent_name", default=None,
              help="Primary agent for build (default: claude-sonnet)")
@click.option("--config", "-c", "config_path", help="Path to forge.yaml")
@click.option("--dir", "-d", "working_dir", help="Working directory", default=None)
@click.option("--new", "-n", "project_name", help="Create new project in projects_root")
@click.option("--max-iter", "-i", type=int, default=10, help="Max build iterations")
@click.option("--test-cmd", "-t", "test_commands", multiple=True,
              help="Test commands to verify build")
@click.option("--auto-commit", is_flag=True, help="Auto-commit successful iterations")
def build(
    objective: str,
    agent_name: str | None,
    config_path: str | None,
    working_dir: str | None,
    project_name: str | None,
    max_iter: int,
    test_commands: tuple[str, ...],
    auto_commit: bool,
) -> None:
    """Autonomous build mode — iterative code generation with verification.

    \b
    Examples:
      forge build "Create a Flask REST API with /health and tests"
      forge build "Add auth to the Express app" --test-cmd "npm test"
      forge build --new my-api "Build a FastAPI project"
      forge build -a claude-opus "Refactor to TypeScript" --auto-commit
    """
    print_header()

    from forge.build.pipeline import BuildPipeline

    cfg = load_config(config_path)
    engine = ForgeEngine(cfg)

    # Resolve working directory
    if project_name:
        root = os.path.expanduser(cfg.workspace.projects_root or "~/Projects")
        wd = os.path.join(root, project_name)
        os.makedirs(wd, exist_ok=True)
        if cfg.workspace.create_git and not os.path.exists(os.path.join(wd, ".git")):
            import subprocess
            subprocess.run(["git", "init"], cwd=wd, capture_output=True)
            console.print(f"[green]📁 Created project: {wd}[/]")
    else:
        wd = os.path.abspath(working_dir or ".")

    # Resolve agent
    available = engine.get_available_agents()
    if agent_name is None:
        # Default to first available Claude variant, then any
        for default in ["claude-sonnet", "claude-opus", "claude-haiku"]:
            if default in available and available[default]:
                agent_name = default
                break
        if agent_name is None:
            agent_name = next((n for n, a in available.items() if a), None)
        if agent_name is None:
            console.print("[bold red]❌ No agents available![/]")
            sys.exit(1)

    # Use config test commands if none specified
    cmds = list(test_commands) if test_commands else cfg.build.test_commands

    pipeline = BuildPipeline(
        engine=engine,
        working_dir=wd,
        primary_agent=agent_name,
        max_iterations=max_iter,
        test_commands=cmds,
        auto_commit=auto_commit,
    )

    console.print(f"[dim]📂 Working dir: {wd}[/]")
    console.print(f"[dim]🎯 Objective: {objective}[/]")
    console.print(f"[dim]🤖 Agent: {agent_name}[/]")
    console.print(f"[dim]🔄 Max iterations: {max_iter}[/]")
    if cmds:
        console.print(f"[dim]🧪 Tests: {', '.join(cmds)}[/]")
    console.print()

    steps = asyncio.run(pipeline.run(objective))

    # Summary
    total_cost = sum(
        r.cost_usd or 0
        for step in steps
        for r in step.agent_results
    )
    console.print(f"\n[dim]📊 Total iterations: {len(steps)}[/]")
    if total_cost > 0:
        console.print(f"[dim]💰 Total cost: ${total_cost:.4f}[/]")
    if steps and steps[-1].build_success:
        console.print(f"[bold green]Build completed successfully.[/]")
    else:
        console.print(f"[bold yellow]Build did not fully complete.[/]")


# ─── AGENTS ───────────────────────────────────────────────────


@main.command()
@click.option("--config", "-c", "config_path", help="Path to forge.yaml")
def agents(config_path: str | None) -> None:
    """List all configured agents and their capabilities."""
    print_header()

    cfg = load_config(config_path)
    engine = ForgeEngine(cfg)
    available = engine.get_available_agents()

    print_config_status(available, cfg)

    # Orchestration modes help
    console.print()
    modes_table = Table(
        title="🔀 Orchestration Modes",
        show_header=True,
        header_style="bold",
        border_style="bright_black",
    )
    modes_table.add_column("Mode", style="bold bright_magenta", min_width=12)
    modes_table.add_column("Description", min_width=50)
    modes_table.add_column("Min Agents", justify="center", min_width=10)

    modes_table.add_row("single", "One agent, one shot", "1")
    modes_table.add_row("parallel", "All agents produce, pick best result", "2+")
    modes_table.add_row("chain", "Sequential: Agent A → B → C, each improves", "2+")
    modes_table.add_row("review", "Produce → Critique → Refine (3 rounds)", "2+")
    modes_table.add_row("consensus", "All produce, then judge synthesizes best", "2+")
    modes_table.add_row("swarm", "Break into subtasks, assign to best agents", "2+")

    console.print(modes_table)

    console.print("\n[dim]Usage: forge run --mode chain -a claude-sonnet -a claude-opus \"your prompt\"[/]")


# ─── INIT ─────────────────────────────────────────────────────


@main.command(name="init")
@click.argument("template")
@click.option("--dir", "-d", "target_dir", default=".", help="Target directory")
@click.option("--list", "-l", "list_only", is_flag=True, help="List available templates")
def init_project(template: str, target_dir: str, list_only: bool) -> None:
    """Initialize a project from a built-in template.

    \b
    Available templates:
      flask-api    Flask REST API with config, routes, and tests
      fastapi      FastAPI application with async routes and tests
      cli-tool     Python CLI application with Click
      nextjs       Next.js application (manual setup required)

    \b
    Examples:
      forge init flask-api
      forge init fastapi --dir ./my-app
    """
    from forge.build.templates import list_templates, scaffold_template

    if list_only:
        for name, desc in list_templates():
            console.print(f"  [bold]{name:16}[/] {desc}")
        return

    print_header()

    target = os.path.abspath(target_dir)
    os.makedirs(target, exist_ok=True)

    try:
        created = scaffold_template(template, target)
    except ValueError as e:
        console.print(f"[red]{e}[/]")
        sys.exit(1)

    console.print(f"[green]Initialized '{template}' template in {target}[/]")
    for f in created:
        console.print(f"[dim]  + {f}[/]")

    # Auto-init git
    if not os.path.exists(os.path.join(target, ".git")):
        import subprocess
        subprocess.run(["git", "init"], cwd=target, capture_output=True)
        console.print(f"[dim]  Initialized git repository[/]")

    console.print(
        f"\n[dim]Next: cd {target_dir} && forge build \"your objective\"[/]"
    )


# ─── DUO ──────────────────────────────────────────────────────


@main.command()
@click.argument("objective")
@click.option("--planner", "-p", "planner_agent", default="gemini",
              help="Agent for planning and reviewing (default: gemini)")
@click.option("--coder", "-c", "coder_agent", default="claude-sonnet",
              help="Agent for coding and fixing (default: claude-sonnet)")
@click.option("--rounds", "-r", "max_rounds", default=5, type=int,
              help="Max review/fix rounds (default: 5)")
@click.option("--dir", "-d", "work_dir", default=".", help="Working directory")
@click.option("--new", "new_project", default=None, help="Create new project directory")
@click.option("--no-commit", is_flag=True, help="Skip auto-commit")
@click.option("--interactive", "-i", is_flag=True, help="Pause for review after plan and review phases")
@click.option("--timeout", "-t", default=300, type=int, help="Timeout per agent call (seconds)")
def duo(
    objective: str,
    planner_agent: str,
    coder_agent: str,
    max_rounds: int,
    work_dir: str,
    new_project: str | None,
    no_commit: bool,
    interactive: bool,
    timeout: int,
) -> None:
    """Collaborative build — two agents iterate toward v1.

    One agent PLANS and REVIEWS, the other CODES and FIXES.
    They iterate until the reviewer approves or max rounds are reached.

    \b
    Flow:
      1. Planner creates README + architecture
      2. Coder implements all files
      3. Planner reviews the code
      4. Coder fixes issues
      5. Repeat 3-4 until approved

    \b
    Examples:
      forge duo "Build a REST API with user auth"
      forge duo "Create a CLI calculator" --planner gemini --coder claude-sonnet
      forge duo "Todo app with tests" --new my-todo --rounds 3
    """
    print_header()

    from forge.build.duo import DuoBuildPipeline

    cfg = load_config()
    engine = ForgeEngine(cfg)

    # Resolve working directory
    if new_project:
        projects_root = cfg.workspace.projects_root or "."
        wd = os.path.join(os.path.expanduser(projects_root), new_project)
        os.makedirs(wd, exist_ok=True)
        console.print(f"[dim]📂 Created project: {wd}[/]\n")
    else:
        wd = os.path.abspath(work_dir)

    # Validate agents
    available = engine.get_available_agents()
    for agent_name, label in [(planner_agent, "planner"), (coder_agent, "coder")]:
        if agent_name not in available:
            console.print(f"[red]Agent '{agent_name}' not configured[/]")
            sys.exit(1)
        if not available[agent_name]:
            console.print(f"[yellow]Warning: {label} '{agent_name}' is not available[/]")

    console.print(
        f"[bold]🏗️  Collaborative Build[/]\n"
        f"[dim]  Planner/Reviewer: [bold bright_cyan]{planner_agent}[/][/]\n"
        f"[dim]  Coder:            [bold bright_magenta]{coder_agent}[/][/]\n"
        f"[dim]  Max rounds:       {max_rounds}[/]\n"
        f"[dim]  Working dir:      {wd}[/]\n"
    )

    pipeline = DuoBuildPipeline(
        engine=engine,
        working_dir=wd,
        planner_agent=planner_agent,
        coder_agent=coder_agent,
        max_rounds=max_rounds,
        auto_commit=not no_commit,
        timeout=timeout,
    )
    pipeline.interactive = interactive

    result = asyncio.run(pipeline.run(objective))

    if result.approved:
        console.print("\n[bold green]Build complete. Project ready.[/]")
    else:
        console.print("\n[bold yellow]Build finished. Review manually.[/]")


# ─── Async Helpers ────────────────────────────────────────────


async def _dispatch_single(engine: ForgeEngine, agent: str, ctx: TaskContext) -> AgentResult:
    return await engine.dispatch_single(agent, ctx)


async def _dispatch_all(engine: ForgeEngine, ctx: TaskContext) -> list[AgentResult]:
    return await engine.dispatch_all(ctx)


if __name__ == "__main__":
    main()
