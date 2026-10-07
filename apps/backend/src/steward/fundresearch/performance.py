"""Fund return against a documented benchmark (plan M3.4).

A fund's exchange-listing price is not its NAV, and an index's price series is not its total-return
series. The tracking difference is therefore computed only when both sides are on the same basis
(price against price, total against total, same currency); otherwise both returns are shown and
the difference is withheld with the reason. NAV is not stored, so only listing prices are used.
"""

# ruff: noqa: E501
from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from decimal import Decimal
from itertools import pairwise
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.analytics import risk
from steward.analytics.contract import AnalyticsResult, unavailable
from steward.analytics.returns import Dividend, price_return, total_return
from steward.analytics.versions import version
from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.fundresearch.data import FundData, FundListing
from steward.fx.convert import normalise_currency

ZERO = Decimal(0)
ONE = Decimal(1)
Basis = Literal["price", "total", "unknown"]
PERIODS: tuple[tuple[str, int], ...] = (("1 year", 365), ("3 years", 3 * 365), ("5 years", 5 * 365))
START_TOLERANCE_DAYS = 7
MIN_SESSIONS = 30
MAX_CHART_POINTS = 300
Closes = list[tuple[dt.date, Decimal]]


class BenchmarkOption(BaseModel):
    symbol: str
    label: str
    basis: Basis
    currency: str | None = None
    has_prices: bool = True


class BenchmarkInfo(BaseModel):
    symbol: str
    label: str
    basis: Basis
    currency: str | None
    note: str


class PeriodReturn(BaseModel):
    label: str
    start: dt.date | None
    end: dt.date | None
    fund_price: AnalyticsResult
    fund_total: AnalyticsResult | None
    benchmark: AnalyticsResult | None
    compared_on: Basis | None = Field(
        description="Which fund return the benchmark was compared with."
    )
    difference: Decimal | None = Field(
        description="Cumulative fund return minus benchmark return, same basis."
    )
    difference_annualised: Decimal | None
    note: str | None = None


class RiskStat(BaseModel):
    id: str
    label: str
    fund: AnalyticsResult
    benchmark: AnalyticsResult | None = None


class PerformancePoint(BaseModel):
    date: dt.date
    fund: Decimal
    fund_total: Decimal | None = None
    benchmark: Decimal | None = None


class Alignment(BaseModel):
    start: dt.date | None
    end: dt.date | None
    sessions: int
    note: str


class PerformanceView(BaseModel):
    isin: str
    listing: FundListing | None
    price_currency: str | None
    fund_policy: str | None
    nav_available: bool = False
    nav_note: str = "NAV is not stored. Returns are for the exchange listing's price, which differs from NAV by the daily premium or discount."
    benchmark: BenchmarkInfo | None
    benchmark_options: list[BenchmarkOption]
    suggested_benchmark: str | None
    alignment: Alignment | None
    periods: list[PeriodReturn]
    risk: list[RiskStat]
    tracking_error: AnalyticsResult | None
    series: list[PerformancePoint]
    warnings: list[str]
    method_version: str = Field(default_factory=lambda: version("price_return"))


def _at_or_before(closes: Closes, day: dt.date) -> tuple[dt.date, Decimal] | None:
    best = None
    for d, c in closes:
        if d <= day:
            best = (d, c)
        else:
            break
    return best


def align(a: Closes, b: Closes) -> list[tuple[dt.date, Decimal, Decimal]]:
    by_b = dict(b)
    return [(d, c, by_b[d]) for d, c in a if d in by_b]


def daily_returns(
    closes: Closes, dividends: Sequence[Dividend] = ()
) -> list[tuple[dt.date, Decimal]]:
    """`(close_t + dividend)/close_{t-1} - 1`; a dividend on a non-trading day is paid at the next close."""
    out: list[tuple[dt.date, Decimal]] = []
    for (d0, c0), (d1, c1) in pairwise(closes):
        due = sum((x.amount for x in dividends if d0 < x.ex_date <= d1), ZERO)
        out.append((d1, (c1 + due) / c0 - ONE))
    return out


def _annualised(fund: Decimal, bench: Decimal, days: int) -> Decimal | None:
    if days < 365:
        return None
    years = Decimal(days) / Decimal("365.25")
    try:
        return (ONE + fund) ** (ONE / years) - (ONE + bench) ** (ONE / years)
    except (ArithmeticError, ValueError):
        return None


def _fund_total(
    closes: Closes,
    dividends: Sequence[Dividend],
    policy: str | None,
    start: dt.date,
    end: dt.date,
    dividend_note: list[str],
) -> AnalyticsResult:
    window = [(d, c) for d, c in closes if start <= d <= end]
    inside = [x for x in dividends if start < x.ex_date <= end]
    if policy == "accumulating":
        complete = True
        dividend_note.append("accumulating share class: income stays in the price")
    elif policy == "distributing":
        complete = bool(inside)
        if inside:
            dividend_note.append(f"{len(inside)} recorded distributions assumed to be all of them")
    else:
        complete = False
    return total_return(window, inside, distributions_complete=complete)


def build_performance(
    data: FundData,
    listing: FundListing | None,
    fund_closes: Closes,
    dividends: Sequence[Dividend],
    dividend_currency_ok: bool,
    benchmark: BenchmarkInfo | None,
    bench_closes: Closes,
    options: list[BenchmarkOption],
    suggested: str | None,
    as_of: dt.date,
) -> PerformanceView:
    policy = (data.factsheet or {}).get("distribution_policy")
    warnings: list[str] = []
    ccy = None
    closes = [(d, c) for d, c in fund_closes if d <= as_of]
    if listing and listing.currency:
        ccy, factor = normalise_currency(listing.currency)
        closes = [(d, c * factor) for d, c in closes]
    if not closes:
        reason = (
            "no stored prices for any exchange listing of this fund"
            if listing is None
            else "no stored prices for this listing"
        )
        return PerformanceView(
            isin=data.isin,
            listing=listing,
            price_currency=ccy,
            fund_policy=policy,
            benchmark=benchmark,
            benchmark_options=options,
            suggested_benchmark=suggested,
            alignment=None,
            periods=[],
            risk=[],
            tracking_error=None,
            series=[],
            warnings=[reason],
        )
    divs = list(dividends) if dividend_currency_ok else []
    if dividends and not dividend_currency_ok:
        warnings.append(
            "Recorded distributions are in a different currency from the listing price and are not used."
        )

    bclose = [(d, c) for d, c in bench_closes if d <= as_of] if benchmark else []
    use_bench = benchmark is not None and len(bclose) > 1
    if benchmark is not None and not use_bench:
        warnings.append(f"No stored prices for the benchmark series {benchmark.symbol}.")
    aligned = align(closes, bclose) if use_bench else [(d, c, ZERO) for d, c in closes]
    fund_aligned: Closes = [(d, c) for d, c, _ in aligned]
    bench_aligned: Closes = [(d, b) for d, _, b in aligned] if use_bench else []
    if use_bench and len(aligned) < MIN_SESSIONS:
        warnings.append(
            f"Only {len(aligned)} sessions are shared by the fund and the benchmark; no comparison is drawn."
        )
        use_bench = False
        fund_aligned, bench_aligned = closes, []
    end = fund_aligned[-1][0]
    alignment = Alignment(
        start=fund_aligned[0][0],
        end=end,
        sessions=len(fund_aligned),
        note="Both series are cut to the sessions they share."
        if use_bench
        else "Fund listing only; no benchmark is compared.",
    )
    bench_basis: Basis = benchmark.basis if (benchmark and use_bench) else "unknown"
    if benchmark and use_bench:
        if benchmark.currency is None:
            warnings.append(
                "The benchmark's currency is not recorded; the difference assumes it matches the listing currency."
            )
        elif ccy and benchmark.currency != ccy:
            warnings.append(
                f"The benchmark is in {benchmark.currency} and the listing in {ccy}; the difference mixes currencies and is withheld."
            )

    mismatch_currency = bool(
        benchmark and use_bench and benchmark.currency and ccy and benchmark.currency != ccy
    )
    periods: list[PeriodReturn] = []
    targets = [(label, days) for label, days in PERIODS] + [
        ("Since the series starts", (end - fund_aligned[0][0]).days)
    ]
    for label, days in targets:
        start_d: dt.date | None
        if label.startswith("Since"):
            start_d = fund_aligned[0][0]
        else:
            hit = _at_or_before(fund_aligned, end - dt.timedelta(days=days))
            start_d = (
                hit[0]
                if hit and (end - dt.timedelta(days=days) - hit[0]).days <= START_TOLERANCE_DAYS
                else None
            )
        if start_d is None or start_d >= end:
            periods.append(
                PeriodReturn(
                    label=label,
                    start=None,
                    end=end,
                    fund_price=unavailable(
                        "fraction",
                        version("price_return"),
                        f"price history starts {fund_aligned[0][0]}, after the start of this window",
                    ),
                    fund_total=None,
                    benchmark=None,
                    compared_on=None,
                    difference=None,
                    difference_annualised=None,
                )
            )
            continue
        c0 = dict(fund_aligned)[start_d]
        c1 = dict(fund_aligned)[end]
        fp = price_return((start_d, c0), (end, c1), currency=ccy or "")
        notes: list[str] = []
        ft = _fund_total(fund_aligned, divs, policy, start_d, end, notes)
        bm: AnalyticsResult | None = None
        compared: Basis | None = None
        diff = ann = None
        note = None
        if use_bench:
            b = dict(bench_aligned)
            bm = price_return(
                (start_d, b[start_d]),
                (end, b[end]),
                currency=benchmark.currency or "" if benchmark else "",
            )
            if mismatch_currency:
                note = "currencies differ"
            elif bench_basis == "price":
                compared = "price"
                if fp.value is not None and bm.value is not None:
                    diff = fp.value - bm.value
                    ann = _annualised(fp.value, bm.value, (end - start_d).days)
            elif bench_basis == "total":
                compared = "total"
                if ft.value is not None and bm.value is not None:
                    diff = ft.value - bm.value
                    ann = _annualised(ft.value, bm.value, (end - start_d).days)
                else:
                    note = "the fund's total return needs a complete distribution history, so it cannot be compared with a total-return index"
            else:
                note = "the benchmark's return basis (price or total) is not recorded, so no difference is drawn"
            if diff is None and note is None:
                note = "one of the two returns is unavailable"
        if notes:
            note = (note + "; " if note else "") + "; ".join(notes)
        periods.append(
            PeriodReturn(
                label=label,
                start=start_d,
                end=end,
                fund_price=fp,
                fund_total=ft,
                benchmark=bm,
                compared_on=compared,
                difference=diff,
                difference_annualised=ann,
                note=note,
            )
        )

    frets = daily_returns(fund_aligned)
    fvals = [r for _, r in frets]
    risk_rows = [
        RiskStat(
            id="volatility",
            label="Volatility, annualised (daily price returns)",
            fund=risk.volatility(fvals),
            benchmark=risk.volatility([r for _, r in daily_returns(bench_aligned)])
            if use_bench
            else None,
        ),
        RiskStat(
            id="drawdown",
            label="Maximum drawdown",
            fund=risk.max_drawdown(fvals),
            benchmark=risk.max_drawdown([r for _, r in daily_returns(bench_aligned)])
            if use_bench
            else None,
        ),
    ]
    te: AnalyticsResult | None = None
    if use_bench and bench_basis == "price" and not mismatch_currency:
        diffs = [a - b for (_, a), (_, b) in zip(frets, daily_returns(bench_aligned), strict=True)]
        te = risk.volatility(diffs)
    elif use_bench:
        te = unavailable(
            "fraction",
            version("risk"),
            "tracking error needs fund and benchmark returns on the same basis and currency",
        )
    series = _series(fund_aligned, bench_aligned if use_bench else [], divs, policy)
    return PerformanceView(
        isin=data.isin,
        listing=listing,
        price_currency=ccy,
        fund_policy=policy,
        benchmark=benchmark,
        benchmark_options=options,
        suggested_benchmark=suggested,
        alignment=alignment,
        periods=periods,
        risk=risk_rows,
        tracking_error=te,
        series=series,
        warnings=warnings,
    )


def _series(
    fund: Closes, bench: Closes, divs: Sequence[Dividend], policy: str | None
) -> list[PerformancePoint]:
    """Indexed to 100 at the window start: listing price, the fund's total return when its
    distributions are recorded, and the benchmark on its own basis."""
    window = fund[-(5 * 252) :]
    if not window:
        return []
    start, f0 = window[0]
    bd = dict(bench)
    b0 = bd.get(start)
    show_total = policy == "distributing" and any(d.ex_date > start for d in divs)
    level = ONE
    prev_d, prev_c = window[0]
    out: list[PerformancePoint] = []
    for d, c in window:
        if d != start:
            due = sum((x.amount for x in divs if prev_d < x.ex_date <= d), ZERO)
            level *= (c + due) / prev_c
        prev_d, prev_c = d, c
        out.append(
            PerformancePoint(
                date=d,
                fund=c / f0 * 100,
                fund_total=level * 100 if show_total else None,
                benchmark=bd[d] / b0 * 100 if (b0 and d in bd) else None,
            )
        )
    if len(out) > MAX_CHART_POINTS:
        stride = math.ceil(len(out) / MAX_CHART_POINTS)
        out = [p for i, p in enumerate(out) if i % stride == 0 or i == len(out) - 1]
    return out


async def load_prices(pool: asyncpg.Pool, symbol: str, since: dt.date, until: dt.date) -> Closes:
    rows = await pool.fetch(
        """select date, close, source from public.price_bar
           where symbol=$1 and bar_type='adjusted' and access_state='available' and date between $2 and $3
           order by source, date""",
        symbol,
        since,
        until,
    )
    by_source: dict[str, Closes] = {}
    for r in rows:
        by_source.setdefault(r["source"], []).append((r["date"], r["close"]))
    if not by_source:
        return []
    # One source of record per series: the one with the newest last bar, then the most bars.
    best = max(by_source.values(), key=lambda s: (s[-1][0], len(s)))
    return best


async def load_dividends(
    pool: asyncpg.Pool, ticker: str
) -> list[tuple[dt.date, Decimal, str | None]]:
    rows = await pool.fetch(
        """select a.ex_date, a.cash_amount, a.currency from public.corporate_action a
           join public.instrument_security_link k on k.listing_id=a.listing_id
           where k.ticker=$1 and a.action_type='cash_dividend' and a.status='confirmed' and a.cash_amount is not null
           order by a.ex_date""",
        ticker,
    )
    return [(r["ex_date"], r["cash_amount"], r["currency"]) for r in rows]


async def benchmark_options(pool: asyncpg.Pool) -> list[BenchmarkOption]:
    """Index series the app can compare with: those in the coverage ledger (which says whether the
    series is a price or total-return index) and the configured benchmark symbols (basis unknown)."""
    ledger = await pool.fetch(
        "select subject_key, label, total_return_available from public.fund_index_coverage where subject_kind='index' order by subject_key"
    )
    stored = await load_active(pool, "jobs", JobsConfig)
    config = stored.body if stored else JobsConfig()
    seen: dict[str, BenchmarkOption] = {}
    for r in ledger:
        basis: Basis = (
            "unknown"
            if r["total_return_available"] is None
            else ("total" if r["total_return_available"] else "price")
        )
        seen[r["subject_key"]] = BenchmarkOption(
            symbol=r["subject_key"], label=r["label"], basis=basis
        )
    for sym in config.prices.benchmark_symbols:
        seen.setdefault(str(sym), BenchmarkOption(symbol=str(sym), label=str(sym), basis="unknown"))
    out: list[BenchmarkOption] = []
    for opt in seen.values():
        has = await pool.fetchval(
            "select exists (select 1 from public.price_bar where symbol=$1)", opt.symbol
        )
        out.append(opt.model_copy(update={"has_prices": bool(has)}))
    return out


def suggest(options: Sequence[BenchmarkOption], benchmark_name: str | None) -> str | None:
    """A benchmark only when its label equals the factsheet's benchmark name (case-insensitive)."""
    if not benchmark_name:
        return None
    norm = benchmark_name.strip().lower()
    for o in options:
        if o.has_prices and o.label.strip().lower() == norm:
            return o.symbol
    return None


def pick_listing(data: FundData, ticker: str | None) -> FundListing | None:
    if ticker:
        return next((x for x in data.listings if x.ticker == ticker), None)
    return next(
        (x for x in data.listings if x.has_prices), data.listings[0] if data.listings else None
    )


def tracking_headline(view: PerformanceView) -> tuple[Decimal | None, str | None]:
    """The annualised tracking difference for the checklist: the longest period that has one."""
    for p in reversed(view.periods):
        if p.difference_annualised is not None:
            return p.difference_annualised, None
    reasons = [p.note for p in view.periods if p.note]
    return None, (
        reasons[0]
        if reasons
        else "no benchmark series is selected"
        if view.benchmark is None
        else "no period has a same-basis difference"
    )


__all__: list[Any] = [
    "BenchmarkInfo",
    "BenchmarkOption",
    "PerformanceView",
    "benchmark_options",
    "build_performance",
    "load_dividends",
    "load_prices",
    "pick_listing",
    "suggest",
    "tracking_headline",
]
