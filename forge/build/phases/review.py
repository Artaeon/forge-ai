"""Phase: REVIEW + FIX — Reviewer examines code, coder fixes issues.

Extracted from DuoBuildPipeline._review() and _fix().
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge.build.compact import gather_compact, build_history_summary
from forge.build.phases.dispatch import dispatch, dispatch_agentic

if TYPE_CHECKING:
    from forge.build.duo import DuoBuildPipeline, DuoRound


async def run_review(
    pipeline: DuoBuildPipeline,
    objective: str,
    iteration: int,
    verify_errors: str = "",
    validation_text: str = "",
) -> DuoRound:
    """Reviewer examines the code and produces feedback."""
    from forge.build.duo import PHASE_REVIEW

    ctx = gather_compact(pipeline.working_dir)

    # Build compact history of previous rounds
    history = build_history_summary(
        [{"agent_name": r.agent_name, "phase": r.phase, "output": r.output}
         for r in pipeline.rounds],
        max_total=800,
    )

    # Read key files for the reviewer to actually inspect
    file_samples = pipeline._read_key_files_for_review()

    # Git diff for rounds 2+
    diff_text = ""
    if iteration > 1:
        diff_text = pipeline._get_round_diff()

    prompt = (
        f"You are a senior code reviewer performing a thorough quality audit.\n\n"
        f"OBJECTIVE: {objective}\n"
        f"Review round: {iteration}/{pipeline.max_rounds}\n\n"
        f"PROJECT FILES: {ctx.to_prompt()}\n\n"
    )

    if file_samples:
        prompt += f"KEY FILE CONTENTS:\n{file_samples}\n\n"

    # Show verification errors (real stack traces!)
    if verify_errors:
        prompt += (
            f"🔴 BUILD/TEST ERRORS (these are REAL errors from running the code):\n"
            f"{verify_errors[:2000]}\n\n"
        )

    if validation_text:
        prompt += f"{validation_text}\n\n"

    if diff_text and iteration > 1:
        prompt += f"CHANGES SINCE LAST ROUND:\n{diff_text}\n\n"

    if history:
        prompt += f"PREVIOUS ROUNDS:\n{history}\n\n"

    prompt += (
        f"REVIEW CRITERIA (check each):\n"
        f"1. COMPLETENESS — Does the code fully implement the objective?\n"
        f"2. CORRECTNESS — Are there bugs, logic errors, or crashes?\n"
        f"3. STRUCTURE — Is the code well-organized with proper separation?\n"
        f"4. QUALITY — Type hints, docstrings, error handling present?\n"
        f"5. TESTS — Do test files exist with meaningful test cases?\n"
        f"6. PACKAGING — Is there pyproject.toml/package.json with deps?\n"
        f"7. DOCS — Does README have install + usage instructions?\n\n"
        f"RESPONSE FORMAT:\n"
        f"If the project is COMPLETE and PRODUCTION-READY, respond:\n"
        f"APPROVED\n"
        f"[brief summary of what's good]\n\n"
        f"If NOT ready, respond with:\n"
        f"ISSUES:\n"
        f"- [CRITICAL] file.py: description of critical bug\n"
        f"- [MISSING] description of missing feature\n"
        f"- [QUALITY] file.py: quality improvement needed\n\n"
        f"List max 7 issues, prioritized by severity. Be specific with file names."
    )
    return await dispatch(pipeline, PHASE_REVIEW, pipeline.planner, prompt)


async def run_fix(
    pipeline: DuoBuildPipeline,
    objective: str,
    review_feedback: str,
    iteration: int,
    verify_errors: str = "",
) -> DuoRound:
    """Coder fixes issues identified in the review."""
    from forge.build.duo import PHASE_FIX

    ctx = gather_compact(pipeline.working_dir)

    # Pass FULL review feedback
    if len(review_feedback) > 3000:
        feedback_text = review_feedback[:2500] + "\n\n... (truncated)"
    else:
        feedback_text = review_feedback

    prompt = (
        f"You are a senior software engineer fixing issues from a code review.\n\n"
        f"OBJECTIVE: {objective}\n\n"
        f"REVIEW FEEDBACK — fix ALL of these:\n{feedback_text}\n\n"
    )

    # Include real errors from verification (stack traces!)
    if verify_errors:
        prompt += (
            f"🔴 ACTUAL BUILD/TEST ERRORS (fix these first!):\n"
            f"{verify_errors[:2000]}\n\n"
        )

    prompt += (
        f"CURRENT PROJECT: {ctx.to_prompt()}\n"
        f"Working directory: {pipeline.working_dir}\n\n"
        f"INSTRUCTIONS:\n"
        f"- Fix every issue listed in the review\n"
        f"- Fix ALL build/test errors shown above\n"
        f"- Create any missing files mentioned\n"
        f"- Do NOT rewrite files that are already working correctly\n"
        f"- Only modify files that have issues\n"
        f"- After fixing, verify the project still runs/imports correctly\n\n"
        f"Fix iteration: {iteration}/{pipeline.max_rounds}"
    )
    return await dispatch_agentic(pipeline, PHASE_FIX, pipeline.coder, prompt)
