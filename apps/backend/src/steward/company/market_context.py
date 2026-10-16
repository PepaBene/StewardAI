"""Market context for a company's Overview: shareholder returns over 7 days and 1 year against
its industry and the market, price volatility (the spread of weekly moves) on the same three, and
up to four competitors with their check-axis scores.

Everything is read from stored daily bars (adjusted closes, as the screens use them) and cached
check scores; nothing is computed by a model. The industry is the confirmed peer set when it has
at least `MIN_INDUSTRY` names, else the other universe members with the same provider industry.
The market is the first benchmark of the Prices config, the series the portfolio history uses.
Every figure is nullable and `reasons` says what was missing; nothing here raises for missing data.
"""

from __future__ import annotations

import datetime as dt
import statistics
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

import asyncpg
from pydantic import BaseModel, Field

from steward.checks.service import latest_scores
from steward.company.ratios import market_from
from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.discovery import read as discovery_read
from steward.discovery.read import _member
from steward.reference.read import index_series

Series = Sequence[tuple[dt.date, float]]

MIN_INDUSTRY = 3
MAX_INDUSTRY = 30
MAX_COMPETITORS = 4
MIN_WEEKS = 26
STALE_DAYS = 10
TOLERANCE_DAYS = 5
WINDOWS = {"7d": 7, "1y": 365}
HISTORY_DAYS = 400


class ReturnRow(BaseModel):
    window: str = Field(description="7d or 1y.")
    company: float | None
    industry: float | None
    market: float | None
    vs_market: float | None = Field(description="Company minus market, as a fraction.")
    vs_industry: float | None


class Volatility(BaseModel):
    company: float | None = Field(description="Sample std of weekly returns, over 52 weeks.")
    industry: float | None
    market: float | None
    company_annualised: float | None = Field(description="Weekly std x sqrt(52).")
    industry_annualised: float | None
    market_annualised: float | None
    sentence: str | None


class Competitor(BaseModel):
    ticker: str
    name: str | None
    last_price: float | None
    currency: str | None
    basis: str = Field(description="confirmed_peer or same_industry.")
    scores: dict[str, Decimal | None] | None = Field(
        description="Latest cached check-axis scores (0-6); null when the company has none."
    )


class CompanyMarketContext(BaseModel):
    ticker: str
    as_of: dt.date | None
    industry_label: str | None
    industry_basis: str | None
    industry_members: int
    market_label: str | None
    returns: list[ReturnRow]
    return_sentence: str | None
    industry_sentence: str | None
    volatility: Volatility
    competitors: list[Competitor]
    reasons: list[str]


def window_return(series: Series, days: int, today: dt.date | None = None) -> tuple[float | None, str | None]:
    """Return over the last `days` calendar days of a series, or (None, why)."""
    pts = sorted((d, c) for d, c in series if c and c > 0)
    if len(pts) < 2:
        return None, "no price history"
    end_d, end_c = pts[-1]
    if today is not None and (today - end_d).days > STALE_DAYS:
        return None, f"last price is from {end_d.isoformat()}"
    target = end_d - dt.timedelta(days=days)
    if pts[0][0] > target + dt.timedelta(days=TOLERANCE_DAYS):
        return None, f"history starts {pts[0][0].isoformat()}, short of {days} days"
    base = next((c for d, c in reversed(pts) if d <= target), pts[0][1])
    return end_c / base - 1, None


def weekly_std(series: Series, today: dt.date | None = None) -> tuple[float | None, str | None]:
    """Sample std of weekly (last close of the ISO week) returns over the last 52 weeks."""
    pts = sorted((d, c) for d, c in series if c and c > 0)
    if not pts:
        return None, "no price history"
    end = pts[-1][0]
    if today is not None and (today - end).days > STALE_DAYS:
        return None, f"last price is from {end.isoformat()}"
    weekly: dict[tuple[int, int], float] = {}
    for d, c in pts:
        if d >= end - dt.timedelta(days=371):
            y, w, _ = d.isocalendar()
            weekly[(y, w)] = c
    closes = [weekly[k] for k in sorted(weekly)]
    rets = [b / a - 1 for a, b in zip(closes, closes[1:], strict=False)]
    if len(rets) < MIN_WEEKS:
        return None, f"{len(rets)} weekly moves, needs {MIN_WEEKS}"
    return statistics.stdev(rets), None


def _mean(values: Sequence[float | None]) -> float | None:
    got = [v for v in values if v is not None]
    return sum(got) / len(got) if len(got) >= 2 else None


def _diff(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else a - b


def compare_sentence(subject: str, diff: float | None, against: str, span: str) -> str | None:
    """Deterministic pass sentence; the gap is in percentage points of return."""
    if diff is None:
        return None
    pct = f"{abs(diff) * 100:.1f}%"
    if round(abs(diff) * 100, 1) == 0:
        return f"{subject} matched the {against} over the {span}"
    return f"{'Outperformed' if diff > 0 else 'Underperformed'} the {against} by {pct} over the {span}"


def volatility_sentence(company: float | None, industry: float | None, market: float | None) -> str | None:
    if company is None:
        return None
    parts = [f"Average weekly move {company * 100:.1f}%"]
    for label, other in (("market", market), ("industry", industry)):
        if other is None or other <= 0:
            continue
        ratio = company / other
        word = "more volatile than" if ratio > 1.1 else "less volatile than" if ratio < 0.9 else "about as volatile as"
        parts.append(f"{word} the {label} ({other * 100:.1f}%)")
    return ", ".join(parts[:1]) if len(parts) == 1 else f"{parts[0]}: {'; '.join(parts[1:])}"


def build_context(
    ticker: str,
    own: Series,
    industry: Mapping[str, Series],
    market: Series | None,
    today: dt.date,
    *,
    industry_label: str | None = None,
    industry_basis: str | None = None,
    market_label: str | None = None,
    competitors: list[Competitor] | None = None,
    reasons: list[str] | None = None,
) -> CompanyMarketContext:
    reasons = list(reasons or [])
    rows: list[ReturnRow] = []
    for name, days in WINDOWS.items():
        c, why_c = window_return(own, days, today)
        m, why_m = window_return(market, days, today) if market else (None, "no market series")
        members = [window_return(s, days, today)[0] for s in industry.values()]
        i = _mean(members)
        for who, why, val in (("company", why_c, c), ("market", why_m, m)):
            if val is None and why:
                reasons.append(f"{who} {name} return: {why}")
        if i is None:
            reasons.append(f"industry {name} return: fewer than 2 members with {name} history")
        rows.append(ReturnRow(window=name, company=c, industry=i, market=m,
                              vs_market=_diff(c, m), vs_industry=_diff(c, i)))  # fmt: skip
    year = next(r for r in rows if r.window == "1y")
    sc, why = weekly_std(own, today)
    if sc is None and why:
        reasons.append(f"company volatility: {why}")
    sm, why_m = weekly_std(market, today) if market else (None, "no market series")
    if sm is None and why_m:
        reasons.append(f"market volatility: {why_m}")
    si = _mean([weekly_std(s, today)[0] for s in industry.values()])
    ann = (52**0.5)
    vol = Volatility(
        company=sc, industry=si, market=sm,
        company_annualised=sc * ann if sc is not None else None,
        industry_annualised=si * ann if si is not None else None,
        market_annualised=sm * ann if sm is not None else None,
        sentence=volatility_sentence(sc, si, sm),
    )  # fmt: skip
    return CompanyMarketContext(
        ticker=ticker, as_of=today, industry_label=industry_label, industry_basis=industry_basis,
        industry_members=len(industry), market_label=market_label, returns=rows,
        return_sentence=compare_sentence("Company", year.vs_market, "market", "past year"),
        industry_sentence=compare_sentence("Company", year.vs_industry, "industry", "past year"),
        volatility=vol, competitors=competitors or [], reasons=reasons,
    )  # fmt: skip


# ------------------------------------------------------------------------------- loading


async def _peer_tickers(
    pool: asyncpg.Pool, ticker: str
) -> tuple[list[str], list[str], str | None]:
    """(confirmed peers, same-industry universe members, the provider industry)."""
    confirmed = [
        r["t"]
        for r in await pool.fetch(
            "select distinct peer_ticker as t from public.peer_set"
            " where ticker=$1 and status='confirmed' and peer_ticker<>$1 order by 1",
            ticker,
        )
    ]
    industry = await pool.fetchval(
        "select text_value from public.company_research_observation"
        " where provider='fmp' and dataset='profile' and metric='industry' and ticker=$1"
        " and text_value is not null order by known_at desc limit 1",
        ticker,
    )
    same: list[str] = []
    if industry:
        same = [
            r["t"]
            for r in await pool.fetch(
                """select distinct o.ticker as t from public.company_research_observation o
                   join public.investment_universe u on u.ticker = o.ticker
                   where o.dataset='profile' and o.metric='industry' and o.text_value=$1
                     and o.ticker<>$2 and u.has_prices order by 1 limit $3""",
                industry, ticker, MAX_INDUSTRY,
            )
        ]  # fmt: skip
    return confirmed, same, industry


def _float_series(rows: Sequence[tuple[dt.date, Decimal]]) -> list[tuple[dt.date, float]]:
    return [(d, float(c)) for d, c in rows]


async def market_context(
    pool: asyncpg.Pool, owner_id: Any, ticker: str, today: dt.date | None = None
) -> CompanyMarketContext | None:
    """None when the ticker is not in the universe; otherwise a context with reasons."""
    today = today or dt.date.today()
    row = await pool.fetchrow("select * from public.investment_universe where ticker=$1", ticker)
    if row is None:
        return None
    reasons: list[str] = []
    confirmed, same, industry = await _peer_tickers(pool, ticker)
    use_confirmed = len(confirmed) >= MIN_INDUSTRY
    group = confirmed if use_confirmed else same
    basis = "confirmed_peer" if use_confirmed else ("same_industry" if same else None)
    if not group:
        reasons.append("no confirmed peers and no other stored company in the same industry")
    candidates = list(dict.fromkeys([*confirmed, *same]))[: MAX_INDUSTRY * 2]
    wanted = [ticker, *candidates]
    members = [
        _member(r)
        for r in await pool.fetch(
            "select * from public.investment_universe where ticker = any($1::text[])", wanted
        )
    ]
    closes = await discovery_read.closes_between(
        pool, members, today - dt.timedelta(days=HISTORY_DAYS), today
    )
    by_ticker = {m.ticker: m for m in members}
    major: dict[str, list[tuple[dt.date, float]]] = {}
    last_price: dict[str, tuple[float, str | None]] = {}
    for t, rows in closes.items():
        m = by_ticker[t]
        mk = market_from(rows, m.currency, None)
        major[t] = _float_series(mk.closes)
        if mk.closes:
            last_price[t] = (float(mk.closes[-1][1]), mk.currency)
    if ticker not in major:
        reasons.append("no stored daily prices for this company")
    industry_series = {t: major[t] for t in group if t in major}
    if group and len(industry_series) < len(group):
        reasons.append(f"{len(group) - len(industry_series)} industry members have no stored prices")

    market: list[tuple[dt.date, float]] | None = None
    market_label: str | None = None
    stored = await load_active(pool, "jobs", JobsConfig)
    prices = (stored.body if stored else JobsConfig()).prices
    if prices.benchmark_symbols:
        market_label = prices.index_label(prices.benchmark_symbols[0])
        _ref, bars = await index_series(
            pool, prices, prices.benchmark_symbols[0], today - dt.timedelta(days=HISTORY_DAYS)
        )
        market = bars or None
        if market is None:
            reasons.append(f"no stored prices for the benchmark {market_label}")
    else:
        reasons.append("no benchmark is configured in the Prices config")

    scores = await latest_scores(pool, owner_id, candidates[:MAX_COMPETITORS * 3] or None) if candidates else {}
    comps: list[Competitor] = []
    for t in candidates:
        if len(comps) >= MAX_COMPETITORS:
            break
        m = by_ticker.get(t)
        if m is None:
            continue
        price, ccy = last_price.get(t, (None, m.currency))
        cached = scores.get(t)
        comps.append(Competitor(
            ticker=t, name=m.name, last_price=price, currency=ccy,
            basis="confirmed_peer" if t in confirmed else "same_industry",
            scores={a: s.score for a, s in cached.axes.items()} if cached else None,
        ))  # fmt: skip
    if not comps:
        reasons.append("no competitors to list")
    return build_context(
        ticker, major.get(ticker, []), industry_series, market, today,
        industry_label=industry, industry_basis=basis, market_label=market_label,
        competitors=comps, reasons=reasons,
    )  # fmt: skip
