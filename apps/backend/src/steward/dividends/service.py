# ruff: noqa: E501, RUF001 - response text stays on one line each
"""Dividend read models, the confirmation workflow and the `dividend_forecast` job (plan I1).

Reads stored facts only: `corporate_action` (declared events), the Trading212 ledger (received
payments), the latest position snapshot (shares, price, cost), stored reference rates and the
cached checks. Every figure is computed here; the browser formats it.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.analytics.contract import Coverage, Explain, input_hash, rounded
from steward.analytics.versions import version
from steward.checks.contract import AxisScore, Check
from steward.config.checks import ChecksConfig
from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.performance import PerformanceSettings
from steward.config.store import load_active
from steward.dividends import accuracy as acc
from steward.dividends import forecast as fc
from steward.dividends import metrics as mx
from steward.dividends.model import (
    METHOD,
    ZERO,
    DeclaredEvent,
    Holding,
    HoldingSchedule,
    PaymentPoint,
    ReceivedPayment,
)
from steward.dividends.reconcile import (
    EventGroup,
    FiledDividend,
    Reconciliation,
    filing_confirmable,
    group_events,
    implied_fourth_quarters,
    reconcile,
)
from steward.fx.convert import FxSourceName, normalise_currency
from steward.fx.history import FxHistory, load_fx_history
from steward.jobs.runner import JobContext
from steward.performance.ledger import Kind

FORMULA = [
    "Received: Trading212 dividend rows (net of withholding, account currency), the rows the Performance returns table sums.",
    "Declared: confirmed cash-dividend events whose ex-date or pay date is after today, per share × today's shares × the latest stored reference rate.",
    "Estimated: the trailing cadence (monthly, quarterly, semi-annual, annual) projected from the last regular payment at the last amount; grown by the 5-year dividend growth only when 'assume growth' is on. Needs four regular payments.",
    "Unconfirmed (suggested) events are listed and counted, never added to a total.",
    "Yield on price: next twelve months per share / today's price. Yield on cost: next twelve months income / open lots' cost.",
]


# --------------------------------------------------------------------------------- loading


@dataclass
class Context:
    today: dt.date
    account_currency: str | None
    holdings: dict[str, Holding]
    events: list[DeclaredEvent]
    payments: list[ReceivedPayment]
    held_from: dict[str, dt.date]
    fx_hist: FxHistory | None
    fx_latest: dict[str, tuple[Decimal, dt.date]]
    fx_source: FxSourceName
    rules: ChecksConfig
    open_cost: dict[str, Decimal] = field(default_factory=dict)
    names: dict[str, str | None] = field(default_factory=dict)
    groups: list[EventGroup] = field(default_factory=list)
    rec: Reconciliation | None = None

    def fx(self, src: str, dst: str, day: dt.date) -> Decimal | None:
        if self.fx_hist is None:
            return None
        return self.fx_hist.conversion(src, dst, day)

    def latest_fx(self, src: str, dst: str) -> tuple[Decimal, dt.date | None] | None:
        s_iso, s_factor = normalise_currency(src)
        d_iso, d_factor = normalise_currency(dst)
        if s_iso == d_iso:
            return s_factor / d_factor, None
        home = "CZK" if self.fx_source == "cnb" else "EUR"
        s = (Decimal(1), None) if s_iso == home else self.fx_latest.get(s_iso)
        d = (Decimal(1), None) if d_iso == home else self.fx_latest.get(d_iso)
        if s is None or d is None:
            return None
        days = [x for x in (s[1], d[1]) if x is not None]
        return s_factor * s[0] / (d[0] * d_factor), (min(days) if days else None)


async def rules(pool: asyncpg.Pool) -> ChecksConfig:
    stored = await load_active(pool, "checks", ChecksConfig)
    return stored.body if stored else ChecksConfig()


async def _holdings(pool: asyncpg.Pool) -> tuple[dict[str, Holding], str | None]:
    rows = await pool.fetch(
        "select p.ticker, p.quantity, p.current_price, p.instrument_currency, p.value_account,"
        " p.cost_account, p.account_currency, i.name, u.currency as listing_currency"
        " from public.position_snapshot p"
        " left join public.instrument i on i.t212_ticker = p.ticker"
        " left join public.investment_universe u on u.ticker = p.ticker"
        " where p.snapshot_date = coalesce((select max(snapshot_date) from public.account_snapshot),"
        "  (select max(snapshot_date) from public.position_snapshot))"
    )
    out = {
        r["ticker"]: Holding(
            ticker=r["ticker"],
            name=r["name"],
            quantity=r["quantity"],
            price=r["current_price"],
            instrument_currency=r["instrument_currency"],
            value_account=r["value_account"],
            cost_account=r["cost_account"],
            account_currency=r["account_currency"],
            listing_currency=r["listing_currency"],
        )
        for r in rows
        if r["quantity"] and r["quantity"] > 0
    }
    account = next((h.account_currency for h in out.values() if h.account_currency), None)
    if account is None:
        account = await pool.fetchval(
            "select account_currency from public.account_snapshot order by snapshot_date desc limit 1"
        )
    return out, account


async def _symbol_map(
    pool: asyncpg.Pool, tickers: Sequence[str]
) -> tuple[dict[str, str], dict[int, str]]:
    """Every symbol and listing id a corporate action of each ticker may be stored under."""
    from steward.reference.listings import holding_symbol_names

    profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    body = profiles.body if profiles else HoldingProfilesConfig()
    by_symbol: dict[str, str] = {}
    for t in tickers:
        for name in await holding_symbol_names(pool, t, body):
            by_symbol.setdefault(name, t)
    rows = await pool.fetch(
        "select ticker, listing_id from public.instrument_security_link"
        " where ticker = any($1::text[]) and listing_id is not null",
        list(tickers),
    )
    by_listing = {r["listing_id"]: r["ticker"] for r in rows}
    return by_symbol, by_listing


def _event_currency(raw: dict[str, Any], stored: str | None, listing: str | None) -> str | None:
    if stored:
        return stored
    provider = raw.get("provider_currency") if isinstance(raw, dict) else None
    return provider or listing


async def load_events(
    pool: asyncpg.Pool, tickers: Sequence[str], listing_currency: dict[str, str | None]
) -> list[DeclaredEvent]:
    if not tickers:
        return []
    by_symbol, by_listing = await _symbol_map(pool, tickers)
    rows = await pool.fetch(
        "select id, listing_id, symbol, ex_date, record_date, payment_date, cash_amount, currency,"
        " source, source_url, status, known_at, raw from public.corporate_action"
        " where action_type = 'cash_dividend' and cash_amount is not null and cash_amount > 0"
        " and (symbol = any($1::text[]) or listing_id = any($2::bigint[]))",
        list(by_symbol),
        list(by_listing),
    )
    out: list[DeclaredEvent] = []
    for r in rows:
        ticker = by_listing.get(r["listing_id"]) or by_symbol.get(r["symbol"])
        if ticker is None:
            continue
        raw = r["raw"]
        raw = json.loads(raw) if isinstance(raw, str) else (raw or {})
        provider_row = raw.get("provider_row") if isinstance(raw, dict) else None
        text = (
            " ".join(
                str(v)
                for v in ((provider_row or {}).values() if isinstance(provider_row, dict) else [])
            )
            + " "
            + str(raw.get("type", "") if isinstance(raw, dict) else "")
        )
        out.append(
            DeclaredEvent(
                id=r["id"],
                ticker=ticker,
                symbol=r["symbol"],
                ex_date=r["ex_date"],
                payment_date=r["payment_date"],
                record_date=r["record_date"],
                amount=r["cash_amount"],
                currency=_event_currency(raw, r["currency"], listing_currency.get(ticker)),
                source=r["source"],
                source_url=r["source_url"],
                status=r["status"],
                known_at=r["known_at"],
                flagged_special=fc.is_special_text(text),
            )
        )
    return out


async def load_payments(
    pool: asyncpg.Pool, account_currency: str | None
) -> tuple[list[ReceivedPayment], dict[str, dt.date]]:
    """Dividend rows from the same classified ledger the Performance report reads, with the
    share count and gross per share Trading212 sent; and each ticker's first buy date."""
    from steward.performance.service import ledger_entries

    stored = await load_active(pool, "performance", PerformanceSettings)
    settings = stored.body if stored else PerformanceSettings()
    entries = await ledger_entries(pool, settings)
    raw = {
        r["id"]: r
        for r in await pool.fetch(
            "select id, quantity, price from public.transaction where type = 'dividend'"
        )
    }
    payments: list[ReceivedPayment] = []
    held_from: dict[str, dt.date] = {}
    for e in entries:
        if e.kind is Kind.BUY and e.ticker and e.ticker not in held_from:
            held_from[e.ticker] = e.day
        if e.kind is not Kind.DIVIDEND or e.needs_fx or not e.ticker:
            continue
        r = raw.get(e.id)
        payments.append(
            ReceivedPayment(
                id=e.id,
                ticker=e.ticker,
                paid_on=e.day,
                amount=e.cash_effect,
                currency=account_currency,
                quantity=r["quantity"] if r else None,
                gross_per_share=r["price"] if r else None,
            )
        )
    return payments, held_from


async def _open_cost(pool: asyncpg.Pool) -> dict[str, Decimal]:
    """Open lots' cost per ticker (account currency), from the ledger replay the Lots report uses."""
    from steward.performance.lots import build_lots
    from steward.performance.service import ledger_entries, spin_offs_of

    stored = await load_active(pool, "performance", PerformanceSettings)
    settings = stored.body if stored else PerformanceSettings()
    entries = await ledger_entries(pool, settings)
    if not entries:
        return {}
    book = build_lots(
        entries, settings.cost_method, spin_offs_of(settings), settings.time_test_years
    )
    out: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for lot in book.lots:
        out[lot.ticker] += lot.remaining_quantity * lot.unit_cost
    return dict(out)


async def load_context(
    pool: asyncpg.Pool, today: dt.date | None = None, extra: Sequence[str] = ()
) -> Context:
    today = today or dt.date.today()
    holdings, account = await _holdings(pool)
    payments, held_from = await load_payments(pool, account)
    tickers = sorted({*holdings, *extra, *(p.ticker for p in payments)})
    listing = {t: h.listing_currency or h.instrument_currency for t, h in holdings.items()}
    for row in await pool.fetch(
        "select ticker, currency, name from public.investment_universe where ticker = any($1::text[])",
        tickers,
    ):
        listing.setdefault(row["ticker"], row["currency"])
    events = await load_events(pool, tickers, listing)
    source: FxSourceName = "cnb" if account == "CZK" else "ecb"
    currencies = {
        c
        for c in [
            *(e.currency for e in events),
            *(h.instrument_currency for h in holdings.values()),
            account,
        ]
        if c
    }
    start = min([p.paid_on for p in payments] + [today - dt.timedelta(days=30)])
    fx_hist = await load_fx_history(pool, source, currencies, start, today) if currencies else None
    latest: dict[str, tuple[Decimal, dt.date]] = {}
    isos = sorted({normalise_currency(c)[0] for c in currencies})
    for r in await pool.fetch(
        "select distinct on (currency) currency, rate, rate_date from public.fx_rate"
        " where source = $1 and currency = any($2::text[]) order by currency, rate_date desc",
        source,
        isos,
    ):
        latest[r["currency"]] = (r["rate"], r["rate_date"])
    names = {t: h.name for t, h in holdings.items()}
    for row in await pool.fetch(
        "select t212_ticker, name from public.instrument where t212_ticker = any($1::text[])",
        tickers,
    ):
        names.setdefault(row["t212_ticker"], row["name"])
    ctx = Context(
        today=today,
        account_currency=account,
        holdings=holdings,
        events=events,
        payments=payments,
        held_from=held_from,
        fx_hist=fx_hist,
        fx_latest=latest,
        fx_source=source,
        rules=await rules(pool),
        names=names,
    )
    ctx.groups = group_events(events)
    ctx.rec = reconcile(
        events,
        payments,
        account_currency=account,
        fx=ctx.fx,
        window_days=ctx.rules.dividends.match_window_days,
        tolerance=Decimal(str(ctx.rules.dividends.auto_confirm_tolerance)),
        today=today,
        held_from=held_from,
    )
    return ctx


# --------------------------------------------------------------------------- schedules


@dataclass
class HoldingIncomeFigures:
    schedule: HoldingSchedule
    points: list[PaymentPoint]
    annual: dict[int, Decimal]
    annual_problem: str | None
    growth_5y: Decimal | None
    growth_5y_reason: str | None
    growth_10y: Decimal | None
    growth_10y_reason: str | None
    years_without_cut: int | None


def holding_figures(
    ctx: Context, ticker: str, quantity: Decimal | None = None
) -> HoldingIncomeFigures:
    assert ctx.rec is not None
    h = ctx.holdings.get(ticker)
    qty = quantity if quantity is not None else (h.quantity if h else ZERO)
    instrument_ccy = h.instrument_currency if h else None
    groups = [g for g in ctx.groups if g.event.ticker == ticker]
    points = fc.history_points(
        ticker,
        ctx.rec,
        groups,
        instrument_ccy,
        single_source=ctx.rules.dividends.single_source_history,
    )
    fc.mark_specials(points)
    annual, problem = mx.annual_dps(points)
    g5, r5 = mx.dps_cagr(annual, 5, ctx.today) if not problem else (None, problem)
    g10, r10 = mx.dps_cagr(annual, 10, ctx.today) if not problem else (None, problem)
    schedule = fc.build_schedule(
        ticker,
        today=ctx.today,
        quantity=qty,
        account_currency=ctx.account_currency,
        points=points,
        groups=groups,
        fx=ctx.latest_fx,
        min_payments=ctx.rules.dividends.min_payments_for_estimate,
        assume_growth=ctx.rules.dividends.assume_growth,
        growth_5y=g5,
        withholding_share=ctx.rec.withholding_share(ticker),
    )
    if qty <= 0:
        for row in schedule.rows:
            row.amount = row.amount_net = None
    return HoldingIncomeFigures(
        schedule, points, annual, problem, g5, r5, g10, r10, mx.years_without_cut(annual, ctx.today)
    )


# ---------------------------------------------------------------------------- API models


class PaymentLine(BaseModel):
    ticker: str
    name: str | None
    date: dt.date
    ex_date: dt.date | None = None
    kind: Literal["received", "declared", "estimated", "unconfirmed"]
    amount: Decimal | None = Field(
        description="Account currency; gross for declared and estimated rows, net for received."
    )
    per_share: Decimal | None = None
    currency: str | None = Field(default=None, description="Currency of the per-share amount.")
    note: str | None = None


class MonthBar(BaseModel):
    month: str
    phase: Literal["history", "forecast"]
    received: Decimal
    declared: Decimal
    estimated: Decimal
    payments: list[PaymentLine]


class YearBar(BaseModel):
    year: int
    phase: Literal["history", "forecast", "mixed"]
    received: Decimal
    declared: Decimal
    estimated: Decimal


class ContributorLine(BaseModel):
    ticker: str
    name: str | None
    amount: Decimal
    share: Decimal | None


class NextPayment(BaseModel):
    date: dt.date
    ex_date: dt.date | None
    kind: Literal["declared", "estimated", "unconfirmed"]
    amount: Decimal | None
    per_share: Decimal
    currency: str | None


class DividendScore(BaseModel):
    passes: int
    applicable: int
    unknown: int
    tier: Literal["low", "medium", "high"] | None
    sentence: str


class HoldingIncome(BaseModel):
    ticker: str
    name: str | None
    frequency: str
    status: Literal["complete", "partial", "unavailable"]
    reason: str | None
    pays: bool
    trailing_12m: Decimal
    next_12m: Decimal | None
    next_12m_declared: Decimal
    next_12m_estimated: Decimal
    unconfirmed: int
    yield_on_price: Decimal | None
    yield_on_cost: Decimal | None
    next_payment: NextPayment | None
    growth_5y: Decimal | None
    growth_5y_reason: str | None
    growth_10y: Decimal | None
    years_without_cut: int | None
    withholding_share: Decimal | None
    score: DividendScore | None


class IncomeTier(BaseModel):
    tier: Literal["low", "medium", "high", "unscored"]
    holdings: int
    income: Decimal
    share: Decimal | None
    tickers: list[str]


class AccuracyOut(BaseModel):
    available: bool
    available_from: dt.date | None
    snapshot_as_of: dt.date | None
    rows: int
    hits: int
    hit_rate: Decimal | None
    mean_error: Decimal | None
    note: str | None


class IncomeSummary(BaseModel):
    currency: str | None
    as_of: dt.date
    trailing_12m: Decimal
    next_12m: Decimal
    next_12m_declared: Decimal
    next_12m_estimated: Decimal
    next_12m_unconfirmed: Decimal
    change_vs_trailing: Decimal | None
    monthly_average: Decimal
    yield_on_price: Decimal | None
    yield_on_cost: Decimal | None
    coverage: Coverage
    coverage_sentence: str


class PortfolioDividends(BaseModel):
    status: Literal["complete", "partial", "unavailable"]
    summary: IncomeSummary
    months: list[MonthBar]
    years: list[YearBar]
    largest: list[ContributorLine]
    smallest: list[ContributorLine]
    holdings: list[HoldingIncome]
    tiers: list[IncomeTier]
    tier_rule: str
    unconfirmed_events: int
    without_schedule: list[str]
    disagreements: list[str]
    accuracy: AccuracyOut
    assume_growth: bool
    method_version: str
    input_hash: str
    warnings: list[str]
    explain: Explain


# ----------------------------------------------------------------------------- building


def _tier(
    passes: int, applicable: int, cuts: Sequence[float]
) -> Literal["low", "medium", "high"] | None:
    if applicable <= 0:
        return None
    share = passes / applicable
    if share < cuts[0]:
        return "low"
    if share < cuts[1]:
        return "medium"
    return "high"


async def cached_dividend_scores(
    pool: asyncpg.Pool, owner_id: Any, tickers: Sequence[str], cuts: Sequence[float]
) -> dict[str, DividendScore]:
    """The dividend axis of each holding's latest cached checks (written by the checks job or a
    company page visit); a holding never checked has no score, never a zero."""
    if owner_id is None:
        return {}
    rows = await pool.fetch(
        "select distinct on (ticker) ticker, scores from public.company_check_snapshot"
        " where owner_id = $1 and ticker = any($2::text[]) order by ticker, computed_at desc",
        owner_id,
        list(tickers),
    )
    out: dict[str, DividendScore] = {}
    for r in rows:
        scores = r["scores"]
        scores = json.loads(scores) if isinstance(scores, str) else scores
        d = (scores or {}).get("dividend")
        if not isinstance(d, dict):
            continue
        passes, applicable, unknown = (
            int(d.get("passes", 0)),
            int(d.get("applicable", 0)),
            int(d.get("unknown", 0)),
        )
        tier = _tier(passes, applicable, cuts)
        sentence = (
            "Dividend checks do not apply"
            if applicable == 0
            else f"{passes} of {applicable} applicable dividend checks pass"
        ) + (f"; {unknown} could not be checked" if unknown else "")
        out[r["ticker"]] = DividendScore(
            passes=passes, applicable=applicable, unknown=unknown, tier=tier, sentence=sentence
        )
    return out


def _yield_on_price(ctx: Context, ticker: str, fig: HoldingIncomeFigures) -> Decimal | None:
    h = ctx.holdings.get(ticker)
    if h is None or h.price is None or h.price <= 0 or not h.instrument_currency:
        return None
    rows = [r for r in fig.schedule.rows if r.kind in ("declared", "estimated")]
    if not rows or fig.schedule.status == "unavailable":
        return None
    total = ZERO
    for r in rows:
        if not r.currency:
            return None
        conv = ctx.latest_fx(r.currency, h.instrument_currency)
        if conv is None:
            return None
        total += r.per_share * conv[0]
    return total / h.price


def _line(ctx: Context, row: Any) -> PaymentLine:
    return PaymentLine(
        ticker=row.ticker,
        name=ctx.names.get(row.ticker),
        date=row.pay_date,
        ex_date=row.ex_date,
        kind=row.kind,
        amount=rounded(row.amount, "money"),
        per_share=rounded(row.per_share, "per_share"),
        currency=row.currency,
        note=row.note,
    )


def build_portfolio(ctx: Context, scores: dict[str, DividendScore]) -> PortfolioDividends:
    assert ctx.rec is not None
    today = ctx.today
    figures = {t: holding_figures(ctx, t) for t in sorted(ctx.holdings)}
    schedules = [f.schedule for f in figures.values()]
    held_payments = ctx.payments
    trailing = mx.trailing_received(held_payments, today)
    by_ticker_trailing: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for c in mx.contributors(held_payments, today):
        by_ticker_trailing[c.ticker] = c.amount
    holdings_out: list[HoldingIncome] = []
    declared_total = estimated_total = unconfirmed_total = ZERO
    unconfirmed_count = 0
    paying = with_schedule = 0
    without: list[str] = []
    for t, f in figures.items():
        s = f.schedule
        d, e, u = fc.schedule_total(s)
        declared_total += d
        estimated_total += e
        unconfirmed_total += u
        unconf = sum(1 for r in s.rows if r.kind == "unconfirmed")
        unconfirmed_count += unconf
        pays = s.frequency != "none"
        if pays:
            paying += 1
            if s.status != "unavailable" and any(
                r.kind in ("declared", "estimated") for r in s.rows
            ):
                with_schedule += 1
            else:
                without.append(t)
        cost = ctx.open_cost.get(t) or (ctx.holdings[t].cost_account if t in ctx.holdings else None)
        nxt_rows = [r for r in s.rows if r.pay_date >= today]
        nxt = nxt_rows[0] if nxt_rows else None
        next12 = (d + e) if s.status != "unavailable" or d > 0 else None
        holdings_out.append(
            HoldingIncome(
                ticker=t,
                name=ctx.names.get(t),
                frequency=s.frequency,
                status=s.status,
                reason=s.reason,
                pays=pays,
                trailing_12m=rounded(by_ticker_trailing.get(t, ZERO), "money") or ZERO,
                next_12m=rounded(next12, "money"),
                next_12m_declared=rounded(d, "money") or ZERO,
                next_12m_estimated=rounded(e, "money") or ZERO,
                unconfirmed=unconf,
                yield_on_price=rounded(_yield_on_price(ctx, t, f), "fraction"),
                yield_on_cost=rounded(mx.ratio(next12, cost), "fraction"),
                next_payment=NextPayment(
                    date=nxt.pay_date,
                    ex_date=nxt.ex_date,
                    kind=nxt.kind,
                    amount=rounded(nxt.amount, "money"),
                    per_share=rounded(nxt.per_share, "per_share") or ZERO,
                    currency=nxt.currency,
                )
                if nxt
                else None,
                growth_5y=rounded(f.growth_5y, "fraction"),
                growth_5y_reason=f.growth_5y_reason,
                growth_10y=rounded(f.growth_10y, "fraction"),
                years_without_cut=f.years_without_cut,
                withholding_share=rounded(s.withholding_share, "fraction"),
                score=scores.get(t),
            )
        )
    next12_total = declared_total + estimated_total
    value_total = sum((h.value_account or ZERO for h in ctx.holdings.values()), ZERO)
    cost_total = sum(
        ((ctx.open_cost.get(t) or h.cost_account or ZERO) for t, h in ctx.holdings.items()), ZERO
    )
    # Months: 36 of history, then the current and next twelve.
    hist = mx.received_by_month(held_payments, today, 36)
    fut = mx.forecast_by_month(schedules, today, 12)
    lines_by_month: dict[str, list[PaymentLine]] = defaultdict(list)
    for p in held_payments:
        k = mx.month_key(p.paid_on)
        if k in hist:
            lines_by_month[k].append(PaymentLine(
                ticker=p.ticker, name=ctx.names.get(p.ticker), date=p.paid_on, kind="received",
                amount=rounded(p.amount, "money"), per_share=p.gross_per_share,
                currency=ctx.holdings[p.ticker].instrument_currency if p.ticker in ctx.holdings else None,
            ))  # fmt: skip
    for s in schedules:
        for r in s.rows:
            if r.kind == "unconfirmed":
                continue
            k = mx.month_key(r.pay_date)
            if k in fut:
                lines_by_month[k].append(_line(ctx, r))
    current = mx.month_key(today)
    # History starts at the first dividend: the months before it were not lean, there was no
    # account (or nothing in it paid yet).
    first = min((k for k, v in hist.items() if v), default=current)
    months: list[MonthBar] = []
    for k, v in hist.items():
        if k == current or k < first:
            continue
        months.append(MonthBar(month=k, phase="history", received=rounded(v, "money") or ZERO, declared=ZERO, estimated=ZERO,
                               payments=sorted(lines_by_month[k], key=lambda x: x.date)))  # fmt: skip
    for k, parts in fut.items():
        received_now = hist.get(k, ZERO) if k == current else ZERO
        months.append(MonthBar(
            month=k, phase="forecast", received=rounded(received_now, "money") or ZERO,
            declared=rounded(parts["declared"], "money") or ZERO, estimated=rounded(parts["estimated"], "money") or ZERO,
            payments=sorted(lines_by_month[k], key=lambda x: x.date),
        ))  # fmt: skip
    years: dict[int, dict[str, Decimal]] = defaultdict(
        lambda: {"received": ZERO, "declared": ZERO, "estimated": ZERO}
    )
    phases: dict[int, set[str]] = defaultdict(set)
    for m in months:
        y = int(m.month[:4])
        years[y]["received"] += m.received
        years[y]["declared"] += m.declared
        years[y]["estimated"] += m.estimated
        phases[y].add(m.phase)
    year_bars = [
        YearBar(year=y, phase=("mixed" if len(phases[y]) > 1 else next(iter(phases[y]))),
                received=v["received"], declared=v["declared"], estimated=v["estimated"])
        for y, v in sorted(years.items())[-4:]
    ]  # fmt: skip
    contrib = mx.contributors(held_payments, today)
    lines = [ContributorLine(ticker=c.ticker, name=ctx.names.get(c.ticker), amount=rounded(c.amount, "money") or ZERO,
                             share=rounded(c.share, "fraction")) for c in contrib]  # fmt: skip
    # Tiers by the dividend axis of the cached checks.
    cuts = ctx.rules.dividends.tier_cuts
    tier_map: dict[str, list[str]] = {"low": [], "medium": [], "high": [], "unscored": []}
    income_of = {h.ticker: (h.next_12m or ZERO) for h in holdings_out}
    for h in holdings_out:
        if not h.pays:
            continue
        tier_map[(h.score.tier if h.score and h.score.tier else "unscored")].append(h.ticker)
    tier_income_total = sum((income_of[t] for ts in tier_map.values() for t in ts), ZERO)
    tiers = [
        IncomeTier(
            tier=k,
            holdings=len(v),
            income=rounded(sum((income_of[t] for t in v), ZERO), "money") or ZERO,
            share=rounded(
                mx.ratio(sum((income_of[t] for t in v), ZERO), tier_income_total), "fraction"
            ),
            tickers=v,
        )
        for k, v in tier_map.items()
    ]
    cov = Coverage(numerator=Decimal(with_schedule), denominator=Decimal(paying), basis="count")
    warnings: list[str] = []
    if unconfirmed_count:
        warnings.append(
            f"{unconfirmed_count} suggested dividend event{'s' if unconfirmed_count != 1 else ''} in the next twelve months await confirmation and are not in the totals"
        )
    if without:
        warnings.append(
            f"{len(without)} paying holding{'s' if len(without) != 1 else ''} without a schedule: "
            + ", ".join(ctx.names.get(t) or t for t in without)
        )
    status: Literal["complete", "partial", "unavailable"] = (
        "unavailable"
        if not ctx.holdings
        else ("complete" if with_schedule == paying and not unconfirmed_count else "partial")
    )
    change = mx.ratio(next12_total - trailing, trailing) if trailing > 0 else None
    disagreements = list(ctx.rec.disagreements)
    return PortfolioDividends(
        status=status,
        summary=IncomeSummary(
            currency=ctx.account_currency,
            as_of=today,
            trailing_12m=rounded(trailing, "money") or ZERO,
            next_12m=rounded(next12_total, "money") or ZERO,
            next_12m_declared=rounded(declared_total, "money") or ZERO,
            next_12m_estimated=rounded(estimated_total, "money") or ZERO,
            next_12m_unconfirmed=rounded(unconfirmed_total, "money") or ZERO,
            change_vs_trailing=rounded(change, "fraction"),
            monthly_average=rounded(next12_total / 12, "money") or ZERO,
            yield_on_price=rounded(mx.ratio(next12_total, value_total), "fraction"),
            yield_on_cost=rounded(mx.ratio(next12_total, cost_total), "fraction"),
            coverage=cov,
            coverage_sentence=(
                f"{with_schedule} of {paying} paying holdings have a schedule"
                if paying
                else "No holding has a dividend on record"
            ),
        ),
        months=months,
        years=year_bars,
        largest=lines[:5],
        smallest=list(reversed(lines[-5:])) if len(lines) > 5 else list(reversed(lines)),
        holdings=sorted(holdings_out, key=lambda h: (-(h.next_12m or ZERO), h.ticker)),
        tiers=tiers,
        tier_rule=f"Tier by the share of applicable dividend checks that pass: low below {cuts[0]:.0%}, medium below {cuts[1]:.0%}, high from {cuts[1]:.0%}.",
        unconfirmed_events=unconfirmed_count,
        without_schedule=without,
        disagreements=disagreements,
        accuracy=AccuracyOut(
            available=False,
            available_from=None,
            snapshot_as_of=None,
            rows=0,
            hits=0,
            hit_rate=None,
            mean_error=None,
            note=None,
        ),
        assume_growth=ctx.rules.dividends.assume_growth,
        method_version=version(METHOD),
        input_hash=input_hash(
            {
                "events": sorted((e.id, e.status) for e in ctx.events),
                "payments": sorted(p.id for p in ctx.payments),
                "holdings": sorted((h.ticker, str(h.quantity)) for h in ctx.holdings.values()),
                "rules": ctx.rules.dividends.model_dump(mode="json"),
                "today": today,
            }
        ),
        warnings=warnings,
        explain=Explain(
            formula_version=version(METHOD), formula=" ".join(FORMULA), gaps=[*without]
        ),
    )


async def portfolio_dividends(
    pool: asyncpg.Pool, owner_id: Any, today: dt.date | None = None
) -> PortfolioDividends:
    ctx = await load_context(pool, today)
    ctx.open_cost = await _open_cost(pool)
    scores = await cached_dividend_scores(
        pool, owner_id, list(ctx.holdings), ctx.rules.dividends.tier_cuts
    )
    out = build_portfolio(ctx, scores)
    out.accuracy = await accuracy_for(pool, ctx)
    return out


async def accuracy_for(pool: asyncpg.Pool, ctx: Context) -> AccuracyOut:
    first = await pool.fetchval(
        "select min(as_of) from public.dividend_forecast_snapshot where method_version = $1",
        version(METHOD),
    )
    target = ctx.today - dt.timedelta(days=365)
    as_of = await pool.fetchval(
        "select max(as_of) from public.dividend_forecast_snapshot where as_of <= $1", target
    )
    bodies: dict[str, dict[str, Any]] = {}
    if as_of is not None:
        for r in await pool.fetch(
            "select ticker, schedule from public.dividend_forecast_snapshot where as_of = $1", as_of
        ):
            body = r["schedule"]
            bodies[r["ticker"]] = json.loads(body) if isinstance(body, str) else body
    a = acc.forecast_accuracy(
        bodies, as_of, first, ctx.payments, ctx.today, ctx.rules.dividends.match_window_days
    )
    return AccuracyOut(
        available=a.available, available_from=a.available_from, snapshot_as_of=a.snapshot_as_of,
        rows=a.rows, hits=a.hits, hit_rate=rounded(a.hit_rate, "fraction"),
        mean_error=rounded(a.mean_error, "fraction"), note=a.note,
    )  # fmt: skip


# ------------------------------------------------------------------------------ calendar


class CalendarEvent(BaseModel):
    date: dt.date
    ticker: str
    name: str | None
    kind: Literal[
        "ex_dividend",
        "payment",
        "estimated_ex_dividend",
        "estimated_payment",
        "unconfirmed_ex_dividend",
        "unconfirmed_payment",
        "earnings",
    ]
    amount: Decimal | None = Field(description="Account currency, for payments.")
    per_share: Decimal | None = None
    currency: str | None = None
    note: str | None = None


class DividendCalendar(BaseModel):
    month: str
    first_day: dt.date
    last_day: dt.date
    currency: str | None
    events: list[CalendarEvent]
    total_payments: Decimal
    legend: dict[str, str]


LEGEND = {
    "ex_dividend": "Ex-dividend date (declared): shares bought on or after it do not receive the payment",
    "payment": "Payment date (declared or received)",
    "estimated_ex_dividend": "Estimated ex-dividend date, from the cadence",
    "estimated_payment": "Estimated payment, from the cadence",
    "unconfirmed_ex_dividend": "Suggested ex-dividend date awaiting confirmation",
    "unconfirmed_payment": "Suggested payment awaiting confirmation",
    "earnings": "Expected earnings report",
}


async def calendar(
    pool: asyncpg.Pool, month: str, today: dt.date | None = None
) -> DividendCalendar:
    year, mon = int(month[:4]), int(month[5:7])
    first = dt.date(year, mon, 1)
    last = (dt.date(year + (mon == 12), (mon % 12) + 1, 1)) - dt.timedelta(days=1)
    ctx = await load_context(pool, today)
    assert ctx.rec is not None
    events: list[CalendarEvent] = []

    def add(day: dt.date | None, **kw: Any) -> None:
        if day is not None and first <= day <= last:
            events.append(CalendarEvent(date=day, **kw))

    for t in sorted(ctx.holdings):
        fig = holding_figures(ctx, t)
        name = ctx.names.get(t)
        for r in fig.schedule.rows:
            prefix = {"declared": "", "estimated": "estimated_", "unconfirmed": "unconfirmed_"}[
                r.kind
            ]
            add(r.pay_date, ticker=t, name=name, kind=f"{prefix}payment", amount=rounded(r.amount, "money"),
                per_share=rounded(r.per_share, "per_share"), currency=r.currency, note=r.note)  # fmt: skip
            if r.ex_date is not None:
                add(r.ex_date, ticker=t, name=name, kind=f"{prefix}ex_dividend", amount=None,
                    per_share=rounded(r.per_share, "per_share"), currency=r.currency,
                    note="estimated from the usual ex-to-pay lag" if r.ex_date_estimated else r.note)  # fmt: skip
    # Past declared events and received payments in the month.
    for g in ctx.groups:
        e = g.event
        if e.status != "confirmed" or e.ticker not in ctx.holdings or e.pay_or_ex > ctx.today:
            continue
        add(e.ex_date, ticker=e.ticker, name=ctx.names.get(e.ticker), kind="ex_dividend", amount=None,
            per_share=rounded(e.amount, "per_share"), currency=e.currency, note=None)  # fmt: skip
    for p in ctx.payments:
        if p.paid_on <= ctx.today:
            add(p.paid_on, ticker=p.ticker, name=ctx.names.get(p.ticker), kind="payment",
                amount=rounded(p.amount, "money"), per_share=p.gross_per_share, currency=None, note="received")  # fmt: skip
    for r in await pool.fetch(
        "select t212_ticker, name, next_report_date from public.instrument"
        " where t212_ticker = any($1::text[]) and next_report_date between $2 and $3",
        list(ctx.holdings), first, last,
    ):  # fmt: skip
        add(
            r["next_report_date"],
            ticker=r["t212_ticker"],
            name=r["name"],
            kind="earnings",
            amount=None,
        )
    events.sort(key=lambda e: (e.date, e.kind, e.ticker))
    total = sum(
        (e.amount or ZERO for e in events if e.kind in ("payment", "estimated_payment")), ZERO
    )
    return DividendCalendar(
        month=month, first_day=first, last_day=last, currency=ctx.account_currency, events=events,
        total_payments=rounded(total, "money") or ZERO, legend=LEGEND,
    )  # fmt: skip


# ----------------------------------------------------------------------- company panel


class DividendPoint(BaseModel):
    pay_date: dt.date
    ex_date: dt.date | None
    per_share: Decimal
    currency: str | None
    origin: Literal["event", "receipt", "single_source"]
    special: bool
    ref: str


class AnnualDps(BaseModel):
    year: int
    per_share: Decimal


class ReceivedLine(BaseModel):
    payment_id: str
    paid_on: dt.date
    received: Decimal
    quantity: Decimal | None
    gross_per_share: Decimal | None
    declared_event_id: int | None
    declared_ex_date: dt.date | None
    declared_per_share: Decimal | None
    declared_currency: str | None
    declared_status: str | None
    declared_sources: list[str]
    declared_in_account: Decimal | None
    fx_rate: Decimal | None
    withholding_gap: Decimal | None
    withholding_share: Decimal | None
    agrees: bool | None
    note: str


class EventLine(BaseModel):
    id: int
    ex_date: dt.date
    payment_date: dt.date | None
    per_share: Decimal
    currency: str | None
    status: str
    source: str
    source_url: str
    alternatives: list[str]
    special: bool


class CompanyDividends(BaseModel):
    ticker: str
    name: str | None
    held: bool
    quantity: Decimal | None
    account_currency: str | None
    status: Literal["complete", "partial", "unavailable"]
    reason: str | None
    frequency: str
    history: list[DividendPoint]
    annual: list[AnnualDps]
    annual_problem: str | None
    growth_5y: Decimal | None
    growth_10y: Decimal | None
    years_without_cut: int | None
    schedule: list[PaymentLine]
    specials: list[DividendPoint]
    received: list[ReceivedLine]
    events: list[EventLine]
    unmatched_events: list[EventLine]
    disagreements: list[str]
    buy_before: str | None = Field(
        description="'Buy before <ex-date>' for the next declared or estimated ex-date."
    )
    sources: list[str]
    method_version: str
    explain: Explain
    checks: list[Check] = Field(default_factory=list, description="The six dividend checks.")
    dividend_axis: AxisScore | None = None
    yield_vs_peers: YieldVsPeers | None = None
    payout: Payout | None = None
    filed_dps_check: str | None = Field(
        default=None,
        description="Cross-check of the event history against the filed dividends per share.",
    )


class YieldVsPeers(BaseModel):
    company: Decimal | None
    peer_median: Decimal | None
    peer_count: int
    basis: str | None
    reason: str | None


class Payout(BaseModel):
    earnings: Decimal | None
    cash: Decimal | None
    limit_earnings: Decimal
    limit_cash: Decimal
    fiscal_year: int | None


CompanyDividends.model_rebuild()


def _event_line(g: EventGroup) -> EventLine:
    e = g.event
    return EventLine(
        id=e.id, ex_date=e.ex_date, payment_date=e.payment_date, per_share=e.amount, currency=e.currency,
        status=e.status, source=e.source, source_url=e.source_url,
        alternatives=[f"{a.source}: {a.amount} {a.currency or ''}".strip() for a in g.alternatives],
        special=e.flagged_special,
    )  # fmt: skip


async def company_dividends(
    pool: asyncpg.Pool, ticker: str, today: dt.date | None = None
) -> CompanyDividends:
    ctx = await load_context(pool, today, extra=[ticker])
    assert ctx.rec is not None
    held = ticker in ctx.holdings
    fig = holding_figures(ctx, ticker)
    s = fig.schedule
    received: list[ReceivedLine] = []
    for m in ctx.rec.matches:
        if m.payment.ticker != ticker:
            continue
        e = m.group.event if m.group else None
        received.append(ReceivedLine(
            payment_id=m.payment.id, paid_on=m.payment.paid_on, received=m.payment.amount,
            quantity=m.payment.quantity, gross_per_share=m.payment.gross_per_share,
            declared_event_id=e.id if e else None, declared_ex_date=e.ex_date if e else None,
            declared_per_share=e.amount if e else None, declared_currency=e.currency if e else None,
            declared_status=e.status if e else None, declared_sources=m.group.sources if m.group else [],
            declared_in_account=rounded(m.declared_in_account, "money"), fx_rate=rounded(m.fx_rate, "ratio"),
            withholding_gap=rounded(m.withholding_gap, "money"), withholding_share=rounded(m.withholding_share, "fraction"),
            agrees=m.agrees, note=m.note,
        ))  # fmt: skip
    received.sort(key=lambda r: r.paid_on, reverse=True)
    groups = [g for g in ctx.groups if g.event.ticker == ticker]
    nxt_ex = next(
        (r for r in s.rows if r.ex_date and r.ex_date > ctx.today and r.kind != "unconfirmed"), None
    )
    buy_before = None
    if nxt_ex is not None and nxt_ex.ex_date is not None:
        word = "estimated " if nxt_ex.kind == "estimated" else ""
        buy_before = f"Buy before {nxt_ex.ex_date.isoformat()} ({word}ex-dividend date) to receive the {nxt_ex.pay_date.isoformat()} payment of {rounded(nxt_ex.per_share, 'per_share')} {nxt_ex.currency or ''} per share.".replace(
            " .", "."
        )
    hist = [DividendPoint(pay_date=p.pay_date, ex_date=p.ex_date, per_share=p.per_share, currency=p.currency,
                         origin=p.origin, special=p.special, ref=p.ref) for p in fig.points]  # fmt: skip
    sources = sorted({g.event.source for g in groups} | {a.source for g in groups for a in g.alternatives}
                     | ({"trading212"} if any(m.payment.ticker == ticker for m in ctx.rec.matches) else set()))  # fmt: skip
    return CompanyDividends(
        ticker=ticker, name=ctx.names.get(ticker), held=held,
        quantity=ctx.holdings[ticker].quantity if held else None, account_currency=ctx.account_currency,
        status=s.status, reason=s.reason, frequency=s.frequency,
        history=sorted(hist, key=lambda h: h.pay_date, reverse=True),
        annual=[AnnualDps(year=y, per_share=v) for y, v in sorted(fig.annual.items())],
        annual_problem=fig.annual_problem,
        growth_5y=rounded(fig.growth_5y, "fraction"), growth_10y=rounded(fig.growth_10y, "fraction"),
        years_without_cut=fig.years_without_cut,
        schedule=[_line(ctx, r) for r in s.rows],
        specials=[h for h in hist if h.special],
        received=received,
        events=[_event_line(g) for g in sorted(groups, key=lambda g: g.event.ex_date, reverse=True)],
        unmatched_events=[_event_line(g) for g in ctx.rec.unmatched_events if g.event.ticker == ticker],
        disagreements=[d for g in groups if (d := g.disagreement())],
        buy_before=buy_before,
        sources=sources,
        method_version=version(METHOD),
        explain=Explain(formula_version=version(METHOD), formula=" ".join(FORMULA)),
    )  # fmt: skip


# ----------------------------------------------------------------- confirmation workflow


async def _evidence_for(con: asyncpg.Connection, listing_id: int | None, *, source: str, url: str, day: dt.date,
                        value: Decimal, unit: str, quote: str, vintage: str, raw: dict[str, Any]) -> int:  # fmt: skip
    obs = await con.fetchval(
        """insert into public.source_observation
           (domain, metric, listing_id, period_start, period_end, value, unit, currency, source,
            source_url, known_at, vintage, access_state, raw)
           values ('dividend', 'cash_dividend', $1, $2, $2, $3, $4, $5, $6, $7, now(), $8, 'available', $9::jsonb)
           on conflict do nothing returning id""",
        listing_id, day, value, unit, unit if len(unit) == 3 else None, source, url, vintage,
        json.dumps(raw, default=str),
    )  # fmt: skip
    if obs is None:
        obs = await con.fetchval(
            "select id from public.source_observation where domain = 'dividend' and metric = 'cash_dividend'"
            " and source = $1 and vintage = $2 order by id desc limit 1",
            source, vintage,
        )  # fmt: skip
    evidence: int = await con.fetchval(
        "insert into public.source_evidence (observation_id, quote) values ($1, $2) returning id",
        obs,
        quote,
    )
    return evidence


async def confirm_events(
    pool: asyncpg.Pool,
    ctx: Context,
    ticker: str,
    *,
    event_ids: Sequence[int] | None,
    owner_id: Any,
    auto: bool,
) -> list[int]:
    """Confirm suggested events of one holding. With `event_ids` None, every suggested event whose
    matched Trading212 payment agrees (the auto-confirm rule); the received payment is the
    evidence. With ids, those suggested events, evidenced by the provider's stored row."""
    assert ctx.rec is not None
    from steward.dividends.reconcile import auto_confirmable

    confirmed: list[int] = []
    pairs = {g.event.id: (g, m) for g, m in auto_confirmable(ctx.rec) if g.event.ticker == ticker}
    targets: list[int] = list(pairs) if event_ids is None else [i for i in event_ids]
    by_id = {e.id: e for e in ctx.events if e.ticker == ticker}
    listing = await pool.fetchval(
        "select listing_id from public.instrument_security_link where ticker = $1 and listing_id is not null",
        ticker,
    )
    for eid in targets:
        e = by_id.get(eid)
        if e is None or e.status != "suggested":
            continue
        async with pool.acquire() as con, con.transaction():
            if eid in pairs:
                _g, m = pairs[eid]
                p = m.payment
                quote = (
                    f"Trading212 paid {p.amount} {p.currency or ''} on {p.paid_on.isoformat()} for {p.quantity} shares"
                    f" at {p.gross_per_share} gross per share; declared {e.amount} {e.currency or ''} per share, ex-date {e.ex_date.isoformat()}"
                )
                ev = await _evidence_for(con, listing, source="trading212", url=f"trading212:transaction/{p.id}", day=p.paid_on,
                                         value=p.amount, unit=p.currency or "XXX", quote=quote, vintage=p.id,
                                         raw={"transaction_id": p.id, "event_id": eid})  # fmt: skip
                reason = (
                    ("auto-confirmed: " if auto else "confirmed: ")
                    + f"matched Trading212 payment {p.id} ({'per-share gross agrees' if m.per_share_agrees else 'received amount within the tolerance of the declared gross'})"
                )
            else:
                quote = f"{e.source} states {e.amount} {e.currency or ''} per share, ex-date {e.ex_date.isoformat()}"
                ev = await _evidence_for(con, listing, source=e.source, url=e.source_url, day=e.ex_date, value=e.amount,
                                         unit=e.currency or "XXX", quote=quote, vintage=f"corporate_action:{eid}",
                                         raw={"event_id": eid})  # fmt: skip
                reason = "confirmed by the owner from the provider's stored record"
            done = await con.execute(
                "update public.corporate_action set status = 'confirmed', evidence_id = $2, decided_at = now(),"
                " decided_by = $3::uuid, decision_reason = $4 where id = $1 and status = 'suggested'",
                eid, ev, owner_id, reason,
            )  # fmt: skip
            if done.endswith(" 1"):
                confirmed.append(eid)
    return confirmed


FILED_DPS_CONCEPTS = [
    "us-gaap:CommonStockDividendsPerShareDeclared",
    "us-gaap:CommonStockDividendsPerShareCashPaid",
    "ifrs-full:DividendsRecognisedAsDistributionsToOwnersPerShare",
]


async def load_filed_dividends(pool: asyncpg.Pool, tickers: Sequence[str]) -> list[FiledDividend]:
    """Quarterly per-share dividends from the issuers' filings, the latest-known row per quarter."""
    rows = await pool.fetch(
        "select distinct on (ticker, concept, period_start, period_end) ticker, concept, period_start,"
        # The XBRL unit (iso4217:TWD/shares) states the currency; the column is not always set.
        " period_end, value, coalesce(nullif(substring(unit from '^(?:iso4217:)?([A-Z]{3})(?:/|$)'), ''),"
        " nullif(currency, '')) currency, form, coalesce(filing_url, source_url, '') url"
        " from public.financial_statement_fact where ticker = any($1::text[]) and concept = any($2::text[])"
        # Quarters, and the nine-month and fiscal-year figures the fourth quarter is implied by.
        " and value is not null and (period_end - period_start between 80 and 100"
        "  or period_end - period_start between 260 and 380)"
        " and coalesce(dimensions -> 'members', '[]'::jsonb) = '[]'::jsonb"
        " order by ticker, concept, period_start, period_end, known_at desc",
        list(tickers), FILED_DPS_CONCEPTS,
    )  # fmt: skip
    quarters: list[FiledDividend] = []
    nine_months: list[FiledDividend] = []
    years: list[FiledDividend] = []
    for r in rows:
        f = FiledDividend(
            ticker=r["ticker"],
            period_start=r["period_start"],
            period_end=r["period_end"],
            per_share=Decimal(r["value"]),
            currency=r["currency"],
            concept=r["concept"],
            form=r["form"],
            url=r["url"],
        )
        days = (f.period_end - f.period_start).days
        (quarters if days <= 100 else nine_months if days <= 290 else years).append(f)
    # A 10-K states no fourth quarter of its own, so a Q4 dividend is never in a quarter row.
    filed = {(f.ticker, f.concept, f.period_end) for f in quarters}
    return quarters + [
        q
        for q in implied_fourth_quarters(years, nine_months)
        if (q.ticker, q.concept, q.period_end) not in filed
    ]


async def confirm_from_filings(pool: asyncpg.Pool, ctx: Context) -> dict[str, list[int]]:
    """Confirm the suggested events the issuer's filings back (`filing_confirmable`)."""
    filed = await load_filed_dividends(pool, sorted({e.ticker for e in ctx.events}))
    done: dict[str, list[int]] = defaultdict(list)
    listings = {
        r["ticker"]: r["listing_id"]
        for r in await pool.fetch(
            "select ticker, listing_id from public.instrument_security_link where listing_id is not null"
        )
    }
    for c in filing_confirmable(ctx.groups, filed):
        e = c.group.event
        f = c.filed
        listing = listings.get(e.ticker)
        if f is not None:
            split = f" before a {c.split}-for-1 split" if c.split > 1 else ""
            quote = (
                f"The issuer's {f.form or 'filing'} states {f.per_share} {f.currency or ''} per share for"
                f" {f.period_start.isoformat()} to {f.period_end.isoformat()} ({f.concept.split(':')[-1]}){split};"
                f" {e.source} states {e.amount} {e.currency or ''} per share, ex-date {e.ex_date.isoformat()}"
            )
            source, url, value = "sec_xbrl", f.url or e.source_url, f.per_share
            reason = f"auto-confirmed: agrees with the per-share dividend in the issuer's {f.form or 'filing'}{split}"
        else:
            quote = (
                f"{e.source} states {e.amount} {e.currency or ''} per share, ex-date {e.ex_date.isoformat()};"
                f" it predates the filed record, and the filings agree with {c.corroborated} of the"
                f" holding's {e.source} events and contradict none"
            )
            source, url, value = e.source, e.source_url, e.amount
            reason = f"auto-confirmed: older {e.source} history; the filings agree with {c.corroborated} of its events and contradict none"
        async with pool.acquire() as con, con.transaction():
            ev = await _evidence_for(con, listing, source=source, url=url, day=e.ex_date, value=value,
                                     unit=e.currency or "XXX", quote=quote, vintage=f"filing_check:{e.id}",
                                     raw={"event_id": e.id, "filing": f.url if f else None, "split": c.split})  # fmt: skip
            status = await con.execute(
                "update public.corporate_action set status = 'confirmed', evidence_id = $2, decided_at = now(),"
                " decision_reason = $3 where id = $1 and status = 'suggested'",
                e.id, ev, reason,
            )  # fmt: skip
        if status.endswith(" 1"):
            done[e.ticker].append(e.id)
    return dict(done)


TASK_KIND = "dividend_confirm"


async def dividend_forecast_handler(ctx_job: JobContext) -> None:
    """Daily: auto-confirm matched events (when the rule is on), write each holding's forecast
    snapshot, and keep one "Needs you" line per holding with suggested events to confirm."""
    from steward.ops import tasks

    pool = ctx_job.pool
    today = dt.date.today()
    async with ctx_job.step("dividend_forecast") as step:
        ctx = await load_context(pool, today)
        auto: dict[str, list[int]] = {}
        if ctx.rules.dividends.auto_confirm:
            for t in sorted({e.ticker for e in ctx.events}):
                ids = await confirm_events(pool, ctx, t, event_ids=None, owner_id=None, auto=True)
                if ids:
                    auto[t] = ids
            if auto:
                ctx = await load_context(pool, today)
        step.detail["auto_confirmed"] = auto
        if ctx.rules.dividends.confirm_from_filings:
            from_filings = await confirm_from_filings(pool, ctx)
            if from_filings:
                ctx = await load_context(pool, today)
            step.detail["confirmed_from_filings"] = {t: len(ids) for t, ids in from_filings.items()}
        written = 0
        method = version(METHOD)
        for t in sorted(ctx.holdings):
            fig = holding_figures(ctx, t)
            body = fc.snapshot_body(fig.schedule)
            status = await pool.execute(
                "insert into public.dividend_forecast_snapshot (ticker, as_of, schedule, method_version, input_hash)"
                " values ($1, $2, $3::jsonb, $4, $5) on conflict (ticker, as_of, method_version) do nothing",
                t, today, json.dumps(body), method, input_hash(body),
            )  # fmt: skip
            written += 1 if status.endswith(" 1") else 0
        step.detail["snapshots_written"] = written
        pending: dict[str, int] = defaultdict(int)
        for g in ctx.groups:
            if g.event.status == "suggested" and g.event.ticker in ctx.holdings:
                pending[g.event.ticker] += 1
        for t, n in pending.items():
            await tasks.see(
                pool, TASK_KIND, t,
                title=f"{n} dividend event{'s' if n != 1 else ''} to confirm for {ctx.names.get(t) or t}",
                detail="Suggested by a data provider. Confirm the ones that match the company's announcement; payments Trading212 already made are confirmed automatically when the amounts agree.",
                href=f"/companies/{t}/dividends", weight=0.2, status="needs_owner",
            )  # fmt: skip
        await tasks.settle(pool, TASK_KIND, set(pending))
        step.detail["to_confirm"] = dict(pending)
