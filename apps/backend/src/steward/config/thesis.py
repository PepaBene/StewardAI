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
    bear_case: float | None = Field(
        default=None,
        gt=0,
        description="Price the stock would fall to if the thesis fails, in the same currency. "
        "The pre-trade asymmetry check and the scorecard's risk section read it.",
    )
    currency: CurrencyCode = Field(
        description="Currency of all the prices, as Trading212 quotes it (GBX for pence)."
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


MainReason = Literal["quality", "value", "catalyst", "momentum", "other"]


class ThesisBaseRate(ConfigModel):
    """How often companies of this size and sector have achieved the growth the price assumes."""

    share_achieved: float = Field(
        ge=0, le=1, description="Share of comparable companies that reached that growth (0-1)."
    )
    comparison: str = Field(
        min_length=1,
        max_length=300,
        examples=["US software, 5-10 bn revenue, 10-year CAGR above 12%"],
        description="Which companies, which period and which growth were compared.",
    )
    source: str = Field(min_length=1, max_length=300, description="Where the figure comes from.")


class ThesisVariantPerception(ConfigModel):
    """What the owner believes that the market does not, and what would prove it wrong."""

    belief: str = Field(min_length=1, max_length=1000)
    disproof: str = Field(
        min_length=1,
        max_length=1000,
        description="The evidence that would show the belief is wrong.",
    )


class ThesisConfig(ConfigModel):
    ticker: Ticker
    why_owned: str = Field(min_length=1)
    kpis: list[ThesisKPI] = Field(default_factory=list)
    exit_conditions: list[str] = Field(
        default_factory=list, description="Conditions that would end the thesis, in plain words."
    )
    review_date: dt.date | None = None
    price_target: ThesisPriceTarget | None = None
    implied_growth: float | None = Field(
        default=None,
        description="Yearly growth the current price implies (reverse DCF), as a fraction; "
        "filled from the valuation workbench when available, otherwise entered by the owner.",
    )
    base_rate: ThesisBaseRate | None = None
    pre_mortem: list[str] = Field(
        default_factory=list,
        max_length=3,
        description="The likeliest reasons the stock loses 40%, in plain words.",
    )
    variant_perception: ThesisVariantPerception | None = None
    conviction: int | None = Field(default=None, ge=1, le=5)
    main_reason: MainReason | None = None

    @field_validator("pre_mortem")
    @classmethod
    def _pre_mortem_text(cls, value: list[str]) -> list[str]:
        cleaned = [line.strip() for line in value]
        if any(not line for line in cleaned):
            raise ValueError("a pre-mortem reason cannot be blank")
        return cleaned

    @model_validator(mode="after")
    def _unique_kpi_ids(self) -> Self:
        require_unique(f"kpi id in thesis {self.ticker}", [k.id for k in self.kpis])
        return self

    def missing_requirements(self) -> list[str]:
        """The strategy's thesis requirements this thesis does not yet meet, in plain words."""
        gaps: list[str] = []
        if self.implied_growth is None:
            gaps.append("the growth the price implies (reverse DCF)")
        if self.base_rate is None:
            gaps.append("a base-rate check for that growth")
        if len(self.pre_mortem) < 3:
            gaps.append("a pre-mortem with three reasons")
        if self.variant_perception is None:
            gaps.append("a variant perception and what would disprove it")
        if self.conviction is None:
            gaps.append("a conviction level")
        if self.main_reason is None:
            gaps.append("the main reason for owning it")
        return gaps


class ThesesConfig(ConfigModel):
    """All theses. Stored as the body of `config_version` rows, domain `theses`."""

    schema_version: Literal[1] = 1
    theses: list[ThesisConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_thesis_per_ticker(self) -> Self:
        require_unique("thesis ticker", [t.ticker for t in self.theses])
        return self
