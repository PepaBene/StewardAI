"""Thesis config: why each holding is owned, the KPIs that must hold, and what would end it."""

import datetime as dt
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from steward.config.common import (
    ComparisonOperator,
    ConfigModel,
    CurrencyCode,
    Slug,
    Ticker,
    require_unique,
)
from steward.earnings.units import parse_kpi_unit


class ThesisKPI(ConfigModel):
    """An absolute threshold the owner sets. v1 has no consensus-estimate comparisons."""

    id: Slug
    metric: str = Field(
        pattern=r"^[a-z][a-z0-9_]{0,63}$",
        examples=["revenue_growth_yoy"],
        description="Metric name the extraction job looks for.",
    )
    operator: ComparisonOperator
    threshold: float
    unit: str = Field(
        min_length=1,
        max_length=32,
        examples=["%", "EUR bn"],
        description="What the threshold is in: '%', 'x', 'count', or a currency with an "
        "optional scale ('EUR bn', 'USD m', 'USD' for per-share amounts).",
    )
    basis: Literal["reported", "adjusted"] = "reported"
    period: Literal["quarter", "annual", "ttm"] = "quarter"
    description: str = ""

    @field_validator("unit")
    @classmethod
    def _known_unit(cls, value: str) -> str:
        parse_kpi_unit(value)
        return value


class ThesisPriceTarget(ConfigModel):
    """The price levels the thesis's valuation sets, checked after each sync against the
    Trading212 price. Reaching the sell target asks for the research to be re-run: sell
    unless the new thesis raises the target. At or below the buy target the holding is a
    candidate to add to."""

    sell_target: float | None = Field(
        default=None, gt=0, description="Price at which the thesis has played out."
    )
    buy_target: float | None = Field(
        default=None, gt=0, description="Price at or below which buying more is attractive."
    )
    currency: CurrencyCode = Field(
        description="Currency of both targets, as Trading212 quotes the instrument (GBX for pence)."
    )
    method: str = Field(
        min_length=1,
        max_length=200,
        examples=["DCF, 10% discount rate", "20x 2027 EPS"],
        description="Valuation method the targets come from.",
    )
    set_on: dt.date = Field(description="Date of the valuation the targets come from.")

    def describe(self) -> str:
        """'sell 260 USD, buy 180 USD (DCF, set 2026-10-01)', for prompts and evidence."""
        levels = [
            f"{side} {value:g} {self.currency}"
            for side, value in (("sell", self.sell_target), ("buy", self.buy_target))
            if value is not None
        ]
        return f"{', '.join(levels)} ({self.method}, set {self.set_on.isoformat()})"

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.sell_target is None and self.buy_target is None:
            raise ValueError("set a sell target, a buy target or both")
        if (
            self.sell_target is not None
            and self.buy_target is not None
            and self.buy_target >= self.sell_target
        ):
            raise ValueError("the buy target must be below the sell target")
        return self


class ThesisConfig(ConfigModel):
    ticker: Ticker
    why_owned: str = Field(min_length=1)
    kpis: list[ThesisKPI] = Field(default_factory=list)
    exit_conditions: list[str] = Field(
        default_factory=list, description="Conditions that would end the thesis, in plain words."
    )
    review_date: dt.date | None = None
    price_target: ThesisPriceTarget | None = None

    @model_validator(mode="after")
    def _unique_kpi_ids(self) -> Self:
        require_unique(f"kpi id in thesis {self.ticker}", [k.id for k in self.kpis])
        return self


class ThesesConfig(ConfigModel):
    """All theses. Stored as the body of `config_version` rows, domain `theses`."""

    schema_version: Literal[1] = 1
    theses: list[ThesisConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_thesis_per_ticker(self) -> Self:
        require_unique("thesis ticker", [t.ticker for t in self.theses])
        return self
