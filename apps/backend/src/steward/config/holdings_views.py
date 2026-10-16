# ruff: noqa: E501 - the column list reads better packed
"""Holdings table views (insight plan I9.3): the selected preset and the owner's custom columns,
versioned like every other config domain. The presets themselves are fixed in the web app's
column registry; only the choice and the custom column order are stored here."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from steward.config.common import ConfigModel

# Every column the web app's registry offers (apps/web/src/lib/portfolio/holding-columns.ts).
COLUMN_IDS: frozenset[str] = frozenset(
    {
        "holding", "quantity", "avg_price", "price", "day_move", "spark", "value", "weight", "drift", "ppl",
        "last_event", "fair_vs_price", "fair_value", "thesis", "fair_spark",
        "yield", "yield_on_cost", "next_payment", "income_12m", "dividend_quality",
        "mark", "score_value", "score_future", "score_past", "score_health", "score_dividend",
        "net_margin", "operating_margin", "gross_margin", "past_growth", "forecast_growth", "revenue_growth",
        "debt_to_equity", "net_debt_to_equity", "roce", "roe", "roic",
        "pe", "forward_pe", "pb", "ps", "fcf_yield", "ev_ebit", "earnings_yield",
        "return_1y", "volatility", "momentum", "share_change", "market_cap", "notes",
    }
)  # fmt: skip
PRESETS = ("check_in", "holding", "income", "growth", "value", "score", "fundamentals", "custom")
DEFAULT_CUSTOM = [
    "holding",
    "price",
    "day_move",
    "value",
    "weight",
    "fair_vs_price",
    "mark",
    "notes",
]


class HoldingsViewsConfig(ConfigModel):
    schema_version: Literal[1] = 1
    selected: Literal[
        "check_in", "holding", "income", "growth", "value", "score", "fundamentals", "custom"
    ] = "holding"
    custom_columns: list[str] = Field(
        default_factory=lambda: list(DEFAULT_CUSTOM), min_length=1, max_length=20
    )
    basis: str = Field(default="headline", description="Fair-value basis the value columns use; headline: each holding's best available.")

    @field_validator("custom_columns")
    @classmethod
    def _known(cls, v: list[str]) -> list[str]:
        unknown = [c for c in v if c not in COLUMN_IDS]
        if unknown:
            raise ValueError(f"unknown columns: {', '.join(unknown)}")
        if len(set(v)) != len(v):
            raise ValueError("a column appears twice")
        if "holding" not in v:
            raise ValueError("the holding column is always shown")
        return v
