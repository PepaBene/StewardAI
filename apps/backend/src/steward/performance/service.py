# ruff: noqa: E501 - SQL and explanatory strings stay readable
"""Loads the stored snapshots, transactions and prices and assembles the Phase 4 reports with the
pure calculators. Read-only: nothing here writes to the broker tables or invents a missing event.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from decimal import Decimal
from itertools import pairwise
from typing import Any

import asyncpg

from steward.analytics.contract import Status
from steward.analytics.returns import DayValue
from steward.analytics.versions import version
from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.config.strategy import StrategyConfig
from steward.config.thesis import ThesesConfig
from steward.fx.convert import normalise_currency
from steward.fx.history import load_fx_history
from steward.performance import reports as r
from steward.performance.attribution import (
    AccountDay,
    Amount,
    PositionDay,
    RelativeContribution,
    Trade,
    attribute,
    benchmark_relative,
    group_contributions,
    ticker_fx_lookup,
)
from steward.performance.benchmark import blended_index, compare_window, series_return
from steward.performance.ledger import (
    EXTERNAL_KINDS,
    CashReconciliation,
    Kind,
    LedgerEntry,
    RawTransaction,
    Split,
    apply_splits,
    build_ledger,
    position_breaks,
    reconcile_cash,
    summarize,
)
from steward.performance.lots import build_lots, exemption_year, holding_periods
from steward.performance.settings import BenchmarkSpec, PerformanceSettings
from steward.performance.sleeves import compare_sleeves, sleeve_days
from steward.performance.windows import WINDOW_LABELS, window_return, window_start
from steward.reference.read import index_series

ZERO = Decimal(0)
ONE = Decimal(1)
DISCLAIMER = (
    "Holding-period and cost-basis facts from your own ledger and the thresholds you set. "
    "Not a tax liability, a filing figure or tax advice."
)


def _d(value: float | Decimal | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


async def _snapshots(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    """Daily account snapshots in the latest snapshot's base currency, oldest first."""
    rows: list[asyncpg.Record] = await pool.fetch(
        "select snapshot_date, total_base, cash_base, invested_base, account_currency,"
        " base_currency, fx_rate, total_value, invested_value from public.account_snapshot"
        " order by snapshot_date"
    )
    if not rows:
        return []
    base = rows[-1]["base_currency"]
    return [row for row in rows if row["base_currency"] == base]


async def _raw_transactions(pool: asyncpg.Pool) -> list[RawTransaction]:
    rows = await pool.fetch(
        "select t.id, t.type, t.subtype, t.ticker, t.quantity, t.price, t.amount, t.currency,"
        " t.occurred_at, t.fee_basis, (t.fees_captured_at is not null or t.type <> 'order') as captured,"
        " coalesce((select array_agg(f.amount order by f.seq) from public.transaction_fee f"
        "   where f.transaction_id = t.id and (f.currency is null or f.currency = t.currency)),"
        "   '{}'::numeric[]) as fees"
        " from public.transaction t order by t.occurred_at, t.id"
    )
    return [
        RawTransaction(
            id=x["id"], type=x["type"], subtype=x["subtype"], ticker=x["ticker"],
            quantity=x["quantity"], price=x["price"], amount=x["amount"], currency=x["currency"],
            occurred_at=x["occurred_at"], fee_basis=x["fee_basis"], fees_captured=x["captured"],
            fee_amounts=tuple(x["fees"] or ()),
        )
        for x in rows
    ]  # fmt: skip


async def _ledger(
    pool: asyncpg.Pool, account_currency: str | None, settings: PerformanceSettings
) -> list[LedgerEntry]:
    """The classified ledger with the owner's entered splits applied to earlier fills."""
    entries = build_ledger(await _raw_transactions(pool), account_currency)
    return apply_splits(entries, [Split(s.ticker, s.day, s.ratio) for s in settings.splits])


STALE_AFTER_DAYS = 5  # covers a weekend plus a market holiday


def freshness(
    prices_as_of: dt.date | None,
    benchmark: list[tuple[dt.date, Decimal]],
    today: dt.date,
) -> r.FreshnessOut:
    """Ages of the position snapshot (prices and exchange rates) and the benchmark's last close."""
    bench_day = max((d for d, _ in benchmark), default=None)
    prices_age = (today - prices_as_of).days if prices_as_of else None
    bench_age = (today - bench_day).days if bench_day else None
    stale = any(a is not None and a > STALE_AFTER_DAYS for a in (prices_age, bench_age))
    return r.FreshnessOut(
        prices_as_of=prices_as_of, prices_age_days=prices_age, benchmark_as_of=bench_day,
        benchmark_age_days=bench_age, stale=stale, stale_after_days=STALE_AFTER_DAYS,
    )  # fmt: skip


def _float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _day_values(snapshots: list[asyncpg.Record], ledger: list[LedgerEntry]) -> list[DayValue]:
    """Value per snapshot day in the base currency, each carrying the external flow that landed
    after the previous snapshot day and up to this one, converted at this day's account rate."""
    out: list[DayValue] = []
    for i, snap in enumerate(snapshots):
        flow = ZERO
        if i:
            previous = snapshots[i - 1]["snapshot_date"]
            flow = (
                sum(
                    (
                        e.external_flow
                        for e in ledger
                        if e.kind in EXTERNAL_KINDS and previous < e.day <= snap["snapshot_date"]
                    ),
                    ZERO,
                )
                * snap["fx_rate"]
            )
        out.append(DayValue(snap["snapshot_date"], snap["total_base"], flow))
    return out


def _cash_checks(
    snapshots: list[asyncpg.Record], ledger: list[LedgerEntry], tolerance: Decimal
) -> list[CashReconciliation]:
    checks = []
    for before, after in pairwise(snapshots):
        checks.append(
            reconcile_cash(
                ledger,
                start_date=before["snapshot_date"],
                end_date=after["snapshot_date"],
                opening_cash=before["total_value"] - before["invested_value"],
                closing_cash=after["total_value"] - after["invested_value"],
                tolerance=max(tolerance, abs(after["total_value"]) * Decimal("0.0001")),
            )
        )
    return checks


def _missing_weight(
    checks: list[CashReconciliation], since: dt.date | None, end_value: Decimal
) -> Decimal:
    """Share of the ending value whose cash movement the ledger cannot explain inside the
    window: an unrecorded deposit or withdrawal would distort a time-weighted return."""
    if end_value <= 0:
        return ZERO
    unexplained = sum(
        (
            abs(c.difference)
            for c in checks
            if not c.within_tolerance and (since is None or c.end_date > since)
        ),
        ZERO,
    )
    return min(ONE, unexplained / end_value)


def _cumulative_index(days: list[DayValue]) -> list[tuple[dt.date, Decimal]]:
    index = Decimal(100)
    points = [(days[0].date, index)]
    for before, after in pairwise(days):
        if before.value > 0:
            index *= (after.value - after.flow) / before.value
        points.append((after.date, index))
    return points


def _rolling(
    index: list[tuple[dt.date, Decimal]], span: int = 90
) -> list[r.PerformanceSeriesPoint]:
    out = []
    for i, (day, value) in enumerate(index):
        start = day - dt.timedelta(days=span)
        earlier = [p for p in index[: i + 1] if p[0] <= start]
        if earlier and earlier[-1][1] > 0:
            out.append(
                r.PerformanceSeriesPoint(date=day, value=float(value / earlier[-1][1] - ONE))
            )
    return out


async def _benchmark_series(
    pool: asyncpg.Pool, spec: BenchmarkSpec | None, jobs: JobsConfig, since: dt.date
) -> tuple[str | None, list[tuple[dt.date, Decimal]], str | None, str, list[str]]:
    """(label, series, currency, basis, notes) of the configured benchmark or blend."""
    notes: list[str] = []
    if spec is None:
        symbols = jobs.prices.benchmark_symbols
        if not symbols:
            return None, [], None, "unknown", ["no benchmark chosen"]
        _ref, bars = await index_series(pool, jobs.prices, symbols[0], since)
        notes.append("benchmark currency and dividend basis are not recorded: set them below")
        return symbols[0], [(d, Decimal(str(c))) for d, c in bars], None, "unknown", notes
    series: dict[str, list[tuple[dt.date, Decimal]]] = {}
    for component in spec.components:
        _ref, bars = await index_series(pool, jobs.prices, component.symbol, since)
        if not bars:
            notes.append(f"no stored closes for {component.symbol}")
            return None, [], None, "unknown", notes
        series[component.symbol] = [(d, Decimal(str(c))) for d, c in bars]
    currencies = {c.currency for c in spec.components}
    bases = {c.basis for c in spec.components}
    currency = next(iter(currencies)) if len(currencies) == 1 else None
    basis = next(iter(bases)) if len(bases) == 1 else "unknown"
    if len(spec.components) == 1:
        only = spec.components[0]
        return only.symbol, series[only.symbol], currency, basis, notes
    if currency is None:
        notes.append("blend mixes quote currencies: no like-for-like active return")
    blend = blended_index(series, {c.symbol: c.weight for c in spec.components}, spec.rebalance)
    label = " + ".join(f"{c.weight:.0%} {c.symbol}" for c in spec.components)
    return f"{label} ({spec.rebalance} rebalance)", blend, currency, basis, notes


def _costs(
    ledger: list[LedgerEntry], currency: str | None, average_value: Decimal | None, since: dt.date
) -> r.CostsOut:
    recent = [e for e in ledger if e.day >= since and not e.needs_fx]
    fill_fees = sum((e.fees for e in recent if e.kind in (Kind.BUY, Kind.SELL)), ZERO)
    cash_fees = sum((-e.cash_effect for e in recent if e.kind is Kind.FEE), ZERO)
    total = fill_fees + cash_fees
    return r.CostsOut(
        currency=currency,
        fill_fees=float(fill_fees),
        cash_fees=float(cash_fees),
        total=float(total),
        fraction_of_average_value=float(total / average_value) if average_value else None,
        window_start=since,
        note="Fees and taxes charged on fills plus cash-account fees, last 12 months, in the "
        "account currency. Fills whose fees were not captured count as zero here and are listed "
        "under the ledger health.",
    )


def _timeline(ledger: list[LedgerEntry], months: int = 24) -> list[r.TimelineMonth]:
    buckets: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for e in ledger:
        if e.needs_fx:
            continue
        key = f"{e.day.year}-{e.day.month:02d}"
        if e.kind is Kind.DEPOSIT or (e.kind is Kind.TRANSFER and e.cash_effect > 0):
            buckets[key]["deposits"] += e.cash_effect
        elif e.kind is Kind.WITHDRAWAL or (e.kind is Kind.TRANSFER and e.cash_effect < 0):
            buckets[key]["withdrawals"] += -e.cash_effect
        elif e.kind is Kind.DIVIDEND:
            buckets[key]["dividends"] += e.cash_effect
        if e.kind is Kind.FEE:
            buckets[key]["fees"] += -e.cash_effect
        elif e.kind in (Kind.BUY, Kind.SELL):
            buckets[key]["fees"] += e.fees
    return [
        r.TimelineMonth(
            month=m,
            deposits=float(v["deposits"]),
            withdrawals=float(v["withdrawals"]),
            dividends=float(v["dividends"]),
            fees=float(v["fees"]),
        )
        for m, v in sorted(buckets.items())[-months:]
    ]


def _empty_ledger() -> r.LedgerHealth:
    return r.LedgerHealth(
        status="empty", entries=0, first=None, last=None, by_kind={}, unsupported=[], needs_fx=0,
        fees_not_captured=0, position_breaks=[], cash_breaks=[], cash_breaks_total=0,
        unexplained_cash=0.0, cash_tolerance=0.0,
    )  # fmt: skip


PERFORMANCE_FORMULA = [
    "Time-weighted return: chain (value - flow) / previous value over each valuation day, minus 1. "
    "Deposits and withdrawals are the only flows; dividends, fees and trades are inside the return.",
    "Money-weighted return (XIRR): the annual rate that makes your dated deposits and withdrawals, "
    "the opening value and the ending value sum to zero (actual/365).",
    "A window is annualized only when it spans a year or more. A window the stored history does "
    "not reach is unavailable, never shortened.",
    "A precise return is withheld when the ledger leaves part of the ending value's cash movement "
    "unexplained (a missing deposit or withdrawal).",
]


async def performance_report(
    pool: asyncpg.Pool, settings: PerformanceSettings
) -> r.PerformanceResponse:
    explain = r.PerformanceExplain(formula_version=f"twr {version('twr')}, xirr {version('xirr')}",
                        formula=PERFORMANCE_FORMULA)  # fmt: skip
    snapshots = await _snapshots(pool)
    if len(snapshots) < 2:
        return r.PerformanceResponse(
            status="unavailable", as_of=snapshots[-1]["snapshot_date"] if snapshots else None,
            base_currency=snapshots[-1]["base_currency"] if snapshots else None,
            account_currency=snapshots[-1]["account_currency"] if snapshots else None,
            gap="fewer than two daily snapshots are stored", windows=[], index=[],
            benchmark_index=[], benchmark_label=None, rolling_90d=[], values=[],
            ledger=_empty_ledger(), costs=_costs([], None, None, dt.date.today()), timeline=[],
            freshness_note=None, explain=explain,
        )  # fmt: skip
    latest = snapshots[-1]
    account_currency = latest["account_currency"]
    ledger = await _ledger(pool, account_currency, settings)
    days = _day_values(snapshots, ledger)
    checks = _cash_checks(snapshots, ledger, settings.cash_tolerance)
    jobs_cfg = await load_active(pool, "jobs", JobsConfig)
    jobs = jobs_cfg.body if jobs_cfg else JobsConfig()
    first_day = days[0].date
    label, bench_series, bench_ccy, bench_basis, bench_notes = await _benchmark_series(
        pool, settings.benchmark, jobs, first_day - dt.timedelta(days=10)
    )
    base_currency = latest["base_currency"]
    windows: list[r.WindowOut] = []
    for window_label in WINDOW_LABELS:
        start = window_start(window_label, days[-1].date)
        missing = _missing_weight(checks, start, latest["total_value"])
        w = window_return(days, window_label, missing_weight=missing)
        benchmark_out = None
        if label is not None:
            cmp = compare_window(
                w, label, bench_series, benchmark_currency=bench_ccy,
                portfolio_currency=base_currency, basis=bench_basis,  # type: ignore[arg-type]
                portfolio_days=[d.date for d in days],
            )  # fmt: skip
            benchmark_out = r.BenchmarkOut(
                symbol=label, label=label, benchmark_return=r.num(cmp.benchmark_return),
                active_return=r.figure(cmp.active_return), basis=cmp.basis, currency=cmp.currency,
                missing_share=r.num(cmp.missing_share),
            )  # fmt: skip
        windows.append(
            r.WindowOut(
                label=w.label,
                start=w.start,
                end=w.end,
                calendar_days=w.calendar_days,
                observations=w.observations,
                start_value=r.num(w.start_value),
                end_value=r.num(w.end_value),
                net_flows=float(w.net_flows),
                gain=r.num(w.gain),
                twr=r.figure(w.twr),
                twr_annualized=r.num(w.twr_annualized),
                xirr=r.figure(w.xirr),
                reason=w.reason,
                benchmark=benchmark_out,
            )
        )
    index = _cumulative_index(days)
    bench_index: list[r.PerformanceSeriesPoint] = []
    if bench_series:
        base_close = next((c for d, c in bench_series if d >= first_day), None)
        if base_close:
            bench_index = [
                r.PerformanceSeriesPoint(date=d, value=float(Decimal(100) * c / base_close))
                for d, c in bench_series
                if d >= first_day
            ]
    cash_breaks = [c for c in checks if not c.within_tolerance]
    quantities = {
        row["ticker"]: row["quantity"]
        for row in await pool.fetch(
            "select ticker, quantity from public.position_snapshot where snapshot_date ="
            " (select max(snapshot_date) from public.position_snapshot)"
        )
    }
    pos_breaks = position_breaks(ledger, quantities)
    summary = summarize(ledger)
    year_ago = days[-1].date - dt.timedelta(days=365)
    # Costs are in the account currency, so measure them against the account-currency value.
    recent_account = [s["total_value"] for s in snapshots if s["snapshot_date"] >= year_ago]
    average_account = sum(recent_account, ZERO) / len(recent_account) if recent_account else None
    clean = not (cash_breaks or pos_breaks or summary.unsupported or summary.needs_fx)
    ledger_health = r.LedgerHealth(
        status="clean" if clean else "breaks", entries=summary.entries, first=summary.first,
        last=summary.last, by_kind=summary.by_kind, unsupported=summary.unsupported[:50],
        needs_fx=summary.needs_fx, fees_not_captured=summary.fees_not_captured,
        position_breaks=[
            r.PositionBreakOut(ticker=b.ticker, ledger_quantity=float(b.ledger_quantity),
                               broker_quantity=float(b.broker_quantity),
                               difference=float(b.difference), reason=b.reason)
            for b in pos_breaks
        ],
        cash_breaks=[
            r.CashBreakOut(start_date=c.start_date, end_date=c.end_date,
                           difference=float(c.difference), explanation=c.explanation)
            for c in cash_breaks[-25:]
        ],
        cash_breaks_total=len(cash_breaks),
        unexplained_cash=float(sum((abs(c.difference) for c in cash_breaks), ZERO)),
        cash_tolerance=float(settings.cash_tolerance),
    )  # fmt: skip
    statuses = {w.twr.status for w in windows if w.twr.value is not None}
    status: r.ReportStatus = "complete" if statuses == {"complete"} and clean else "partial"
    if not statuses:
        status = "unavailable"
    return r.PerformanceResponse(
        status=status, as_of=days[-1].date, base_currency=base_currency,
        account_currency=account_currency, gap=None, windows=windows,
        index=[r.PerformanceSeriesPoint(date=d, value=float(v)) for d, v in index],
        benchmark_index=bench_index, benchmark_label=label, rolling_90d=_rolling(index),
        values=[r.PerformanceSeriesPoint(date=d.date, value=float(d.value)) for d in days],
        ledger=ledger_health,
        costs=_costs(ledger, account_currency, average_account, year_ago),
        timeline=_timeline(ledger),
        freshness_note=" ".join(bench_notes) or None,
        freshness=freshness(days[-1].date, bench_series, dt.date.today()),
        explain=explain.model_copy(update={"gaps": [*bench_notes]}),
    )  # fmt: skip


# --- lots and holding periods ------------------------------------------------------------------


LOTS_FORMULA = [
    "Each purchase is a lot: its cost is the cash it took, fees included, in the account currency.",
    "A sale consumes lots oldest first (FIFO) or at the pooled average cost, as you set. A sale "
    "larger than the ledger holds is listed as unmatched and given no gain.",
    "A lot passes the time test on the first day after its third anniversary (you set the years). "
    "A lot whose ticker has reached its thesis sell target and passes within the warning months "
    "is flagged: a full sale would give up the exemption.",
    "The exemption total is gross sale proceeds, in your limit currency, from lots still inside "
    "the time test on the sale date.",
]


async def lots_report(pool: asyncpg.Pool, settings: PerformanceSettings) -> r.LotsResponse:
    explain = r.PerformanceExplain(formula_version="lots 1.0.0", formula=LOTS_FORMULA)
    snapshots = await _snapshots(pool)
    account_currency = snapshots[-1]["account_currency"] if snapshots else None
    ledger = await _ledger(pool, account_currency, settings)
    if not ledger:
        return r.LotsResponse(
            status="unavailable", as_of=None, method=settings.cost_method, currency=account_currency,
            lots=[], realized=[], unmatched=[], realized_gain=0.0, dividends=0.0, fees=0.0,
            exemption=[], time_test_years=settings.time_test_years,
            warnings=["no transactions stored"], disclaimer=DISCLAIMER, explain=explain,
        )  # fmt: skip
    book = build_lots(ledger, settings.cost_method)
    as_of = snapshots[-1]["snapshot_date"] if snapshots else dt.date.today()
    prices: dict[str, Decimal] = {}
    names: dict[str, str | None] = {}
    currencies: dict[str, str] = {}
    for row in await pool.fetch(
        "select p.ticker, p.current_price, p.instrument_currency, i.name from public.position_snapshot p"
        " left join public.instrument i on i.t212_ticker = p.ticker where p.snapshot_date ="
        " (select max(snapshot_date) from public.position_snapshot)"
    ):
        prices[row["ticker"]] = row["current_price"]
        names[row["ticker"]] = row["name"]
        currencies[row["ticker"]] = row["instrument_currency"]
    warnings: list[str] = []
    theses = await load_active(pool, "theses", ThesesConfig)
    targets: dict[str, Decimal] = {}
    for thesis in theses.body.theses if theses else []:
        target = thesis.price_target
        if target is None or target.sell_target is None:
            continue
        if currencies.get(thesis.ticker) and currencies[thesis.ticker] != target.currency:
            warnings.append(
                f"{thesis.ticker}: sell target is in {target.currency} but the price is quoted in "
                f"{currencies[thesis.ticker]}; the target is not compared"
            )
            continue
        targets[thesis.ticker] = Decimal(str(target.sell_target))
    periods = holding_periods(
        book.lots, as_of, sell_targets=targets, prices=prices,
        years=settings.time_test_years, warn_within_months=Decimal(settings.time_test_warning_months),
    )  # fmt: skip
    lot_cost = {lot.lot_id: lot.unit_cost for lot in book.lots}
    exemption: list[r.ExemptionOut] = []
    if account_currency:
        stored = await load_active(pool, "strategy", StrategyConfig)
        source = stored.body.fx_source if stored and stored.body.fx_source else "ecb"
        start = dt.date(as_of.year - 1, 1, 1)
        fx = await load_fx_history(
            pool, source, [account_currency, settings.exemption_currency], start, as_of
        )
        for year in (as_of.year, as_of.year - 1):
            sales: list[tuple[dt.date, Decimal | None]] = []
            for sale in book.realized:
                if sale.sold_on.year != year:
                    continue
                rate = (
                    ONE
                    if normalise_currency(account_currency)[0]
                    == normalise_currency(settings.exemption_currency)[0]
                    else fx.conversion(account_currency, settings.exemption_currency, sale.sold_on)
                )
                sales.append(
                    (sale.sold_on, None if rate is None else sale.gross_within_time_test * rate)
                )
            y = exemption_year(year, sales, settings.exemption_limit)
            exemption.append(
                r.ExemptionOut(
                    year=y.year,
                    currency=settings.exemption_currency,
                    gross_proceeds=float(y.gross_proceeds),
                    limit=float(y.limit),
                    headroom=float(y.headroom),
                    exceeded=y.exceeded,
                    sales_counted=y.sales_counted,
                    sales_without_rate=y.sales_without_rate,
                )
            )
            if y.sales_without_rate:
                warnings.append(
                    f"{y.year}: {y.sales_without_rate} sale(s) have no stored exchange rate to "
                    f"{settings.exemption_currency}; the total is a lower bound"
                )
    if book.unmatched:
        warnings.append(
            f"{len(book.unmatched)} sale(s) exceed the units the ledger holds (history before the "
            "first stored fill, or a corporate action): no gain is given for the excess"
        )
    if any(e.needs_fx for e in ledger):
        warnings.append("entries in a currency other than the account currency are not included")
    status: r.ReportStatus = "partial" if warnings else "complete"
    return r.LotsResponse(
        status=status, as_of=as_of, method=settings.cost_method, currency=account_currency,
        lots=[
            r.LotOut(
                lot_id=p.lot_id, ticker=p.ticker, name=names.get(p.ticker), bought_on=p.bought_on,
                quantity=float(p.quantity), unit_cost=float(lot_cost[p.lot_id]),
                cost=float(lot_cost[p.lot_id] * p.quantity), passes_on=p.passes_on,
                months_remaining=float(p.months_remaining), passed=p.passed,
                sell_target=r.num(p.sell_target), current_price=r.num(p.current_price),
                target_reached=p.target_reached,
                forfeits_exemption_warning=p.forfeits_exemption_warning,
            )
            for p in periods
        ],
        realized=[
            r.RealizedOut(
                sale_id=s.sale_id, ticker=s.ticker, sold_on=s.sold_on, quantity=float(s.quantity),
                proceeds=float(s.proceeds), cost=float(s.cost), gain=float(s.gain),
                holding_days=s.holding_days, gross_within_time_test=float(s.gross_within_time_test),
            )
            for s in sorted(book.realized, key=lambda s: s.sold_on, reverse=True)
        ],
        unmatched=[
            r.UnmatchedOut(sale_id=u.sale_id, ticker=u.ticker, sold_on=u.sold_on,
                           quantity=float(u.quantity), reason=u.reason)
            for u in book.unmatched
        ],
        realized_gain=float(sum((s.gain for s in book.realized), ZERO)),
        dividends=float(book.dividends), fees=float(book.fees), exemption=exemption,
        time_test_years=settings.time_test_years, warnings=warnings, disclaimer=DISCLAIMER,
        explain=explain,
    )  # fmt: skip


# --- attribution --------------------------------------------------------------------------------


ATTRIBUTION_FORMULA = [
    "Per holding and valuation day: price effect = (closing value - opening value - fills) at the "
    "closing rate, in the listing's own currency; FX effect = opening local value x rate change.",
    "Dividends are income; fill fees (FX conversion included) and cash fees are costs.",
    "gain = ending value - starting value - external flows. Components plus the residual equal the "
    "gain; the residual holds interest, cash translation and broker rounding the data cannot assign.",
    "A holding whose units changed without a stored fill cannot be attributed: it is excluded and "
    "its value is reported as unattributed.",
]


async def attribution_report(
    pool: asyncpg.Pool, settings: PerformanceSettings, days: int
) -> r.AttributionResponse:
    explain = r.PerformanceExplain(
        formula_version=f"attribution {version('attribution')}", formula=ATTRIBUTION_FORMULA
    )
    snapshots = await _snapshots(pool)
    if len(snapshots) < 2:
        return r.AttributionResponse(
            status="unavailable", start=None, end=None, base_currency=None, days=days,
            gap="fewer than two daily snapshots are stored", bridge=None, holdings=[], groups={},
            explain=explain,
        )  # fmt: skip
    end = snapshots[-1]["snapshot_date"]
    window = [s for s in snapshots if s["snapshot_date"] >= end - dt.timedelta(days=days)]
    if len(window) < 2:
        window = snapshots[-2:]
    account_currency = snapshots[-1]["account_currency"]
    ledger = await _ledger(pool, account_currency, settings)
    day_values = _day_values(snapshots, ledger)
    flow_by_day = {d.date: d.flow for d in day_values}
    rate_by_day = {s["snapshot_date"]: s["fx_rate"] for s in snapshots}
    start_date = window[0]["snapshot_date"]
    pos_rows = await pool.fetch(
        "select p.snapshot_date, p.ticker, p.quantity, p.current_price, p.fx_rate, p.value_base,"
        " p.instrument_currency, i.name, i.home_country from public.position_snapshot p"
        " left join public.instrument i on i.t212_ticker = p.ticker"
        " where p.snapshot_date >= $1 and p.snapshot_date <= $2",
        start_date, end,
    )  # fmt: skip
    positions = [
        PositionDay(x["snapshot_date"], x["ticker"], x["quantity"], x["current_price"],
                    x["fx_rate"], x["value_base"])
        for x in pos_rows
    ]  # fmt: skip
    meta = {x["ticker"]: x for x in pos_rows}
    snapshot_days = [s["snapshot_date"] for s in window]

    def snapshot_day(day: dt.date) -> dt.date | None:
        return next((d for d in snapshot_days if d >= day), None)

    trades: list[Trade] = []
    income: list[Amount] = []
    costs: list[Amount] = []
    for e in ledger:
        if e.needs_fx or not (start_date < e.day <= end):
            continue
        # An event belongs to the interval ending on the next snapshot day, at that day's rate.
        bucket = snapshot_day(e.day)
        if bucket is None:
            continue
        rate = rate_by_day.get(bucket, ONE)
        if e.kind in (Kind.BUY, Kind.SELL) and e.ticker and e.price is not None:
            trades.append(Trade(bucket, e.ticker, e.quantity, e.price))
            if e.fees:
                costs.append(Amount(bucket, e.fees * rate, e.ticker))
        elif e.kind is Kind.DIVIDEND:
            income.append(Amount(bucket, e.gross * rate, e.ticker))
        elif e.kind is Kind.FEE:
            costs.append(Amount(bucket, -e.cash_effect * rate))
    account_days = [
        AccountDay(
            s["snapshot_date"], s["total_base"], flow_by_day.get(s["snapshot_date"], ZERO) or ZERO
        )
        for s in window
    ]
    report = attribute(
        account_days, positions, trades, income, costs, ticker_fx_lookup(positions)
    )
    if report is None:
        return r.AttributionResponse(
            status="unavailable", start=None, end=None, base_currency=None, days=days,
            gap="no valuation interval in this window", bridge=None, holdings=[], groups={},
            explain=explain,
        )  # fmt: skip
    stored = await load_active(pool, "strategy", StrategyConfig)
    classifications = stored.body.classifications if stored else {}
    dimensions = stored.body.categories if stored else []
    groups: dict[str, dict[str, float]] = {}
    currency_map = {t: str(m["instrument_currency"]) for t, m in meta.items()}
    country_map = {t: str(m["home_country"]) for t, m in meta.items() if m["home_country"]}
    groups["currency"] = {
        k: float(v) for k, v in group_contributions(report.holdings, currency_map).items()
    }
    groups["country"] = {
        k: float(v) for k, v in group_contributions(report.holdings, country_map).items()
    }
    for dimension in dimensions:
        if dimension in ("currency",):
            continue
        mapping = {t: b[dimension] for t, b in classifications.items() if dimension in b}
        groups[dimension] = {
            k: float(v) for k, v in group_contributions(report.holdings, mapping).items()
        }
    relative: dict[str, RelativeContribution] = {}
    benchmark_out: r.BenchmarkRelativeOut | None = None
    jobs_cfg = await load_active(pool, "jobs", JobsConfig)
    label, bench_series, bench_ccy, bench_basis, bench_notes = await _benchmark_series(
        pool,
        settings.benchmark,
        jobs_cfg.body if jobs_cfg else JobsConfig(),
        report.start - dt.timedelta(days=10),
    )
    bench_return = series_return(bench_series, report.start, report.end) if label else None
    if label is not None and bench_return is not None:
        opening = {p.ticker: p.value_base for p in positions if p.day == report.start}
        relative = {x.ticker: x for x in benchmark_relative(report.holdings, opening, bench_return)}
        notes = list(bench_notes)
        if bench_basis != "total":
            notes.append("the benchmark may leave out dividends, which favours the holdings")
        if bench_ccy != snapshots[-1]["base_currency"]:
            notes.append("the benchmark is not quoted in your base currency")
        benchmark_out = r.BenchmarkRelativeOut(
            label=label, benchmark_return=float(bench_return), basis=bench_basis, currency=bench_ccy,
            like_for_like=bench_basis == "total" and bench_ccy == snapshots[-1]["base_currency"],
            active_total=float(sum((x.active or ZERO for x in relative.values()), ZERO)),
            notes=notes,
        )  # fmt: skip
    start_total = report.start_value
    bridge = report.bridge
    status: r.ReportStatus = "partial" if bridge.status is Status.PARTIAL else "complete"
    return r.AttributionResponse(
        status=status, start=report.start, end=report.end, base_currency=snapshots[-1]["base_currency"],
        days=days, gap=None,
        bridge=r.BridgeOut(
            gain=r.figure(bridge), start_value=float(report.start_value),
            end_value=float(report.end_value), net_external_flows=float(report.net_external_flows),
            net_trading=float(report.net_trading),
            components={k: float(v) for k, v in bridge.components.items()},
            residual=float(bridge.residual), unattributed_tickers=report.unattributed_tickers,
        ),
        holdings=[
            r.ContributionOut(
                ticker=h.ticker, name=meta[h.ticker]["name"] if h.ticker in meta else None,
                price=float(h.price), fx=float(h.fx), income=float(h.income), fees=float(h.fees),
                total=float(h.total),
                pct_of_start=float(h.total / start_total) if start_total else None,
                attributed=h.attributed,
                benchmark_equivalent=_float(relative[h.ticker].benchmark_equivalent)
                if h.ticker in relative else None,
                active=_float(relative[h.ticker].active) if h.ticker in relative else None,
                active_reason=relative[h.ticker].reason if h.ticker in relative else None,
            )
            for h in report.holdings
        ],
        groups=groups, benchmark=benchmark_out, explain=explain,
    )  # fmt: skip


# --- satellites versus core -----------------------------------------------------------------------


SLEEVES_FORMULA = [
    "Core = the tickers you list as core ETFs; stocks = every other holding. Cash belongs to neither.",
    "A sleeve's daily return is (closing value - flow) / opening value - 1, where flow = net buys "
    "at fill value - dividends received + fees (FX conversion included).",
    "The comparison is the annual gap of the core over the stocks across the rolling window, and "
    "needs 12 months of history before it is called a finding.",
]


async def ledger_entries(pool: asyncpg.Pool, settings: PerformanceSettings) -> list[LedgerEntry]:
    """The dated ledger as built for every report, for export."""
    snapshots = await _snapshots(pool)
    account_currency = snapshots[-1]["account_currency"] if snapshots else None
    return await _ledger(pool, account_currency, settings)


def _sleeve_out(s: Any, tickers: list[str]) -> r.SleeveOut:
    return r.SleeveOut(
        name=s.name, start=s.start, end=s.end, days=s.days, total_return=r.num(s.total_return),
        annualized=r.num(s.annualized), reason=s.reason, tickers=tickers,
    )  # fmt: skip


async def sleeves_report(
    pool: asyncpg.Pool, settings: PerformanceSettings, years: int | None = None
) -> r.SleevesResponse:
    """`years` overrides the saved comparison window for this request only."""
    lookback_years = years if years is not None else settings.sleeve_lookback_years
    explain = r.PerformanceExplain(formula_version="sleeves 1.0.0", formula=SLEEVES_FORMULA)
    limit = float(settings.sleeve_gap_points)

    def unavailable(gap: str, as_of: dt.date | None = None) -> r.SleevesResponse:
        return r.SleevesResponse(
            status="unavailable", as_of=as_of, gap=gap, core=None, stocks=None, history_days=0,
            annual_gap_points=None, gap_limit_points=limit, too_early=True, review_item=False,
            message=gap, reconciliation_error=None, explain=explain,
        )  # fmt: skip

    if not settings.core_tickers:
        return unavailable("List your core ETFs in the settings to compare them with your stocks.")
    snapshots = await _snapshots(pool)
    if len(snapshots) < 2:
        return unavailable("fewer than two daily snapshots are stored")
    account_currency = snapshots[-1]["account_currency"]
    ledger = await _ledger(pool, account_currency, settings)
    rows = await pool.fetch(
        "select snapshot_date, ticker, quantity, current_price, fx_rate, value_base"
        " from public.position_snapshot where base_currency = $1 order by snapshot_date",
        snapshots[-1]["base_currency"],
    )
    positions = [PositionDay(x["snapshot_date"], x["ticker"], x["quantity"], x["current_price"],
                             x["fx_rate"], x["value_base"]) for x in rows]  # fmt: skip
    valuation_days = [s["snapshot_date"] for s in snapshots]
    rate_by_day = {s["snapshot_date"]: s["fx_rate"] for s in snapshots}
    fx_for_trades = {(p.day, p.ticker): p.fx for p in positions}
    core = set(settings.core_tickers)
    held = {p.ticker for p in positions}
    stocks = held - core
    trades: list[Trade] = []
    income: list[Amount] = []
    costs: list[Amount] = []
    first = valuation_days[0]
    for e in ledger:
        if e.needs_fx or e.day <= first or not e.ticker:
            continue
        nearest = next((d for d in valuation_days if d >= e.day), None)
        if nearest is None:
            continue
        rate = rate_by_day[nearest]
        if e.kind in (Kind.BUY, Kind.SELL) and e.price is not None:
            trades.append(Trade(nearest, e.ticker, e.quantity, e.price))
            if e.fees:
                costs.append(Amount(nearest, e.fees * rate, e.ticker))
        elif e.kind is Kind.DIVIDEND:
            income.append(Amount(nearest, e.gross * rate, e.ticker))
    fallback = ticker_fx_lookup(positions)
    core_series = sleeve_days(
        core, valuation_days, positions, trades, income, costs, fx_for_trades, fallback
    )
    stock_series = sleeve_days(
        stocks, valuation_days, positions, trades, income, costs, fx_for_trades, fallback
    )
    outcome = compare_sleeves(
        core_series, stock_series, lookback_years=lookback_years,
        gap_points=settings.sleeve_gap_points,
    )  # fmt: skip
    status: r.ReportStatus = "partial" if outcome.too_early else "complete"
    if outcome.annual_gap_points is None:
        status = "unavailable"
    return r.SleevesResponse(
        status=status, as_of=valuation_days[-1], gap=None,
        core=_sleeve_out(outcome.core, sorted(core & held)),
        stocks=_sleeve_out(outcome.stocks, sorted(stocks)),
        history_days=outcome.history_days, annual_gap_points=r.num(outcome.annual_gap_points),
        gap_limit_points=limit, too_early=outcome.too_early, review_item=outcome.review_item,
        message=outcome.message, reconciliation_error=r.num(outcome.reconciliation_error),
        explain=explain,
    )  # fmt: skip
