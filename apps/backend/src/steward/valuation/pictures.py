# ruff: noqa: E501 - reason strings read better unbroken
"""Valuation pictures (insight plan I7): the company's key multiple beside its peers, inside its
sector of the covered universe, against its own history, and against a peer regression.

Every picture is either drawn from enough data or absent with the reason; a median is never drawn
from fewer than `min_peers_for_median` peers and a distribution never from fewer than
`min_universe_for_percentile` members. Nothing here is a recommendation.
"""

from __future__ import annotations

import datetime as dt
import statistics
from collections.abc import Sequence
from decimal import Decimal
from typing import Any, Literal

import asyncpg
import numpy as np
from pydantic import BaseModel, Field

from steward.analytics.contract import rounded
from steward.analytics.thresholds import THRESHOLDS
from steward.analytics.versions import version

KeyMultiple = Literal["pe", "ps", "pb"]
LABEL: dict[str, str] = {"pe": "P/E", "ps": "P/S", "pb": "P/B", "forward_pe": "Forward P/E"}
# The discovery metric that measures the same multiple across the covered universe.
UNIVERSE_METRIC: dict[str, str] = {"pe": "pe_ratio", "ps": "price_to_sales", "pb": "price_to_book"}
GROWTH_ID = "revenue_cagr_3y"
MARGIN_ID = "net_margin"
HISTORY_WINDOWS = (1, 3, 5, 10)
HIST_BINS = 12
SOURCE_STATEMENTS = "SEC XBRL and filed statements; stored closes"


class PictureFooter(BaseModel):
    as_of: dt.date
    source: str
    basis: str


class PeerBar(BaseModel):
    ticker: str
    name: str | None
    is_subject: bool
    value: Decimal | None
    reason: str | None = None
    growth: Decimal | None = Field(default=None, description="Filed revenue growth, 3-year CAGR.")


class PeerBars(BaseModel):
    available: bool
    reason: str | None = None
    multiple: KeyMultiple | None = None
    label: str | None = None
    rule: str
    scope_label: str
    bars: list[PeerBar] = Field(default_factory=list)
    median: Decimal | None = None
    peer_count: int = 0
    withheld_reason: str | None = None
    forward_available: bool = False
    forward_reason: str | None = None
    forward_bars: list[PeerBar] = Field(default_factory=list)
    forward_median: Decimal | None = None
    excluded: list[dict[str, str]] = Field(default_factory=list)
    footer: PictureFooter


class HistBin(BaseModel):
    lo: Decimal
    hi: Decimal
    count: int
    open_low: bool = False
    open_high: bool = False
    has_company: bool = False


class IndustryHistogram(BaseModel):
    available: bool
    reason: str | None = None
    sector: str | None
    multiple: KeyMultiple | None
    label: str | None
    universe_count: int = Field(default=0, description="Sector members in the covered universe.")
    value_count: int = Field(default=0, description="Members with a meaningful value.")
    bins: list[HistBin] = Field(default_factory=list)
    mean: Decimal | None = None
    median: Decimal | None = None
    company_value: Decimal | None = None
    company_percentile: Decimal | None = None
    note: str = (
        "Covered universe only (instruments the app has data for), grouped by provider sector. "
        "Not a global industry distribution."
    )
    footer: PictureFooter


class HistoryPoint(BaseModel):
    date: dt.date
    price: Decimal
    eps: Decimal | None
    pe: Decimal | None
    eps_period_end: dt.date | None


class HistoryWindow(BaseModel):
    years: int
    available: bool
    reason: str | None = None
    points: int = 0
    median: Decimal | None = None
    sd: Decimal | None = None
    low: Decimal | None = Field(default=None, description="Median minus one standard deviation.")
    high: Decimal | None = Field(default=None, description="Median plus one standard deviation.")


class MultipleHistory(BaseModel):
    available: bool
    reason: str | None = None
    currency: str | None = None
    points: list[HistoryPoint] = Field(default_factory=list)
    windows: list[HistoryWindow] = Field(default_factory=list)
    current: Decimal | None = None
    basis: str = (
        "Weekly closes over trailing diluted EPS: each week uses the latest fiscal-year EPS as first "
        "reported and known by that date, put on today's share basis with stored splits."
    )
    footer: PictureFooter


class RegressionPoint(BaseModel):
    ticker: str
    growth: Decimal
    margin: Decimal
    pe: Decimal
    fitted: Decimal


class FairMultiple(BaseModel):
    available: bool
    reason: str | None = None
    label: str = "Indicative"
    n: int = 0
    r_squared: Decimal | None = None
    min_r_squared: Decimal
    intercept: Decimal | None = None
    growth_coef: Decimal | None = None
    margin_coef: Decimal | None = None
    company_growth: Decimal | None = None
    company_margin: Decimal | None = None
    company_pe: Decimal | None = None
    fair_pe: Decimal | None = None
    implied_price: Decimal | None = None
    price: Decimal | None = None
    currency: str | None = None
    points: list[RegressionPoint] = Field(default_factory=list)
    formula: str = (
        "P/E = a + b x revenue growth (3-year CAGR) + c x net margin, least squares over peers"
    )
    method_version: str = Field(default_factory=lambda: version("fair_multiple"))
    footer: PictureFooter


class ValuationPictures(BaseModel):
    ticker: str
    peers: PeerBars
    histogram: IndustryHistogram
    history: MultipleHistory
    fair_multiple: FairMultiple
    method_version: str


# ------------------------------------------------------------------------------ pure helpers


def choose_multiple(
    pe: Decimal | None, ps: Decimal | None, pb: Decimal | None
) -> tuple[KeyMultiple | None, str]:
    """The plan's rule: P/E when earnings are positive, else P/S, else P/B."""
    if pe is not None and pe > 0:
        return "pe", "P/E, because the company's earnings are positive."
    if ps is not None and ps > 0:
        return "ps", "P/S, because earnings are not positive, so a P/E is not meaningful."
    if pb is not None and pb > 0:
        return "pb", "P/B, because neither earnings nor revenue give a meaningful multiple."
    return None, "No P/E, P/S or P/B can be computed for the company."


def histogram(
    values: Sequence[Decimal], company: Decimal | None, bins: int = HIST_BINS
) -> list[HistBin]:
    """Equal-width bins between the 5th and 95th percentiles; values beyond fall into the open
    first and last bins, so one extreme multiple does not flatten the picture."""
    xs = sorted(float(v) for v in values)
    if len(xs) < 2:
        return []
    lo = float(np.percentile(xs, 5))
    hi = float(np.percentile(xs, 95))
    if hi <= lo:
        lo, hi = xs[0], xs[-1]
    if hi <= lo:
        hi = lo + 1
    width = (hi - lo) / bins
    counts = [0] * bins

    def index(x: float) -> int:
        return min(bins - 1, max(0, int((x - lo) // width)))

    for x in xs:
        counts[index(x)] += 1
    at = index(float(company)) if company is not None else None
    return [
        HistBin(
            lo=Decimal(str(round(lo + i * width, 4))),
            hi=Decimal(str(round(lo + (i + 1) * width, 4))),
            count=counts[i],
            open_low=i == 0 and xs[0] < lo,
            open_high=i == bins - 1 and xs[-1] > hi,
            has_company=at == i,
        )
        for i in range(bins)
    ]


def percentile_rank(value: Decimal, values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    below = sum(1 for v in values if v < value)
    equal = sum(1 for v in values if v == value)
    return Decimal(str(round((below + equal / 2) / len(values), 4)))


def first_reported(rows: Sequence[Any]) -> list[tuple[dt.date, Decimal, dt.date]]:
    """(period end, EPS, known date) keeping only each fiscal year's first-reported value."""
    seen: dict[dt.date, tuple[dt.date, Decimal, dt.date]] = {}
    for r in sorted(rows, key=lambda r: (r["period_end"], r["known_at"])):
        if r["period_end"] not in seen:
            known = r["known_at"]
            seen[r["period_end"]] = (
                r["period_end"],
                Decimal(r["value"]),
                known.date() if isinstance(known, dt.datetime) else known,
            )
    return sorted(seen.values(), key=lambda t: t[2])


def weekly(closes: Sequence[tuple[dt.date, Decimal]]) -> list[tuple[dt.date, Decimal]]:
    """The last close of each ISO week."""
    out: dict[tuple[int, int], tuple[dt.date, Decimal]] = {}
    for d, c in closes:
        y, w, _ = d.isocalendar()
        out[(y, w)] = (d, c)
    return [out[k] for k in sorted(out)]


def pe_series(
    closes: Sequence[tuple[dt.date, Decimal]],
    eps: Sequence[tuple[dt.date, Decimal, dt.date]],
    splits: Sequence[tuple[dt.date, Decimal]],
) -> list[HistoryPoint]:
    points: list[HistoryPoint] = []
    for d, price in weekly(closes):
        known = [e for e in eps if e[2] <= d]
        if not known:
            points.append(HistoryPoint(date=d, price=price, eps=None, pe=None, eps_period_end=None))
            continue
        end, value, _ = max(known, key=lambda e: e[0])
        factor = Decimal(1)
        for ex, ratio in splits:
            if ex > end and ratio > 0:
                factor *= ratio
        adj = value / factor
        pe = price / adj if adj > 0 else None
        points.append(
            HistoryPoint(
                date=d,
                price=rounded(price, "money") or price,
                eps=rounded(adj, "money"),
                pe=rounded(pe, "ratio") if pe is not None else None,
                eps_period_end=end,
            )
        )
    return points


def window_stats(points: Sequence[HistoryPoint], years: int, today: dt.date) -> HistoryWindow:
    start = today - dt.timedelta(days=round(365.25 * years))
    inside = [p for p in points if p.date >= start]
    pes = [float(p.pe) for p in inside if p.pe is not None]
    if not inside or inside[0].date > start + dt.timedelta(days=45):
        return HistoryWindow(
            years=years,
            available=False,
            reason=f"price and EPS history starts after {start.isoformat()}",
        )
    if len(pes) < max(8, len(inside) // 2):
        return HistoryWindow(
            years=years,
            available=False,
            reason=f"{len(pes)} of {len(inside)} weeks have a meaningful P/E (earnings not positive or not filed)",
            points=len(pes),
        )
    med = statistics.median(pes)
    sd = statistics.pstdev(pes)
    return HistoryWindow(
        years=years,
        available=True,
        points=len(pes),
        median=Decimal(str(round(med, 2))),
        sd=Decimal(str(round(sd, 2))),
        low=Decimal(str(round(med - sd, 2))),
        high=Decimal(str(round(med + sd, 2))),
    )


def fit(
    points: Sequence[tuple[str, Decimal, Decimal, Decimal]],
) -> tuple[list[float], float, list[float]] | None:
    """Least squares of P/E on (growth, margin) with an intercept: (coefficients, R squared,
    fitted values). None when the design is singular."""
    if len(points) < 4:
        return None
    x = np.array([[1.0, float(g), float(m)] for _, g, m, _ in points])
    y = np.array([float(pe) for *_, pe in points])
    if np.linalg.matrix_rank(x) < 3:
        return None
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    fitted = x @ beta
    ss_tot = float(((y - y.mean()) ** 2).sum())
    if ss_tot == 0:
        return None
    r2 = 1 - float(((y - fitted) ** 2).sum()) / ss_tot
    return [float(b) for b in beta], r2, [float(f) for f in fitted]


def _d(x: float, places: int = 4) -> Decimal:
    return Decimal(str(round(x, places)))


# ------------------------------------------------------------------------------- assembly


async def valuation_pictures(
    pool: asyncpg.Pool,
    owner_id: Any,
    company: Any,
    scope: Literal["confirmed", "group", "home_market", "global"],
    group_id: int | None,
    *,
    min_r2: float,
    today: dt.date | None = None,
) -> ValuationPictures:
    from steward.company import service

    today = today or dt.date.today()
    cmp = await service.compare(
        pool,
        company,
        scope,
        owner_id,
        group_id,
        ("pe", "ps", "pb", "forward_pe", GROWTH_ID, MARGIN_ID),
        "revenue",
        5,
        None,
    )
    rows = {r.id: r for r in cmp.rows}
    subject = company.ticker

    def cell(rid: str, t: str) -> tuple[Decimal | None, str | None]:
        row = rows.get(rid)
        c = row.cells.get(t) if row else None
        return (c.value, c.reason) if c else (None, "not computed")

    key, rule = choose_multiple(
        cell("pe", subject)[0], cell("ps", subject)[0], cell("pb", subject)[0]
    )
    basis_peer = f"Latest fiscal year of each company; {cmp.scope_label}"
    peers = _peer_bars(
        cmp,
        rows,
        key,
        rule,
        subject,
        PictureFooter(as_of=today, source=SOURCE_STATEMENTS, basis=basis_peer),
    )
    hist = await _histogram(pool, company, key, cell(key, subject)[0] if key else None, today)
    history = await _history(pool, company, today)
    fair = _fair(cmp, rows, subject, company, min_r2, today)
    return ValuationPictures(
        ticker=subject,
        peers=peers,
        histogram=hist,
        history=history,
        fair_multiple=fair,
        method_version=version("valuation_pictures"),
    )


def _bars(cmp: Any, rows: dict[str, Any], rid: str) -> list[PeerBar]:
    out: list[PeerBar] = []
    row = rows.get(rid)
    growth = rows.get(GROWTH_ID)
    for c in cmp.companies:
        v = row.cells.get(c.ticker) if row else None
        g = growth.cells.get(c.ticker) if growth else None
        out.append(
            PeerBar(
                ticker=c.ticker,
                name=c.name,
                is_subject=c.is_subject,
                value=v.value if v else None,
                reason=(v.reason if v else "not computed"),
                growth=g.value if g else None,
            )
        )
    return sorted(out, key=lambda b: (b.value is None, -(b.value or 0)))


def _peer_bars(
    cmp: Any,
    rows: dict[str, Any],
    key: KeyMultiple | None,
    rule: str,
    subject: str,
    footer: PictureFooter,
) -> PeerBars:
    excluded = [{"ticker": e.ticker, "reason": e.reason} for e in cmp.excluded]
    if key is None:
        return PeerBars(
            available=False,
            reason=rule,
            rule=rule,
            scope_label=cmp.scope_label,
            excluded=excluded,
            footer=footer,
        )
    row = rows[key]
    fwd = rows.get("forward_pe")
    fwd_subject = fwd.cells.get(subject) if fwd else None
    forward_ok = key == "pe" and fwd_subject is not None and fwd_subject.value is not None
    return PeerBars(
        available=row.count > 0,
        reason=None if row.count > 0 else f"no peer in {cmp.scope_label} has a {LABEL[key]}",
        multiple=key,
        label=LABEL[key],
        rule=rule,
        scope_label=cmp.scope_label,
        bars=_bars(cmp, rows, key),
        median=row.median,
        peer_count=row.count,
        withheld_reason=row.withheld_reason,
        forward_available=forward_ok,
        forward_reason=None
        if forward_ok
        else (
            "forward multiples are compared only for P/E"
            if key != "pe"
            else (
                fwd_subject.reason
                if fwd_subject and fwd_subject.reason
                else "no tested forward estimate for the company"
            )
        ),
        forward_bars=_bars(cmp, rows, "forward_pe") if forward_ok else [],
        forward_median=fwd.median if forward_ok and fwd else None,
        excluded=excluded,
        footer=footer,
    )


async def _histogram(
    pool: asyncpg.Pool,
    company: Any,
    key: KeyMultiple | None,
    company_value: Decimal | None,
    today: dt.date,
) -> IndustryHistogram:
    from steward.discovery import read

    footer = PictureFooter(
        as_of=today,
        source="Covered discovery universe; filed statements and stored closes",
        basis="Latest fiscal year per member; provider sector",
    )
    label = LABEL[key] if key else None
    if key is None:
        return IndustryHistogram(
            available=False,
            reason="no key multiple for the company",
            sector=company.sector,
            multiple=None,
            label=None,
            footer=footer,
        )
    if not company.sector:
        return IndustryHistogram(
            available=False,
            reason="no provider sector is stored for this company",
            sector=None,
            multiple=key,
            label=label,
            footer=footer,
        )
    tickers = {
        r["ticker"]
        for r in await pool.fetch(
            """select distinct ticker from public.company_research_observation
               where dataset='profile' and metric='sector' and text_value=$1""",
            company.sector,
        )
    }
    tickers.add(company.ticker)
    members = [
        m for m in await read.load_universe(pool) if m.ticker in tickers and m.asset_type != "fund"
    ]
    records = await read.stock_records(
        pool,
        members,
        today,
        cutoff=None,
        reconciled_only=False,
        access=await read.provider_states(pool),
    )
    mid = UNIVERSE_METRIC[key]
    values: list[Decimal] = []
    own = company_value
    for r in records:
        mv = r.values.get(mid)
        v = mv.value if mv is not None else None
        if isinstance(v, Decimal) and v > 0:
            values.append(v)
            if r.key == company.ticker:
                own = v
    need = THRESHOLDS.min_universe_for_percentile
    if len(values) < need:
        return IndustryHistogram(
            available=False,
            reason=(
                f"{len(values)} of {len(members)} {company.sector} members in the covered universe have a "
                f"{label}; a distribution needs {need}. The peer bars stand in."
            ),
            sector=company.sector,
            multiple=key,
            label=label,
            universe_count=len(members),
            value_count=len(values),
            company_value=own,
            footer=footer,
        )
    return IndustryHistogram(
        available=True,
        sector=company.sector,
        multiple=key,
        label=label,
        universe_count=len(members),
        value_count=len(values),
        bins=histogram(values, own),
        mean=rounded(sum(values, Decimal(0)) / len(values), "ratio"),
        median=rounded(Decimal(str(statistics.median([float(v) for v in values]))), "ratio"),
        company_value=own,
        company_percentile=percentile_rank(own, values) if own is not None else None,
        footer=footer,
    )


async def _history(pool: asyncpg.Pool, company: Any, today: dt.date) -> MultipleHistory:
    from steward.company import service
    from steward.company.ratios import market_from
    from steward.discovery import read

    footer = PictureFooter(
        as_of=today,
        source="Stored adjusted closes; SEC XBRL diluted EPS as first reported",
        basis="Weekly; trailing fiscal-year EPS known at each date",
    )
    got = await service.load_market(pool, company.ticker)
    if got is None:
        return MultipleHistory(available=False, reason="not in the covered universe", footer=footer)
    member = got[0]
    start = today - dt.timedelta(days=round(365.25 * 10) + 30)
    raw = (await read.closes_between(pool, [member], start, today)).get(company.ticker, [])
    market = market_from(raw, member.currency, None)
    if not market.closes:
        return MultipleHistory(available=False, reason="no stored closes", footer=footer)
    ccy = company.annual.currency
    if ccy and market.currency and ccy != market.currency:
        return MultipleHistory(
            available=False,
            reason=f"prices are in {market.currency} and EPS in {ccy}; no P/E history is drawn across currencies",
            currency=market.currency,
            footer=footer,
        )
    rows = await pool.fetch(
        """select period_end, value, known_at from public.financial_statement_fact
           where ticker=$1 and line_item='eps_diluted' and fiscal_period ~ '^FY[0-9]{4}$'
             and coalesce(dimensions->'members', '[]'::jsonb) = '[]'::jsonb and value is not null""",
        company.ticker,
    )
    eps = first_reported(rows)
    if not eps:
        return MultipleHistory(
            available=False,
            reason="no filed annual diluted EPS",
            currency=market.currency,
            footer=footer,
        )
    splits = (
        [
            (r["ex_date"], r["ratio"])
            for r in await pool.fetch(
                """select ex_date, ratio from public.corporate_action where listing_id=$1 and action_type='split'
               and status='confirmed' and ratio is not null and ratio > 0 order by ex_date""",
                member.listing_id,
            )
        ]
        if member.listing_id
        else []
    )
    points = pe_series(market.closes, eps, splits)
    windows = [window_stats(points, y, today) for y in HISTORY_WINDOWS]
    current = next((p.pe for p in reversed(points) if p.pe is not None), None)
    if not any(w.available for w in windows):
        return MultipleHistory(
            available=False,
            reason=windows[0].reason or "not enough history",
            currency=market.currency,
            points=points,
            windows=windows,
            current=current,
            footer=footer,
        )
    return MultipleHistory(
        available=True,
        currency=market.currency,
        points=points,
        windows=windows,
        current=current,
        footer=footer,
    )


def _fair(
    cmp: Any, rows: dict[str, Any], subject: str, company: Any, min_r2: float, today: dt.date
) -> FairMultiple:
    footer = PictureFooter(
        as_of=today,
        source=SOURCE_STATEMENTS,
        basis=f"Peers' latest fiscal year; {cmp.scope_label}",
    )
    pe, growth, margin = rows.get("pe"), rows.get(GROWTH_ID), rows.get(MARGIN_ID)
    base = FairMultiple(available=False, min_r_squared=_d(min_r2, 2), footer=footer)
    if pe is None or growth is None or margin is None:
        return base.model_copy(update={"reason": "peer ratios not computed"})
    pts: list[tuple[str, Decimal, Decimal, Decimal]] = []
    for c in cmp.companies:
        if c.is_subject:
            continue
        p, g, m = (r.cells.get(c.ticker) for r in (pe, growth, margin))
        if (
            p
            and g
            and m
            and p.value is not None
            and g.value is not None
            and m.value is not None
            and 0 < p.value < 200
        ):
            pts.append((c.ticker, g.value, m.value, p.value))
    need = THRESHOLDS.min_peers_for_median
    cg, cm, cp = (r.cells.get(subject) for r in (growth, margin, pe))
    update: dict[str, Any] = {
        "n": len(pts),
        "company_growth": cg.value if cg else None,
        "company_margin": cm.value if cm else None,
        "company_pe": cp.value if cp else None,
        "price": company.market.closes[-1][1] if company.market.closes else None,
        "currency": company.market.currency,
    }
    if len(pts) < need:
        return base.model_copy(
            update={
                **update,
                "reason": f"{len(pts)} peers have a positive P/E, growth and margin; the regression needs {need}",
            }
        )
    got = fit(pts)
    if got is None:
        return base.model_copy(
            update={**update, "reason": "peer growth and margin do not vary enough to fit"}
        )
    beta, r2, fitted = got
    update |= {
        "r_squared": _d(r2, 3),
        "intercept": _d(beta[0]),
        "growth_coef": _d(beta[1]),
        "margin_coef": _d(beta[2]),
        "points": [
            RegressionPoint(ticker=t, growth=g, margin=m, pe=p, fitted=_d(f, 2))
            for (t, g, m, p), f in zip(pts, fitted, strict=True)
        ],
    }
    if r2 < min_r2:
        return base.model_copy(
            update={
                **update,
                "reason": f"the fit explains too little (R squared {r2:.2f}, needs {min_r2:.2f})",
            }
        )
    if update["company_growth"] is None or update["company_margin"] is None:
        return base.model_copy(
            update={**update, "reason": "the company has no filed 3-year growth or net margin"}
        )
    fair_pe = (
        beta[0]
        + beta[1] * float(update["company_growth"])
        + beta[2] * float(update["company_margin"])
    )
    if fair_pe <= 0:
        return base.model_copy(
            update={**update, "reason": "the fit gives a non-positive P/E for the company"}
        )
    implied = None
    if update["company_pe"] and update["price"] is not None and update["company_pe"] > 0:
        implied = rounded(update["price"] * _d(fair_pe) / update["company_pe"], "money")
    return base.model_copy(
        update={**update, "available": True, "fair_pe": _d(fair_pe, 2), "implied_price": implied}
    )
