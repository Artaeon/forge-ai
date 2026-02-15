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
