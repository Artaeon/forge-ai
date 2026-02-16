"""E2E smoke test for the duo pipeline.

Runs the full pipeline flow with mocked agent dispatching to verify
integration of all phases: SCAFFOLD → PLAN → CODE → VERIFY → REVIEW.

Usage:
    python -m pytest tests/smoke_duo.py -v
"""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from forge.agents.base import AgentResult, AgentStatus
from forge.build.duo import DuoBuildPipeline, DuoResult, DuoRound


def _make_result(output: str, success: bool = True) -> AgentResult:
    return AgentResult(
        agent_name="mock",
        output=output,
        status=AgentStatus.SUCCESS if success else AgentStatus.ERROR,
        duration_ms=500,
        cost_usd=0.001,
    )


def _make_round(phase: str, output: str, success: bool = True) -> DuoRound:
    return DuoRound(
        round_number=1,
        phase=phase,
        agent_name="mock",
        prompt="test",
        output=output,
        success=success,
        duration_ms=500,
        cost_usd=0.001,
    )


def _create_project_files(wd: Path) -> None:
    """Create a realistic project layout in wd."""
    (wd / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n'
    )
    (wd / "README.md").write_text(
        "# Demo\n\nA demo project.\n\n## Installation\n\n"
        "```pip install -e .```\n\n## Usage\n\n```python\nimport demo\n```\n"
        + "\n" * 15
    )
    (wd / ".gitignore").write_text("__pycache__/\n*.pyc\n")
    src = wd / "src"
    src.mkdir(exist_ok=True)
    (src / "__init__.py").write_text('"""Demo."""\n')
    (src / "main.py").write_text(
        '"""Main."""\n\ndef greet(name: str) -> str:\n'
        '    """Greet."""\n    return f"Hello, {name}!"\n'
    )
    tests = wd / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "__init__.py").touch()
    (tests / "test_main.py").write_text(
        'from src.main import greet\n\n'
        'def test_greet():\n    assert greet("X") == "Hello, X!"\n'
    )


class TestSmokeE2E:
    """E2E smoke test patching at the dispatch level."""

    def test_full_pipeline_flow(self, tmp_path):
        """Pipeline runs PLAN → CODE → VERIFY → REVIEW end-to-end."""
        engine = MagicMock()
        engine.adapters = {}
        wd = str(tmp_path)

        pipe = DuoBuildPipeline(
            engine=engine,
            working_dir=wd,
            planner_agent="p",
            coder_agent="c",
            max_rounds=1,
        )

        dispatch_calls = []

        async def mock_dispatch(phase, agent, prompt):
            dispatch_calls.append(phase)
            return _make_round(phase, f"Plan for {phase}")

        async def mock_dispatch_agentic(phase, agent, prompt):
            dispatch_calls.append(phase)
            # Actually create files during CODE
            if phase == "CODE":
                _create_project_files(tmp_path)
            return _make_round(phase, f"Code for {phase}")

        pipe._dispatch = mock_dispatch
        pipe._dispatch_agentic = mock_dispatch_agentic

        result = asyncio.get_event_loop().run_until_complete(
            pipe.run("Build a greeting library")
        )

        assert isinstance(result, DuoResult)
        assert len(result.rounds) >= 3  # PLAN + CODE + VERIFY
        assert "PLAN" in dispatch_calls
        assert "CODE" in dispatch_calls
        assert (tmp_path / "pyproject.toml").exists()
        assert (tmp_path / "src" / "main.py").exists()

    def test_pipeline_survives_verify_failure(self, tmp_path):
        """Pipeline completes even when no files are created."""
        engine = MagicMock()
        engine.adapters = {}

        pipe = DuoBuildPipeline(
            engine=engine, working_dir=str(tmp_path),
            planner_agent="p", coder_agent="c", max_rounds=1,
        )

        async def mock_dispatch(phase, agent, prompt):
            if "APPROVED" in prompt or "approve" in prompt.lower():
                return _make_round(phase, "APPROVED")
            return _make_round(phase, f"Output for {phase}")

        async def mock_dispatch_agentic(phase, agent, prompt):
            return _make_round(phase, "Built stuff (no files)")

        pipe._dispatch = mock_dispatch
        pipe._dispatch_agentic = mock_dispatch_agentic

        result = asyncio.get_event_loop().run_until_complete(
            pipe.run("Build something")
        )
        assert isinstance(result, DuoResult)

    def test_pipeline_rounds_tracked(self, tmp_path):
        """Each phase adds a round to the result."""
        engine = MagicMock()
        engine.adapters = {}

        pipe = DuoBuildPipeline(
            engine=engine, working_dir=str(tmp_path),
            planner_agent="p", coder_agent="c", max_rounds=1,
        )

        async def mock_dispatch(phase, agent, prompt):
            return _make_round(phase, "APPROVED" if "REVIEW" in phase else "ok")

        async def mock_dispatch_agentic(phase, agent, prompt):
            return _make_round(phase, "done")

        pipe._dispatch = mock_dispatch
        pipe._dispatch_agentic = mock_dispatch_agentic

        result = asyncio.get_event_loop().run_until_complete(
            pipe.run("Test")
        )

        phases = [r.phase for r in result.rounds]
        assert "PLAN" in phases
        assert "CODE" in phases
        assert "VERIFY" in phases

    def test_quality_score_after_code(self, tmp_path):
        """Quality scoring works on generated project files."""
        from forge.build.scoring import score_project

        _create_project_files(tmp_path)
        score = score_project(str(tmp_path))
        assert score.total > 30
        assert score.grade in ("A", "B", "C", "D")
        assert len(score.details) > 0
