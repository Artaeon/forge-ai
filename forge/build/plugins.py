"""Plugin system for extending the duo pipeline.

Plugins can hook into various pipeline phases to add custom
verification steps, templates, scoring rules, and post-processing.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console

console = Console()
logger = logging.getLogger(__name__)


# ─── Plugin Interface ─────────────────────────────────────────


class ForgePlugin(ABC):
    """Base class for Forge plugins.

    Subclass this and implement any hooks you need.
    All hooks are optional — implement only what you need.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique plugin name."""
        ...

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return ""

    # ─── Lifecycle hooks ──────────────

    def on_pipeline_start(self, objective: str, working_dir: str) -> None:
        """Called before the pipeline starts."""
        pass

    def on_pipeline_end(self, result: Any) -> None:
        """Called after the pipeline completes."""
        pass

    # ─── Phase hooks ──────────────────

    def on_plan(self, plan_output: str) -> str:
        """Called after PLAN phase. Return modified plan or pass through."""
        return plan_output

    def on_code(self, code_output: str) -> str:
        """Called after CODE phase."""
        return code_output

    def on_verify(self, verify_output: str) -> str:
        """Called after VERIFY phase."""
        return verify_output

    def on_review(self, review_output: str) -> str:
        """Called after REVIEW phase."""
        return review_output

    # ─── Extension hooks ──────────────

    def extra_verify_commands(self, working_dir: str) -> list[str]:
        """Return additional verification commands to run."""
        return []

    def extra_scoring_rules(self, working_dir: str) -> list[tuple[str, int]]:
        """Return extra (detail_message, score_adjustment) pairs."""
        return []

    def custom_template_files(self) -> dict[str, str]:
        """Return extra files to scaffold: {path: content}."""
        return {}


# ─── Plugin Registry ──────────────────────────────────────────


@dataclass
class PluginRegistry:
    """Manages loaded plugins."""
    _plugins: dict[str, ForgePlugin] = field(default_factory=dict)

    @property
    def plugins(self) -> list[ForgePlugin]:
        return list(self._plugins.values())

    @property
    def count(self) -> int:
        return len(self._plugins)

    def register(self, plugin: ForgePlugin) -> None:
        """Register a plugin instance."""
        if plugin.name in self._plugins:
            console.print(f"[yellow]⚠ Plugin '{plugin.name}' already loaded, skipping.[/]")
            return
        self._plugins[plugin.name] = plugin
        console.print(
            f"[dim]  🔌 Plugin loaded: {plugin.name} v{plugin.version}[/]"
        )

    def unregister(self, name: str) -> None:
        """Remove a plugin by name."""
        self._plugins.pop(name, None)

    def get(self, name: str) -> ForgePlugin | None:
        return self._plugins.get(name)

    # ─── Hook dispatching ─────────────

    def dispatch_plan(self, plan_output: str) -> str:
        for p in self.plugins:
            plan_output = p.on_plan(plan_output)
        return plan_output

    def dispatch_code(self, code_output: str) -> str:
        for p in self.plugins:
            code_output = p.on_code(code_output)
        return code_output

    def dispatch_verify(self, verify_output: str) -> str:
        for p in self.plugins:
            verify_output = p.on_verify(verify_output)
        return verify_output

    def dispatch_review(self, review_output: str) -> str:
        for p in self.plugins:
            review_output = p.on_review(review_output)
        return review_output

    def collect_verify_commands(self, working_dir: str) -> list[str]:
        cmds = []
        for p in self.plugins:
            cmds.extend(p.extra_verify_commands(working_dir))
        return cmds

    def collect_scoring_rules(self, working_dir: str) -> list[tuple[str, int]]:
        rules = []
        for p in self.plugins:
            rules.extend(p.extra_scoring_rules(working_dir))
        return rules

    def on_start(self, objective: str, working_dir: str) -> None:
        for p in self.plugins:
            try:
                p.on_pipeline_start(objective, working_dir)
            except (TypeError, AttributeError, RuntimeError) as e:
                logger.warning("Plugin %s error on start: %s", p.name, e)
                console.print(f"[red]Plugin {p.name} error on start: {e}[/]")

    def on_end(self, result: Any) -> None:
        for p in self.plugins:
            try:
                p.on_pipeline_end(result)
            except (TypeError, AttributeError, RuntimeError) as e:
                logger.warning("Plugin %s error on end: %s", p.name, e)
                console.print(f"[red]Plugin {p.name} error on end: {e}[/]")


# ─── Plugin Loading ───────────────────────────────────────────


def load_plugins_from_dir(
    directory: str | Path,
    registry: PluginRegistry | None = None,
) -> PluginRegistry:
    """Load all .py plugins from a directory.

    Each plugin file should define a class that extends ForgePlugin.
    Convention: the file should have a top-level `plugin` variable
    or a `create_plugin()` function.

    Example plugin file:

        from forge.build.plugins import ForgePlugin

        class MyPlugin(ForgePlugin):
            @property
            def name(self): return "my-plugin"

        plugin = MyPlugin()
    """
    if registry is None:
        registry = PluginRegistry()

    plugin_dir = Path(directory)
    if not plugin_dir.exists():
        return registry

    for py_file in sorted(plugin_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue

        try:
            spec = importlib.util.spec_from_file_location(
                f"forge_plugin_{py_file.stem}", py_file
            )
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                # Look for `plugin` variable or `create_plugin()` function
                if hasattr(module, "plugin"):
                    registry.register(module.plugin)
                elif hasattr(module, "create_plugin"):
                    registry.register(module.create_plugin())
                else:
                    # Look for ForgePlugin subclasses
                    for attr in dir(module):
                        obj = getattr(module, attr)
                        if (
                            isinstance(obj, type)
                            and issubclass(obj, ForgePlugin)
                            and obj is not ForgePlugin
                        ):
                            registry.register(obj())
                            break
        except (ImportError, AttributeError, TypeError, OSError) as e:
            logger.warning("Failed to load plugin %s: %s", py_file.name, e)
            console.print(f"[red]Failed to load plugin {py_file.name}: {e}[/]")

    return registry


def discover_plugins(working_dir: str) -> PluginRegistry:
    """Discover plugins from the project's .forge/plugins/ directory."""
    return load_plugins_from_dir(Path(working_dir) / ".forge" / "plugins")


# ─── Built-in Example Plugins ────────────────────────────────


class SecurityCheckPlugin(ForgePlugin):
    """Built-in plugin that checks for common security issues."""

    @property
    def name(self) -> str:
        return "security-check"

    @property
    def description(self) -> str:
        return "Scans for hardcoded secrets and insecure patterns"

    def extra_verify_commands(self, working_dir: str) -> list[str]:
        return []  # Could add `bandit` or `semgrep` here

    def extra_scoring_rules(self, working_dir: str) -> list[tuple[str, int]]:
        """Check for hardcoded secrets in source files."""
        wd = Path(working_dir)
        issues = []

        secret_patterns = [
            "password =", "api_key =", "secret =",
            "AWS_ACCESS_KEY", "PRIVATE_KEY",
        ]

        for py in wd.rglob("*.py"):
            try:
                content = py.read_text(errors="replace").lower()
                for pattern in secret_patterns:
                    if pattern.lower() in content:
                        rel = py.relative_to(wd)
                        issues.append(
                            (f"⚠️  Possible hardcoded secret in {rel}", -3)
                        )
                        break
            except (OSError, PermissionError):
                continue

        if not issues:
            issues.append(("✅ No hardcoded secrets detected", 0))

        return issues


# Global registry for built-in plugins
BUILTIN_PLUGINS = [SecurityCheckPlugin()]
