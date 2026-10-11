"""Per-step usage counts for `job_step`.

The cost of a step is computed by the job runner from these counts and the Budget config's
price table (see `steward.costs`), so a price change needs no deploy.

Every model call goes through `steward.llm.client.LLM`: inside a job, each request is checked
against the Budget config's caps before it is sent, and may be sent to a cheaper model at the
monthly cap (see `steward.budget_guard`). Every call site gets its client from `create_llm`
and `routing_client`, so the caps hold without any call site knowing about them.
"""

from dataclasses import dataclass
from typing import Any

from steward.jobs.runner import StepUsage
from steward.llm.types import AINotConfigured

__all__ = ["AINotConfigured", "Usage"]


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
