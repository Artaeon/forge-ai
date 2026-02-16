"""Project templates for scaffolding.

Built-in templates that create a project skeleton before the agent starts,
giving it a solid foundation to build on.
"""

from __future__ import annotations

import os
from pathlib import Path


# Template definitions: name -> (description, files)
TEMPLATES: dict[str, tuple[str, dict[str, str]]] = {
    "flask-api": (
        "Flask REST API with config, routes, and tests",
        {
            "app.py": '''"""Flask API application."""

from flask import Flask, jsonify


def create_app():
    """Application factory."""
    app = Flask(__name__)

    @app.route("/health")
    def health():
        return jsonify({"status": "ok"})

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, host="0.0.0.0", port=5000)
''',
            "requirements.txt": "flask>=3.0\npytest>=8.0\n",
            "tests/__init__.py": "",
            "tests/test_app.py": '''"""Tests for the Flask API."""

import pytest
from app import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"
''',
            ".gitignore": "__pycache__/\n*.pyc\n.venv/\nvenv/\n",
        },
    ),

    "fastapi": (
        "FastAPI application with async routes and tests",
        {
            "main.py": '''"""FastAPI application."""

from fastapi import FastAPI

app = FastAPI(title="API", version="0.1.0")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/hello/{name}")
async def hello(name: str):
    return {"message": f"Hello, {name}!"}
''',
            "requirements.txt": "fastapi>=0.110\nuvicorn[standard]>=0.29\npytest>=8.0\nhttpx>=0.27\n",
            "tests/__init__.py": "",
            "tests/test_main.py": '''"""Tests for the FastAPI application."""

import pytest
from fastapi.testclient import TestClient
from main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_hello(client):
    response = client.get("/hello/World")
    assert response.status_code == 200
    assert response.json()["message"] == "Hello, World!"
''',
            ".gitignore": "__pycache__/\n*.pyc\n.venv/\nvenv/\n",
        },
    ),

    "cli-tool": (
        "Python CLI application with Click",
        {
            "cli.py": '''"""CLI application."""

import click


@click.group()
@click.version_option(version="0.1.0")
def main():
    """A command-line tool."""
    pass


@main.command()
@click.argument("name")
def hello(name: str):
    """Say hello."""
    click.echo(f"Hello, {name}!")


if __name__ == "__main__":
    main()
''',
            "requirements.txt": "click>=8.0\npytest>=8.0\n",
            "tests/__init__.py": "",
            "tests/test_cli.py": '''"""Tests for the CLI."""

from click.testing import CliRunner
from cli import main


def test_hello():
    runner = CliRunner()
    result = runner.invoke(main, ["hello", "World"])
    assert result.exit_code == 0
    assert "Hello, World!" in result.output
''',
            ".gitignore": "__pycache__/\n*.pyc\n.venv/\nvenv/\n",
        },
    ),

    "nextjs": (
        "Next.js application (manual setup required)",
        {
            "README.md": "# Next.js App\n\nRun `npx create-next-app@latest .` to initialize.\n",
        },
    ),

    "python-lib": (
        "Python library with src-layout, pyproject.toml, tests",
        {
            "pyproject.toml": '''[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "mylib"
version = "0.1.0"
description = "A Python library"
requires-python = ">=3.10"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=8.0", "ruff>=0.4"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
''',
            "src/mylib/__init__.py": '"""mylib — a Python library."""\n\n__version__ = "0.1.0"\n',
            "src/mylib/core.py": '"""Core module."""\n\n\ndef hello(name: str) -> str:\n    """Return a greeting."""\n    return f"Hello, {name}!"\n',
            "tests/__init__.py": "",
            "tests/test_core.py": '''"""Tests for core module."""

from mylib.core import hello


def test_hello():
    assert hello("World") == "Hello, World!"
''',
            "README.md": "# mylib\n\nA Python library.\n\n## Installation\n\n```bash\npip install -e .\n```\n\n## Usage\n\n```python\nfrom mylib.core import hello\nprint(hello(\"World\"))\n```\n",
            ".gitignore": "__pycache__/\n*.pyc\n.venv/\nvenv/\ndist/\n*.egg-info/\n",
        },
    ),

    "mcp-server": (
        "MCP server with tool definitions and httpx",
        {
            "pyproject.toml": '''[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "mcp-server"
version = "0.1.0"
description = "An MCP server"
requires-python = ">=3.10"
dependencies = [
    "mcp>=1.0",
    "httpx>=0.27",
    "click>=8.0",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
mcp-server = "server:main"
''',
            "server.py": '''"""MCP server entrypoint."""

import click


@click.command()
@click.option("--port", default=3000, help="Port to listen on")
def main(port: int) -> None:
    """Start the MCP server."""
    click.echo(f"Starting MCP server on port {port}...")
    # Server initialization handled by MCP framework


if __name__ == "__main__":
    main()
''',
            "tools.py": '"""MCP tool definitions."""\n\n\ndef list_tools() -> list[dict]:\n    """Return available MCP tools."""\n    return []\n',
            "tests/__init__.py": "",
            "tests/test_server.py": '''"""Tests for MCP server."""

from click.testing import CliRunner
from server import main


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "MCP server" in result.output
''',
            "README.md": "# MCP Server\n\nAn MCP (Model Context Protocol) server.\n\n## Installation\n\n```bash\npip install -e .\n```\n\n## Usage\n\n```bash\nmcp-server --port 3000\n```\n",
            ".gitignore": "__pycache__/\n*.pyc\n.venv/\nvenv/\n",
        },
    ),

    "express-api": (
        "Express.js REST API with tests",
        {
            "package.json": '''{
  "name": "api",
  "version": "0.1.0",
  "description": "Express REST API",
  "main": "src/index.js",
  "scripts": {
    "start": "node src/index.js",
    "dev": "node --watch src/index.js",
    "test": "jest"
  },
  "dependencies": {
    "express": "^4.18.0",
    "cors": "^2.8.5"
  },
  "devDependencies": {
    "jest": "^29.0.0",
    "supertest": "^6.3.0"
  }
}
''',
            "src/index.js": '''const express = require("express");
const cors = require("cors");

const app = express();
app.use(cors());
app.use(express.json());

app.get("/health", (req, res) => {
  res.json({ status: "ok" });
});

const PORT = process.env.PORT || 3000;
if (require.main === module) {
  app.listen(PORT, () => console.log(`Server running on port ${PORT}`));
}

module.exports = app;
''',
            "tests/app.test.js": '''const request = require("supertest");
const app = require("../src/index");

describe("API", () => {
  test("GET /health returns ok", async () => {
    const res = await request(app).get("/health");
    expect(res.statusCode).toBe(200);
    expect(res.body.status).toBe("ok");
  });
});
''',
            "README.md": "# Express API\n\n## Install\n\n```bash\nnpm install\n```\n\n## Run\n\n```bash\nnpm run dev\n```\n\n## Test\n\n```bash\nnpm test\n```\n",
            ".gitignore": "node_modules/\n.env\n",
        },
    ),
}


def list_templates() -> list[tuple[str, str]]:
    """Return list of (name, description) for all available templates."""
    return [(name, desc) for name, (desc, _) in TEMPLATES.items()]


def scaffold_template(template_name: str, target_dir: str) -> list[str]:
    """Create project files from a template.
    
    Returns list of created file paths (relative to target_dir).
    """
    if template_name not in TEMPLATES:
        raise ValueError(
            f"Unknown template: {template_name}. "
            f"Available: {', '.join(TEMPLATES.keys())}"
        )

    _, files = TEMPLATES[template_name]
    created = []
    target = Path(target_dir)

    for rel_path, content in files.items():
        full_path = target / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content)
        created.append(rel_path)

    return created


def detect_template(objective: str) -> str | None:
    """Auto-detect the best template based on objective keywords.

    Returns template name or None if no match.
    """
    obj_lower = objective.lower()

    # Keyword → template mapping (ordered by specificity)
    keyword_map = [
        (["mcp", "model context protocol"], "mcp-server"),
        (["flask", "flask api"], "flask-api"),
        (["fastapi", "fast api"], "fastapi"),
        (["express", "node api", "node.js api"], "express-api"),
        (["next.js", "nextjs", "next js"], "nextjs"),
        (["cli", "command-line", "command line", "terminal tool"], "cli-tool"),
        (["library", "package", "sdk", "pip install"], "python-lib"),
    ]

    for keywords, template in keyword_map:
        if any(kw in obj_lower for kw in keywords):
            return template

    # Fallback: detect language-level templates
    if any(w in obj_lower for w in ["python", "py ", ".py"]):
        return "python-lib"
    if any(w in obj_lower for w in ["javascript", "node", "npm"]):
        return "express-api"

    return None
