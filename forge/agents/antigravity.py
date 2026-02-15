"""Antigravity adapter — Google Gemini API via the google-genai SDK.

Uses the Google Generative AI Python SDK for direct API access to
Gemini models (2.5 Pro, 2.5 Flash, etc.). This is the same model
family that powers Antigravity, providing high-quality code generation
with full control over parameters.

Requires: pip install google-genai
Auth: Set GOOGLE_API_KEY or GEMINI_API_KEY environment variable.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import AsyncIterator

from forge.agents.base import AgentResult, AgentStatus, BaseAdapter, TaskContext


# Pricing per million tokens (approximate, USD)
_PRICING: dict[str, dict[str, float]] = {
    "gemini-2.5-pro": {"input": 1.25, "output": 10.0},
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
}


class AntigravityAdapter(BaseAdapter):
    """Adapter for Google Gemini API via google-genai SDK.

    Provides direct API access to Gemini models with:
    - Full control over temperature, max tokens, system prompt
    - Token usage and cost tracking
    - Both synchronous and streaming execution
    - Agentic mode for code generation with file writes
    """

    name = "antigravity"
    display_name = "Antigravity"

    def __init__(
        self,
        model: str = "gemini-2.5-pro",
        api_key: str | None = None,
        max_tokens: int = 65536,
        temperature: float = 0.7,
        extra_args: list[str] | None = None,
    ):
        self.model = model
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.extra_args = extra_args or []
        self._client = None

    def _get_api_key(self) -> str | None:
        """Resolve the API key from config, env vars, or None."""
        if self.api_key:
            return self.api_key
        return os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")

    def _get_client(self):
        """Lazy-initialize the GenAI client."""
        if self._client is None:
            try:
                from google import genai
                api_key = self._get_api_key()
                if not api_key:
                    return None
                self._client = genai.Client(api_key=api_key)
            except ImportError:
                return None
        return self._client

    def is_available(self) -> bool:
        """Check if google-genai is installed and API key is set."""
        try:
            from google import genai  # noqa: F401
        except ImportError:
            return False
        return self._get_api_key() is not None

    def _estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Estimate cost based on token usage and model pricing."""
        pricing = _PRICING.get(self.model, _PRICING.get("gemini-2.5-flash", {}))
        input_cost = (input_tokens / 1_000_000) * pricing.get("input", 0.15)
        output_cost = (output_tokens / 1_000_000) * pricing.get("output", 0.60)
        return round(input_cost + output_cost, 6)

    async def execute(self, ctx: TaskContext) -> AgentResult:
        """Execute a prompt via the Gemini API."""
        if not self.is_available():
            return self._make_unavailable_result()

        client = self._get_client()
        if client is None:
            return self._make_error_result(
                "Failed to initialize Gemini client. Check GOOGLE_API_KEY.", 0
            )

        start = self._now_ms()

        try:
            from google.genai import types

            # Build configuration
            config = types.GenerateContentConfig(
                max_output_tokens=self.max_tokens,
                temperature=self.temperature,
            )

            # Add system instruction if provided
            if ctx.system_prompt:
                config.system_instruction = ctx.system_prompt

            # Run in a thread to avoid blocking the event loop
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=self.model,
                contents=ctx.prompt,
                config=config,
            )

        except asyncio.TimeoutError:
            return AgentResult(
                agent_name=self.name,
                output="",
                status=AgentStatus.TIMEOUT,
                duration_ms=self._now_ms() - start,
                error=f"Antigravity timed out after {ctx.timeout}s",
            )
        except Exception as e:
            return self._make_error_result(str(e), self._now_ms() - start)

        elapsed = self._now_ms() - start

        # Extract response text
        try:
            output_text = response.text or ""
        except Exception:
            output_text = str(response)

        # Extract usage metadata
        input_tokens = None
        output_tokens = None
        cost_usd = None

        if hasattr(response, "usage_metadata") and response.usage_metadata:
            usage = response.usage_metadata
            input_tokens = getattr(usage, "prompt_token_count", None)
            output_tokens = getattr(usage, "candidates_token_count", None)
            if input_tokens and output_tokens:
                cost_usd = self._estimate_cost(input_tokens, output_tokens)

        return AgentResult(
            agent_name=self.name,
            output=output_text,
            status=AgentStatus.SUCCESS,
            duration_ms=elapsed,
            cost_usd=cost_usd,
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    async def execute_agentic(self, ctx: TaskContext) -> AgentResult:
        """Execute in agentic mode — generates code that should be written to files.

        For Antigravity, agentic mode enhances the prompt to instruct the model
        to produce complete file contents, then writes them to disk.
        """
        # Enhance prompt for file generation
        agentic_prompt = (
            "You are an autonomous coding agent. Your task is to create or modify "
            "files in the working directory to accomplish the objective below.\n\n"
            "For EACH file you create or modify, output it in this exact format:\n\n"
            "=== FILE: <relative/path/to/file> ===\n"
            "<complete file contents>\n"
            "=== END FILE ===\n\n"
            "Output ALL files needed. Include complete file contents, not partial.\n\n"
            f"Working directory: {ctx.working_dir}\n\n"
            f"OBJECTIVE: {ctx.prompt}"
        )

        modified_ctx = TaskContext(
            working_dir=ctx.working_dir,
            prompt=agentic_prompt,
            files=ctx.files,
            system_prompt=ctx.system_prompt or (
                "You are a senior software engineer. Produce production-quality code. "
                "Follow best practices for the detected language and framework."
            ),
            previous_results=ctx.previous_results,
            max_budget_usd=ctx.max_budget_usd,
            timeout=ctx.timeout,
        )

        result = await self.execute(modified_ctx)

        if result.is_success and result.output:
            # Parse and write files from the output
            files_written = self._write_files_from_output(result.output, ctx.working_dir)
            if files_written:
                result.output = (
                    f"Created/modified {len(files_written)} file(s):\n"
                    + "\n".join(f"  - {f}" for f in files_written)
                    + "\n\n" + result.output
                )

        return result

    def _write_files_from_output(self, output: str, working_dir: str) -> list[str]:
        """Parse file blocks from agent output and write them to disk."""
        import re
        from pathlib import Path

        pattern = r"=== FILE: (.+?) ===\n(.*?)(?==== END FILE ===|=== FILE:|\Z)"
        matches = re.findall(pattern, output, re.DOTALL)
        written = []

        for filepath, content in matches:
            filepath = filepath.strip()
            content = content.rstrip("\n") + "\n"

            # Security: prevent path traversal
            if ".." in filepath or filepath.startswith("/"):
                continue

            full_path = Path(working_dir) / filepath
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content)
            written.append(filepath)

        return written

    async def stream(self, ctx: TaskContext) -> AsyncIterator[str]:
        """Stream response chunks from the Gemini API."""
        if not self.is_available():
            yield f"[error] {self.display_name} is not available"
            return

        client = self._get_client()
        if client is None:
            yield "[error] Failed to initialize Gemini client"
            return

        try:
            from google.genai import types

            config = types.GenerateContentConfig(
                max_output_tokens=self.max_tokens,
                temperature=self.temperature,
            )

            if ctx.system_prompt:
                config.system_instruction = ctx.system_prompt

            # Use streaming — run in thread since the SDK is sync
            def _stream():
                return client.models.generate_content_stream(
                    model=self.model,
                    contents=ctx.prompt,
                    config=config,
                )

            response_stream = await asyncio.to_thread(_stream)

            for chunk in response_stream:
                try:
                    text = chunk.text
                    if text:
                        yield text
                except Exception:
                    continue

        except Exception as e:
            yield f"[error] {e}"
