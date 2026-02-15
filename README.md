# Forge

**Multi-agent AI coding orchestrator.** Forge unifies Claude Code, Gemini CLI, and GitHub Copilot into a single command-line interface with inter-agent communication, autonomous builds, and structured result aggregation.

---

## Overview

Forge dispatches coding tasks to multiple AI agents, compares their outputs, and selects the best result. It supports advanced orchestration patterns where agents collaborate: one drafts, another reviews, a third refines. For autonomous builds, Forge iteratively generates code, installs dependencies, runs tests, and feeds errors back to agents until the project compiles and passes.

### Key Capabilities

- **Multi-model dispatch** -- Run the same prompt across Claude Sonnet, Opus, Haiku, Gemini, and Copilot simultaneously
- **Inter-agent communication** -- Chain, review, consensus, and swarm modes where agents build on each other's output
- **Autonomous build pipeline** -- Agentic code generation with iterative verification and error correction
- **Structured output** -- Cost tracking, token counts, duration, and model identification per agent
- **Project scaffolding** -- Create new projects with `forge build --new <name>` with automatic git initialization

## Installation

Requires Python 3.11 or later.

```bash
git clone https://github.com/Artaeon/forge.git
cd forge
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Prerequisites

At least one of the following AI CLIs must be installed:

| Agent | Install | Documentation |
|-------|---------|---------------|
| Claude Code | `npm install -g @anthropic-ai/claude-code` | [claude.ai/code](https://claude.ai/code) |
| Gemini CLI | `npm install -g @google/gemini-cli` | [github.com/google/gemini-cli](https://github.com/google/gemini-cli) |
| GitHub Copilot | `gh extension install github/gh-copilot` | [docs.github.com/copilot](https://docs.github.com/en/copilot) |

Verify available agents:

```bash
forge config
```

## Usage

### Single Agent

```bash
forge run "Write a Python fibonacci function"
forge run -a claude-opus "Design a database schema for a blog"
forge run -a claude-haiku "Explain what asyncio does in three sentences"
```

### Orchestration Modes

Forge supports six orchestration patterns that control how agents interact:

| Mode | Description | Agents Required |
|------|-------------|-----------------|
| `single` | One agent, one shot | 1+ |
| `parallel` | All agents answer, best result auto-selected | 2+ |
| `chain` | Sequential pipeline where each agent improves the previous output | 2+ |
| `review` | Produce, critique, refine across three rounds | 2+ |
| `consensus` | All agents produce independently, then a judge synthesizes | 2+ |
| `swarm` | Planner breaks the task into subtasks assigned to best-fit agents | 2+ |

```bash
# Chain: fast agent drafts, strong agent polishes
forge run --mode chain -a claude-haiku -a claude-sonnet "Implement a linked list"

# Review: produce, critique, refine
forge run --mode review -a claude-sonnet -a claude-opus "Write a secure auth module"

# Consensus: all agents produce, judge picks best parts
forge run --all --mode consensus "Write a caching layer"

# Swarm: planner assigns subtasks to best agents
forge run --all --mode swarm "Build a full CRUD application"
```

### Autonomous Build

The build pipeline operates in agentic mode, where the AI agent creates and modifies files directly on disk. It iterates until verification commands pass.

```bash
# Create a new project
forge build --new my-api "Create a FastAPI REST API with authentication"

# Build in the current directory
forge build "Add unit tests for all modules" --test-cmd "python -m pytest"

# With auto-commit on each successful iteration
forge build "Refactor to TypeScript" --auto-commit
```

Build pipeline behavior:

1. Dispatches the objective to the agent in agentic mode (file write access)
2. Detects created and modified files
3. Auto-installs dependencies (`requirements.txt`, `package.json`)
4. Runs verification commands
5. Feeds errors back to the agent for correction
6. Repeats until all checks pass or the iteration limit is reached

### Agent Status

```bash
forge config     # Show detected agents and configuration
forge agents     # List agent capabilities and orchestration modes
```

## Configuration

Forge reads configuration from `forge.yaml`, searching upward from the working directory. Fallback: `~/.config/forge/forge.yaml`.

```yaml
global:
  timeout: 120
  max_parallel: 5
  auto_commit: false
  max_build_iterations: 10

agents:
  claude-sonnet:
    enabled: true
    agent_type: claude
    command: claude
    model: sonnet
    max_budget_usd: 1.0

  claude-opus:
    enabled: true
    agent_type: claude
    command: claude
    model: opus
    max_budget_usd: 5.0

  claude-haiku:
    enabled: true
    agent_type: claude
    command: claude
    model: haiku
    max_budget_usd: 0.25

  gemini:
    enabled: true
    agent_type: gemini
    command: gemini

  copilot:
    enabled: true
    agent_type: copilot
    command: gh

workspace:
  default_dir: "."
  create_git: true
  projects_root: "~/Projects"

build:
  test_commands:
    - "python -m pytest"
  lint_commands:
    - "python -m ruff check ."
```

### Agent Configuration Fields

| Field | Type | Description |
|-------|------|-------------|
| `enabled` | bool | Whether the agent is active |
| `agent_type` | string | Backend type: `claude`, `gemini`, or `copilot` |
| `command` | string | CLI binary name |
| `model` | string | Model variant (e.g., `sonnet`, `opus`, `haiku`) |
| `max_budget_usd` | float | Per-request cost cap |
| `skip_permissions` | bool | Skip file write permission prompts |
| `extra_args` | list | Additional CLI arguments |

## Architecture

```
forge/
  cli.py              CLI entry point (Click)
  config.py            Configuration loading and validation (Pydantic)
  engine.py            Agent lifecycle and parallel dispatch (asyncio)
  aggregator.py        Result scoring and comparison
  orchestrate.py       Inter-agent communication patterns
  agents/
    base.py            Agent protocol and data models
    claude.py          Claude Code adapter (print + agentic modes)
    gemini.py          Gemini CLI adapter
    copilot.py         GitHub Copilot adapter
  build/
    pipeline.py        Autonomous build loop with verification
    workspace.py       File tree and git state management
  tui/
    panels.py          Terminal UI components (Rich)
```

The engine initializes agent adapters from configuration, supporting multiple model variants per backend. The orchestrator implements communication patterns on top of the engine's dispatch primitives. The build pipeline uses the Claude adapter's agentic mode for file system access.

## Cost Model

Forge tracks per-agent costs reported by the underlying CLIs. Claude Code costs are deducted from your Anthropic subscription (Pro/Max plan), not billed separately via API. The `max_budget_usd` setting acts as a safety cap per request.

## License

MIT

## Author

Artaeon (raphael.lugmayr@stoicera.com)
