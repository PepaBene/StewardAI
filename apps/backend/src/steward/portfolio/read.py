"""Read models for the portfolio API: the latest snapshot, with drift against the ACTIVE
strategy config (so a config change shows up immediately, before the next sync)."""

import datetime as dt
from decimal import Decimal
from typing import Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.config.store import load_active
from steward.config.strategy import StrategyConfig
from steward.portfolio.drift import (
    DriftReport,
    DriftStatus,
    HoldingValue,
    compute_drift,
    weight_denominator,
)

JobStatus = Literal["queued", "waiting", "running", "succeeded", "failed"]


class JobSummary(BaseModel):
    id: int
    job_type: str
    trigger: str
    status: JobStatus
    error: str | None
    created_at: dt.datetime
    started_at: dt.datetime | None
    finished_at: dt.datetime | None


class FxRateUsed(BaseModel):
    currency: str
    rate: float = Field(description="Base-currency units per one unit of `currency`.")
    rate_date: dt.date | None


class AllocationSlice(BaseModel):
    key: str = Field(description="Ticker, or 'CASH'.")
    label: str
    value_base: float
    weight: float | None


class Freshness(BaseModel):
    snapshot_date: dt.date
    synced_at: dt.datetime = Field(
        description="When the sync that wrote this snapshot finished its last step."
    )
    job_id: int


class AccountTotals(BaseModel):
    account_currency: str
    total_value: float = Field(description="Total value as Trading212 reports it.")
    cash: float
    invested_value: float
    invested_cost: float
    unrealized_ppl: float
    realized_ppl: float


class BaseTotals(BaseModel):
    base_currency: str
    fx_source: Literal["cnb", "ecb"]
    total: float
    cash: float
    invested: float


class PortfolioOverview(BaseModel):
    freshness: Freshness | None = Field(description="Null until the first successful sync.")
    last_sync: JobSummary | None = Field(description="The latest sync job, whatever its status.")
    account: AccountTotals | None
    base: BaseTotals | None
    strategy_config_version_id: int | None
    base_currency_mismatch: bool = Field(
        description="The active strategy's base currency differs from the snapshot's; "
        "the next sync converts to the new one."
    )
    allocation: list[AllocationSlice]
    drift: DriftReport | None
    fx_rates: list[FxRateUsed]


class Holding(BaseModel):
    ticker: str
    name: str | None
    isin: str | None
    quantity: float
    avg_price: float
    current_price: float
    instrument_currency: str
    value: float = Field(description="quantity x current_price, instrument currency.")
    account_currency: str
    value_account: float
    cost_account: float
    ppl: float = Field(description="Unrealised P/L in the account currency (Trading212).")
    ppl_pct: float | None = Field(description="ppl / cost_account.")
    fx_rate: float
    fx_rate_date: dt.date | None
    value_base: float
    weight: float | None
    target_weight: float | None
    drift: float | None
    drift_status: DriftStatus


class HoldingsResponse(BaseModel):
    freshness: Freshness | None
    base_currency: str | None
    account_currency: str | None
    holdings: list[Holding] = Field(description="Every open position once, by value descending.")


async def latest_sync_job(conn: asyncpg.Pool) -> JobSummary | None:
    row = await conn.fetchrow(
        "select id, job_type, trigger, status, error, created_at, started_at, finished_at"
        " from public.job where job_type = 'portfolio_sync' order by id desc limit 1"
    )
    return JobSummary(**dict(row)) if row else None


async def _latest_account(conn: asyncpg.Pool) -> asyncpg.Record | None:
    return await conn.fetchrow(
        "select a.*, (select max(s.finished_at) from public.job_step s where s.job_id = a.job_id)"
        " as synced_at from public.account_snapshot a order by snapshot_date desc limit 1"
    )


async def _positions(conn: asyncpg.Pool, snapshot_date: dt.date) -> list[asyncpg.Record]:
    rows: list[asyncpg.Record] = await conn.fetch(
        "select p.*, i.name, i.isin from public.position_snapshot p"
        " left join public.instrument i on i.t212_ticker = p.ticker"
        " where p.snapshot_date = $1 order by p.value_base desc, p.ticker",
        snapshot_date,
    )
    return rows


def _strategy_for(stored_body: StrategyConfig | None, account: asyncpg.Record) -> StrategyConfig:
    return stored_body or StrategyConfig(base_currency=account["base_currency"])


def _freshness(account: asyncpg.Record) -> Freshness:
    return Freshness(
        snapshot_date=account["snapshot_date"],
        synced_at=account["synced_at"] or account["captured_at"],
        job_id=account["job_id"],
    )


async def portfolio_overview(pool: asyncpg.Pool) -> PortfolioOverview:
    last_sync = await latest_sync_job(pool)
    stored = await load_active(pool, "strategy", StrategyConfig)
    account = await _latest_account(pool)
    if account is None:
        return PortfolioOverview(
            freshness=None,
            last_sync=last_sync,
            account=None,
            base=None,
            strategy_config_version_id=stored.id if stored else None,
            base_currency_mismatch=False,
            allocation=[],
            drift=None,
            fx_rates=[],
        )

    positions = await _positions(pool, account["snapshot_date"])
    strategy = _strategy_for(stored.body if stored else None, account)
    cash_base: Decimal = account["cash_base"]
    invested_base: Decimal = account["invested_base"]
    denominator = weight_denominator(invested_base, cash_base, strategy.weight_basis)

    def weight(value: Decimal) -> float | None:
        return float(value / denominator) if denominator > 0 else None

    allocation = [
        AllocationSlice(
            key=p["ticker"],
            label=p["name"] or p["ticker"],
            value_base=float(p["value_base"]),
            weight=weight(p["value_base"]),
        )
        for p in positions
    ]
    allocation.append(
        AllocationSlice(
            key="CASH",
            label="Cash",
            value_base=float(cash_base),
            weight=weight(cash_base) if strategy.weight_basis == "total" else None,
        )
    )

    rates: dict[str, FxRateUsed] = {
        account["account_currency"]: FxRateUsed(
            currency=account["account_currency"],
            rate=float(account["fx_rate"]),
            rate_date=account["fx_rate_date"],
        )
    }
    for p in positions:
        rates.setdefault(
            p["instrument_currency"],
            FxRateUsed(
                currency=p["instrument_currency"],
                rate=float(p["fx_rate"]),
                rate_date=p["fx_rate_date"],
            ),
        )

    return PortfolioOverview(
        freshness=_freshness(account),
        last_sync=last_sync,
        account=AccountTotals(
            account_currency=account["account_currency"],
            total_value=float(account["total_value"]),
            cash=float(
                account["cash_available"] + account["cash_in_pies"] + account["cash_reserved"]
            ),
            invested_value=float(account["invested_value"]),
            invested_cost=float(account["invested_cost"]),
            unrealized_ppl=float(account["unrealized_ppl"]),
            realized_ppl=float(account["realized_ppl"]),
        ),
        base=BaseTotals(
            base_currency=account["base_currency"],
            fx_source=account["fx_source"],
            total=float(account["total_base"]),
            cash=float(cash_base),
            invested=float(invested_base),
        ),
        strategy_config_version_id=stored.id if stored else None,
        base_currency_mismatch=strategy.base_currency != account["base_currency"],
        allocation=allocation,
        drift=compute_drift(
            [
                HoldingValue(p["ticker"], p["instrument_currency"], p["value_base"])
                for p in positions
            ],
            cash_base,
            strategy,
        ),
        fx_rates=sorted(rates.values(), key=lambda r: r.currency),
    )


async def portfolio_holdings(pool: asyncpg.Pool) -> HoldingsResponse:
    account = await _latest_account(pool)
    if account is None:
        return HoldingsResponse(
            freshness=None, base_currency=None, account_currency=None, holdings=[]
        )
    stored = await load_active(pool, "strategy", StrategyConfig)
    strategy = _strategy_for(stored.body if stored else None, account)
    positions = await _positions(pool, account["snapshot_date"])
    report = compute_drift(
        [HoldingValue(p["ticker"], p["instrument_currency"], p["value_base"]) for p in positions],
        account["cash_base"],
        strategy,
    )
    drift_by_ticker = {d.ticker: d for d in report.positions}

    holdings = []
    for p in positions:
        d = drift_by_ticker[p["ticker"]]
        cost: Decimal = p["cost_account"]
        holdings.append(
            Holding(
                ticker=p["ticker"],
                name=p["name"],
                isin=p["isin"],
                quantity=float(p["quantity"]),
                avg_price=float(p["avg_price"]),
                current_price=float(p["current_price"]),
                instrument_currency=p["instrument_currency"],
                value=float(p["value"]),
                account_currency=p["account_currency"],
                value_account=float(p["value_account"]),
                cost_account=float(cost),
                ppl=float(p["ppl"]),
                ppl_pct=float(p["ppl"] / cost) if cost else None,
                fx_rate=float(p["fx_rate"]),
                fx_rate_date=p["fx_rate_date"],
                value_base=float(p["value_base"]),
                weight=d.weight,
                target_weight=d.target_weight,
                drift=d.drift,
                drift_status=d.status,
            )
        )
    return HoldingsResponse(
        freshness=_freshness(account),
        base_currency=account["base_currency"],
        account_currency=account["account_currency"],
        holdings=holdings,
    )
