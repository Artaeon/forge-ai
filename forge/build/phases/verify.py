"""Phase: VERIFY — Run build + lint + tests and capture real errors.

Extracted from DuoBuildPipeline._verify().
"""

from __future__ import annotations

import logging
import subprocess
from typing import TYPE_CHECKING

from rich.console import Console

from forge.build.testing import detect_verification_suite
from forge.build.validate import validate_project

if TYPE_CHECKING:
    from forge.build.duo import DuoBuildPipeline, DuoRound

console = Console()
logger = logging.getLogger(__name__)


async def run_verify(pipeline: DuoBuildPipeline, objective: str) -> DuoRound:
    """Run build + lint + tests and capture real errors."""
    from forge.build.duo import DuoRound, PHASE_VERIFY

    pipeline._print_phase(PHASE_VERIFY, "system", "Running build, lint & tests...")

    suite = detect_verification_suite(pipeline.working_dir)
    errors: list[str] = []
    output_parts: list[str] = []

    if not suite.has_commands:
        output_parts.append("No verification commands detected for this project type.")
    else:
        categories = [
            ("🔨 BUILD", suite.build_commands),
            ("🔍 LINT", suite.lint_commands),
            ("🧪 TESTS", suite.test_commands),
        ]

        # Syntax check first
        if suite.syntax_check:
            try:
                result = subprocess.run(
                    suite.syntax_check, shell=True, capture_output=True,
                    text=True, cwd=pipeline.working_dir, timeout=30,
                )
                if result.returncode != 0:
                    combined = (result.stdout + "\n" + result.stderr).strip()
                    errors.append(f"SYNTAX CHECK:\n{combined}")
                    output_parts.append(f"❌ SYNTAX: {combined[:300]}")
                else:
                    output_parts.append("✅ SYNTAX: OK")
            except subprocess.TimeoutExpired:
                output_parts.append("⏰ SYNTAX: timeout after 30s")
                logger.warning("Syntax check timed out")
            except FileNotFoundError as e:
                output_parts.append(f"⚠ SYNTAX: command not found ({e})")
                logger.warning("Syntax check command not found: %s", e)
            except OSError as e:
                output_parts.append(f"⚠ SYNTAX: {e}")
                logger.warning("Syntax check OS error: %s", e)

        for category_name, commands in categories:
            if not commands:
                continue

            for cmd in commands:
                try:
                    result = subprocess.run(
                        cmd, shell=True, capture_output=True, text=True,
                        cwd=pipeline.working_dir, timeout=60,
                    )
                    stdout = result.stdout.strip()
                    stderr = result.stderr.strip()
                    combined = (stdout + "\n" + stderr).strip()

                    if result.returncode != 0:
                        errors.append(
                            f"{category_name}:\n$ {cmd}\n"
                            f"Exit code: {result.returncode}\n{combined}"
                        )
                        output_parts.append(
                            f"❌ {category_name}: {cmd}\n{combined[:500]}"
                        )
                    else:
                        output_parts.append(f"✅ {category_name}: {cmd}")
                        if combined:
                            output_parts.append(f"   {combined[:200]}")
                except subprocess.TimeoutExpired:
                    errors.append(f"{category_name}:\n$ {cmd}\nTIMEOUT after 60s")
                    output_parts.append(f"⏰ {category_name}: {cmd} → TIMEOUT")
                except FileNotFoundError as e:
                    errors.append(f"{category_name}:\n$ {cmd}\nCOMMAND NOT FOUND: {e}")
                    output_parts.append(f"❌ {category_name}: {cmd} → command not found")
                except OSError as e:
                    errors.append(f"{category_name}:\n$ {cmd}\nOS ERROR: {e}")
                    output_parts.append(f"❌ {category_name}: {cmd} → {e}")

    # Also run validation gate
    validation = validate_project(pipeline.working_dir)
    if not validation.passed:
        output_parts.append(f"\n{validation.to_prompt()}")
        for issue in validation.issues:
            if issue.severity.value == "CRITICAL":
                errors.append(
                    f"VALIDATION: {issue.message}"
                    + (f" ({issue.file})" if issue.file else "")
                )

    error_text = "\n\n".join(errors) if errors else ""
    success = len(errors) == 0

    if success:
        output_parts.insert(0, "🟢 All checks passed")
    else:
        output_parts.insert(0, f"🔴 {len(errors)} check(s) failed")

    return DuoRound(
        round_number=len(pipeline.rounds) + 1,
        phase=PHASE_VERIFY,
        agent_name="system",
        prompt="verify build, lint & tests",
        output="\n".join(output_parts),
        success=success,
        errors=error_text,
    )
