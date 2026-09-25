"""The Anthropic client, and per-step usage and cost for `job_step`.

Prices are USD per million tokens (and per 1,000 web searches), from the Claude API price
list; they are facts about the provider, not preferences, so they live here. Unknown models
record tokens with a null cost rather than a guess.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import anthropic

from steward.jobs.runner import StepUsage
from steward.settings import Settings

# model -> (input, output) USD per 1M tokens.
PRICES: dict[str, tuple[Decimal, Decimal]] = {
    "claude-haiku-4-5": (Decimal("1"), Decimal("5")),
    "claude-sonnet-5": (Decimal("2"), Decimal("10")),
    "claude-sonnet-4-6": (Decimal("3"), Decimal("15")),
    "claude-opus-5": (Decimal("5"), Decimal("25")),
    "claude-opus-5-5": (Decimal("4"), Decimal("20")),
}
WEB_SEARCH_PER_1000 = Decimal("10")
CACHE_WRITE_FACTOR = Decimal("1.25")
CACHE_READ_FACTOR = Decimal("0.1")


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

    def cost_usd(self) -> Decimal | None:
        price = PRICES.get(self.model)
        if price is None:
            return None
        per_input, per_output = price
        tokens_in = (
            Decimal(self.input_tokens)
            + Decimal(self.cache_write_tokens) * CACHE_WRITE_FACTOR
            + Decimal(self.cache_read_tokens) * CACHE_READ_FACTOR
        )
        cost = tokens_in * per_input / 10**6 + Decimal(self.output_tokens) * per_output / 10**6
        cost += Decimal(self.searches) * WEB_SEARCH_PER_1000 / 1000
        return cost.quantize(Decimal("0.000001"))

    def step_usage(self) -> StepUsage:
        cost = self.cost_usd()
        return StepUsage(
            model=self.model,
            input_tokens=self.input_tokens + self.cache_write_tokens + self.cache_read_tokens,
            output_tokens=self.output_tokens,
            searches=self.searches,
            cost_usd=float(cost) if cost is not None else None,
        )
