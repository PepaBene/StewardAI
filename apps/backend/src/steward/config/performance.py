"""Config domain `performance`: the owner's settings for performance and holding-period tracking
(core ETFs, cost-basis method, the time-test length and annual exemption limit, the sleeve gap
and window, alert severity and the benchmark blend). Thresholds are the owner's own, not tax
advice. Stored as `config_version` rows, so history, diff, rollback, export and import apply."""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from steward.performance.benchmark import WEIGHT_TOLERANCE, Basis, Rebalance
from steward.performance.lots import CostMethod

Ticker = str
_CURRENCY = re.compile(r"^[A-Z]{3}$")


class BenchmarkComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=40, description="Price series symbol.")
    weight: Decimal = Field(gt=0, le=1)
    currency: str = Field(description="Quote currency of the series (ISO code).")
    basis: Basis = Field(
        default="unknown", description="Whether the series includes dividends (total) or not."
    )

    @field_validator("currency")
    @classmethod
    def _iso(cls, value: str) -> str:
        if not _CURRENCY.match(value):
            raise ValueError("currency must be a three-letter ISO code")
        return value


class BenchmarkSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    components: list[BenchmarkComponent] = Field(min_length=1, max_length=8)
    rebalance: Rebalance = "none"

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> BenchmarkSpec:
        total = sum((c.weight for c in self.components), Decimal(0))
        if abs(total - 1) > WEIGHT_TOLERANCE:
            raise ValueError("benchmark weights must sum to 1")
        if len({c.symbol for c in self.components}) != len(self.components):
            raise ValueError("a benchmark symbol appears twice")
        if total != 1:
            # Rounded entries such as 33.33333 x 3 are scaled to sum to exactly 1.
            for c in self.components:
                c.weight = c.weight / total
        return self


class SplitEntry(BaseModel):
    """A split or reverse split you entered. The broker's fills are not restated for it, so
    without this a split shows up as a position the fills cannot explain."""

    model_config = ConfigDict(extra="forbid")
    ticker: Ticker = Field(min_length=1, max_length=40)
    day: dt.date = Field(description="First day the new share count applies.")
    ratio: Decimal = Field(
        gt=0,
        description="New shares per old share: 2 for a 2-for-1 split, 0.1 for a 1-for-10 "
        "reverse split.",
    )
    note: str = Field(default="", max_length=200)

    @field_validator("ratio")
    @classmethod
    def _not_one(cls, value: Decimal) -> Decimal:
        if value == 1:
            raise ValueError("a split ratio of 1 changes nothing")
        return value


class MergerEntry(BaseModel):
    """A share-exchange merger you entered: each old share became `ratio` new shares. Earlier
    fills of the old ticker are carried over to the new one with their cost and purchase date."""

    model_config = ConfigDict(extra="forbid")
    old_ticker: Ticker = Field(min_length=1, max_length=40)
    new_ticker: Ticker = Field(min_length=1, max_length=40)
    day: dt.date = Field(description="First day the new ticker applies.")
    ratio: Decimal = Field(gt=0, description="New shares per old share.")
    note: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def _different(self) -> MergerEntry:
        if self.old_ticker == self.new_ticker:
            raise ValueError("a merger needs two different tickers")
        return self


class SpinOffEntry(BaseModel):
    """A spin-off you entered: holders on `day` received `ratio` child shares per parent share,
    and `cost_share` of the parent's cost moved to the child. The company's own notice gives the
    split of cost; Steward does not look it up."""

    model_config = ConfigDict(extra="forbid")
    parent_ticker: Ticker = Field(min_length=1, max_length=40)
    child_ticker: Ticker = Field(min_length=1, max_length=40)
    day: dt.date = Field(description="First day the child shares exist.")
    ratio: Decimal = Field(gt=0, description="Child shares per parent share.")
    cost_share: Decimal = Field(
        ge=0, le=1, description="Fraction of the parent's cost that moves to the child (0 to 1)."
    )
    note: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def _different(self) -> SpinOffEntry:
        if self.parent_ticker == self.child_ticker:
            raise ValueError("a spin-off needs two different tickers")
        return self


class PerformanceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    core_tickers: list[Ticker] = Field(
        default_factory=list,
        max_length=200,
        description="Trading212 tickers that form the core ETF sleeve; every other holding is a "
        "stock (satellite).",
    )
    cost_method: CostMethod = "fifo"
    time_test_years: int = Field(default=3, ge=1, le=10)
    exemption_limit: Decimal = Field(default=Decimal(100000), ge=0)
    exemption_currency: str = "CZK"
    time_test_warning_months: int = Field(default=6, ge=1, le=24)
    sleeve_gap_points: Decimal = Field(default=Decimal(2), ge=0, le=50)
    sleeve_lookback_years: int = Field(default=2, ge=1, le=10)
    cash_tolerance: Decimal = Field(
        default=Decimal(1),
        ge=0,
        description="Largest unexplained daily cash change accepted, in the account currency.",
    )
    alert_severity: int = Field(
        default=2,
        ge=0,
        le=3,
        description="Severity of the alert raised with the monthly review when stocks trail the "
        "core ETFs or a lot's sell target is reached early. It is sent when a notification rule "
        "matches that severity (the default rules send severity 3 only; otherwise it waits for "
        "the digest). 0 turns these alerts off.",
    )
    benchmark: BenchmarkSpec | None = None
    splits: list[SplitEntry] = Field(default_factory=list, max_length=200)
    mergers: list[MergerEntry] = Field(default_factory=list, max_length=100)
    spin_offs: list[SpinOffEntry] = Field(default_factory=list, max_length=100)
    note: str = Field(default="", max_length=500)

    @field_validator("exemption_currency")
    @classmethod
    def _iso(cls, value: str) -> str:
        if not _CURRENCY.match(value):
            raise ValueError("currency must be a three-letter ISO code")
        return value

    @field_validator("core_tickers")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        cleaned = [t.strip() for t in value if t.strip()]
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("a core ticker is listed twice")
        return cleaned
