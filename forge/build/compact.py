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


# ─── Smart File Chunking ──────────────────────────────────────


@dataclass
class FileChunk:
    """A chunk of a file with metadata."""
    path: str
    content: str
    start_line: int
    end_line: int
    priority: int  # 0=highest priority

    @property
    def token_estimate(self) -> int:
        """Rough token estimate (~4 chars per token)."""
        return len(self.content) // 4


def _file_priority(path: str) -> int:
    """Score file importance (lower = more important)."""
    name = Path(path).name.lower()
    # Config files are critical context
    if name in ("pyproject.toml", "package.json", "cargo.toml", "go.mod"):
        return 0
    if name in ("readme.md", "readme.rst", "readme.txt"):
        return 1
    # Entry points
    if name in ("main.py", "app.py", "index.js", "index.ts", "main.go"):
        return 2
    if name in ("cli.py", "server.py", "__init__.py"):
        return 3
    # Test files are lower priority for code generation
    if "test" in name:
        return 7
    # Regular source files
    return 5


def chunk_file(path: str, content: str, max_chunk_chars: int = 2000) -> list[FileChunk]:
    """Split a file into semantic chunks at function/class boundaries.

    For files under max_chunk_chars, returns the whole file as one chunk.
    For larger files, splits at class/function definitions.
    """
    if len(content) <= max_chunk_chars:
        return [FileChunk(
            path=path, content=content,
            start_line=1, end_line=content.count("\n") + 1,
            priority=_file_priority(path),
        )]

    lines = content.split("\n")
    chunks: list[FileChunk] = []
    chunk_lines: list[str] = []
    chunk_start = 1

    for i, line in enumerate(lines, 1):
        # Split at class/function definitions when chunk is big enough
        is_boundary = (
            line.startswith(("def ", "class ", "async def "))
            or line.startswith(("function ", "export function ", "export default "))
            or line.startswith(("func ", "type ", "struct "))
        )

        if is_boundary and len("\n".join(chunk_lines)) > max_chunk_chars // 2:
            chunks.append(FileChunk(
                path=path,
                content="\n".join(chunk_lines),
                start_line=chunk_start,
                end_line=i - 1,
                priority=_file_priority(path),
            ))
            chunk_lines = []
            chunk_start = i

        chunk_lines.append(line)

    # Final chunk
    if chunk_lines:
        chunks.append(FileChunk(
            path=path,
            content="\n".join(chunk_lines),
            start_line=chunk_start,
            end_line=len(lines),
            priority=_file_priority(path),
        ))

    return chunks


def select_context_window(
    working_dir: str,
    token_budget: int = 8000,
    focus_files: list[str] | None = None,
) -> str:
    """Select the most relevant file content within a token budget.

    Prioritizes: focus_files > config > entry points > source.
    Each file is chunked at semantic boundaries and included
    until the budget is exhausted.
    """
    wd = Path(working_dir)
    if not wd.exists():
        return ""

    skip = {".git", "__pycache__", "node_modules", ".venv", "venv",
            ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache"}

    # Gather all file chunks
    all_chunks: list[FileChunk] = []

    for p in sorted(wd.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(wd))
        if any(part in skip or part.startswith(".") for part in Path(rel).parts):
            continue
        # Skip binary files
        if p.suffix in (".pyc", ".pyo", ".so", ".dll", ".exe", ".whl", ".egg"):
            continue
        try:
            content = p.read_text(errors="replace")
        except Exception:
            continue

        chunks = chunk_file(rel, content)

        # Boost priority of focus files
        if focus_files and rel in focus_files:
            for c in chunks:
                c.priority = -1  # Highest priority

        all_chunks.extend(chunks)

    # Sort by priority (lower = more important)
    all_chunks.sort(key=lambda c: (c.priority, c.start_line))

    # Select chunks within budget
    selected: list[FileChunk] = []
    remaining = token_budget

    for chunk in all_chunks:
        tokens = chunk.token_estimate
        if tokens <= remaining:
            selected.append(chunk)
            remaining -= tokens
        elif remaining > 200:
            # Partial include: truncate
            chars = remaining * 4
            truncated = FileChunk(
                path=chunk.path,
                content=chunk.content[:chars] + "\n... (truncated)",
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                priority=chunk.priority,
            )
            selected.append(truncated)
            break
        else:
            break

    # Format output
    parts = []
    current_file = None
    for chunk in selected:
        if chunk.path != current_file:
            current_file = chunk.path
            parts.append(f"\n--- {chunk.path} (L{chunk.start_line}-{chunk.end_line}) ---")
        parts.append(chunk.content)

    return "\n".join(parts)

