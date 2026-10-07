"""Database-facing assembly for the company workbench: load a company's statements and market,
resolve a peer set, compare peers and keep saved peer groups and valuation scenarios."""

# ruff: noqa: E501
from __future__ import annotations

import datetime as dt
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.analytics.contract import input_hash, rounded
from steward.analytics.ratio_set import COMPANY_RATIOS
from steward.analytics.ratios import RatioValue
from steward.analytics.thresholds import THRESHOLDS
from steward.analytics.versions import version
from steward.company.ratios import (
    Market,
    PeerExcluded,
    build_peer_values,
    build_periods,
    figures_by_column,
    is_financial_issuer,
    market_from,
    peer_stat,
)
from steward.company.statements import StatementsView, build_view, load_rows
from steward.company.valuation import Assumptions, ValuationRequest
from steward.discovery import read as discovery_read
from steward.discovery.build import StockInputs
from steward.discovery.read import _member, provider_states

Scope = Literal["confirmed", "group", "home_market", "global"]
MAX_PEERS = 30
COMPARE_DEFAULT = (
    "gross_margin",
    "operating_margin",
    "net_margin",
    "fcf_margin",
    "roe",
    "roic",
    "revenue_cagr_3y",
    "net_debt_to_ebitda",
    "interest_coverage",
    "pe",
    "ev_to_ebit",
    "p_fcf",
    "dividend_yield",
)


@dataclass
class Company:
    ticker: str
    annual: StatementsView
    quarterly: StatementsView
    market: Market
    sector: str | None
    industry: str | None
    country: str | None
    name: str | None


async def _inputs(pool: asyncpg.Pool, tickers: list[str]) -> dict[str, tuple[Any, StockInputs]]:
    rows = await pool.fetch(
        "select * from public.investment_universe where ticker = any($1::text[])", tickers
    )
    members = [_member(r) for r in rows]
    if not members:
        return {}
    data = await discovery_read.load_stock_inputs(
        pool,
        members,
        dt.date.today(),
        cutoff=None,
        reconciled_only=False,
        access=await provider_states(pool),
    )
    return {m.ticker: (m, data[m.ticker]) for m in members if m.ticker in data}


def _market(member: Any, data: StockInputs) -> Market:
    shares = data.shares[-1][1] if data.shares else None
    return market_from(data.closes, member.currency, shares)


async def load_company(
    pool: asyncpg.Pool, ticker: str, cutoff: dt.datetime | None = None
) -> Company | None:
    got = (await _inputs(pool, [ticker])).get(ticker)
    if got is None:
        return None
    member, data = got
    rows = await load_rows(pool, ticker, cutoff)
    return Company(
        ticker=ticker,
        annual=build_view(ticker, rows, units=1),
        quarterly=build_view(ticker, rows, frequency="quarterly", units=1),
        market=_market(member, data),
        sector=data.sector,
        industry=data.industry,
        country=member.country,
        name=member.name,
    )


# ------------------------------------------------------------------------------- peers


class PeerGroup(BaseModel):
    id: int
    ticker: str
    name: str
    peers: list[str]


class PeerGroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    peers: list[str] = Field(min_length=1, max_length=40)


async def list_groups(pool: asyncpg.Pool, owner_id: Any, ticker: str) -> list[PeerGroup]:
    rows = await pool.fetch(
        "select id, ticker, name, peers from public.company_peer_group where owner_id=$1 and ticker=$2 order by name",
        owner_id,
        ticker,
    )
    return [PeerGroup(**dict(r)) for r in rows]


async def save_group(
    pool: asyncpg.Pool, owner_id: Any, ticker: str, body: PeerGroupIn
) -> PeerGroup:
    peers = sorted(
        {p.strip().upper() for p in body.peers if p.strip() and p.strip().upper() != ticker.upper()}
    )
    r = await pool.fetchrow(
        """insert into public.company_peer_group (owner_id, ticker, name, peers) values ($1,$2,$3,$4)
           on conflict (owner_id, ticker, name) do update set peers=excluded.peers, updated_at=now()
           returning id, ticker, name, peers""",
        owner_id,
        ticker,
        body.name.strip(),
        peers,
    )
    assert r is not None
    return PeerGroup(**dict(r))


async def delete_group(pool: asyncpg.Pool, owner_id: Any, ticker: str, group_id: int) -> bool:
    return (
        await pool.fetchval(
            "delete from public.company_peer_group where id=$1 and owner_id=$2 and ticker=$3 returning 1",
            group_id,
            owner_id,
            ticker,
        )
        is not None
    )


async def resolve_peers(
    pool: asyncpg.Pool,
    company: Company,
    scope: Scope,
    owner_id: Any,
    group_id: int | None,
) -> tuple[list[str], str, list[PeerExcluded]]:
    """Peer tickers for a scope, plus the scope's description and peers dropped before loading."""
    excluded: list[PeerExcluded] = []
    if scope == "confirmed":
        rows = await pool.fetch(
            "select distinct peer_ticker as t from public.peer_set where ticker=$1 and status='confirmed' order by 1",
            company.ticker,
        )
        label = "confirmed peers"
    elif scope == "group":
        row = await pool.fetchrow(
            "select name, peers from public.company_peer_group where id=$1 and owner_id=$2 and ticker=$3",
            group_id,
            owner_id,
            company.ticker,
        )
        rows = [{"t": p} for p in (row["peers"] if row else [])]
        label = f"saved group {row['name']}" if row else "saved group (not found)"
    else:
        if not company.sector:
            return (
                [],
                f"{scope} peers",
                [
                    PeerExcluded(
                        ticker="*",
                        reason="no provider sector is stored for this company, so sector peers cannot be chosen",
                    )
                ],
            )
        rows = await pool.fetch(
            """select distinct o.ticker as t
               from public.company_research_observation o
               join public.investment_universe u on u.ticker = o.ticker
               where o.dataset='profile' and o.metric='sector' and o.text_value=$1 and o.ticker<>$2
                 and u.has_statements and ($3::text is null or u.country=$3) order by 1 limit 200""",
            company.sector,
            company.ticker,
            company.country if scope == "home_market" else None,
        )
        label = f"{company.sector} issuers" + (
            f" in {company.country}"
            if scope == "home_market" and company.country
            else ", all markets"
        )
    tickers = [r["t"] for r in rows if r["t"] != company.ticker]
    if len(tickers) > MAX_PEERS:
        excluded.append(
            PeerExcluded(
                ticker="*",
                reason=f"{len(tickers) - MAX_PEERS} more peers not loaded; the first {MAX_PEERS} by ticker are used",
            )
        )
        tickers = tickers[:MAX_PEERS]
    return tickers, label, excluded


async def load_peer_views(
    pool: asyncpg.Pool, tickers: list[str]
) -> tuple[
    dict[str, tuple[StatementsView, Market, str | None, str | None]],
    dict[str, str | None],
    list[PeerExcluded],
]:
    got = await _inputs(pool, tickers)
    views: dict[str, tuple[StatementsView, Market, str | None, str | None]] = {}
    names: dict[str, str | None] = {}
    excluded: list[PeerExcluded] = []
    for t in tickers:
        if t not in got:
            excluded.append(PeerExcluded(ticker=t, reason="not in the covered universe"))
            continue
        member, data = got[t]
        rows = await load_rows(pool, t)
        if not rows:
            excluded.append(PeerExcluded(ticker=t, reason="no filed statements stored"))
            continue
        views[t] = (build_view(t, rows, units=1), _market(member, data), data.sector, data.industry)
        names[t] = member.name
    return views, names, excluded


# ----------------------------------------------------------------------------- compare


class CompareCompany(BaseModel):
    ticker: str
    name: str | None
    is_subject: bool
    fiscal_end: dt.date | None
    fiscal_distance_days: int | None
    currency: str | None
    financial: bool


class CompareCell(BaseModel):
    value: Decimal | None
    reason: str | None = None


class CompareRow(BaseModel):
    id: str
    label: str
    group: str
    unit: str
    cells: dict[str, CompareCell]
    median: Decimal | None
    low: Decimal | None
    high: Decimal | None
    count: int
    members: int
    withheld_reason: str | None
    percentile: Decimal | None


class IndexSeries(BaseModel):
    ticker: str
    points: list[dict[str, Any]]


class PeerCompare(BaseModel):
    ticker: str
    scope: str
    scope_label: str
    companies: list[CompareCompany]
    rows: list[CompareRow]
    index_metric: str
    index_series: list[IndexSeries]
    excluded: list[PeerExcluded]
    warnings: list[str]
    method_version: str
    input_hash: str


def _index(view: StatementsView, key: str, years: int) -> list[dict[str, Any]]:
    fig = figures_by_column(view)
    cols = [c for c in view.columns if c.kind == "annual"][-years:]
    values: list[tuple[int, dt.date, Decimal | None]] = []
    for i, c in enumerate(cols):
        f = fig[c.id]
        v = f.get(key)
        if key == "fcf":
            cfo, capex = f.get("cash_from_operations"), f.get("capex")
            v = None if cfo is None or capex is None else cfo - abs(capex)
        values.append((i - len(cols) + 1, c.end, v))
    first = next(((i, d, v) for i, d, v in values if v is not None and v > 0), None)
    out: list[dict[str, Any]] = []
    for i, d, v in values:
        idx = None
        if first is not None and v is not None and v > 0 and i >= first[0]:
            idx = rounded(v / first[2] * 100, "ratio")
        out.append({"offset": i, "end": d.isoformat(), "index": idx})
    return out


async def compare(
    pool: asyncpg.Pool,
    company: Company,
    scope: Scope,
    owner_id: Any,
    group_id: int | None,
    ratio_ids: Sequence[str] = COMPARE_DEFAULT,
    index_metric: str = "revenue",
    years: int = 5,
) -> PeerCompare:
    tickers, label, pre_excluded = await resolve_peers(pool, company, scope, owner_id, group_id)
    peer_views, names, dropped = await load_peer_views(pool, tickers)
    subject_pairs = build_periods(
        company.annual,
        company.quarterly,
        company.market,
        financial=is_financial_issuer(company.sector, company.industry),
    )
    subject_col, subject_period = subject_pairs[-1] if subject_pairs else (None, None)
    values, stale = build_peer_values(peer_views, subject_col.end if subject_col else None)
    excluded = [*pre_excluded, *dropped, *stale]
    subject_values: dict[str, RatioValue] = (
        {r.id: r.fn(subject_period) for r in COMPANY_RATIOS.values()} if subject_period else {}
    )
    companies = [
        CompareCompany(
            ticker=company.ticker,
            name=company.name,
            is_subject=True,
            fiscal_end=subject_col.end if subject_col else None,
            fiscal_distance_days=0,
            currency=company.annual.currency,
            financial=is_financial_issuer(company.sector, company.industry),
        )
    ]
    for t in values:
        v, _m, sec, ind = peer_views[t]
        ends = [c.end for c in v.columns if c.kind == "annual"]
        companies.append(
            CompareCompany(
                ticker=t,
                name=names.get(t),
                is_subject=False,
                fiscal_end=ends[-1] if ends else None,
                fiscal_distance_days=abs((ends[-1] - subject_col.end).days)
                if ends and subject_col
                else None,
                currency=v.currency,
                financial=is_financial_issuer(sec, ind),
            )
        )
    rows: list[CompareRow] = []
    for rid in ratio_ids:
        ratio = COMPANY_RATIOS.get(rid)
        if ratio is None:
            continue
        cells = {
            company.ticker: CompareCell(
                value=rounded(subject_values.get(rid, RatioValue(None)).value, "ratio"),
                reason=subject_values.get(rid, RatioValue(None)).reason,
            )
        }
        peer_vals: dict[str, Decimal | None] = {}
        for t, rv in values.items():
            r = rv[rid]
            cells[t] = CompareCell(value=rounded(r.value, "ratio"), reason=r.reason)
            peer_vals[t] = r.value
        stat = peer_stat(
            subject_values[rid].value if rid in subject_values else None, peer_vals, scope
        )
        rows.append(
            CompareRow(
                id=rid,
                label=ratio.label,
                group=ratio.group,
                unit=ratio.unit,
                cells=cells,
                median=stat.median,
                low=stat.low,
                high=stat.high,
                count=stat.count,
                members=stat.members,
                withheld_reason=stat.withheld_reason,
                percentile=stat.percentile,
            )
        )
    warnings: list[str] = []
    kinds = {c.financial for c in companies}
    if len(kinds) > 1:
        warnings.append(
            "This set mixes banks or insurers with non-financial issuers; margins, leverage and EV ratios are not comparable across them (financials show n/m)."
        )
    far = [
        c.ticker
        for c in companies
        if (c.fiscal_distance_days or 0) > THRESHOLDS.peer_period_distance_days and not c.is_subject
    ]
    if far:
        warnings.append(
            f"Fiscal year-ends differ by more than {THRESHOLDS.peer_period_distance_days} days for: {', '.join(far)}. Ratios use each company's own latest fiscal year."
        )
    currencies = {c.currency for c in companies if c.currency}
    if len(currencies) > 1:
        warnings.append(
            f"Statements are in {', '.join(sorted(currencies))}. Ratios are currency-free, and the index chart compares each company with its own first year, so no conversion is applied."
        )
    if len(values) < THRESHOLDS.min_peers_for_median:
        warnings.append(
            f"{len(values)} comparable peers: a median needs {THRESHOLDS.min_peers_for_median}, so none is shown."
        )
    series = [
        IndexSeries(ticker=company.ticker, points=_index(company.annual, index_metric, years))
    ]
    series += [
        IndexSeries(ticker=t, points=_index(peer_views[t][0], index_metric, years)) for t in values
    ]
    return PeerCompare(
        ticker=company.ticker,
        scope=scope,
        scope_label=label,
        companies=companies,
        rows=rows,
        index_metric=index_metric,
        index_series=series,
        excluded=excluded,
        warnings=warnings,
        method_version=version("peer_compare"),
        input_hash=input_hash(
            {"c": company.annual.input_hash, "p": sorted(values), "ids": list(ratio_ids)}
        ),
    )


async def peer_values_for(
    pool: asyncpg.Pool, company: Company, scope: Scope, owner_id: Any, group_id: int | None
) -> tuple[Mapping[str, Mapping[str, RatioValue]], str, list[PeerExcluded]]:
    tickers, label, pre = await resolve_peers(pool, company, scope, owner_id, group_id)
    views, _names, dropped = await load_peer_views(pool, tickers)
    end = next((c.end for c in reversed(company.annual.columns) if c.kind == "annual"), None)
    values, stale = build_peer_values(views, end)
    return values, label, [*pre, *dropped, *stale]


# ------------------------------------------------------------------- saved scenarios


class SavedScenario(BaseModel):
    id: int
    name: str
    version: int
    assumptions: list[Assumptions]
    selected: str | None
    method_version: str
    input_hash: str | None
    note: str | None
    created_at: dt.datetime


class SaveScenarioIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    request: ValuationRequest
    note: str | None = Field(default=None, max_length=500)


def _saved(r: asyncpg.Record) -> SavedScenario:
    a = r["assumptions"]
    a = json.loads(a) if isinstance(a, str) else a
    return SavedScenario(
        id=r["id"],
        name=r["name"],
        version=r["version"],
        assumptions=[Assumptions(**x) for x in a["scenarios"]],
        selected=a.get("selected"),
        method_version=r["method_version"],
        input_hash=r["input_hash"],
        note=r["note"],
        created_at=r["created_at"],
    )


async def list_scenarios(pool: asyncpg.Pool, owner_id: Any, ticker: str) -> list[SavedScenario]:
    rows = await pool.fetch(
        "select * from public.company_valuation_scenario where owner_id=$1 and ticker=$2 order by name, version desc",
        owner_id,
        ticker,
    )
    return [_saved(r) for r in rows]


async def save_scenario(
    pool: asyncpg.Pool, owner_id: Any, ticker: str, body: SaveScenarioIn, hash_: str | None
) -> SavedScenario:
    payload = json.dumps(
        {
            "scenarios": [json.loads(a.model_dump_json()) for a in body.request.scenarios],
            "selected": body.request.selected,
        }
    )
    async with pool.acquire() as con, con.transaction():
        nxt = await con.fetchval(
            "select coalesce(max(version), 0) + 1 from public.company_valuation_scenario where owner_id=$1 and ticker=$2 and name=$3",
            owner_id,
            ticker,
            body.name.strip(),
        )
        r = await con.fetchrow(
            """insert into public.company_valuation_scenario (owner_id, ticker, name, version, assumptions, method_version, input_hash, note)
               values ($1,$2,$3,$4,$5::jsonb,$6,$7,$8) returning *""",
            owner_id,
            ticker,
            body.name.strip(),
            nxt,
            payload,
            version("valuation_scenarios"),
            hash_,
            body.note,
        )
    assert r is not None
    return _saved(r)
