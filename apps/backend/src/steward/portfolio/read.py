"""Read models for the portfolio API: the latest snapshot, with drift against the ACTIVE
strategy config (so a config change shows up immediately, before the next sync)."""

import datetime as dt
from decimal import Decimal
from typing import Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.config.strategy import StrategyConfig
from steward.portfolio.drift import (
    DriftReport,
    DriftStatus,
    HoldingValue,
    compute_drift,
    weight_denominator,
)
from steward.portfolio.history import DailyValue, flow_adjusted_index
from steward.portfolio.snapshot import SNAPSHOT_TIMEZONE
from steward.reference.read import PricePoint, load_series

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


# ---------------------------------------------------------------------------
# Portfolio value history (vs. benchmark)
# ---------------------------------------------------------------------------


class PortfolioHistory(BaseModel):
    as_of: dt.date | None = Field(description="Date of the latest snapshot included.")
    base_currency: str | None
    points: list[PricePoint] = Field(
        description="Daily flow-adjusted portfolio value index, 100 on the first day. Not a "
        "price: deposits and withdrawals are excluded, so day-over-day change is investment "
        "return only. Only meaningful rebased (against itself or the benchmark), never read "
        "as an absolute figure."
    )
    benchmark_symbol: str | None
    benchmark: list[PricePoint]
    gap: str | None = Field(description="Why there is no portfolio history, when there is none.")


# Cash movements between the owner and the account: excluded from the index above. A fee
# or a dividend is not here — both are part of investment return, not external money moving.
_EXTERNAL_FLOW_SUBTYPES = ("DEPOSIT", "WITHDRAW", "TRANSFER")


async def _snapshot_series(pool: asyncpg.Pool, since: dt.date) -> list[asyncpg.Record]:
    rows: list[asyncpg.Record] = await pool.fetch(
        "select snapshot_date, total_base, account_currency, fx_rate"
        " from public.account_snapshot where snapshot_date >= $1 order by snapshot_date",
        since,
    )
    return rows


async def _cash_flows(pool: asyncpg.Pool, since: dt.datetime) -> list[asyncpg.Record]:
    rows: list[asyncpg.Record] = await pool.fetch(
        "select occurred_at, amount from public.transaction where type = 'cash'"
        " and subtype = any($1::text[]) and occurred_at >= $2",
        list(_EXTERNAL_FLOW_SUBTYPES),
        since,
    )
    return rows


def _period_start(snapshot_date: dt.date) -> dt.datetime:
    """Midnight Prague time at the start of `snapshot_date` — the day boundary snapshots
    already use (steward.portfolio.snapshot.snapshot_date_for)."""
    return dt.datetime.combine(snapshot_date, dt.time(0), tzinfo=SNAPSHOT_TIMEZONE)


def _daily_values(snapshots: list[asyncpg.Record], flows: list[asyncpg.Record]) -> list[DailyValue]:
    """One row per snapshot day, each carrying the net external flow that landed strictly
    after the PREVIOUS snapshot day and up to and including this one — robust to a day
    with no snapshot in between, unlike matching flows to snapshot dates one-for-one.
    Every flow is assumed to be in that period's account currency (true for every
    Trading212 cash movement observed so far); its own `currency` field is not re-checked
    against it, since a mismatch would be a Trading212 account with no clean way to price
    the flow at that day's single fx_rate."""
    rows: list[DailyValue] = []
    for i, row in enumerate(snapshots):
        if i == 0:
            # The baseline day always starts the index at 100 regardless of its own
            # flows (flow_adjusted_index never reads this value); zero is the honest
            # figure rather than summing flows with no lower bound to net them against.
            rows.append(
                DailyValue(
                    date=row["snapshot_date"], total_base=row["total_base"], flow_base=Decimal(0)
                )
            )
            continue
        period_end = _period_start(row["snapshot_date"]) + dt.timedelta(days=1)
        # Exclusive of the previous snapshot's own day: those flows are already the
        # baseline (day 0 always starts at index 100, whatever its own flows were), never
        # netted out of the FOLLOWING period too.
        period_start = _period_start(snapshots[i - 1]["snapshot_date"]) + dt.timedelta(days=1)
        flow = sum(
            (f["amount"] for f in flows if period_start <= f["occurred_at"] < period_end),
            Decimal(0),
        )
        rows.append(
            DailyValue(
                date=row["snapshot_date"],
                total_base=row["total_base"],
                flow_base=flow * row["fx_rate"],
            )
        )
    return rows


async def portfolio_history(pool: asyncpg.Pool, days: int) -> PortfolioHistory:
    jobs = await load_active(pool, "jobs", JobsConfig)
    benchmarks = (jobs.body if jobs else JobsConfig()).prices.benchmark_symbols
    benchmark_symbol = benchmarks[0] if benchmarks else None

    latest_date = await pool.fetchval("select max(snapshot_date) from public.account_snapshot")
    if latest_date is None:
        return PortfolioHistory(
            as_of=None,
            base_currency=None,
            points=[],
            benchmark_symbol=benchmark_symbol,
            benchmark=[],
            gap="no portfolio snapshot yet",
        )

    since = latest_date - dt.timedelta(days=days)
    snapshots = await _snapshot_series(pool, since)
    if len(snapshots) < 2:
        return PortfolioHistory(
            as_of=latest_date,
            base_currency=snapshots[0]["account_currency"] if snapshots else None,
            points=[],
            benchmark_symbol=benchmark_symbol,
            benchmark=[],
            gap="fewer than two snapshots in this window",
        )

    flows = await _cash_flows(pool, _period_start(snapshots[0]["snapshot_date"]))
    values = _daily_values(snapshots, flows)
    index = flow_adjusted_index(values)
    bench = await load_series(pool, benchmark_symbol, since=since) if benchmark_symbol else []

    stored = await load_active(pool, "strategy", StrategyConfig)
    base_currency = (stored.body.base_currency if stored else None) or snapshots[-1][
        "account_currency"
    ]

    return PortfolioHistory(
        as_of=latest_date,
        base_currency=base_currency,
        points=[PricePoint(date=d, close=v) for d, v in index],
        benchmark_symbol=benchmark_symbol,
        benchmark=[PricePoint(date=d, close=c) for d, c in bench],
        gap=None,
    )
