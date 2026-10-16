# ruff: noqa: E501 - reason strings read better unbroken
"""One batched summary per holding for the holdings presets and drawer (insight plan I9.1).

Every preset column reads a field of this response, so switching presets never refetches. Each
value is taken from stored, already-computed data (fair values, the dividend engine, cached checks,
the discovery metrics, the event tables); a missing figure stays null, never zero.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.analytics.versions import version
from steward.news.link import INDIRECT_VIA

# The discovery metrics the presets show (formula and coverage are in the screener catalogue).
DISCOVERY_FIELDS = (
    "revenue_growth",
    "gross_margin",
    "operating_margin",
    "net_margin",
    "roic",
    "net_debt_to_equity",
    "pe_ratio",
    "forward_pe",
    "price_to_book",
    "price_to_sales",
    "fcf_yield",
    "ev_ebit",
    "earnings_yield",
    "share_change_1y",
    "return_1y",
    "volatility_1y",
    "market_cap",
    "momentum_12_1",
)
# Check values the presets show: (field, check id).
CHECK_FIELDS = (
    ("roe", "past_roe"),
    ("roce", "past_roce"),
    ("debt_to_equity", "health_debt_to_equity"),
    ("past_growth", "past_earnings_growth"),
    ("forecast_growth", "future_earnings_vs_risk_free"),
    ("payout_earnings", "dividend_payout_earnings"),
)

EventKind = Literal["news", "company", "verdict", "dividend"]


class LastEvent(BaseModel):
    kind: EventKind
    date: dt.date
    title: str
    href: str | None = None


class StepValue(BaseModel):
    as_of: dt.date
    value: Decimal


class NextPay(BaseModel):
    date: dt.date
    kind: str
    amount: Decimal | None
    per_share: Decimal
    currency: str | None


class HoldingInsight(BaseModel):
    ticker: str
    name: str | None
    last_event: LastEvent | None = None
    fair_value: Decimal | None = None
    fair_upside: Decimal | None = None
    fair_basis: str | None = None
    fair_stale: bool = False
    fair_reason: str | None = None
    fair_history: list[StepValue] = Field(default_factory=list)
    thesis: str | None = Field(
        default=None, description="First sentence of why the holding is owned."
    )
    scenarios: dict[str, float] | None = Field(
        default=None,
        description="The thesis's bear, base and bull value per share (keys bear/base/bull), "
        "in `scenario_currency`; null without scenarios.",
    )
    scenario_currency: str | None = None
    review_date: dt.date | None = Field(
        default=None, description="When the thesis is due a review."
    )
    next_report_date: dt.date | None = Field(
        default=None, description="The company's next results date, when one is known."
    )
    yield_on_price: Decimal | None = None
    yield_on_cost: Decimal | None = None
    income_12m: Decimal | None = None
    income_currency: str | None = None
    next_payment: NextPay | None = None
    pays_dividend: bool | None = None
    dividend_passes: int | None = None
    dividend_applicable: int | None = None
    dividend_tier: str | None = None
    mark: dict[str, Decimal | None] = Field(default_factory=dict)
    metrics: dict[str, Decimal | None] = Field(default_factory=dict)
    metric_reasons: dict[str, str] = Field(default_factory=dict)
    notes: int = 0


class HoldingsInsight(BaseModel):
    as_of: dt.date
    basis: str
    holdings: list[HoldingInsight]
    method_version: str


def first_sentence(text: str | None, limit: int = 140) -> str | None:
    if not text:
        return None
    s = text.strip().split("\n")[0]
    for stop in (". ", "; "):
        if stop in s:
            s = s.split(stop)[0] + ("." if stop == ". " else "")
            break
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def newest(events: list[LastEvent]) -> LastEvent | None:
    order = {"verdict": 0, "company": 1, "dividend": 2, "news": 3}
    return max(events, key=lambda e: (e.date, -order[e.kind])) if events else None


async def last_events(
    pool: asyncpg.Pool, tickers: list[str], today: dt.date
) -> dict[str, LastEvent]:
    """The newest dated event per ticker across verdicts, company events, dividends and news."""
    out: dict[str, list[LastEvent]] = {t: [] for t in tickers}
    # Only a story about the company itself: one reached through a rival, a supplier, a customer
    # or a watch topic is about someone else and never the holding's last event.
    for r in await pool.fetch(
        """select distinct on (c.ticker) c.ticker, coalesce(c.event_date, c.first_seen::date) as d, c.headline, c.id
           from public.story_cluster c where c.ticker = any($1::text[]) and not c.recycled and c.status <> 'pending'
             and coalesce(c.event_date, c.first_seen::date) <= $2
             and exists (select 1 from public.news_item n, jsonb_array_elements(n.links) l
                          where n.cluster_id = c.id and l->>'ticker' = c.ticker
                            and not (l->>'via' = any($3::text[])))
           order by c.ticker, coalesce(c.event_date, c.first_seen::date) desc""",
        tickers, today, sorted(INDIRECT_VIA),
    ):  # fmt: skip
        out[r["ticker"]].append(
            LastEvent(kind="news", date=r["d"], title=r["headline"], href=f"/news?story={r['id']}")
        )
    for r in await pool.fetch(
        """select distinct on (ticker) ticker, coalesce(announced_at, known_at::date) as d, event_type, event_state
           from public.company_event where ticker = any($1::text[]) and status <> 'rejected'
           order by ticker, coalesce(announced_at, known_at::date) desc""",
        tickers,
    ):  # fmt: skip
        out[r["ticker"]].append(
            LastEvent(
                kind="company",
                date=r["d"],
                title=f"{r['event_type'].capitalize()} {r['event_state']}",
            )
        )
    for r in await pool.fetch(
        """select distinct on (ticker) ticker, created_at::date as d, metric, status
           from public.verdict where ticker = any($1::text[]) and status <> 'unknown'
           order by ticker, created_at desc""",
        tickers,
    ):  # fmt: skip
        word = "breached" if r["status"] == "breach" else "passed"
        out[r["ticker"]].append(
            LastEvent(
                kind="verdict",
                date=r["d"],
                title=f"Thesis KPI {r['metric']} {word}",
                href=f"/holdings/{r['ticker']}",
            )
        )
    return {t: e for t, evs in out.items() if (e := newest(evs)) is not None}


async def holdings_insight(
    pool: asyncpg.Pool, owner_id: Any, basis: str = "headline", today: dt.date | None = None
) -> HoldingsInsight:
    from steward.checks.contract import SCORED_AXES
    from steward.checks.service import latest_scores
    from steward.checks.strips import _check_value
    from steward.config.store import load_active
    from steward.config.thesis import ThesesConfig
    from steward.discovery import read
    from steward.dividends.service import portfolio_dividends
    from steward.valuation.fair_value_view import portfolio_fair_value

    today = today or dt.date.today()
    fv = await portfolio_fair_value(pool, owner_id, basis)
    tickers = [h.ticker for h in fv.holdings]
    out = {h.ticker: HoldingInsight(ticker=h.ticker, name=h.name) for h in fv.holdings}
    for h in fv.holdings:
        o = out[h.ticker]
        o.fair_value, o.fair_upside, o.fair_basis = h.fair_value, h.upside, h.basis_label
        o.fair_stale, o.fair_reason = h.stale, h.reason
        o.fair_history = [StepValue(as_of=p.as_of, value=p.value) for p in h.history]
    if not tickers:
        return HoldingsInsight(
            as_of=today, basis=basis, holdings=[], method_version=version("holdings_insight")
        )

    theses = await load_active(pool, "theses", ThesesConfig)
    for th in theses.body.theses if theses else []:
        if th.ticker in out:
            o = out[th.ticker]
            o.thesis = first_sentence(th.why_owned)
            o.review_date = th.review_date
            target = th.price_target
            if target is not None and target.scenarios is not None:
                s = target.scenarios
                o.scenarios = {"bear": s.bear, "base": s.base, "bull": s.bull}
                o.scenario_currency = target.currency
    for r in await pool.fetch(
        "select t212_ticker, next_report_date from public.instrument"
        " where t212_ticker = any($1::text[]) and next_report_date >= $2",
        tickers,
        today,
    ):
        out[r["t212_ticker"]].next_report_date = r["next_report_date"]

    div = await portfolio_dividends(pool, owner_id)
    for d in div.holdings:
        if d.ticker not in out:
            continue
        o = out[d.ticker]
        o.pays_dividend = d.pays
        o.yield_on_price, o.yield_on_cost, o.income_12m = (
            d.yield_on_price,
            d.yield_on_cost,
            d.next_12m,
        )
        o.income_currency = div.summary.currency
        if d.next_payment:
            n = d.next_payment
            o.next_payment = NextPay(
                date=n.date,
                kind=n.kind,
                amount=n.amount,
                per_share=n.per_share,
                currency=n.currency,
            )
        if d.score:
            o.dividend_passes, o.dividend_applicable, o.dividend_tier = (
                d.score.passes,
                d.score.applicable,
                d.score.tier,
            )

    cached = await latest_scores(pool, owner_id, tickers)
    for t, c in cached.items():
        if t in out:
            out[t].mark = {a: (c.axes[a].score if a in c.axes else None) for a in SCORED_AXES}
    rows = await pool.fetch(
        """select distinct on (ticker) ticker, result from public.company_check_snapshot
           where owner_id = $1 and ticker = any($2::text[]) order by ticker, computed_at desc""",
        owner_id, tickers,
    )  # fmt: skip
    results = {
        r["ticker"]: (json.loads(r["result"]) if isinstance(r["result"], str) else r["result"])
        for r in rows
    }
    for t, o in out.items():
        res = results.get(t)
        for field, cid in CHECK_FIELDS:
            v, why = _check_value(res, cid) if res else (None, "checks not computed yet")
            o.metrics[field] = v
            if v is None and why:
                o.metric_reasons[field] = why

    members = [m for m in await read.load_universe(pool) if m.ticker in out]
    if members:
        records = await read.stock_records(
            pool,
            members,
            today,
            cutoff=None,
            reconciled_only=False,
            access=await read.provider_states(pool),
        )
        for rec in records:
            row = out.get(rec.key)
            if row is None:
                continue
            for f in DISCOVERY_FIELDS:
                mv = rec.values.get(f)
                v = mv.value if mv is not None and isinstance(mv.value, Decimal) else None
                row.metrics[f] = v
                if v is None:
                    row.metric_reasons[f] = (
                        mv.reason if mv is not None else None
                    ) or "not computed"
    for t in out:
        for f in DISCOVERY_FIELDS:
            if f not in out[t].metrics:
                out[t].metrics[f] = None
                out[t].metric_reasons[f] = "not in the covered universe"

    for t, e in (await last_events(pool, tickers, today)).items():
        out[t].last_event = e
    for r in await pool.fetch(
        "select ticker, count(*) as n from public.holding_note where owner_id = $1 and ticker = any($2::text[]) group by ticker",
        owner_id, tickers,
    ):  # fmt: skip
        out[r["ticker"]].notes = r["n"]
    return HoldingsInsight(
        as_of=today,
        basis=basis,
        holdings=list(out.values()),
        method_version=version("holdings_insight"),
    )


# ------------------------------------------------------------------------------- notes


class HoldingNote(BaseModel):
    id: int
    ticker: str
    body: str
    created_at: dt.datetime
    updated_at: dt.datetime


class NoteIn(BaseModel):
    body: str = Field(min_length=1, max_length=20000)


async def list_notes(pool: asyncpg.Pool, owner_id: Any, ticker: str) -> list[HoldingNote]:
    rows = await pool.fetch(
        "select id, ticker, body, created_at, updated_at from public.holding_note where owner_id=$1 and ticker=$2 order by updated_at desc",
        owner_id, ticker,
    )  # fmt: skip
    return [HoldingNote(**dict(r)) for r in rows]


async def add_note(pool: asyncpg.Pool, owner_id: Any, ticker: str, body: str) -> HoldingNote:
    r = await pool.fetchrow(
        "insert into public.holding_note (owner_id, ticker, body) values ($1,$2,$3) returning id, ticker, body, created_at, updated_at",
        owner_id, ticker, body.strip(),
    )  # fmt: skip
    assert r is not None
    return HoldingNote(**dict(r))


async def update_note(
    pool: asyncpg.Pool, owner_id: Any, ticker: str, note_id: int, body: str
) -> HoldingNote | None:
    r = await pool.fetchrow(
        "update public.holding_note set body=$4 where owner_id=$1 and ticker=$2 and id=$3 returning id, ticker, body, created_at, updated_at",
        owner_id, ticker, note_id, body.strip(),
    )  # fmt: skip
    return HoldingNote(**dict(r)) if r else None


async def delete_note(pool: asyncpg.Pool, owner_id: Any, ticker: str, note_id: int) -> bool:
    done = await pool.execute(
        "delete from public.holding_note where owner_id=$1 and ticker=$2 and id=$3",
        owner_id,
        ticker,
        note_id,
    )
    return str(done).endswith(" 1")


async def search_notes(
    pool: asyncpg.Pool, owner_id: Any, query: str, ticker: str | None = None, limit: int = 20
) -> list[HoldingNote]:
    """Full-text (and substring) search over the owner's notes, newest first."""
    rows = await pool.fetch(
        """select id, ticker, body, created_at, updated_at from public.holding_note
           where owner_id=$1 and ($3::text is null or ticker=$3)
             and (to_tsvector('simple', body) @@ plainto_tsquery('simple', $2) or body ilike '%' || $2 || '%')
           order by updated_at desc limit $4""",
        owner_id, query, ticker, limit,
    )  # fmt: skip
    return [HoldingNote(**dict(r)) for r in rows]
