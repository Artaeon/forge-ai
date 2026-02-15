"""Context compaction — minimize token usage between agent rounds.

Instead of passing full file trees, diffs, and raw outputs between agents,
this module compresses context into concise summaries that carry the
essential information in minimal tokens.
"""

from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class CompactContext:
    """Minimal project context optimized for low token count."""
    working_dir: str
    language: str = "unknown"
    framework: str | None = None
    file_count: int = 0
    file_list: list[str] = field(default_factory=list)  # max ~20 essential files
    achievements: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    def to_prompt(self) -> str:
        """Render as minimal prompt section (~200-500 tokens max)."""
        parts = [f"Dir: {self.working_dir}"]

        if self.language != "unknown":
            tech = self.language
            if self.framework:
                tech += f"/{self.framework}"
            parts.append(f"Stack: {tech}")

        if self.file_list:
            files_str = ", ".join(self.file_list[:20])
            parts.append(f"Files ({self.file_count}): {files_str}")

        if self.achievements:
            parts.append("Done: " + "; ".join(self.achievements[-5:]))

        if self.issues:
            parts.append("Open issues: " + "; ".join(self.issues[-5:]))

        return "\n".join(parts)


def gather_compact(working_dir: str) -> CompactContext:
    """Gather minimal project context — optimized for small token footprint.

    Unlike gather_context(), this:
    - Lists only essential files (not full tree)
    - Skips git diff entirely
    - Skips file contents entirely
    - Returns ~100-300 tokens instead of ~5000-15000
    """
    wd = Path(working_dir)
    ctx = CompactContext(working_dir=working_dir)

    if not wd.exists():
        return ctx

    # Get file list (compact — just names, max 30)
    skip = {".git", "__pycache__", "node_modules", ".venv", "venv",
            ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
    all_files = []
    for p in sorted(wd.rglob("*")):
        if p.is_file():
            rel = p.relative_to(wd)
            if not any(part in skip or part.startswith(".") for part in rel.parts):
                all_files.append(str(rel))

    ctx.file_count = len(all_files)
    ctx.file_list = all_files[:30]

    # Detect language (quick)
    markers = {
        "pyproject.toml": "python", "setup.py": "python", "requirements.txt": "python",
        "package.json": "javascript", "tsconfig.json": "typescript",
        "go.mod": "go", "Cargo.toml": "rust",
    }
    for marker, lang in markers.items():
        if (wd / marker).exists():
            ctx.language = lang
            break

    # Detect framework (quick scan of deps)
    if ctx.language == "python":
        for dep_file in ["requirements.txt", "pyproject.toml"]:
            fpath = wd / dep_file
            if fpath.exists():
                try:
                    content = fpath.read_text(errors="replace")[:1000].lower()
                    for fw in ["flask", "fastapi", "django"]:
                        if fw in content:
                            ctx.framework = fw
                            break
                except Exception:
                    pass
                break
    elif ctx.language in ("javascript", "typescript"):
        pkg = wd / "package.json"
        if pkg.exists():
            try:
                content = pkg.read_text(errors="replace")[:1000].lower()
                for fw in ["next", "express", "react", "vue"]:
                    if fw in content:
                        ctx.framework = fw
                        break
            except Exception:
                pass

    return ctx


def summarize_round(agent_name: str, phase: str, output: str, max_chars: int = 500) -> str:
    """Compress a round's output into a concise summary.

    Instead of passing the full 5000-char output to the next agent,
    extract the key points in ~100-200 tokens.
    """
    if not output:
        return f"{agent_name} ({phase}): no output"

    # For reviews: extract bullet points / numbered items
    lines = output.strip().split("\n")
    key_lines = []

    for line in lines:
        stripped = line.strip()
        # Keep lines that look like action items, headings, or key findings
        if any([
            stripped.startswith(("-", "*", "•")),          # bullet points
            stripped.startswith(tuple("0123456789")),       # numbered items
            stripped.startswith("#"),                        # headings
            stripped.startswith("APPROVED"),                 # approval
            "error" in stripped.lower(),                     # errors
            "fix" in stripped.lower(),                       # fixes
            "missing" in stripped.lower(),                   # missing items
            "create" in stripped.lower(),                    # actions
            "add" in stripped.lower(),                       # additions
            "bug" in stripped.lower(),                       # bugs
        ]):
            key_lines.append(stripped)

    if key_lines:
        summary = "\n".join(key_lines[:15])
    else:
        # Fallback: first and last few lines
        if len(lines) <= 5:
            summary = output.strip()
        else:
            summary = "\n".join(lines[:3]) + "\n...\n" + "\n".join(lines[-2:])

    # Hard truncate
    if len(summary) > max_chars:
        summary = summary[:max_chars] + "..."

    return summary


def build_history_summary(rounds: list[dict], max_total: int = 1000) -> str:
    """Build a compact history from previous rounds.

    Each round is a dict with: agent_name, phase, output.
    Returns a concise summary of all rounds in ~200-400 tokens.
    """
    if not rounds:
        return ""

    parts = []
    per_round = max(100, max_total // max(len(rounds), 1))

    for r in rounds:
        summary = summarize_round(
            r.get("agent_name", "?"),
            r.get("phase", "?"),
            r.get("output", ""),
            max_chars=per_round,
        )
        parts.append(f"[{r.get('phase', '?')}] {summary}")

    result = "\n\n".join(parts)
    if len(result) > max_total:
        result = result[:max_total] + "\n..."

    return result
