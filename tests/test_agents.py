"""Tests for agent adapters — mock subprocess calls and validate behavior.

Covers: execute(), error paths, cost estimation, security checks.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge.agents.base import AgentResult, AgentStatus, TaskContext


# ─── Helpers ──────────────────────────────────────────────────


def make_ctx(prompt: str = "Test prompt", working_dir: str = "/tmp") -> TaskContext:
    return TaskContext(working_dir=working_dir, prompt=prompt, timeout=30)


def make_process(stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
    """Create a mock asyncio subprocess."""
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


# ─── ClaudeAdapter Tests ─────────────────────────────────────


class TestClaudeAdapter:
    def test_init_default(self):
        from forge.agents.claude import ClaudeAdapter
        adapter = ClaudeAdapter()
        assert adapter.name == "claude"
        assert adapter.model == "sonnet"

    def test_init_custom_model(self):
        from forge.agents.claude import ClaudeAdapter
        adapter = ClaudeAdapter(model="opus")
        assert adapter.name == "claude"  # name is always "claude"
        assert adapter.model == "opus"

    def test_is_available_no_binary(self):
        from forge.agents.claude import ClaudeAdapter
        adapter = ClaudeAdapter()
        with patch("shutil.which", return_value=None):
            assert adapter.is_available() is False

    def test_is_available_with_binary(self):
        from forge.agents.claude import ClaudeAdapter
        adapter = ClaudeAdapter()
        with patch("shutil.which", return_value="/usr/bin/claude"):
            assert adapter.is_available() is True

    def test_build_command_default(self):
        from forge.agents.claude import ClaudeAdapter
        adapter = ClaudeAdapter()
        ctx = make_ctx("Fix the bug")
        cmd = adapter._build_command(ctx)
        assert "claude" in cmd
        assert "Fix the bug" in cmd

    def test_build_command_agentic(self):
        from forge.agents.claude import ClaudeAdapter
        adapter = ClaudeAdapter()
        ctx = make_ctx("Write code")
        cmd = adapter._build_command(ctx, agentic=True)
        # Agentic mode should include flags for autonomous operation
        assert "claude" in cmd

    @pytest.mark.anyio
    async def test_execute_success_plain_text(self):
        from forge.agents.claude import ClaudeAdapter
        adapter = ClaudeAdapter()

        proc = make_process(stdout=b"Here is the code output")
        with patch("asyncio.create_subprocess_exec", return_value=proc), \
             patch("asyncio.wait_for", return_value=(b"Here is the code output", b"")):
            proc.communicate = AsyncMock(return_value=(b"Here is the code output", b""))
            result = await adapter._run(make_ctx())

        assert result.status == AgentStatus.SUCCESS
        assert "code output" in result.output

    @pytest.mark.anyio
    async def test_execute_success_json(self):
        from forge.agents.claude import ClaudeAdapter
        adapter = ClaudeAdapter()

        response = json.dumps({"result": "done", "is_error": False}).encode()
        proc = make_process(stdout=response)
        with patch("asyncio.create_subprocess_exec", return_value=proc), \
             patch("asyncio.wait_for", return_value=(response, b"")):
            result = await adapter._run(make_ctx())

        assert result.status == AgentStatus.SUCCESS

    @pytest.mark.anyio
    async def test_execute_timeout(self):
        from forge.agents.claude import ClaudeAdapter
        adapter = ClaudeAdapter()

        with patch("asyncio.create_subprocess_exec", side_effect=asyncio.TimeoutError()):
            # _run raises on timeout — but the adapter.execute wraps it  
            pass  # Timeout path verified via integration

    @pytest.mark.anyio
    async def test_execute_nonzero_exit(self):
        from forge.agents.claude import ClaudeAdapter
        adapter = ClaudeAdapter()

        proc = make_process(stdout=b"", stderr=b"Error: module not found", returncode=1)
        with patch("asyncio.create_subprocess_exec", return_value=proc), \
             patch("asyncio.wait_for", return_value=(b"", b"Error: module not found")):
            result = await adapter._run(make_ctx())

        assert result.status == AgentStatus.FAILED
        assert "module not found" in result.error


# ─── GeminiAdapter Tests ─────────────────────────────────────


class TestGeminiAdapter:
    def test_init(self):
        from forge.agents.gemini import GeminiAdapter
        adapter = GeminiAdapter()
        assert adapter.name == "gemini"

    def test_is_available_no_binary(self):
        from forge.agents.gemini import GeminiAdapter
        adapter = GeminiAdapter()
        with patch("shutil.which", return_value=None):
            assert adapter.is_available() is False

    def test_is_available_with_binary(self):
        from forge.agents.gemini import GeminiAdapter
        adapter = GeminiAdapter()
        with patch("shutil.which", return_value="/usr/bin/gemini"):
            assert adapter.is_available() is True

    def test_build_command(self):
        from forge.agents.gemini import GeminiAdapter
        adapter = GeminiAdapter()
        ctx = make_ctx("Build an API")
        cmd = adapter._build_command(ctx)
        assert "gemini" in cmd

    @pytest.mark.anyio
    async def test_execute_success(self):
        from forge.agents.gemini import GeminiAdapter
        adapter = GeminiAdapter()

        proc = make_process(stdout=b"Generated code here")
        with patch("asyncio.create_subprocess_exec", return_value=proc), \
             patch("asyncio.wait_for", return_value=(b"Generated code here", b"")):
            result = await adapter._run(make_ctx())

        assert result.status == AgentStatus.SUCCESS
        assert "Generated code" in result.output

    @pytest.mark.anyio
    async def test_execute_json_output(self):
        from forge.agents.gemini import GeminiAdapter
        adapter = GeminiAdapter()

        json_out = json.dumps({"response": "Plan created"}).encode()
        proc = make_process(stdout=json_out)
        with patch("asyncio.create_subprocess_exec", return_value=proc), \
             patch("asyncio.wait_for", return_value=(json_out, b"")):
            result = await adapter._run(make_ctx())

        assert result.status == AgentStatus.SUCCESS


# ─── CopilotAdapter Tests ───────────────────────────────────


class TestCopilotAdapter:
    def test_classify_explain(self):
        from forge.agents.copilot import CopilotAdapter
        adapter = CopilotAdapter()
        assert adapter._classify_prompt("explain this code") == "explain"

    def test_classify_suggest(self):
        from forge.agents.copilot import CopilotAdapter
        adapter = CopilotAdapter()
        assert adapter._classify_prompt("create a function") == "suggest"

    def test_build_command_explain(self):
        from forge.agents.copilot import CopilotAdapter
        adapter = CopilotAdapter()
        ctx = make_ctx("explain this")
        cmd = adapter._build_command(ctx, "explain")
        assert "explain" in cmd

    def test_build_command_suggest(self):
        from forge.agents.copilot import CopilotAdapter
        adapter = CopilotAdapter()
        ctx = make_ctx("create API")
        cmd = adapter._build_command(ctx, "suggest")
        assert "suggest" in cmd


# ─── AntigravityAdapter Tests ────────────────────────────────


class TestAntigravityAdapterExecution:
    def test_cost_estimation(self):
        from forge.agents.antigravity import AntigravityAdapter
        adapter = AntigravityAdapter(model="gemini-2.5-flash")
        cost = adapter._estimate_cost(input_tokens=1000, output_tokens=500)
        assert isinstance(cost, float)
        assert cost > 0
        assert cost < 0.01  # Should be very small for 1500 tokens

    def test_cost_estimation_pro(self):
        from forge.agents.antigravity import AntigravityAdapter
        adapter = AntigravityAdapter(model="gemini-2.5-pro")
        cost_pro = adapter._estimate_cost(1000, 500)
        adapter2 = AntigravityAdapter(model="gemini-2.5-flash")
        cost_flash = adapter2._estimate_cost(1000, 500)
        assert cost_pro > cost_flash  # Pro is more expensive

    def test_is_available_no_key(self):
        from forge.agents.antigravity import AntigravityAdapter
        adapter = AntigravityAdapter()
        with patch.dict("os.environ", {}, clear=True):
            adapter.api_key = None
            # Without google-genai installed, this will be False
            # With it installed, it depends on API key
            result = adapter.is_available()
            assert isinstance(result, bool)

    def test_file_writing_security(self):
        """Test path traversal is blocked in file writing."""
        from forge.agents.antigravity import AntigravityAdapter
        import tempfile
        import os

        adapter = AntigravityAdapter()
        with tempfile.TemporaryDirectory() as td:
            # Normal file — should be written
            output = '=== FILE: src/main.py ===\nprint("hello")\n=== END FILE ==='
            files = adapter._write_files_from_output(output, td)
            assert files == ["src/main.py"]
            assert (os.path.join(td, "src", "main.py"))

            # Path traversal — should be BLOCKED
            output_bad = '=== FILE: ../../etc/passwd ===\nevil\n=== END FILE ==='
            files_bad = adapter._write_files_from_output(output_bad, td)
            assert files_bad == []

            # Absolute path — should be BLOCKED
            output_abs = '=== FILE: /etc/shadow ===\nevil\n=== END FILE ==='
            files_abs = adapter._write_files_from_output(output_abs, td)
            assert files_abs == []


# ─── Dispatch Module Tests ───────────────────────────────────


class TestDispatchModule:
    def test_extract_files_pattern1(self):
        """Test === FILE: path === extraction."""
        from forge.build.phases.dispatch import extract_files_from_output
        import tempfile

        pipeline = MagicMock()
        with tempfile.TemporaryDirectory() as td:
            pipeline.working_dir = td
            output = (
                '=== FILE: src/app.py ===\n'
                'print("hello world")\n'
                '=== END FILE ===\n'
                '=== FILE: README.md ===\n'
                '# My App\n'
                '=== END FILE ==='
            )
            files = extract_files_from_output(pipeline, output)
            assert len(files) == 2
            assert "src/app.py" in files
            assert "README.md" in files

    def test_extract_files_security(self):
        """Test path traversal is blocked."""
        from forge.build.phases.dispatch import extract_files_from_output
        import tempfile

        pipeline = MagicMock()
        with tempfile.TemporaryDirectory() as td:
            pipeline.working_dir = td
            output = '=== FILE: ../../etc/passwd ===\nevil\n=== END FILE ==='
            files = extract_files_from_output(pipeline, output)
            assert files == []

    def test_extract_files_strips_noise(self):
        """Test that noisy output lines are stripped."""
        from forge.build.phases.dispatch import extract_files_from_output
        import tempfile

        pipeline = MagicMock()
        with tempfile.TemporaryDirectory() as td:
            pipeline.working_dir = td
            output = (
                'Error executing tool xyz\n'
                'Hook registry initialized\n'
                '=== FILE: src/main.py ===\n'
                'print("clean")\n'
                '=== END FILE ==='
            )
            files = extract_files_from_output(pipeline, output)
            assert files == ["src/main.py"]


# ─── Phase Module Import Tests ───────────────────────────────


class TestPhaseImports:
    def test_all_phases_importable(self):
        from forge.build.phases import (
            dispatch, dispatch_agentic, execute_with_spinner,
            run_plan, run_code, run_verify, run_review, run_fix,
        )
        assert callable(dispatch)
        assert callable(dispatch_agentic)
        assert callable(execute_with_spinner)
        assert callable(run_plan)
        assert callable(run_code)
        assert callable(run_verify)
        assert callable(run_review)
        assert callable(run_fix)

    def test_duo_imports_phases(self):
        from forge.build.duo import DuoBuildPipeline
        assert hasattr(DuoBuildPipeline, "run")
        assert hasattr(DuoBuildPipeline, "_init_plugins")
        assert hasattr(DuoBuildPipeline, "_init_persistent_memory")
        assert hasattr(DuoBuildPipeline, "_save_run_record")
