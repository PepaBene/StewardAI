"""Sector and industry map of the companies the app has data for (plan M7.3).

Each group lists how many companies and which markets stand behind every statistic, so a median
is never shown without its sample. A statistic needs at least `MIN_SAMPLE` companies; below that
the group says so and gives no median (the range of two or three values is still listed). Growth
and margins come from filed statements through the same engine the peer comparison uses; the
valuation multiple is the trailing price/earnings the data provider reports. Nothing here is a
forecast, and a company with no value is counted as missing, never as zero.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import asyncpg
from pydantic import BaseModel, Field

from steward.peer_metrics import financial_peer_metrics, latest_period

MIN_SAMPLE = 3

METHOD = (
    "Companies are the ones with a stored profile. Revenue growth and operating margin are the "
    "latest filed year from the peer-comparison engine; price/earnings is the provider's trailing "
    "figure. A median needs at least three companies with a value."
)

NOT_COVERED = [
    "Top products: segment revenue is stored per filing, not yet rolled up to a product list.",
    "Patent areas: patents are stored per company and are not grouped by sector here.",
    "Trade dependencies: trade data is by product code and country, not by company.",
    "Credit cycle signals: the stored credit-spread series is for the whole market, not a sector.",
]


class Spread(BaseModel):
    n: int = Field(description="Companies with a value.")
    of: int = Field(description="Companies in the group.")
    median: float | None = Field(description="Null below the minimum sample.")
    low: float | None
    high: float | None


class GroupStats(BaseModel):
    name: str
    kind: str = Field(description="sector or industry")
    parent: str | None
    companies: int
    tickers: list[str]
    markets: list[str]
    held_weight: float = Field(description="Share of the portfolio held in these companies.")
    held: int
    revenue_growth: Spread
    operating_margin: Spread
    pe: Spread
    confirmed_peer_links: int = Field(
        description="Confirmed peer links with both ends in the group."
    )


class SectorMap(BaseModel):
    universe: int = Field(description="Companies with a profile.")
    sectors: list[GroupStats]
    industries: list[GroupStats]
    unclassified: list[str] = Field(description="Held tickers with no profile.")
    not_covered: list[str] = NOT_COVERED
    method: str = METHOD


@dataclass(frozen=True)
class Profile:
    ticker: str
    sector: str | None
    industry: str | None
    country: str | None


def spread(values: list[float | None], of: int) -> Spread:
    got = sorted(v for v in values if v is not None)
    if not got:
        return Spread(n=0, of=of, median=None, low=None, high=None)
    return Spread(
        n=len(got),
        of=of,
        median=statistics.median(got) if len(got) >= MIN_SAMPLE else None,
        low=got[0],
        high=got[-1],
    )


def _group(
    name: str,
    kind: str,
    parent: str | None,
    members: list[Profile],
    growth: dict[str, float | None],
    margin: dict[str, float | None],
    pe: dict[str, float | None],
    weights: dict[str, float],
    links: set[tuple[str, str]],
) -> GroupStats:
    tickers = sorted(m.ticker for m in members)
    inside = set(tickers)
    return GroupStats(
        name=name,
        kind=kind,
        parent=parent,
        companies=len(members),
        tickers=tickers,
        markets=sorted({m.country for m in members if m.country}),
        held_weight=sum(weights.get(t, 0.0) for t in tickers),
        held=sum(1 for t in tickers if weights.get(t, 0.0) > 0),
        revenue_growth=spread([growth.get(t) for t in tickers], len(tickers)),
        operating_margin=spread([margin.get(t) for t in tickers], len(tickers)),
        pe=spread([pe.get(t) for t in tickers], len(tickers)),
        confirmed_peer_links=sum(1 for a, b in links if a in inside and b in inside),
    )


def build_map(
    profiles: list[Profile],
    growth: dict[str, float | None],
    margin: dict[str, float | None],
    pe: dict[str, float | None],
    weights: dict[str, float],
    links: set[tuple[str, str]],
) -> SectorMap:
    by_sector: dict[str, list[Profile]] = defaultdict(list)
    by_industry: dict[tuple[str, str], list[Profile]] = defaultdict(list)
    for p in profiles:
        if p.sector:
            by_sector[p.sector].append(p)
            if p.industry:
                by_industry[(p.sector, p.industry)].append(p)
    sectors = [
        _group(name, "sector", None, m, growth, margin, pe, weights, links)
        for name, m in by_sector.items()
    ]
    industries = [
        _group(ind, "industry", sec, m, growth, margin, pe, weights, links)
        for (sec, ind), m in by_industry.items()
    ]
    key = lambda g: (-g.companies, g.name)  # noqa: E731
    profiled = {p.ticker for p in profiles}
    return SectorMap(
        universe=len(profiles),
        sectors=sorted(sectors, key=key),
        industries=sorted(industries, key=key),
        unclassified=sorted(t for t, w in weights.items() if w > 0 and t not in profiled),
    )


def _num(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


async def load_profiles(pool: asyncpg.Pool) -> list[Profile]:
    rows = await pool.fetch(
        "select distinct on (ticker, metric) ticker, metric, text_value"
        " from public.company_research_observation"
        " where dataset = 'profile' and metric in ('sector','industry','country')"
        " order by ticker, metric, known_at desc, id desc"
    )
    got: dict[str, dict[str, str | None]] = defaultdict(dict)
    for r in rows:
        got[r["ticker"]][r["metric"]] = r["text_value"]
    return [
        Profile(t, v.get("sector"), v.get("industry"), v.get("country")) for t, v in got.items()
    ]


async def load_weights(pool: asyncpg.Pool) -> dict[str, float]:
    rows = await pool.fetch(
        "select ticker, value_base from public.position_snapshot"
        " where snapshot_date = (select max(snapshot_date) from public.position_snapshot)"
    )
    total = sum(float(r["value_base"]) for r in rows)
    return {r["ticker"]: float(r["value_base"]) / total for r in rows} if total > 0 else {}


async def load_stats(
    pool: asyncpg.Pool, tickers: list[str]
) -> tuple[dict[str, float | None], dict[str, float | None]]:
    bare = sorted({t.split("_")[0] for t in tickers} | set(tickers))
    facts = await pool.fetch(
        "select ticker, concept, period_start, period_end, basis, value, unit, dimensions,"
        " known_at, validation, published from public.financial_statement_fact"
        " where ticker = any($1::text[]) and value is not null"
        " order by ticker, period_end, known_at, concept",
        bare,
    )
    metrics = financial_peer_metrics([dict(r) for r in facts])
    growth: dict[str, float | None] = {}
    margin: dict[str, float | None] = {}
    for t in tickers:
        # Facts may be stored under the bare symbol or the broker ticker; the broker one wins.
        entry = metrics.get(t) or metrics.get(t.split("_")[0])
        period = latest_period(entry["periods"]) if entry else None  # type: ignore[arg-type]
        growth[t] = _num(period.get("revenue_growth")) if period else None
        margin[t] = _num(period.get("operating_margin")) if period else None
    return growth, margin


async def load_pe(pool: asyncpg.Pool) -> dict[str, float | None]:
    rows = await pool.fetch(
        "select distinct on (ticker) ticker, (figures->>'pe') pe from public.fundamentals"
        " where source = 'finnhub' order by ticker, fetched_at desc"
    )
    out: dict[str, float | None] = {}
    for r in rows:
        value = _num(r["pe"])
        out[r["ticker"]] = value if value is not None and value > 0 else None
    return out


async def load_links(pool: asyncpg.Pool) -> set[tuple[str, str]]:
    rows = await pool.fetch(
        "select ticker, peer_ticker from public.peer_set where status = 'confirmed'"
    )
    return {(r["ticker"], r["peer_ticker"]) for r in rows}


async def build_sector_map(pool: asyncpg.Pool) -> SectorMap:
    profiles = await load_profiles(pool)
    growth, margin = await load_stats(pool, [p.ticker for p in profiles])
    return build_map(
        profiles,
        growth,
        margin,
        await load_pe(pool),
        await load_weights(pool),
        await load_links(pool),
    )
