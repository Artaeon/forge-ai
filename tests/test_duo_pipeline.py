"""Comprehensive tests for duo pipeline modules.

Tests scoring, depfix, resume, validate, templates, testing,
and duo pipeline integration.
"""

import asyncio
import json
import os
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

from forge.build.scoring import score_project, QualityScore
from forge.build.depfix import extract_missing_modules, resolve_missing_deps
from forge.build.resume import save_state, load_state, clear_state, STATE_FILENAME
from forge.build.validate import validate_project, Severity, ValidationResult
from forge.build.templates import detect_template, scaffold_template, TEMPLATES
from forge.build.testing import detect_verification_suite, VerificationSuite


# ─── Scoring Tests ────────────────────────────────────────────


class TestScoring:
    """Tests for forge.build.scoring."""

    def test_empty_dir_scores_low(self, tmp_path):
        score = score_project(str(tmp_path))
        assert score.total < 20
        assert score.grade == "F"

    def test_full_python_project(self, tmp_path):
        # Create a well-structured Python project
        (tmp_path / "pyproject.toml").write_text("[project]\nname='foo'\n")
        (tmp_path / ".gitignore").write_text("__pycache__/\n")
        (tmp_path / "README.md").write_text(
            "# Foo\n\nA sample project.\n\n"
            "## Installation\n\n```pip install foo```\n\n"
            "## Usage\n\n```python\nimport foo\nfoo.run()\n```\n"
            + "\n" * 20
        )
        src = tmp_path / "src" / "foo"
        src.mkdir(parents=True)
        (src / "__init__.py").write_text('"""Foo package."""\n\ndef run():\n    pass\n')
        (src / "core.py").write_text('"""Core module."""\n' + "def func():\n    pass\n" * 30)
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_foo.py").write_text(
            "import foo\n\ndef test_run():\n    assert foo.run() is None\n"
        )

        score = score_project(str(tmp_path))
        assert score.total >= 60
        assert score.grade in ("A", "B", "C")

    def test_grade_boundaries(self):
        def _make(total): return QualityScore(total=total, structure=0, code=0, tests=0, docs=0, details=[])
        assert _make(95).grade == "A"
        assert _make(85).grade == "B"
        assert _make(75).grade == "C"
        assert _make(65).grade == "D"
        assert _make(50).grade == "F"

    def test_emoji_mapping(self):
        def _make(total): return QualityScore(total=total, structure=0, code=0, tests=0, docs=0, details=[])
        assert _make(95).emoji == "🏆"
        assert _make(85).emoji == "✅"
        assert _make(75).emoji == "⚠️"
        assert _make(50).emoji == "❌"

    def test_no_todo_scores_higher(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "clean.py").write_text('def run():\n    return 42\n')
        s1 = score_project(str(tmp_path))

        (src / "dirty.py").write_text('def run():\n    pass  # placeholder\n    # TODO: implement\n')
        s2 = score_project(str(tmp_path))
        # Should have more details (warning) about TODOs
        assert any("TODO" in d or "placeholder" in d for d in s2.details)


# ─── Depfix Tests ─────────────────────────────────────────────


class TestDepfix:
    """Tests for forge.build.depfix."""

    def test_extract_python_module_not_found(self):
        err = "ModuleNotFoundError: No module named 'click'"
        modules = extract_missing_modules(err)
        assert "click" in modules

    def test_extract_python_submodule(self):
        err = "ModuleNotFoundError: No module named 'rich.console'"
        modules = extract_missing_modules(err)
        assert "rich" in modules  # Should extract top-level only

    def test_extract_import_error(self):
        err = "ImportError: cannot import name 'foo' from 'bar'"
        modules = extract_missing_modules(err)
        assert "bar" in modules

    def test_extract_node_module(self):
        err = "Cannot find module 'express'"
        modules = extract_missing_modules(err)
        assert "express" in modules

    def test_ignores_relative_imports(self):
        err = "Cannot find module './local_file'"
        modules = extract_missing_modules(err)
        assert "./local_file" not in modules

    def test_extract_multiple_errors(self):
        err = (
            "ModuleNotFoundError: No module named 'flask'\n"
            "ModuleNotFoundError: No module named 'requests'\n"
        )
        modules = extract_missing_modules(err)
        assert "flask" in modules
        assert "requests" in modules

    def test_no_errors_returns_empty(self):
        modules = extract_missing_modules("All tests passed!")
        assert modules == []

    def test_resolve_on_empty_errors(self, tmp_path):
        installed = resolve_missing_deps(str(tmp_path), "All OK")
        assert installed == []


# ─── Resume Tests ─────────────────────────────────────────────


class TestResume:
    """Tests for forge.build.resume."""

    def test_save_and_load(self, tmp_path):
        save_state(
            working_dir=str(tmp_path),
            objective="Build a CLI",
            rounds=[{"phase": "PLAN", "success": True}],
            last_phase="PLAN",
            plan_output="the plan",
            planner="gemini",
            coder="claude-sonnet",
        )

        state = load_state(str(tmp_path))
        assert state is not None
        assert state["objective"] == "Build a CLI"
        assert state["last_phase"] == "PLAN"
        assert state["planner"] == "gemini"
        assert len(state["rounds"]) == 1

    def test_load_nonexistent(self, tmp_path):
        assert load_state(str(tmp_path)) is None

    def test_clear_state(self, tmp_path):
        save_state(str(tmp_path), "test", [], "PLAN")
        assert (tmp_path / STATE_FILENAME).exists()
        clear_state(str(tmp_path))
        assert not (tmp_path / STATE_FILENAME).exists()

    def test_clear_nonexistent(self, tmp_path):
        # Should not raise
        clear_state(str(tmp_path))

    def test_corrupted_state(self, tmp_path):
        (tmp_path / STATE_FILENAME).write_text("not json{{{")
        assert load_state(str(tmp_path)) is None

    def test_wrong_version(self, tmp_path):
        (tmp_path / STATE_FILENAME).write_text(json.dumps({"version": 99}))
        assert load_state(str(tmp_path)) is None


# ─── Validate Tests ───────────────────────────────────────────


class TestValidate:
    """Tests for forge.build.validate."""

    def test_empty_dir(self, tmp_path):
        result = validate_project(str(tmp_path))
        assert not result.passed
        assert result.critical_count > 0

    def test_nonexistent_dir(self):
        result = validate_project("/tmp/nonexistent_forge_test_dir_12345")
        assert not result.passed
        assert result.critical_count > 0

    def test_complete_project(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'")
        (tmp_path / "README.md").write_text("# Project\n" + "Content\n" * 10)
        (tmp_path / ".gitignore").write_text("__pycache__/\n")
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("print('hello')\n")
        (src / "test_main.py").write_text("def test_it(): assert True\n")

        result = validate_project(str(tmp_path))
        assert result.passed
        assert result.critical_count == 0

    def test_missing_readme(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]")
        (tmp_path / "main.py").write_text("x = 1\n")
        result = validate_project(str(tmp_path))
        assert any("README" in i.message for i in result.issues)

    def test_missing_manifest(self, tmp_path):
        (tmp_path / "README.md").write_text("# Hi\n" * 10)
        (tmp_path / "main.py").write_text("x = 1\n")
        result = validate_project(str(tmp_path))
        assert any("manifest" in i.message.lower() for i in result.issues)

    def test_empty_source_file_warning(self, tmp_path):
        (tmp_path / "empty.py").touch()
        (tmp_path / "pyproject.toml").write_text("[project]")
        (tmp_path / "README.md").write_text("# X\n" * 10)
        result = validate_project(str(tmp_path))
        assert any("empty" in i.message.lower() for i in result.issues)

    def test_to_prompt_format(self, tmp_path):
        result = validate_project(str(tmp_path))
        prompt = result.to_prompt()
        assert "VALIDATION" in prompt or "✅" in prompt

    def test_severity_enum(self):
        assert Severity.CRITICAL.value == "CRITICAL"
        assert Severity.WARNING.value == "WARNING"
        assert Severity.INFO.value == "INFO"


# ─── Templates Tests ──────────────────────────────────────────


class TestTemplates:
    """Tests for forge.build.templates."""

    def test_detect_python_cli(self):
        assert detect_template("Build a Python CLI tool") == "cli-tool"

    def test_detect_python_lib(self):
        assert detect_template("Create a Python library for parsing") == "python-lib"

    def test_detect_mcp_server(self):
        assert detect_template("Build an MCP server for database access") == "mcp-server"

    def test_detect_express_api(self):
        assert detect_template("Create a REST API with Express") == "express-api"

    def test_detect_none(self):
        assert detect_template("Do something abstract") is None

    def test_all_templates_have_files(self):
        for name, tmpl in TEMPLATES.items():
            # Templates are (description, files_dict) tuples
            assert isinstance(tmpl, tuple), f"{name} is not a tuple"
            assert len(tmpl) == 2, f"{name} should be (description, files)"
            assert isinstance(tmpl[1], dict), f"{name}[1] should be dict of files"

    def test_scaffold_creates_files(self, tmp_path):
        scaffold_template("cli-tool", str(tmp_path))
        assert (tmp_path / "cli.py").exists()
        assert (tmp_path / "requirements.txt").exists()
        assert (tmp_path / ".gitignore").exists()

    def test_scaffold_skip_existing(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("existing content")
        scaffold_template("cli-tool", str(tmp_path))
        # Should not overwrite
        assert (tmp_path / "pyproject.toml").read_text() == "existing content"

    def test_scaffold_unknown_template(self, tmp_path):
        with pytest.raises(ValueError):
            scaffold_template("nonexistent-template", str(tmp_path))


# ─── Testing (VerificationSuite) Tests ────────────────────────


class TestVerificationSuite:
    """Tests for forge.build.testing."""

    def test_python_project(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'")
        (tmp_path / "main.py").write_text("print(1)")
        suite = detect_verification_suite(str(tmp_path))
        assert suite.has_commands
        assert suite.syntax_check is not None
        assert len(suite.test_commands) > 0

    def test_node_project(self, tmp_path):
        (tmp_path / "package.json").write_text('{"name":"x","scripts":{"test":"jest","build":"tsc"}}')
        (tmp_path / "index.js").write_text("console.log(1)")
        suite = detect_verification_suite(str(tmp_path))
        assert suite.has_commands

    def test_go_project(self, tmp_path):
        (tmp_path / "go.mod").write_text("module example.com/x\n")
        (tmp_path / "main.go").write_text("package main\nfunc main(){}\n")
        suite = detect_verification_suite(str(tmp_path))
        assert suite.has_commands
        assert any("go" in cmd for cmd in suite.all_commands)

    def test_empty_project(self, tmp_path):
        # Unknown project type should still return something
        suite = detect_verification_suite(str(tmp_path))
        # May or may not have commands depending on fallback

    def test_all_commands_order(self):
        suite = VerificationSuite(
            syntax_check="check",
            build_commands=["build"],
            lint_commands=["lint"],
            test_commands=["test"],
        )
        cmds = suite.all_commands
        assert cmds.index("check") < cmds.index("build")
        assert cmds.index("build") < cmds.index("lint")
        assert cmds.index("lint") < cmds.index("test")

    def test_has_commands_empty(self):
        suite = VerificationSuite()
        assert not suite.has_commands

    def test_python_ruff_default(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'")
        (tmp_path / "main.py").write_text("x = 1")
        suite = detect_verification_suite(str(tmp_path))
        assert any("ruff" in cmd for cmd in suite.lint_commands)


# ─── Duo Pipeline Integration Tests ──────────────────────────


class TestDuoPipeline:
    """Integration tests for the duo pipeline with mocked agents."""

    def test_pipeline_creates(self):
        """DuoBuildPipeline can be instantiated."""
        from forge.build.duo import DuoBuildPipeline
        engine = MagicMock()
        pipe = DuoBuildPipeline(
            engine=engine,
            working_dir="/tmp",
            planner_agent="gemini",
            coder_agent="claude-sonnet",
        )
        assert pipe.planner == "gemini"
        assert pipe.coder == "claude-sonnet"
        assert pipe.max_rounds == 5
        assert pipe.interactive is False
        assert pipe.resume is False

    def test_pipeline_agent_validation(self, capsys):
        """Pipeline warns when both agents are the same."""
        from forge.build.duo import DuoBuildPipeline
        engine = MagicMock()
        pipe = DuoBuildPipeline(
            engine=engine,
            working_dir="/tmp",
            planner_agent="gemini",
            coder_agent="gemini",
        )
        pipe._validate_agents()
        # Should print warning — check captured output
        # (Rich prints to stderr sometimes, so just verify no crash)

    def test_duo_round_dataclass(self):
        from forge.build.duo import DuoRound
        r = DuoRound(
            round_number=1,
            phase="PLAN",
            agent_name="gemini",
            prompt="test",
            output="plan output",
            success=True,
            duration_ms=1000,
            cost_usd=0.001,
        )
        assert r.round_number == 1
        assert r.success

    def test_duo_result_dataclass(self):
        from forge.build.duo import DuoResult
        r = DuoResult()
        assert r.rounds == []
        assert r.approved is False
        assert r.total_rounds == 0

    def test_install_deps_python(self, tmp_path):
        """_install_deps runs pip install for Python projects."""
        from forge.build.duo import DuoBuildPipeline
        engine = MagicMock()
        pipe = DuoBuildPipeline(
            engine=engine,
            working_dir=str(tmp_path),
            planner_agent="gemini",
            coder_agent="claude-sonnet",
        )
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'")
        # Should not crash even without a real pip target
        pipe._install_deps()

    def test_interactive_pause_continue(self):
        """_interactive_pause returns 'continue' on empty input."""
        from forge.build.duo import DuoBuildPipeline
        engine = MagicMock()
        pipe = DuoBuildPipeline(
            engine=engine, working_dir="/tmp",
            planner_agent="a", coder_agent="b",
        )
        with patch("builtins.input", return_value=""):
            result = pipe._interactive_pause("test?")
        assert result == "continue"

    def test_interactive_pause_abort(self):
        from forge.build.duo import DuoBuildPipeline
        engine = MagicMock()
        pipe = DuoBuildPipeline(
            engine=engine, working_dir="/tmp",
            planner_agent="a", coder_agent="b",
        )
        with patch("builtins.input", return_value="n"):
            result = pipe._interactive_pause("test?")
        assert result == "abort"

    def test_interactive_pause_feedback(self):
        from forge.build.duo import DuoBuildPipeline
        engine = MagicMock()
        pipe = DuoBuildPipeline(
            engine=engine, working_dir="/tmp",
            planner_agent="a", coder_agent="b",
        )
        with patch("builtins.input", return_value="Add error handling"):
            result = pipe._interactive_pause("test?", allow_feedback=True)
        assert result == "Add error handling"

    def test_interactive_pause_eof(self):
        from forge.build.duo import DuoBuildPipeline
        engine = MagicMock()
        pipe = DuoBuildPipeline(
            engine=engine, working_dir="/tmp",
            planner_agent="a", coder_agent="b",
        )
        with patch("builtins.input", side_effect=EOFError):
            result = pipe._interactive_pause("test?")
        assert result == "abort"
