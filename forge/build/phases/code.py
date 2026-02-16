"""Phase: CODE — Coder implements the full project from the plan.

Extracted from DuoBuildPipeline._code().
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge.build.phases.dispatch import dispatch_agentic

if TYPE_CHECKING:
    from forge.build.duo import DuoBuildPipeline, DuoRound


async def run_code(
    pipeline: DuoBuildPipeline, objective: str, plan: str,
) -> DuoRound:
    """Coder implements the full project from the plan."""
    from forge.build.duo import PHASE_CODE

    # Pass the FULL plan — it's the blueprint, don't summarize it
    if len(plan) > 8000:
        plan_text = plan[:7500] + "\n\n... (plan truncated for length)"
    else:
        plan_text = plan

    prompt = (
        f"You are a senior software engineer. Implement this project completely.\n\n"
        f"OBJECTIVE: {objective}\n\n"
        f"PROJECT PLAN:\n{plan_text}\n\n"
        f"Working directory: {pipeline.working_dir}\n\n"
        f"QUALITY STANDARDS:\n"
        f"- Create ALL files from the plan — missing files = failed build\n"
        f"- Write COMPLETE code — no TODOs, no placeholders, no 'implement later'\n"
        f"- Include proper type hints, docstrings, and error handling\n"
        f"- Add __init__.py files for all packages\n"
        f"- Create pyproject.toml (or package.json) with all dependencies\n"
        f"- Write at least one test file with real test cases\n"
        f"- Create a proper .gitignore\n"
        f"- The README.md should match what the plan specified\n\n"
        f"Write production-ready code that works out of the box after install."
    )
    return await dispatch_agentic(pipeline, PHASE_CODE, pipeline.coder, prompt)
