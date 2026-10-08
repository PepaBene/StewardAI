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
from dataclasses import dataclass, field
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
    "Top products: only companies whose annual report tags revenue by product are listed.",
    "Patent areas: empty until patent data is collected (EPO OPS credentials are not set).",
    "Trade dependencies: shown as the countries companies report revenue in, not customs data.",
    "Credit cycle signals: the stored credit-spread series is for the whole market, not a sector.",
]

TOP_N = 5


class ProductLine(BaseModel):
    ticker: str
    label: str
    share: float = Field(description="Share of the company's annual revenue.")


class GeoShare(BaseModel):
    geography: str
    mean_share: float = Field(description="Average share of revenue across reporting companies.")
    n: int = Field(description="Companies that report revenue for this country.")


class PatentArea(BaseModel):
    code: str = Field(description="CPC subclass, such as H01L.")
    families: int


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
    top_products: list[ProductLine] = Field(default_factory=list)
    geography: list[GeoShare] = Field(default_factory=list)
    patent_areas: list[PatentArea] = Field(default_factory=list)


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


@dataclass(frozen=True)
class Detail:
    """Per-company inputs for the group lists; empty when the data is not stored."""

    products: dict[str, list[tuple[str, float]]] = field(default_factory=dict)  # label, share
    geography: dict[str, dict[str, float]] = field(default_factory=dict)  # country -> share
    patents: dict[str, dict[str, int]] = field(default_factory=dict)  # CPC subclass -> families


def top_products(
    tickers: list[str], products: dict[str, list[tuple[str, float]]]
) -> list[ProductLine]:
    lines = [
        ProductLine(ticker=t, label=label, share=share)
        for t in tickers
        for label, share in products.get(t, [])
    ]
    return sorted(lines, key=lambda p: (-p.share, p.ticker, p.label))[:TOP_N]


def geography_mix(tickers: list[str], geography: dict[str, dict[str, float]]) -> list[GeoShare]:
    seen: dict[str, list[float]] = defaultdict(list)
    for t in tickers:
        for country, share in geography.get(t, {}).items():
            seen[country].append(share)
    mix = [GeoShare(geography=c, mean_share=sum(v) / len(v), n=len(v)) for c, v in seen.items()]
    return sorted(mix, key=lambda g: (-g.mean_share, g.geography))[:TOP_N]


def patent_areas(tickers: list[str], patents: dict[str, dict[str, int]]) -> list[PatentArea]:
    total: dict[str, int] = defaultdict(int)
    for t in tickers:
        for code, n in patents.get(t, {}).items():
            total[code] += n
    areas = [PatentArea(code=c, families=n) for c, n in total.items()]
    return sorted(areas, key=lambda a: (-a.families, a.code))[:TOP_N]


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
    detail: Detail,
) -> GroupStats:
    tickers = sorted(m.ticker for m in members)
    inside = set(tickers)
    return GroupStats(
        top_products=top_products(tickers, detail.products),
        geography=geography_mix(tickers, detail.geography),
        patent_areas=patent_areas(tickers, detail.patents),
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
    detail: Detail | None = None,
) -> SectorMap:
    detail = detail or Detail()
    by_sector: dict[str, list[Profile]] = defaultdict(list)
    by_industry: dict[tuple[str, str], list[Profile]] = defaultdict(list)
    for p in profiles:
        if p.sector:
            by_sector[p.sector].append(p)
            if p.industry:
                by_industry[(p.sector, p.industry)].append(p)
    sectors = [
        _group(name, "sector", None, m, growth, margin, pe, weights, links, detail)
        for name, m in by_sector.items()
    ]
    industries = [
        _group(ind, "industry", sec, m, growth, margin, pe, weights, links, detail)
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


async def load_products(pool: asyncpg.Pool) -> dict[str, list[tuple[str, float]]]:
    """Each company's reported product lines (not subtotals) in its latest annual report, as a
    share of that year's total revenue."""
    rows = await pool.fetch(
        "with p as (select x.ticker, s.label, s.value::float8 v, s.period_end"
        "  from public.segment_revenue s join public.xbrl_filing x on x.id = s.filing_id"
        "  where s.kind = 'product' and not s.subtotal and s.value > 0"
        "  and (s.period_end - s.period_start) between 340 and 380),"
        " latest as (select distinct on (ticker) ticker, period_end from p"
        "  order by ticker, period_end desc),"
        " t as (select x.ticker, s.period_end, max(s.value)::float8 total"
        "  from public.segment_revenue s join public.xbrl_filing x on x.id = s.filing_id"
        "  where s.kind = 'total' and (s.period_end - s.period_start) between 340 and 380"
        "  group by 1, 2)"
        " select p.ticker, p.label, p.v / t.total as share from p"
        " join latest l on l.ticker = p.ticker and l.period_end = p.period_end"
        " join t on t.ticker = p.ticker and t.period_end = p.period_end and t.total > 0"
    )
    out: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for r in rows:
        if 0 < r["share"] <= 1:
            out[r["ticker"]].append((r["label"], float(r["share"])))
    return out


async def load_geography(pool: asyncpg.Pool) -> dict[str, dict[str, float]]:
    """Revenue share by country from each company's latest reported split."""
    rows = await pool.fetch(
        "select n.t212_ticker ticker, m.geography, m.weight::float8 weight"
        " from public.exposure_mapping m join public.issuer i on i.id = m.issuer_id"
        " join public.instrument n on n.cik = i.cik"
        " where m.valid_to is null and m.basis = 'reported' and m.geography is not null"
    )
    out: dict[str, dict[str, float]] = defaultdict(dict)
    for r in rows:
        out[r["ticker"]][r["geography"]] = out[r["ticker"]].get(r["geography"], 0.0) + float(
            r["weight"]
        )
    return out


async def load_patent_areas(pool: asyncpg.Pool) -> dict[str, dict[str, int]]:
    rows = await pool.fetch(
        "select o.ticker, left(code, 4) area, count(distinct f.id) families"
        " from public.patent_family f join public.patent_family_owner o on o.family_id = f.id"
        " cross join lateral unnest(f.cpc_codes) as code"
        " where o.valid_to is null and o.ticker is not null group by 1, 2"
    )
    out: dict[str, dict[str, int]] = defaultdict(dict)
    for r in rows:
        out[r["ticker"]][r["area"]] = int(r["families"])
    return out


async def build_sector_map(pool: asyncpg.Pool) -> SectorMap:
    profiles = await load_profiles(pool)
    growth, margin = await load_stats(pool, [p.ticker for p in profiles])
    detail = Detail(
        products=await load_products(pool),
        geography=await load_geography(pool),
        patents=await load_patent_areas(pool),
    )
    return build_map(
        profiles,
        growth,
        margin,
        await load_pe(pool),
        await load_weights(pool),
        await load_links(pool),
        detail,
    )
