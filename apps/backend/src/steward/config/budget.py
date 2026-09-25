"""Budget config: the model price table every AI step is costed with.

Prices are the provider's list prices, but they change and differ per account (discounts,
new models), so they are config: a step's cost is its tokens and searches times the prices
here, recorded next to the step. Spend caps and the behaviour at the cap arrive with the
Phase 6 budget rules and are added here then (with defaults, so saved versions stay valid).
"""

from typing import Literal, Self

from pydantic import Field, model_validator

from steward.config.common import ConfigModel, require_unique
from steward.config.jobs import ModelId


class ModelPrice(ConfigModel):
    model: ModelId
    input_per_mtok: float = Field(ge=0, description="USD per million input tokens.")
    output_per_mtok: float = Field(ge=0, description="USD per million output tokens.")


def _default_prices() -> list[ModelPrice]:
    table = {
        "claude-haiku-4-5": (1, 5),
        "claude-sonnet-5": (2, 10),
        "claude-sonnet-4-6": (3, 15),
        "claude-opus-5": (5, 25),
        "claude-opus-5-5": (4, 20),
    }
    return [ModelPrice(model=m, input_per_mtok=i, output_per_mtok=o) for m, (i, o) in table.items()]


class BudgetConfig(ConfigModel):
    """Stored as `config_version` rows, domain `budget`."""

    schema_version: Literal[1] = 1
    prices: list[ModelPrice] = Field(default_factory=_default_prices)
    web_search_per_1000: float = Field(default=10, ge=0, description="USD per 1,000 web searches.")
    cache_write_multiplier: float = Field(
        default=1.25, ge=0, description="Cache-write tokens cost this times the input price."
    )
    cache_read_multiplier: float = Field(
        default=0.1, ge=0, description="Cache-read tokens cost this times the input price."
    )
    batch_multiplier: float = Field(
        default=0.5,
        ge=0,
        le=1,
        description="Batch API requests cost this times the token price (searches excluded).",
    )

    @model_validator(mode="after")
    def _one_price_per_model(self) -> Self:
        require_unique("price model", [p.model for p in self.prices])
        return self
