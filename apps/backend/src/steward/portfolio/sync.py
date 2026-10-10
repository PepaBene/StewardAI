"""The portfolio sync job: Trading212 -> FX rates -> day snapshot -> transactions -> drift.

Each stage is a `job_step`, so a failure shows exactly where it stopped. A day's snapshot
is written in one transaction and replaces any earlier snapshot of the same day, so
re-running a sync never leaves two rows for one position on one day.
"""

import datetime as dt
import json
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg
import httpx
from pydantic import BaseModel

from steward import __version__
from steward.config.store import load_active
from steward.config.strategy import StrategyConfig
from steward.config.thesis import ThesesConfig
from steward.fx.convert import FxRate, FxSourceName, FxTable, MissingRateError
from steward.fx.sources import FxSourceError, fetch_rates
from steward.jobs.runner import JobContext, JobFailed
from steward.portfolio.breaches import evaluate, record_breaches
from steward.portfolio.drift import HoldingValue, compute_drift
from steward.portfolio.fees import FeeBasis, FeeCharge, fee_basis, fee_charges
from steward.portfolio.risk_limits import evaluate_risk, gather_risk_inputs, unmeasured_keys
from steward.portfolio.snapshot import Snapshot, build_snapshot, snapshot_date_for
from steward.portfolio.targets import evaluate_targets
from steward.reference.calendar import fx_stale
from steward.settings import get_settings
from steward.trading212.client import Trading212Client
from steward.trading212.models import (
    HistoricalOrder,
    HistoryDividend,
    HistoryTransaction,
    T212Model,
)

JOB_TYPE = "portfolio_sync"

# Stored rates older than this are not used when a source cannot be reached.
FX_FALLBACK_MAX_AGE = dt.timedelta(days=10)
PRAGUE = ZoneInfo("Europe/Prague")

_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


@dataclass
class SyncDeps:
    t212: Trading212Client
    http: httpx.AsyncClient
    now: Callable[[], dt.datetime]


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def portfolio_sync_handler(ctx: JobContext) -> None:
    """Entry point used by the scheduler and the queue consumer."""
    settings = get_settings()
    if not settings.t212_api_key or not settings.t212_api_secret:
        raise JobFailed("T212_API_KEY and T212_API_SECRET are not set")
    async with (
        Trading212Client(
            settings.t212_api_key.get_secret_value(),
            settings.t212_api_secret.get_secret_value(),
            env=settings.t212_env,
        ) as t212,
        httpx.AsyncClient(timeout=30.0, headers={"User-Agent": f"StewardAI/{__version__}"}) as http,
    ):
        await portfolio_sync(ctx, SyncDeps(t212=t212, http=http, now=_utcnow))


async def portfolio_sync(ctx: JobContext, deps: SyncDeps) -> None:
    pool = ctx.pool

    async with ctx.step("load_config") as step:
        stored = await load_active(pool, "strategy", StrategyConfig)
        await ctx.record_config_version(stored.id if stored else None)
        step.detail["strategy_config_version_id"] = stored.id if stored else None

    async with ctx.step("trading212_account") as step:
        summary = await deps.t212.get_account_summary()
        step.detail.update(account_currency=summary.currency, total_value=summary.total_value)

    # Without a saved strategy, report in the account currency with the default source.
    strategy = stored.body if stored else StrategyConfig(base_currency=summary.currency)
    fx_source: FxSourceName = strategy.fx_source or "ecb"

    async with ctx.step("trading212_positions") as step:
        positions = await deps.t212.get_positions()
        step.detail["positions"] = len(positions)
    captured_at = deps.now()
    snapshot_date = snapshot_date_for(captured_at)

    async with ctx.step("fx_rates") as step:
        step.detail.update(source=fx_source, snapshot_date=snapshot_date)
        rates = await _collect_rates(pool, deps.http, fx_source, snapshot_date, step.detail)
        fx = FxTable.as_of(fx_source, rates, snapshot_date)
        currencies = {p.instrument.currency for p in positions} | {summary.currency}
        try:
            snapshot = build_snapshot(summary, positions, fx, strategy, captured_at)
        except MissingRateError as exc:
            raise JobFailed(
                f"{exc}; cannot convert to {strategy.base_currency}. "
                "Pick the other FX source in the strategy config or retry later."
            ) from exc
        step.detail["currencies"] = sorted(currencies)
        step.detail["base_currency"] = strategy.base_currency
        newest = max((r.rate_date for r in rates), default=None)
        # ČNB fixes by 14:30 and the ECB publishes around 16:00 CET: before 17:00 Prague
        # time, today's rate is not expected yet.
        published_by = (
            snapshot_date
            if captured_at.astimezone(PRAGUE).hour >= 17
            else snapshot_date - dt.timedelta(days=1)
        )
        missing = await fx_stale(pool, fx_source, newest, published_by)
        if missing:
            # Publication days with no rate. Weekends and holidays are never on this list.
            step.detail["fx_stale"] = {"newest_rate": newest, "missing_days": missing}

    async with ctx.step("snapshot") as step:
        await _write_snapshot(pool, ctx.job_id, stored.id if stored else None, snapshot)
        step.detail.update(
            snapshot_date=snapshot.snapshot_date,
            positions=len(snapshot.positions),
            total_base=snapshot.account.total_base,
            base_currency=snapshot.account.base_currency,
        )

    async with ctx.step("transactions") as step:
        step.detail.update(await sync_transactions(pool, deps.t212, ctx.job_id))

    sync_book = None
    if strategy.risk.auto_tiers:
        async with ctx.step("risk_tiers") as step:
            from steward.portfolio.exante import book_from_positions, with_book_tiers

            try:
                sync_book = await book_from_positions(pool, list(snapshot.positions))
                tier_changes: list[str] = []
                strategy = await with_book_tiers(pool, sync_book, strategy, tier_changes)
                if tier_changes:
                    # With derived tiers the tier criterion re-tiers a holding itself: the change
                    # is what the owner should re-check (principle D6).
                    step.detail["tier_changes"] = tier_changes
                step.detail["tiers"] = len(
                    [b for b in strategy.classifications.values() if "risk" in b]
                )
            except Exception as exc:  # a missing price history must not fail the sync
                step.detail["unavailable"] = str(exc)
                # The stored tiers keep the tier rules measuring against the last known tiers.
                from steward.portfolio.risk_tier import with_saved_tiers

                strategy = await with_saved_tiers(pool, strategy)

    async with ctx.step("drift") as step:
        report = compute_drift(
            [
                HoldingValue(p.ticker, p.instrument_currency, p.value_base)
                for p in snapshot.positions
            ],
            snapshot.account.cash_base,
            strategy,
        )
        step.detail.update(
            out_of_band=report.out_of_band,
            flagged=[
                p.ticker for p in report.positions if p.status not in ("in_band", "no_target")
            ],
        )

    async with ctx.step("breaches") as step:
        risk_inputs = await gather_risk_inputs(
            pool, strategy, list(snapshot.positions), book=sync_book
        )
        if risk_inputs.unavailable:
            step.detail["risk_unavailable"] = risk_inputs.unavailable
        theses = await load_active(pool, "theses", ThesesConfig)
        await ctx.record_config_version(theses.id if theses else None)
        targets = evaluate_targets(
            list(snapshot.positions), theses.body if theses else ThesesConfig()
        )
        if targets.unavailable:
            step.detail["targets_unavailable"] = targets.unavailable
        breaches = await record_breaches(
            pool,
            [
                *evaluate(report, strategy),
                *evaluate_risk(risk_inputs, strategy.risk),
                *targets.findings,
            ],
            job_id=ctx.job_id,
            config_version_id=stored.id if stored else None,
            now=deps.now(),
            app_url=get_settings().app_url,
            unmeasured=unmeasured_keys(risk_inputs.unavailable),
        )
        step.detail.update(
            opened=breaches.opened,
            still_open=breaches.still_open,
            cleared=breaches.cleared,
            alerts=breaches.alerts,
        )


async def _collect_rates(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    source: FxSourceName,
    date: dt.date,
    detail: dict[str, Any],
) -> list[FxRate]:
    try:
        rates = await fetch_rates(http, source, date)
    except (FxSourceError, httpx.HTTPError) as exc:
        # Keep going on recent stored rates; the step detail says so.
        detail["fetch_error"] = str(exc) or type(exc).__name__
        rows = await pool.fetch(
            "select source, rate_date, currency, rate, published_amount, published_rate"
            " from public.fx_rate where source = $1 and rate_date <= $2 and rate_date >= $3",
            source,
            date,
            date - FX_FALLBACK_MAX_AGE,
        )
        rates = [FxRate(**dict(row)) for row in rows]
        detail["used_stored_rates"] = len(rates)
        return rates

    await store_rates(pool, rates)
    detail["fetched"] = len(rates)
    detail["rate_dates"] = sorted({r.rate_date for r in rates})
    providers = sorted({r.provider for r in rates if r.provider})
    if providers:
        detail["provider"] = providers
    return rates


async def store_rates(pool: asyncpg.Pool, rates: list[FxRate], *, overwrite: bool = True) -> None:
    conflict = (
        "do update set rate = excluded.rate, published_amount = excluded.published_amount,"
        " published_rate = excluded.published_rate, provider = excluded.provider,"
        " fetched_at = now()"
        if overwrite
        else "do nothing"
    )
    await pool.executemany(
        "insert into public.fx_rate (source, rate_date, currency, home_currency, rate,"  # noqa: S608
        " published_amount, published_rate, provider) values ($1, $2, $3, $4, $5, $6, $7, $8)"
        f" on conflict (source, rate_date, currency) {conflict}",
        [
            (
                r.source,
                r.rate_date,
                r.currency,
                r.home_currency,
                r.rate,
                r.published_amount,
                r.published_rate,
                r.provider,
            )
            for r in rates
        ],
    )


async def _write_snapshot(
    pool: asyncpg.Pool, job_id: int, config_version_id: int | None, snapshot: Snapshot
) -> None:
    a = snapshot.account
    async with pool.acquire() as conn, conn.transaction():
        await conn.executemany(
            "insert into public.instrument (t212_ticker, isin, name, currency)"
            " values ($1, $2, $3, $4)"
            " on conflict (t212_ticker) do update set"
            " isin = coalesce(excluded.isin, instrument.isin),"
            " name = coalesce(excluded.name, instrument.name),"
            " currency = coalesce(excluded.currency, instrument.currency)",
            [
                (
                    p.ticker,
                    p.isin if p.isin and _ISIN.match(p.isin) else None,
                    p.name,
                    p.instrument_currency if len(p.instrument_currency) == 3 else None,
                )
                for p in snapshot.positions
            ],
        )
        await conn.execute(
            "delete from public.position_snapshot where snapshot_date = $1", snapshot.snapshot_date
        )
        await conn.execute(
            "delete from public.account_snapshot where snapshot_date = $1", snapshot.snapshot_date
        )
        await conn.execute(
            "insert into public.account_snapshot (snapshot_date, job_id, captured_at,"
            " config_version_id, t212_account_id, account_currency, total_value, cash_available,"
            " cash_in_pies, cash_reserved, invested_value, invested_cost, unrealized_ppl,"
            " realized_ppl, base_currency, fx_source, fx_rate, fx_rate_date, cash_base,"
            " invested_base, total_base, weight_basis) values ($1, $2, $3, $4, $5, $6, $7, $8,"
            " $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22)",
            snapshot.snapshot_date,
            job_id,
            snapshot.captured_at,
            config_version_id,
            a.t212_account_id,
            a.account_currency,
            a.total_value,
            a.cash_available,
            a.cash_in_pies,
            a.cash_reserved,
            a.invested_value,
            a.invested_cost,
            a.unrealized_ppl,
            a.realized_ppl,
            a.base_currency,
            a.fx_source,
            a.fx_rate,
            a.fx_rate_date,
            a.cash_base,
            a.invested_base,
            a.total_base,
            a.weight_basis,
        )
        await conn.executemany(
            "insert into public.position_snapshot (snapshot_date, ticker, job_id, captured_at,"
            " config_version_id, quantity, avg_price, current_price, instrument_currency, value,"
            " account_currency, value_account, cost_account, ppl, fx_impact_account,"
            " base_currency, fx_source, fx_rate, fx_rate_date, value_base, weight)"
            " values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16,"
            " $17, $18, $19, $20, $21)",
            [
                (
                    snapshot.snapshot_date,
                    p.ticker,
                    job_id,
                    snapshot.captured_at,
                    config_version_id,
                    p.quantity,
                    p.avg_price,
                    p.current_price,
                    p.instrument_currency,
                    p.value,
                    p.account_currency,
                    p.value_account,
                    p.cost_account,
                    p.ppl,
                    p.fx_impact_account,
                    a.base_currency,
                    a.fx_source,
                    p.fx_rate,
                    p.fx_rate_date,
                    p.value_base,
                    p.weight,
                )
                for p in snapshot.positions
            ],
        )


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransactionRow:
    id: str
    type: str
    subtype: str | None
    ticker: str | None
    quantity: Decimal | None
    price: Decimal | None
    amount: Decimal
    currency: str | None
    occurred_at: dt.datetime
    raw: dict[str, Any]
    # Orders only: the fill's walletImpact.taxes, and whether netValue includes them.
    fees: tuple[FeeCharge, ...] = ()
    fee_basis: FeeBasis | None = None


def _raw(item: BaseModel) -> dict[str, Any]:
    """The response item exactly as Trading212 sent it; a re-dump of the parsed model only
    for items that did not come through the client (never the case in the sync)."""
    if isinstance(item, T212Model) and item.source_payload is not None:
        return item.source_payload
    dumped: dict[str, Any] = item.model_dump(mode="json", by_alias=True, exclude_none=True)
    return dumped


def json_exact(value: Any) -> str:
    """JSON text for a payload parsed with Decimal numbers, writing each number exactly as
    it was received (json.dumps cannot serialise Decimal, and float would round it)."""
    if isinstance(value, Decimal):
        return str(value) if value.is_finite() else "null"
    if isinstance(value, dict):
        return (
            "{" + ",".join(f"{json.dumps(str(k))}:{json_exact(v)}" for k, v in value.items()) + "}"
        )
    if isinstance(value, list | tuple):
        return "[" + ",".join(json_exact(v) for v in value) + "]"
    return json.dumps(value)


def order_row(item: HistoricalOrder) -> TransactionRow | None:
    fill = item.fill
    if fill is None:
        return None  # never executed: no money moved
    order = item.order
    impact = fill.wallet_impact
    if impact is not None and impact.net_value is not None:
        amount, currency = impact.net_value, impact.currency
    else:
        amount = (fill.quantity or Decimal(0)) * (fill.price or Decimal(0))
        currency = order.currency
    ticker = order.ticker or (order.instrument.ticker if order.instrument else None)
    fees = fee_charges(impact.taxes if impact else None, impact.currency if impact else None)
    basis = fee_basis(
        side=order.side,
        quantity=fill.quantity,
        price=fill.price,
        fx_rate=impact.fx_rate if impact else None,
        net_value=impact.net_value if impact else None,
        # Only charges in netValue's own currency can be compared with it.
        fees=sum((f.amount for f in fees if f.currency in (None, currency)), Decimal(0)),
    )
    return TransactionRow(
        id=f"fill:{fill.id}",
        type="order",
        subtype=order.side,
        ticker=ticker,
        quantity=fill.quantity,
        price=fill.price,
        amount=amount,
        currency=currency,
        occurred_at=fill.filled_at,
        raw=_raw(item),
        fees=tuple(fees),
        fee_basis=basis,
    )


def dividend_row(item: HistoryDividend) -> TransactionRow:
    ticker = item.ticker or (item.instrument.ticker if item.instrument else None)
    return TransactionRow(
        id=f"dividend:{item.reference}",
        type="dividend",
        subtype=item.type,
        ticker=ticker,
        quantity=item.quantity,
        price=item.gross_amount_per_share,
        amount=item.amount,
        currency=item.currency,
        occurred_at=item.paid_on,
        raw=_raw(item),
    )


def cash_row(item: HistoryTransaction) -> TransactionRow:
    return TransactionRow(
        id=f"cash:{item.reference}",
        type="cash",
        subtype=item.type,
        ticker=None,
        quantity=None,
        price=None,
        amount=item.amount,
        currency=item.currency,
        occurred_at=item.date_time,
        raw=_raw(item),
    )


# Bumped when a history kind must be re-read in full once (the next sync ignores its
# backfill_complete flag, re-pages everything and upserts). orders 1: fee capture - fills
# written before walletImpact.taxes was read get their original payload and their fees.
HISTORY_STATE_VERSION: dict[str, int] = {"orders": 1}

_INSERT = (
    "insert into public.transaction (id, type, subtype, ticker, quantity, price, amount,"
    " currency, occurred_at, raw, job_id, fees_captured_at, fee_basis)"
    " values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11, $12, $13)"
)
# Orders are upserted: a re-read (the one-time fee backfill, or Trading212 adding a field)
# refreshes the stored payload and fees. Nothing else of a stored fill changes, and an
# unchanged row is not written at all. Returns true (inserted), false (updated) or no row.
_UPSERT_ORDER = (
    _INSERT + " on conflict (id) do update set raw = excluded.raw,"
    " fees_captured_at = excluded.fees_captured_at, fee_basis = excluded.fee_basis"
    " where transaction.raw is distinct from excluded.raw"
    " or transaction.fees_captured_at is null"
    " or transaction.fee_basis is distinct from excluded.fee_basis"
    " returning (xmax = 0)"
)
_INSERT_OTHER = _INSERT + " on conflict (id) do nothing returning true"


async def _store_row(
    conn: asyncpg.Connection, row: TransactionRow, job_id: int, now: dt.datetime
) -> bool | None:
    """Write one row: true if inserted, false if a stored order was updated, None if it
    was already stored unchanged. An order's fee rows are replaced with it."""
    order = row.type == "order"
    async with conn.transaction():
        result: bool | None = await conn.fetchval(
            _UPSERT_ORDER if order else _INSERT_OTHER,
            row.id,
            row.type,
            row.subtype,
            row.ticker,
            row.quantity,
            row.price,
            row.amount,
            row.currency,
            row.occurred_at,
            json_exact(row.raw),
            job_id,
            now if order else None,
            row.fee_basis,
        )
        if order and result is not None:
            await conn.execute(
                "delete from public.transaction_fee where transaction_id = $1", row.id
            )
            await conn.executemany(
                "insert into public.transaction_fee (transaction_id, seq, name, category,"
                " amount, quantity, currency, charged_at, occurred_at)"
                " values ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                [
                    (
                        row.id,
                        f.seq,
                        f.name,
                        f.category,
                        f.amount,
                        f.quantity,
                        f.currency,
                        f.charged_at,
                        row.occurred_at,
                    )
                    for f in row.fees
                ],
            )
    return result


async def _sync_history_kind[M: BaseModel](
    pool: asyncpg.Pool,
    kind: str,
    pages: AsyncIterator[list[M]],
    to_row: Callable[[M], TransactionRow | None],
    job_id: int,
) -> dict[str, int | bool]:
    """Page through one history endpoint, newest first, storing new items.

    Until the first complete pass (the backfill) every page is read. After it, reading
    stops at the first page that contains an item already stored *and* reaches back to
    the watermark: the newest item time seen by the last run that finished paging. A run
    that fails mid-paging does not move the watermark, so the next run keeps reading
    past pages it had already stored until it is back below the last clean pass.

    A kind whose stored state is older than HISTORY_STATE_VERSION is read in full once
    more, exactly like the first backfill; orders are upserted, so that pass refreshes
    every stored fill (the one-time fee backfill). A failed pass is simply retried by the
    next sync, since the state only moves once paging finishes.
    """
    state_key = f"trading212_history:{kind}"
    state_raw = await pool.fetchval("select value from public.sync_state where key = $1", state_key)
    state = json.loads(state_raw) if state_raw else {}
    version = HISTORY_STATE_VERSION.get(kind, 0)
    backfilled = bool(state.get("backfill_complete")) and int(state.get("version", 0)) >= version
    # Absent for state written before the watermark existed: stop at the first known item.
    watermark = dt.datetime.fromisoformat(state["watermark"]) if state.get("watermark") else None

    inserted = updated = pages_read = 0
    newest: dt.datetime | None = None
    now = _utcnow()
    async for page in pages:
        pages_read += 1
        rows = [row for item in page if (row := to_row(item)) is not None]
        for row in rows:
            if newest is None or row.occurred_at > newest:
                newest = row.occurred_at
        new = 0
        async with pool.acquire() as conn:
            for row in rows:
                result = await _store_row(conn, row, job_id, now)
                if result is True:
                    new += 1
                elif result is False:
                    updated += 1
        inserted += new
        if (
            backfilled
            and new < len(rows)
            and (watermark is None or min(r.occurred_at for r in rows) <= watermark)
        ):
            break

    # Paging finished cleanly (an exception above leaves the state as it was).
    if watermark is not None and (newest is None or newest < watermark):
        newest = watermark
    value: dict[str, Any] = {
        "backfill_complete": True,
        "watermark": newest.isoformat() if newest is not None else None,
    }
    if version:
        value["version"] = version
    await pool.execute(
        "insert into public.sync_state (key, value) values ($1, $2::jsonb)"
        " on conflict (key) do update set value = excluded.value",
        state_key,
        json.dumps(value),
    )
    detail: dict[str, int | bool] = {f"{kind}_new": inserted, f"{kind}_pages": pages_read}
    if updated:
        detail[f"{kind}_updated"] = updated
    if not backfilled and state.get("backfill_complete"):
        detail[f"{kind}_reread"] = True
    return detail


async def sync_transactions(
    pool: asyncpg.Pool, t212: Trading212Client, job_id: int
) -> dict[str, int | bool]:
    detail: dict[str, int | bool] = {}
    detail.update(
        await _sync_history_kind(pool, "orders", t212.iter_order_history(), order_row, job_id)
    )
    detail.update(
        await _sync_history_kind(
            pool, "dividends", t212.iter_dividend_history(), dividend_row, job_id
        )
    )
    detail.update(
        await _sync_history_kind(pool, "cash", t212.iter_cash_history(), cash_row, job_id)
    )
    return detail
