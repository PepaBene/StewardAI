"""Database-facing assembly for the company workbench: load a company's statements and market,
resolve a peer set, compare peers and keep saved peer groups and valuation scenarios."""

# ruff: noqa: E501
from __future__ import annotations

import datetime as dt
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, Any, Literal

import asyncpg
from pydantic import BaseModel, Field, StringConstraints

from steward.analytics.contract import input_hash, rounded
from steward.analytics.ratio_set import COMPANY_RATIOS
from steward.analytics.ratios import RatioValue
from steward.analytics.thresholds import THRESHOLDS
from steward.analytics.versions import version
from steward.company import peer_coverage
from steward.company.currency import Translator, translate
from steward.company.multiple_path import MultipleAssumptions, MultipleRequest
from steward.company.ratios import (
    Market,
    PeerExcluded,
    build_peer_values,
    build_periods,
    evaluate,
    figures_by_column,
    is_financial_issuer,
    market_from,
    peer_stat,
)
from steward.company.statements import StatementsView, build_view, load_rows
from steward.company.valuation import Assumptions, ValuationRequest
from steward.discovery import read as discovery_read
from steward.discovery.build import StockInputs, price_currency
from steward.discovery.read import _member, provider_states
from steward.discovery.universe import UniverseMember
from steward.peer_metrics import ALIASES as PEER_ALIASES

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


async def _peer_inputs(
    pool: asyncpg.Pool, tickers: list[str]
) -> dict[str, tuple[Any, StockInputs]]:
    """Confirmed US peers not held, from the statements and closes stored under their peer key
    (steward.company.peer_coverage). A peer with no stored statements is left out."""
    keys = [t for t in tickers if peer_coverage.peer_symbol(t)]
    if not keys:
        return {}
    rows = await pool.fetch(
        "select distinct ticker from public.financial_statement_fact"
        " where ticker = any($1::text[]) and source = 'sec_xbrl'",
        keys,
    )
    members = [
        UniverseMember(
            ticker=r["ticker"],
            name=None,
            isin=None,
            asset_type="Common Stock",
            issuer_id=None,
            security_id=None,
            listing_id=None,
            mic=None,
            exchange=None,
            country="US",
            currency="USD",
            status="active",
            active_from=None,
            active_to=None,
            identity_status=None,
            has_statements=True,
            has_prices=True,
            history_from=None,
            price_symbol=peer_coverage.price_symbol(peer_coverage.peer_symbol(r["ticker"]) or ""),
        )
        for r in rows
    ]
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
    return market_from(data.closes, price_currency(member), shares)


async def load_market(pool: asyncpg.Pool, ticker: str) -> tuple[Any, Market] | None:
    """The universe member and its price history in major units, without loading statements."""
    got = (await _inputs(pool, [ticker])).get(ticker)
    return None if got is None else (got[0], _market(*got))


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


PeerTicker = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9._-]{1,32}$")
]


class PeerGroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    peers: list[PeerTicker] = Field(min_length=1, max_length=40)


def clean_peers(peers: list[str], ticker: str) -> list[str]:
    """Peer tickers as given, without the company itself. Trading212 tickers are case-sensitive
    (VUSAl_EQ), so the case is kept; only a repeat or the company's own ticker is dropped."""
    own = ticker.casefold()
    return sorted({p for p in (p.strip() for p in peers) if p and p.casefold() != own})


def match_universe(stored: list[str], universe: list[str]) -> tuple[list[str], list[PeerExcluded]]:
    """Map stored peers to the covered universe's actual-cased tickers: an exact match first, else
    the single case-insensitive match. An ambiguous one is dropped with a note; a peer with no match
    is kept as stored, so the loader reports it as not covered."""
    known = set(universe)
    by_fold: dict[str, list[str]] = {}
    for u in sorted(known):
        by_fold.setdefault(u.casefold(), []).append(u)
    out: list[str] = []
    notes: list[PeerExcluded] = []
    for p in stored:
        if p in known:
            hit = p
        else:
            cands = by_fold.get(p.casefold(), [])
            if len(cands) > 1:
                notes.append(
                    PeerExcluded(
                        ticker=p,
                        reason=f"ambiguous: matches {', '.join(cands)} when case is ignored; re-save the group with the exact ticker",
                    )
                )
                continue
            hit = cands[0] if cands else p
        if hit not in out:
            out.append(hit)
    return out, notes


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
    peers = clean_peers(body.peers, ticker)
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
        stored = await pool.fetch(
            "select distinct peer_ticker as t from public.peer_set where ticker=$1 and status='confirmed' order by 1",
            company.ticker,
        )
        # A confirmed peer is a plain symbol: the held instrument trading under it, else the
        # key the peer jobs store its statements and closes under.
        by_symbol = await peer_coverage.universe_symbols(pool)
        universe = set(by_symbol.values())
        rows = [
            {"t": t}
            for t in dict.fromkeys(
                peer_coverage.resolve(r["t"], universe, by_symbol) for r in stored
            )
        ]
        label = "confirmed peers"
    elif scope == "group":
        row = await pool.fetchrow(
            "select name, peers from public.company_peer_group where id=$1 and owner_id=$2 and ticker=$3",
            group_id,
            owner_id,
            company.ticker,
        )
        stored = list(row["peers"]) if row else []
        universe = (
            [
                r["ticker"]
                for r in await pool.fetch(
                    "select ticker from public.investment_universe where lower(ticker) = any($1::text[])",
                    [p.casefold() for p in stored],
                )
            ]
            if stored
            else []
        )
        resolved, unresolved = match_universe(stored, universe)
        excluded.extend(unresolved)
        rows = [{"t": p} for p in resolved]
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
        label = (
            f"{company.sector} issuers"
            + (
                f" in {company.country}"
                if scope == "home_market" and company.country
                else ", all markets"
            )
            + ": sector-label candidates, not verified comparable"
        )
        rows = await _rank_by_size(pool, company, [r["t"] for r in rows])
    tickers = [r["t"] for r in rows if r["t"] != company.ticker]
    if len(tickers) > MAX_PEERS:
        excluded.append(
            PeerExcluded(
                ticker="*",
                reason=f"{len(tickers) - MAX_PEERS} more candidates not loaded; "
                + (
                    f"the {MAX_PEERS} closest in revenue are used"
                    if scope in ("home_market", "global")
                    else f"the first {MAX_PEERS} by ticker are used"
                ),
            )
        )
        tickers = tickers[:MAX_PEERS]
    return tickers, label, excluded


_REVENUE_NAMES = sorted({n for basis in PEER_ALIASES.values() for n in basis.get("revenue", ())})


async def _rank_by_size(
    pool: asyncpg.Pool, company: Company, candidates: list[str]
) -> list[dict[str, str]]:
    """Sector-label candidates ordered by revenue proximity (log distance, same filing currency)
    to the company; candidates with no comparable revenue follow, alphabetically."""
    own: Decimal | None = None
    cols = [c for c in company.annual.columns if c.kind == "annual"]
    if cols:
        own = figures_by_column(company.annual)[cols[-1].id].get("revenue")
    if not own or own <= 0 or not candidates:
        return [{"t": t} for t in candidates]
    facts = await pool.fetch(
        """select distinct on (ticker) ticker, value from public.financial_statement_fact
           where ticker=any($1::text[]) and value > 0 and unit=$2
             and lower(split_part(concept, ':', 2)) = any($3::text[])
             and coalesce(dimensions->'members', '[]'::jsonb) = '[]'::jsonb and period_end - period_start between 330 and 380
           order by ticker, period_end desc, known_at desc""",
        candidates,
        company.annual.currency,
        _REVENUE_NAMES,
    )
    sized = {r["ticker"]: abs(math.log(float(r["value"] / own))) for r in facts}
    ranked = sorted((t for t in candidates if t in sized), key=lambda t: (sized[t], t))
    return [{"t": t} for t in ranked + sorted(t for t in candidates if t not in sized)]


async def load_peer_views(
    pool: asyncpg.Pool, tickers: list[str]
) -> tuple[
    dict[str, tuple[StatementsView, Market, str | None, str | None]],
    dict[str, str | None],
    list[PeerExcluded],
]:
    got = await _inputs(pool, tickers)
    got.update(await _peer_inputs(pool, [t for t in tickers if t not in got]))
    views: dict[str, tuple[StatementsView, Market, str | None, str | None]] = {}
    names: dict[str, str | None] = {}
    excluded: list[PeerExcluded] = []
    for t in tickers:
        if t not in got:
            reason = (
                "a US peer whose statements are not loaded yet (the weekly SEC XBRL job reads "
                "confirmed peers that file a 10-K)"
                if peer_coverage.peer_symbol(t)
                else "not in the covered universe"
            )
            excluded.append(PeerExcluded(ticker=t, reason=reason))
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
    base_currency: str | None = None
    companies: list[CompareCompany]
    rows: list[CompareRow]
    size_rows: list[CompareRow] = Field(
        default_factory=list,
        description="Latest annual size figures converted to the base currency; empty without one.",
    )
    index_metric: str
    index_series: list[IndexSeries]
    excluded: list[PeerExcluded]
    warnings: list[str]
    method_version: str
    input_hash: str


SIZE_ITEMS = (
    ("revenue", "Revenue"),
    ("operating_income", "Operating income"),
    ("net_income", "Net income"),
    ("total_assets", "Total assets"),
)


def _size_rows(
    views: Mapping[str, StatementsView], subject: str, scope: str, target: str
) -> list[CompareRow]:
    """Latest annual size figures in the base currency, with peer statistics under the same
    minimum-count rule as the ratio rows."""
    rows: list[CompareRow] = []
    for key, label in SIZE_ITEMS:
        cells: dict[str, CompareCell] = {}
        vals: dict[str, Decimal | None] = {}
        for t, v in views.items():
            annual = [c for c in v.columns if c.kind == "annual"]
            if not annual:
                cells[t] = CompareCell(value=None, reason="no annual period")
                continue
            value = figures_by_column(v)[annual[-1].id].get(key)
            vals[t] = value
            cells[t] = CompareCell(
                value=rounded(value, "money") if value is not None else None,
                reason=None if value is not None else "not filed, or no rate for the period",
            )
        stat = peer_stat(vals.get(subject), {t: x for t, x in vals.items() if t != subject}, scope)
        rows.append(
            CompareRow(
                id=f"{key}_base",
                label=f"{label} ({target})",
                group="size",
                unit="money",
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
    return rows


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
    base: Translator | None = None,
) -> PeerCompare:
    tickers, label, pre_excluded = await resolve_peers(pool, company, scope, owner_id, group_id)
    peer_views, names, dropped = await load_peer_views(pool, tickers)
    subject_pairs = build_periods(
        company.annual,
        company.quarterly,
        company.market,
        financial=is_financial_issuer(company.sector, company.industry),
    )
    # Peers are loaded on their last fiscal year, so the company is read on its own: a TTM point
    # (the newest pair when a quarter is newer) would not be comparable with them.
    subject_col, subject_period = next(
        ((c, p) for c, p in reversed(subject_pairs) if c.kind == "annual"), (None, None)
    )
    values, stale = build_peer_values(peer_views, subject_col.end if subject_col else None)
    excluded = [*pre_excluded, *dropped, *stale]
    subject_values: dict[str, RatioValue] = (
        {r.id: evaluate(subject_period, r) for r in COMPANY_RATIOS.values()}
        if subject_period
        else {}
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
    if len(currencies) > 1 and base is None:
        warnings.append(
            f"Statements are in {', '.join(sorted(currencies))}. Ratios are currency-free, and the index chart compares each company with its own first year, so no conversion is applied."
        )
    if len(values) < THRESHOLDS.min_peers_for_median:
        warnings.append(
            f"{len(values)} comparable peers: a median needs {THRESHOLDS.min_peers_for_median}, so none is shown."
        )
    views: dict[str, StatementsView] = {company.ticker: company.annual}
    views.update({t: peer_views[t][0] for t in values})
    size_rows: list[CompareRow] = []
    if base is not None:
        converted: dict[str, StatementsView] = {}
        for t, v in views.items():
            copy = v.model_copy(deep=True)
            if v.currency and v.currency != base.target:
                translate(copy.statements, copy.columns, v.currency, base)
                copy.currency = base.target
            elif not v.currency:
                warnings.append(
                    f"{t} has no stated currency, so it is left out of the base-currency view."
                )
                continue
            converted[t] = copy
        views = converted
        size_rows = _size_rows(views, company.ticker, scope, base.target)
        warnings.append(
            f"Size figures and the index chart are in {base.target} at {base.source.upper()} reference rates "
            f"({'period-end' if base.basis == 'period_end' else 'average for flows, period-end for balances'}). "
            "A currency move shows up in the index line as well as business growth. Ratios are unchanged."
        )
    series = [
        IndexSeries(ticker=t, points=_index(v, index_metric, years))
        for t, v in views.items()
        if t == company.ticker
    ]
    series += [
        IndexSeries(ticker=t, points=_index(v, index_metric, years))
        for t, v in views.items()
        if t != company.ticker
    ]
    return PeerCompare(
        ticker=company.ticker,
        scope=scope,
        scope_label=label,
        base_currency=base.target if base else None,
        size_rows=size_rows,
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
    # The figures the owner saw when saving (with their method version); null for versions saved
    # before results were kept. Never recomputed: any fresh valuation is a separate response.
    saved_result: dict[str, Any] | None = None


class SaveScenarioIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    request: ValuationRequest
    note: str | None = Field(default=None, max_length=500)


def _saved(r: asyncpg.Record) -> SavedScenario:
    a = r["assumptions"]
    a = json.loads(a) if isinstance(a, str) else a
    stored = r["result"] if "result" in r.keys() else None
    stored = json.loads(stored) if isinstance(stored, str) else stored
    return SavedScenario(
        saved_result=stored,
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
        "select * from public.company_valuation_scenario where owner_id=$1 and ticker=$2 and method='dcf' order by name, version desc",
        owner_id,
        ticker,
    )
    return [_saved(r) for r in rows]


class SavedMultiple(BaseModel):
    """A saved earnings-multiple scenario set (method `multiple_path`)."""

    id: int
    name: str
    version: int
    assumptions: list[MultipleAssumptions]
    selected: str | None
    method_version: str
    input_hash: str | None
    note: str | None
    created_at: dt.datetime
    saved_result: dict[str, Any] | None = None


class SaveMultipleIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    request: MultipleRequest
    note: str | None = Field(default=None, max_length=500)


def _saved_multiple(r: asyncpg.Record) -> SavedMultiple:
    a = r["assumptions"]
    a = json.loads(a) if isinstance(a, str) else a
    stored = r["result"]
    stored = json.loads(stored) if isinstance(stored, str) else stored
    return SavedMultiple(
        saved_result=stored,
        id=r["id"],
        name=r["name"],
        version=r["version"],
        assumptions=[MultipleAssumptions(**x) for x in a["scenarios"]],
        selected=a.get("selected"),
        method_version=r["method_version"],
        input_hash=r["input_hash"],
        note=r["note"],
        created_at=r["created_at"],
    )


async def list_multiple(pool: asyncpg.Pool, owner_id: Any, ticker: str) -> list[SavedMultiple]:
    rows = await pool.fetch(
        "select * from public.company_valuation_scenario where owner_id=$1 and ticker=$2 and method='multiple_path' order by name, version desc",
        owner_id,
        ticker,
    )
    return [_saved_multiple(r) for r in rows]


async def save_multiple(
    pool: asyncpg.Pool,
    owner_id: Any,
    ticker: str,
    body: SaveMultipleIn,
    hash_: str | None,
    result: dict[str, Any] | None = None,
) -> SavedMultiple:
    payload = json.dumps(
        {
            "scenarios": [json.loads(a.model_dump_json()) for a in body.request.scenarios],
            "selected": body.request.selected,
        }
    )
    r = await _insert_scenario(
        pool,
        owner_id,
        ticker,
        body.name.strip(),
        payload,
        "multiple_path",
        version("multiple_path"),
        hash_,
        body.note,
        result,
    )
    return _saved_multiple(r)


class VersionConflict(Exception):
    """Two saves raced for the same version number; the route answers 409."""


async def save_scenario(
    pool: asyncpg.Pool,
    owner_id: Any,
    ticker: str,
    body: SaveScenarioIn,
    hash_: str | None,
    result: dict[str, Any] | None = None,
) -> SavedScenario:
    payload = json.dumps(
        {
            "scenarios": [json.loads(a.model_dump_json()) for a in body.request.scenarios],
            "selected": body.request.selected,
        }
    )
    r = await _insert_scenario(
        pool,
        owner_id,
        ticker,
        body.name.strip(),
        payload,
        "dcf",
        version("valuation_scenarios"),
        hash_,
        body.note,
        result,
    )
    return _saved(r)


async def _insert_scenario(
    pool: asyncpg.Pool,
    owner_id: Any,
    ticker: str,
    name: str,
    payload: str,
    method: str,
    method_version: str,
    hash_: str | None,
    note: str | None,
    result: dict[str, Any] | None,
) -> asyncpg.Record:
    try:
        async with pool.acquire() as con, con.transaction():
            # One save per owner, ticker and name at a time, so max+1 cannot hand out a number twice.
            await con.execute(
                "select pg_advisory_xact_lock(hashtext($1))",
                f"company_valuation_scenario:{owner_id}:{ticker}:{name}",
            )
            nxt = await con.fetchval(
                "select coalesce(max(version), 0) + 1 from public.company_valuation_scenario where owner_id=$1 and ticker=$2 and name=$3",
                owner_id,
                ticker,
                name,
            )
            r = await con.fetchrow(
                """insert into public.company_valuation_scenario (owner_id, ticker, name, version, assumptions, method_version, input_hash, note, result, method)
                   values ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9::jsonb,$10) returning *""",
                owner_id,
                ticker,
                name,
                nxt,
                payload,
                method_version,
                hash_,
                note,
                json.dumps(result) if result is not None else None,
                method,
            )
    except asyncpg.UniqueViolationError as err:
        raise VersionConflict("Another save took this version number; try again.") from err
    assert r is not None
    return r
