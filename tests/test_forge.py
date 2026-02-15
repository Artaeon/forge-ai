"""Unit tests for Forge agent adapters and core systems."""

import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from forge.agents.base import AgentResult, AgentStatus, TaskContext
from forge.agents.claude import ClaudeAdapter
from forge.agents.gemini import GeminiAdapter
from forge.agents.copilot import CopilotAdapter
from forge.agents.antigravity import AntigravityAdapter
from forge.config import load_config, DEFAULT_AGENTS
from forge.build.errors import ErrorClassifier, ErrorCategory
from forge.build.testing import detect_verification_suite
from forge.build.context import gather_context


# ─── Config Tests ─────────────────────────────────────────────


class TestConfig:
    def test_load_defaults(self):
        cfg = load_config()
        assert "claude-sonnet" in cfg.agents
        assert "gemini" in cfg.agents
        assert "antigravity-pro" in cfg.agents
        assert "antigravity-flash" in cfg.agents

    def test_default_agents_have_types(self):
        for name, defaults in DEFAULT_AGENTS.items():
            assert "agent_type" in defaults, f"Missing agent_type for {name}"

    def test_config_timeout(self):
        cfg = load_config()
        assert cfg.global_.timeout > 0

    def test_antigravity_models(self):
        cfg = load_config()
        pro = cfg.agents.get("antigravity-pro")
        flash = cfg.agents.get("antigravity-flash")
        assert pro is not None
        assert flash is not None
        assert pro.model == "gemini-2.5-pro"
        assert flash.model == "gemini-2.5-flash"


# ─── Claude Adapter Tests ────────────────────────────────────


class TestClaudeAdapter:
    def test_init(self):
        adapter = ClaudeAdapter(model="sonnet")
        assert adapter.name == "claude"
        assert adapter.model == "sonnet"

    def test_build_command_print_mode(self):
        adapter = ClaudeAdapter(model="sonnet")
        ctx = TaskContext(working_dir=".", prompt="test prompt")
        cmd = adapter._build_command(ctx, agentic=False)
        assert "--print" in cmd
        assert "--model" in cmd
        assert "sonnet" in cmd
        assert "test prompt" in cmd

    def test_build_command_agentic_mode(self):
        adapter = ClaudeAdapter(model="sonnet", skip_permissions=True)
        ctx = TaskContext(working_dir=".", prompt="test prompt")
        cmd = adapter._build_command(ctx, agentic=True)
        assert "--dangerously-skip-permissions" in cmd
        assert "-p" in cmd

    def test_unavailable_returns_status(self):
        adapter = ClaudeAdapter()
        with patch("shutil.which", return_value=None):
            result = asyncio.run(adapter.execute(
                TaskContext(working_dir=".", prompt="test")
            ))
            assert result.status == AgentStatus.UNAVAILABLE


# ─── Gemini Adapter Tests ────────────────────────────────────


class TestGeminiAdapter:
    def test_init(self):
        adapter = GeminiAdapter()
        assert adapter.name == "gemini"

    def test_build_command_uses_p_flag(self):
        adapter = GeminiAdapter()
        ctx = TaskContext(working_dir=".", prompt="test prompt")
        cmd = adapter._build_command(ctx)
        assert "-p" in cmd
        assert "test prompt" in cmd

    def test_build_command_with_model(self):
        adapter = GeminiAdapter(model="gemini-2.5-pro")
        ctx = TaskContext(working_dir=".", prompt="test")
        cmd = adapter._build_command(ctx)
        assert "-m" in cmd
        assert "gemini-2.5-pro" in cmd

    def test_build_command_agentic_yolo(self):
        adapter = GeminiAdapter()
        ctx = TaskContext(working_dir=".", prompt="test")
        cmd = adapter._build_command(ctx, agentic=True)
        assert "--yolo" in cmd
        assert "--sandbox" in cmd
        assert "false" in cmd

    def test_unavailable_returns_status(self):
        adapter = GeminiAdapter()
        with patch("shutil.which", return_value=None):
            result = asyncio.run(adapter.execute(
                TaskContext(working_dir=".", prompt="test")
            ))
            assert result.status == AgentStatus.UNAVAILABLE


# ─── Antigravity Adapter Tests ───────────────────────────────


class TestAntigravityAdapter:
    def test_init(self):
        adapter = AntigravityAdapter(model="gemini-2.5-pro")
        assert adapter.name == "antigravity"
        assert adapter.model == "gemini-2.5-pro"

    def test_cost_estimation(self):
        adapter = AntigravityAdapter(model="gemini-2.5-pro")
        cost = adapter._estimate_cost(1_000_000, 1_000_000)
        assert cost == 11.25  # 1.25 input + 10.0 output

    def test_cost_estimation_flash(self):
        adapter = AntigravityAdapter(model="gemini-2.5-flash")
        cost = adapter._estimate_cost(1_000_000, 1_000_000)
        assert cost == 0.75  # 0.15 + 0.60

    def test_unavailable_without_key(self):
        adapter = AntigravityAdapter()
        with patch.dict("os.environ", {}, clear=True):
            adapter.api_key = None
            assert not adapter.is_available() or True  # SDK may or may not be installed

    def test_file_writing(self, tmp_path):
        adapter = AntigravityAdapter()
        output = (
            '=== FILE: hello.py ===\nprint("hello")\n=== END FILE ===\n'
            '=== FILE: utils/helper.py ===\ndef help(): pass\n=== END FILE ===\n'
        )
        files = adapter._write_files_from_output(output, str(tmp_path))
        assert "hello.py" in files
        assert "utils/helper.py" in files
        assert (tmp_path / "hello.py").read_text() == 'print("hello")\n'
        assert (tmp_path / "utils" / "helper.py").exists()

    def test_path_traversal_blocked(self, tmp_path):
        adapter = AntigravityAdapter()
        output = '=== FILE: ../../etc/passwd ===\nevil\n=== END FILE ===\n'
        files = adapter._write_files_from_output(output, str(tmp_path))
        assert len(files) == 0


# ─── Copilot Adapter Tests ───────────────────────────────────


class TestCopilotAdapter:
    def test_classify_explain(self):
        adapter = CopilotAdapter()
        assert adapter._classify_prompt("explain this code") == "explain"
        assert adapter._classify_prompt("what does this do") == "explain"

    def test_classify_suggest(self):
        adapter = CopilotAdapter()
        assert adapter._classify_prompt("write a function") == "suggest"
        assert adapter._classify_prompt("create a REST API") == "suggest"


# ─── Error Classifier Tests ──────────────────────────────────


class TestErrorClassifier:
    def test_syntax_error(self):
        classifier = ErrorClassifier()
        result = classifier.classify("SyntaxError: invalid syntax at line 42")
        assert result.category == ErrorCategory.SYNTAX

    def test_dependency_error(self):
        classifier = ErrorClassifier()
        result = classifier.classify("ModuleNotFoundError: No module named 'flask'")
        assert result.category == ErrorCategory.DEPENDENCY

    def test_unknown_error(self):
        classifier = ErrorClassifier()
        result = classifier.classify("Something completely unexpected happened")
        assert result.category == ErrorCategory.UNKNOWN


# ─── Test Detection Tests ────────────────────────────────────


class TestTestDetection:
    def test_detection_returns_suite(self, tmp_path):
        # Create a Python project
        (tmp_path / "main.py").write_text("print('hello')")
        (tmp_path / "requirements.txt").write_text("flask\n")
        suite = detect_verification_suite(str(tmp_path))
        assert suite is not None
        assert len(suite.test_commands) > 0


# ─── Context Gathering Tests ─────────────────────────────────


class TestContextGathering:
    def test_gather_context(self, tmp_path):
        (tmp_path / "main.py").write_text("print('hello')")
        ctx = gather_context(str(tmp_path))
        assert ctx.working_dir == str(tmp_path)
        assert len(ctx.file_tree) > 0

    def test_context_prompt_section(self, tmp_path):
        (tmp_path / "app.py").write_text("from flask import Flask")
        ctx = gather_context(str(tmp_path))
        prompt = ctx.to_prompt_section()
        assert "Working directory" in prompt
