"""The Anthropic client, and per-step usage counts for `job_step`.

The cost of a step is computed by the job runner from these counts and the Budget config's
price table (see `steward.costs`), so a price change needs no deploy.
"""

from dataclasses import dataclass
from typing import Any

import anthropic

from steward.jobs.runner import StepUsage
from steward.settings import Settings


class AINotConfigured(Exception):
    pass


def create_client(settings: Settings) -> anthropic.AsyncAnthropic:
    if settings.anthropic_api_key is None:
        raise AINotConfigured("ANTHROPIC_API_KEY is not set")
    return anthropic.AsyncAnthropic(
        api_key=settings.anthropic_api_key.get_secret_value(), max_retries=3, timeout=300.0
    )


@dataclass
class Usage:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    searches: int = 0
    fetches: int = 0
    # Batch API requests are billed at half price.
    batch: bool = False

    def add(self, usage: Any) -> None:
        """Add a response's `usage` block."""
        self.input_tokens += usage.input_tokens or 0
        self.output_tokens += usage.output_tokens or 0
        self.cache_write_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0
        self.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        server = getattr(usage, "server_tool_use", None)
        if server is not None:
            self.searches += getattr(server, "web_search_requests", 0) or 0
            self.fetches += getattr(server, "web_fetch_requests", 0) or 0

    def step_usage(self) -> StepUsage:
        return StepUsage(
            model=self.model,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cache_write_tokens=self.cache_write_tokens,
            cache_read_tokens=self.cache_read_tokens,
            searches=self.searches,
            batch=self.batch,
        )
