"""Phase: PLAN — Planner creates the project architecture and README.

Extracted from DuoBuildPipeline._plan().
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge.build.phases.dispatch import dispatch

if TYPE_CHECKING:
    from forge.build.duo import DuoBuildPipeline, DuoRound


async def run_plan(pipeline: DuoBuildPipeline, objective: str) -> DuoRound:
    """Planner creates the project architecture and README."""
    from forge.build.duo import PHASE_PLAN

    # Show existing scaffold files if any
    existing = pipeline._list_project_files()
    scaffold_note = ""
    if existing:
        scaffold_note = (
            f"\n\nNOTE: The project already has a scaffold with these files: "
            f"{', '.join(existing[:10])}\n"
            f"Build on this foundation. Don't recreate files that already exist — extend them."
        )

    prompt = (
        f"You are a senior software architect designing a production-ready project.\n\n"
        f"OBJECTIVE: {objective}\n\n"
        f"Create a detailed project plan with these sections:\n\n"
        f"## 1. README.md Content\n"
        f"Write the FULL README.md including:\n"
        f"- Project name and one-line description\n"
        f"- Features list (bullet points)\n"
        f"- Installation instructions (exact commands)\n"
        f"- Usage examples with code blocks\n"
        f"- Configuration options (if any)\n\n"
        f"## 2. File Structure\n"
        f"List EVERY file to create with:\n"
        f"- Full relative path\n"
        f"- One-line purpose description\n"
        f"- Key classes/functions it should contain\n\n"
        f"## 3. Tech Stack\n"
        f"- Language and version requirements\n"
        f"- Dependencies with version constraints (e.g. click>=8.0)\n"
        f"- Dev dependencies (pytest, ruff, etc.)\n\n"
        f"## 4. Architecture\n"
        f"- Data flow between modules\n"
        f"- Key design patterns (e.g. factory, strategy, plugin)\n"
        f"- Error handling strategy\n"
        f"- Testing strategy (what to test, how)\n\n"
        f"Be precise with file paths and function signatures. "
        f"Another AI agent will implement this — ambiguity causes poor code."
        f"{scaffold_note}"
    )
    return await dispatch(pipeline, PHASE_PLAN, pipeline.planner, prompt)
