"""Tests for forge.build supporting modules — scoring, context, compact, memory, errors, depfix.

Covers: quality scoring, project detection, context windowing, build memory,
        error classification, and dependency resolution.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from forge.build.scoring import score_project, QualityScore
from forge.build.context import gather_context, _detect_project, _list_files, ProjectInfo
from forge.build.compact import (
    gather_compact, summarize_round, build_history_summary,
    chunk_file, FileChunk, select_context_window,
)
from forge.build.memory import BuildMemory, PersistentMemory
from forge.build.errors import ErrorClassifier
from forge.build.depfix import extract_missing_modules


# ─── Helpers ──────────────────────────────────────────────────


def make_project(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create a temp project directory with the given files."""
    for relpath, content in files.items():
        fp = tmp_path / relpath
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
    return tmp_path


# ─── QualityScore Tests ──────────────────────────────────────


class TestQualityScore:
    def test_grade_A(self):
        s = QualityScore(total=92, structure=25, code=25, tests=25, docs=17, details=[])
        assert s.grade == "A"
        assert s.emoji == "🏆"

    def test_grade_B(self):
        s = QualityScore(total=85, structure=20, code=25, tests=20, docs=20, details=[])
        assert s.grade == "B"
        assert s.emoji == "✅"

    def test_grade_F(self):
        s = QualityScore(total=30, structure=5, code=10, tests=5, docs=10, details=[])
        assert s.grade == "F"
        assert s.emoji == "❌"

    def test_score_empty_project(self, tmp_path):
        score = score_project(str(tmp_path))
        assert score.total < 30
        assert score.grade in ("D", "F")

    def test_score_python_project(self, tmp_path):
        make_project(tmp_path, {
            "pyproject.toml": "[project]\nname = 'test'\n",
            ".gitignore": "__pycache__/\n",
            "src/__init__.py": "",
            "src/main.py": '"""Main module."""\n\ndef hello():\n    """Greet."""\n    return "Hello"\n' * 20,
            "tests/test_main.py": "def test_hello():\n    assert True\n",
            "README.md": "# Test Project\n\n## Installation\npip install .\n\n## Usage\n```bash\npython -m src\n```\n" + "\n".join(f"line {i}" for i in range(30)),
        })
        score = score_project(str(tmp_path))
        assert score.total >= 60
        assert score.structure > 0
        assert score.code > 0
        assert score.docs > 0


# ─── Project Detection Tests ─────────────────────────────────


class TestProjectDetection:
    def test_detect_python(self, tmp_path):
        make_project(tmp_path, {
            "requirements.txt": "flask>=3.0\n",
            "app.py": "from flask import Flask\napp = Flask(__name__)\n",
        })
        files = _list_files(tmp_path)
        info = _detect_project(tmp_path, files)
        assert info.language == "python"
        assert info.framework == "flask"
        assert info.entry_point == "app.py"

    def test_detect_javascript(self, tmp_path):
        make_project(tmp_path, {
            "package.json": '{"name":"test","dependencies":{"express":"^4.0"}}\n',
            "index.js": "const express = require('express');\n",
        })
        files = _list_files(tmp_path)
        info = _detect_project(tmp_path, files)
        assert info.language == "javascript"
        assert info.entry_point == "index.js"

    def test_detect_typescript(self, tmp_path):
        make_project(tmp_path, {
            "tsconfig.json": '{"compilerOptions":{"strict":true}}\n',
            "src/index.ts": "console.log('hi');\n",
        })
        files = _list_files(tmp_path)
        info = _detect_project(tmp_path, files)
        assert info.language == "typescript"

    def test_detect_unknown(self, tmp_path):
        make_project(tmp_path, {
            "data.csv": "a,b,c\n1,2,3\n",
        })
        files = _list_files(tmp_path)
        info = _detect_project(tmp_path, files)
        assert info.language == "unknown"

    def test_list_files_excludes_hidden(self, tmp_path):
        make_project(tmp_path, {
            "src/main.py": "pass\n",
            ".git/config": "...\n",
            "__pycache__/x.pyc": "...\n",
        })
        files = _list_files(tmp_path)
        assert "src/main.py" in files
        assert not any(".git" in f for f in files)


# ─── Compact Module Tests ────────────────────────────────────


class TestCompact:
    def test_gather_compact(self, tmp_path):
        make_project(tmp_path, {
            "requirements.txt": "flask>=3.0\n",
            "app.py": "from flask import Flask\n",
        })
        ctx = gather_compact(str(tmp_path))
        assert ctx.language == "python"
        assert ctx.framework == "flask"

    def test_summarize_round_empty(self):
        result = summarize_round("claude", "CODE", "")
        assert "claude" in result
        assert "no output" in result

    def test_summarize_round_bullets(self):
        text = "- Fix the bug\n- Add tests\n- Update README\nSome other content\n"
        result = summarize_round("gemini", "REVIEW", text)
        assert "Fix the bug" in result
        assert "Add tests" in result

    def test_summarize_round_truncation(self):
        long_text = "x" * 2000
        result = summarize_round("claude", "CODE", long_text, max_chars=100)
        assert len(result) <= 110  # small margin for ellipsis

    def test_build_history_summary_empty(self):
        assert build_history_summary([]) == ""

    def test_build_history_summary(self):
        rounds = [
            {"agent_name": "claude", "phase": "CODE", "output": "Created main.py"},
            {"agent_name": "gemini", "phase": "REVIEW", "output": "- Looks good\n- Add tests"},
        ]
        result = build_history_summary(rounds)
        assert "CODE" in result
        assert "REVIEW" in result

    def test_chunk_file_small(self):
        content = "line1\nline2\nline3\n"
        chunks = chunk_file("test.py", content, max_chunk_chars=1000)
        assert len(chunks) == 1
        assert chunks[0].path == "test.py"
        assert chunks[0].start_line == 1

    def test_chunk_file_large(self):
        content = ""
        for i in range(50):
            content += f"def func_{i}():\n    pass\n\n"
        chunks = chunk_file("big.py", content, max_chunk_chars=200)
        assert len(chunks) > 1
        # All lines should be covered
        total_lines = sum(c.end_line - c.start_line + 1 for c in chunks)
        assert total_lines >= content.count("\n")

    def test_select_context_window(self, tmp_path):
        make_project(tmp_path, {
            "pyproject.toml": "[project]\nname='test'\n",
            "main.py": "print('hello')\n",
        })
        result = select_context_window(str(tmp_path), token_budget=5000)
        assert "pyproject.toml" in result or "main.py" in result

    def test_select_context_window_empty(self, tmp_path):
        assert select_context_window(str(tmp_path / "nonexistent")) == ""


# ─── Build Memory Tests ──────────────────────────────────────


class TestBuildMemory:
    def test_initial_state(self):
        mem = BuildMemory()
        assert mem.iteration_count == 0
        assert mem.total_cost == 0.0
        assert not mem.has_successes
        assert mem.consecutive_failures == 0

    def test_record_success(self):
        mem = BuildMemory()
        mem.record_iteration(
            iteration=1, agent="claude", prompt="Build it",
            output="Done", files_created=["main.py"],
            files_modified=[], test_passed=True, cost_usd=0.05,
        )
        assert mem.iteration_count == 1
        assert mem.has_successes
        assert mem.consecutive_failures == 0
        assert mem.total_cost == pytest.approx(0.05)

    def test_record_failures_and_escalation(self):
        mem = BuildMemory()
        for i in range(4):
            mem.record_iteration(
                iteration=i + 1, agent="claude", prompt="Fix it",
                output="Failed", files_created=[], files_modified=[],
                test_passed=False, error="SyntaxError", error_category="syntax",
            )
        assert mem.consecutive_failures == 4
        assert mem.should_escalate(max_failures=3)
        reason = mem.get_escalation_reason()
        assert "4" in reason
        assert "syntax" in reason

    def test_prompt_section(self):
        mem = BuildMemory()
        mem.record_iteration(
            iteration=1, agent="claude", prompt="Do something",
            output="Result", files_created=["app.py"],
            files_modified=[], test_passed=True,
        )
        section = mem.to_prompt_section()
        assert "BUILD HISTORY" in section
        assert "PASSED" in section

    def test_prompt_section_empty(self):
        mem = BuildMemory()
        assert mem.to_prompt_section() == ""


class TestPersistentMemory:
    def test_add_and_retrieve(self, tmp_path):
        pm = PersistentMemory(str(tmp_path))
        pm.add_learning(
            pattern="Flask apps need flask>=3.0",
            category="success",
            objective_hint="flask api rest",
            agent="claude",
        )
        assert pm.count == 1

        relevant = pm.get_relevant("build a flask rest api")
        assert len(relevant) >= 1
        assert relevant[0].pattern == "Flask apps need flask>=3.0"

    def test_persistence(self, tmp_path):
        pm1 = PersistentMemory(str(tmp_path))
        pm1.add_learning("Use pytest", "strategy", "testing", "claude")
        pm1.save()

        pm2 = PersistentMemory(str(tmp_path))
        assert pm2.count == 1
        assert pm2.get_relevant("run tests")[0].pattern == "Use pytest"

    def test_deduplication(self, tmp_path):
        pm = PersistentMemory(str(tmp_path))
        pm.add_learning("Tip A", "strategy", "hint", "claude", confidence=0.5)
        pm.add_learning("Tip A", "strategy", "hint", "claude", confidence=0.5)
        assert pm.count == 1  # Deduped
        assert pm.get_relevant("hint")[0].confidence > 0.5  # Boosted

    def test_prompt_section(self, tmp_path):
        pm = PersistentMemory(str(tmp_path))
        pm.add_learning("Flask tip", "success", "flask api", "claude")
        section = pm.to_prompt_section("build a flask api")
        assert "LEARNINGS" in section
        assert "Flask tip" in section


# ─── Error Classification Tests ──────────────────────────────


class TestErrorClassification:
    def setup_method(self):
        self.classifier = ErrorClassifier()

    def test_syntax_error(self):
        result = self.classifier.classify("SyntaxError: invalid syntax, line 42")
        assert result.category.value == "syntax"

    def test_dependency_error(self):
        result = self.classifier.classify("ModuleNotFoundError: No module named 'flask'")
        assert result.category.value == "dependency"

    def test_import_error(self):
        result = self.classifier.classify("ImportError: cannot import name 'bar' from 'foo'")
        assert result.category.value == "dependency"

    def test_type_error(self):
        result = self.classifier.classify("TypeError: expected str, got int")
        assert result.category.value == "runtime"

    def test_test_failure(self):
        result = self.classifier.classify("FAILED tests/test_main.py::test_hello - AssertionError")
        assert result.category.value == "logic"


# ─── Dependency Resolution Tests ─────────────────────────────


class TestDepfix:
    def test_extract_module_not_found(self):
        mods = extract_missing_modules(
            "ModuleNotFoundError: No module named 'flask'"
        )
        assert "flask" in mods

    def test_extract_import_error(self):
        mods = extract_missing_modules(
            "ImportError: cannot import name 'Response' from 'werkzeug'"
        )
        assert "werkzeug" in mods

    def test_extract_node_module(self):
        mods = extract_missing_modules(
            "Cannot find module 'express'"
        )
        assert "express" in mods

    def test_extract_ignores_relative(self):
        mods = extract_missing_modules(
            "Cannot find module './utils'"
        )
        assert len(mods) == 0

    def test_extract_multiple(self):
        mods = extract_missing_modules(
            "ModuleNotFoundError: No module named 'flask'\n"
            "ModuleNotFoundError: No module named 'sqlalchemy'\n"
        )
        assert "flask" in mods
        assert "sqlalchemy" in mods
        assert len(mods) == 2

    def test_extract_submodule(self):
        """Should extract top-level package from dotted import."""
        mods = extract_missing_modules(
            "ModuleNotFoundError: No module named 'flask.blueprints'"
        )
        assert "flask" in mods
        assert "flask.blueprints" not in mods
