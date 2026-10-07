"""Owner settings for performance and holding-period tracking, stored append-only in
`public.performance_setting` (newest row wins; older rows say what a past result used)."""

from __future__ import annotations

import json
import re
from decimal import Decimal

import asyncpg
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from steward.performance.benchmark import Basis, Rebalance
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
        if abs(sum((c.weight for c in self.components), Decimal(0)) - 1) > Decimal("0.000001"):
            raise ValueError("benchmark weights must sum to 1")
        if len({c.symbol for c in self.components}) != len(self.components):
            raise ValueError("a benchmark symbol appears twice")
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
    benchmark: BenchmarkSpec | None = None
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


async def load_settings(
    pool: asyncpg.Pool, owner_id: str
) -> tuple[PerformanceSettings, int | None]:
    """The owner's newest settings and its row id (None when never saved: the defaults)."""
    row = await pool.fetchrow(
        "select id, settings from public.performance_setting where owner_id = $1::uuid"
        " order by id desc limit 1",
        owner_id,
    )
    if row is None:
        return PerformanceSettings(), None
    raw = row["settings"]
    body = json.loads(raw) if isinstance(raw, str) else raw
    return PerformanceSettings.model_validate(body), int(row["id"])


async def load_latest_settings(pool: asyncpg.Pool) -> PerformanceSettings:
    """The newest saved settings of any owner (the app has one), or the defaults. For jobs that
    run without a signed-in user."""
    raw = await pool.fetchval(
        "select settings from public.performance_setting order by id desc limit 1"
    )
    if raw is None:
        return PerformanceSettings()
    return PerformanceSettings.model_validate(json.loads(raw) if isinstance(raw, str) else raw)


async def save_settings(pool: asyncpg.Pool, owner_id: str, settings: PerformanceSettings) -> int:
    row_id = await pool.fetchval(
        "insert into public.performance_setting (owner_id, settings) values ($1::uuid, $2::jsonb)"
        " returning id",
        owner_id,
        settings.model_dump_json(),
    )
    return int(row_id)
