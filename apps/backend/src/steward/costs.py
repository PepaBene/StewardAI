"""Cost of one AI step from its token and search counts and the Budget config's prices.

A step's cost is recomputable by hand from its `job_step` row: the counts are stored in
their own columns and the prices used are stored with them (`job_step.prices`).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from steward.config.budget import BudgetConfig

MILLION = Decimal(10) ** 6


def _d(value: float) -> Decimal:
    return Decimal(str(value))


def _first(override: float | None, default: float) -> float:
    return default if override is None else override


@dataclass(frozen=True)
class PriceTable:
    config: BudgetConfig

    def prices_for(self, model: str) -> dict[str, Any] | None:
        """The prices applied to a step of `model`, as recorded on the step; None when the
        model has no price (the cost is then left null, never guessed)."""
        price = next((p for p in self.config.prices if p.model == model), None)
        if price is None:
            return None
        return {
            "model": model,
            "input_per_mtok": price.input_per_mtok,
            "output_per_mtok": price.output_per_mtok,
            "cache_write_multiplier": _first(
                price.cache_write_multiplier, self.config.cache_write_multiplier
            ),
            "cache_read_multiplier": _first(
                price.cache_read_multiplier, self.config.cache_read_multiplier
            ),
            "batch_multiplier": self.config.batch_multiplier,
            "web_search_per_1000": _first(
                price.web_search_per_1000, self.config.web_search_per_1000
            ),
        }

    def cost(
        self,
        model: str,
        *,
        input_tokens: int,
        output_tokens: int,
        cache_write_tokens: int = 0,
        cache_read_tokens: int = 0,
        searches: int = 0,
        batch: bool = False,
    ) -> Decimal | None:
        prices = self.prices_for(model)
        if prices is None:
            return None
        return cost_from_prices(
            prices,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_write_tokens=cache_write_tokens,
            cache_read_tokens=cache_read_tokens,
            searches=searches,
            batch=batch,
        )


def cost_from_prices(
    prices: dict[str, Any],
    *,
    input_tokens: int,
    output_tokens: int,
    cache_write_tokens: int,
    cache_read_tokens: int,
    searches: int,
    batch: bool,
) -> Decimal:
    per_in = _d(prices["input_per_mtok"])
    per_out = _d(prices["output_per_mtok"])
    tokens_in = (
        Decimal(input_tokens)
        + Decimal(cache_write_tokens) * _d(prices["cache_write_multiplier"])
        + Decimal(cache_read_tokens) * _d(prices["cache_read_multiplier"])
    )
    cost = tokens_in * per_in / MILLION + Decimal(output_tokens) * per_out / MILLION
    if batch:
        cost *= _d(prices["batch_multiplier"])
    cost += Decimal(searches) * _d(prices["web_search_per_1000"]) / 1000
    return cost.quantize(Decimal("0.000001"))


DEFAULT_PRICES = PriceTable(BudgetConfig())
