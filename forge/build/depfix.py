"""Dependency resolution for common import/module errors.

Detects ModuleNotFoundError and similar patterns in error output,
identifies the missing package, and attempts to install it.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


# Map of common module names to pip package names
_MODULE_TO_PACKAGE = {
    "PIL": "Pillow",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "yaml": "PyYAML",
    "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv",
    "gi": "PyGObject",
    "attr": "attrs",
    "serial": "pyserial",
    "usb": "pyusb",
    "jwt": "PyJWT",
    "lxml": "lxml",
    "magic": "python-magic",
    "dateutil": "python-dateutil",
}


def extract_missing_modules(error_text: str) -> list[str]:
    """Extract missing module names from error output.

    Handles:
    - ModuleNotFoundError: No module named 'foo'
    - ImportError: cannot import name 'bar' from 'foo'
    - Cannot find module 'foo'  (Node.js)
    """
    modules = set()

    # Python: ModuleNotFoundError
    for match in re.finditer(
        r"ModuleNotFoundError:\s*No module named ['\"]([^'\"]+)['\"]",
        error_text,
    ):
        mod = match.group(1).split(".")[0]  # Top-level package only
        modules.add(mod)

    # Python: ImportError
    for match in re.finditer(
        r"ImportError:\s*cannot import name .+ from ['\"]([^'\"]+)['\"]",
        error_text,
    ):
        mod = match.group(1).split(".")[0]
        modules.add(mod)

    # Node.js: Cannot find module
    for match in re.finditer(
        r"Cannot find module ['\"]([^'\"]+)['\"]",
        error_text,
    ):
        mod = match.group(1)
        if not mod.startswith(".") and not mod.startswith("/"):
            modules.add(mod)

    return sorted(modules)


def resolve_missing_deps(
    working_dir: str,
    error_text: str,
) -> list[str]:
    """Detect and install missing dependencies from error output.

    Returns list of packages that were successfully installed.
    """
    modules = extract_missing_modules(error_text)
    if not modules:
        return []

    wd = Path(working_dir)
    installed = []

    # Detect project type
    is_python = (
        (wd / "pyproject.toml").exists()
        or (wd / "setup.py").exists()
        or (wd / "requirements.txt").exists()
        or any((wd / f).exists() for f in wd.glob("*.py"))
    )
    is_node = (wd / "package.json").exists()

    for module in modules:
        # Map module name to package name
        package = _MODULE_TO_PACKAGE.get(module, module)

        if is_python:
            try:
                result = subprocess.run(
                    ["pip", "install", package, "-q"],
                    cwd=working_dir, capture_output=True, text=True, timeout=60,
                )
                if result.returncode == 0:
                    installed.append(package)
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
                logger.debug("Dependency install failed for %s: %s", package, e)

        elif is_node:
            try:
                result = subprocess.run(
                    ["npm", "install", package, "--save"],
                    cwd=working_dir, capture_output=True, text=True, timeout=60,
                )
                if result.returncode == 0:
                    installed.append(package)
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
                logger.debug("Dependency install failed for %s: %s", package, e)

    return installed
