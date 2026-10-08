"""Database-facing assembly for the Portfolio X-Ray: loads what each view needs and hands it to the
pure modules."""

# ruff: noqa: E501
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

import asyncpg
from pydantic import BaseModel

from steward.company import segments
from steward.config.jobs import JobsConfig, LookThroughSettings
from steward.config.store import load_active
from steward.discovery import read as discovery_read
from steward.discovery.read import _member, provider_states
from steward.fundresearch.service import _eur_rates
from steward.funds.aggregate import (
    AggregateReport,
    IssuerRef,
    aggregate_exposure,
    config_groups,
    load_issuer_register,
)
from steward.fx.convert import normalise_currency
from steward.lookthrough.classify import country_code
from steward.lookthrough.factsheet import latest_expense_ratios
from steward.lookthrough.read import (
    PortfolioIn,
    SnapshotIn,
    is_fund_type,
    load_portfolio,
    load_snapshots,
)
from steward.xray import benchmark as bench_mod
from steward.xray import owner_input
from steward.xray.leaves import (
    IssuerFacts,
    LeafSet,
    Profile,
    RevenueSlice,
    XrayData,
    build_leaves,
)
from steward.xray.scenario import (
    Analysis,
    Candidate,
    ProposalError,
    ProposedRequest,
    XrayProposal,
    analyse,
    apply_proposal,
    compare,
)
from steward.xray.views import ViewContext

log = logging.getLogger(__name__)

SCORED_ISSUERS = 200  # issuers beyond the largest 200 are not scored; the coverage says so
FACTS_TIMEOUT_SECONDS = 25.0  # reading stored statements for the scored issuers
CACHE_SECONDS = 60.0


class FactsTimeout(Exception):
    """Reading issuer statements took too long; the X-Ray is built without them."""


REVENUE_ISSUERS = 40  # reported geography is read for the largest 40 equity issuers only
REVENUE_CONTEXTS = {
    "country": "revenue_country",
    "commodity": "commodity",
    "trade_hs": "trade_product",
}


@dataclass
class Loaded:
    """Everything the X-Ray endpoints need, loaded once."""

    config: JobsConfig
    data: XrayData
    facts: dict[str, IssuerFacts]
    analysis: Analysis
    day: dt.date
    scored: int
    issuers_total: int
    notes: list[str] = field(default_factory=list)

    @property
    def settings(self) -> LookThroughSettings:
        return self.config.look_through

    @property
    def leafset(self) -> LeafSet:
        return self.analysis.ctx.leafset


def _isins(portfolio: PortfolioIn | None, snapshots: dict[str, SnapshotIn]) -> list[str]:
    out = {p.isin for p in (portfolio.positions if portfolio else []) if p.isin}
    for s in snapshots.values():
        out.update(r.isin for r in s.rows if r.isin)
    return sorted(out)


async def _profiles(pool: asyncpg.Pool, isins: list[str]) -> dict[str, Profile]:
    """The data provider's sector and industry by ISIN and by Trading212 ticker."""
    if not isins:
        return {}
    ticker_by_isin = {
        r["isin"]: r["t212_ticker"]
        for r in await pool.fetch(
            "select isin, t212_ticker from public.instrument where isin = any($1::text[])", isins
        )
    }
    tickers = sorted(set(ticker_by_isin.values()))
    if not tickers:
        return {}
    rows = await pool.fetch(
        """select distinct on (ticker, metric) ticker, metric, text_value
           from public.company_research_observation
           where provider='fmp' and dataset='profile' and metric in ('sector','industry','country','exchange')
             and ticker = any($1::text[]) and text_value is not null
           order by ticker, metric, known_at desc""",
        tickers,
    )
    by_ticker: dict[str, dict[str, str]] = defaultdict(dict)
    for r in rows:
        by_ticker[r["ticker"]][r["metric"]] = r["text_value"]
    out: dict[str, Profile] = {}
    for isin, ticker in ticker_by_isin.items():
        got = by_ticker.get(ticker)
        if got:
            p = Profile(
                sector=got.get("sector"),
                industry=got.get("industry"),
                country=got.get("country"),
                exchange=got.get("exchange"),
            )
            out[isin.upper()] = p
            out.setdefault(ticker.upper(), p)
    return out


async def _facts(
    pool: asyncpg.Pool,
    ranked: list[tuple[str, str]],
    day: dt.date,
    cutoff: dt.datetime | None = None,
) -> dict[str, IssuerFacts]:
    """Size and valuation inputs for the largest issuers: (issuer key, first ISIN) in rank order."""
    isins = sorted({isin for _, isin in ranked})
    if not isins:
        return {}
    rec = await pool.fetch(
        "select * from public.investment_universe where isin = any($1::text[])", isins
    )
    by_isin: dict[str, Any] = {}
    for r in rec:
        by_isin.setdefault(r["isin"], _member(r))
    members = list(by_isin.values())
    if not members:
        return {}
    try:
        records = await asyncio.wait_for(
            discovery_read.stock_records(
                pool,
                members,
                day,
                cutoff=cutoff,
                reconciled_only=False,
                access=await provider_states(pool),
            ),
            FACTS_TIMEOUT_SECONDS,
        )
    except TimeoutError as err:
        raise FactsTimeout from err
    hit = {m.isin: (m, r) for m, r in zip(members, records, strict=True)}
    rates = await _eur_rates(pool)
    out: dict[str, IssuerFacts] = {}
    for key, isin in ranked:
        got = hit.get(isin)
        if got is None:
            continue
        m, rec_ = got

        def val(metric: str, rec: Any = rec_) -> Decimal | None:
            mv = rec.values.get(metric)
            return mv.value if mv is not None and isinstance(mv.value, Decimal) else None

        cap = val("market_cap")
        cap_eur = None
        if cap is not None and m.currency:
            ccy, factor = normalise_currency(m.currency)
            rate = rates.get(ccy)
            cap_eur = cap * factor * rate if rate else None
        facts = IssuerFacts(
            market_cap_eur=cap_eur,
            earnings_yield=val("earnings_yield"),
            price_to_book=val("price_to_book"),
            profitability=val("gross_profit_to_assets"),
            dividend_yield=val("dividend_yield"),
            net_debt_to_equity=val("net_debt_to_equity"),
            net_debt_to_ebitda=val("net_debt_to_ebitda"),
            covered=True,
        )
        # Being in the universe is not coverage: an issuer counts only when a stored statement or
        # price actually produced a value, so an empty record never inflates a coverage figure.
        if any(
            v is not None
            for v in (
                facts.market_cap_eur,
                facts.earnings_yield,
                facts.price_to_book,
                facts.profitability,
                facts.dividend_yield,
                facts.net_debt_to_equity,
                facts.net_debt_to_ebitda,
            )
        ):
            out[key] = facts
    return out


def _country_value(label: str) -> str:
    return country_code(label) or label


def revenue_slices(
    analysis: segments.SegmentAnalysis,
    regions: Mapping[str, Mapping[str, float]] | None = None,
) -> dict[str, list[RevenueSlice]]:
    """Owner mappings first; where the owner mapped no country, the reported geography members
    whose label is exactly a country name, plus members named for a region the owner has spread
    over countries (`regions`: lower-cased region -> country code -> share). A region spread is the
    owner's own estimate, so its slices carry the `owner` basis."""
    regions = regions or {}
    out: dict[str, list[RevenueSlice]] = {}
    for exp in analysis.exposures:
        dim = REVENUE_CONTEXTS.get(exp.context)
        if dim is None:
            continue
        slices = [
            RevenueSlice(
                _country_value(x.value) if dim == "revenue_country" else x.value,
                float(x.share),
                "owner",
            )
            for x in exp.lines
            if x.share is not None and x.share > 0
        ]
        if slices:
            out[dim] = slices
    if "revenue_country" not in out:
        geo = next((b for b in analysis.breakdowns if b.kind == "geographic"), None)
        if geo is not None:
            acc: dict[str, float] = defaultdict(float)
            spread: dict[str, float] = defaultdict(float)
            for m in geo.members:
                if m.role != "operating" or m.latest_share is None or m.latest_share <= 0:
                    continue
                code = country_code(m.label)
                if code:
                    acc[code] += float(m.latest_share)
                elif m.label.strip().lower() in regions:
                    for c, s in regions[m.label.strip().lower()].items():
                        spread[c] += float(m.latest_share) * s
            slices = [RevenueSlice(c, s, "reported") for c, s in acc.items()]
            slices += [RevenueSlice(c, s, "owner") for c, s in spread.items() if c not in acc]
            for c, s in spread.items():
                if c in acc:  # a country named outright and also inside a region: one slice
                    slices = [
                        RevenueSlice(x.value, x.share + s, "owner") if x.value == c else x
                        for x in slices
                    ]
            if slices:
                out["revenue_country"] = slices
    return out


async def _revenue(
    pool: asyncpg.Pool,
    owner_id: str,
    ranked: list[tuple[str, str]],
    leaf_tickers: dict[str, str],
    regions: Mapping[str, Mapping[str, float]] | None = None,
) -> dict[str, dict[str, list[RevenueSlice]]]:
    """Revenue-country, commodity and trade-product slices by ISIN, for owner-mapped companies and
    the largest issuers' reported geography."""
    mapped = {
        r["ticker"]
        for r in await pool.fetch(
            "select distinct ticker from public.company_segment_map where owner_id=$1", owner_id
        )
    }
    targets: dict[str, str] = {}  # isin -> ticker
    for _, isin in ranked[:REVENUE_ISSUERS]:
        t = leaf_tickers.get(isin)
        if t:
            targets[isin] = t
    for isin, t in leaf_tickers.items():
        if t in mapped:
            targets[isin] = t
    out: dict[str, dict[str, list[RevenueSlice]]] = {}
    for isin, ticker in targets.items():
        try:
            analysis = await segments.analysis(pool, owner_id, ticker, None)
        except Exception:  # one company's missing statements must not stop the view
            log.warning("xray: no segment analysis for %s", ticker, exc_info=True)
            continue
        slices = revenue_slices(analysis, regions)
        if slices:
            out[isin.upper()] = slices
            out.setdefault(ticker.upper(), slices)
    return out


class NoHoldingsThen(Exception):
    """No daily snapshot exists on or before the requested date."""


async def load(
    pool: asyncpg.Pool,
    owner_id: str,
    *,
    today: dt.date | None = None,
    as_of: dt.date | None = None,
) -> Loaded:
    """The X-Ray inputs. With `as_of`, the holdings of the latest snapshot on or before that date,
    fund holdings fetched by the end of it, and statements known by then. Issuer identity, sector
    labels and fund charges are the current ones, and the result says so."""
    stored = await load_active(pool, "jobs", JobsConfig)
    config = stored.body if stored else JobsConfig()
    settings = config.look_through
    day = today or dt.datetime.now(dt.UTC).date()
    known_by = (
        dt.datetime.combine(as_of + dt.timedelta(days=1), dt.time.min, tzinfo=dt.UTC)
        if as_of
        else None
    )
    portfolio = await load_portfolio(pool, as_of=as_of)
    if as_of is not None:
        if portfolio is None:
            raise NoHoldingsThen
        day = portfolio.snapshot_date
    isins = {f.isin for f in settings.funds} | {p.fund_isin for p in settings.benchmark_proxies}
    snapshots = await load_snapshots(pool, sorted(isins), known_by=known_by)
    all_isins = _isins(portfolio, snapshots)
    register = await load_issuer_register(pool, all_isins)
    ratios = await latest_expense_ratios(pool, [f.isin for f in settings.funds])
    profiles = await _profiles(pool, all_isins)
    data = XrayData(
        portfolio=portfolio,
        settings=settings,
        snapshots=snapshots,
        register=register,
        expense_ratios=ratios,
        profiles=profiles,
    )
    first = build_leaves(data)
    # issuers ranked by weight, each with one ISIN to look facts up by
    weight: dict[str, float] = defaultdict(float)
    first_isin: dict[str, str] = {}
    ticker_of: dict[str, str] = {}
    for x in first.leaves:
        if x.is_equity and x.issuer_key:
            weight[x.issuer_key] += x.weight
            if x.isin:
                first_isin.setdefault(x.issuer_key, x.isin)
                if x.ticker:
                    ticker_of.setdefault(x.isin, x.ticker)
    ranked = [
        (k, first_isin[k]) for k in sorted(weight, key=lambda k: -weight[k]) if k in first_isin
    ]
    ranked_scored = ranked[:SCORED_ISSUERS]
    notes: list[str] = []
    try:
        facts = await _facts(pool, ranked_scored, day, known_by)
    except FactsTimeout:
        facts = {}
        log.warning("xray: issuer statements timed out after %ss", FACTS_TIMEOUT_SECONDS)
        notes.append(
            "Reading stored statements took too long, so size, style and fundamentals are "
            "missing from this view. Reload to try again."
        )
    # tickers by ISIN from the instrument list, the reliable path to Trading212 tickers
    t212 = {
        r["isin"]: r["t212_ticker"]
        for r in await pool.fetch(
            "select isin, t212_ticker from public.instrument where isin = any($1::text[])",
            [i for _, i in ranked[:REVENUE_ISSUERS]]
            + [x.isin for x in first.leaves if x.channel_kind == "direct" and x.isin],
        )
    }
    regions = await owner_input.region_splits(pool, owner_id)
    data.revenue = await _revenue(pool, owner_id, ranked, {**ticker_of, **t212}, regions)
    if as_of is not None:
        notes.append(
            f"Holdings as of {day.isoformat()}. Fund holdings and statements are those known by "
            "the end of that day; issuer identity, sector labels, fund charges and the revenue "
            "split are today's."
        )
    analysis = analyse(data, facts)
    if len(ranked) > SCORED_ISSUERS:
        notes.append(
            f"Only the {SCORED_ISSUERS} largest of {len(ranked)} issuers are scored for size, "
            "style and fundamentals."
        )
    return Loaded(
        config=config,
        data=data,
        facts=facts,
        analysis=analysis,
        day=day,
        scored=len(facts),
        issuers_total=len(weight),
        notes=notes,
    )


_cache: dict[str, tuple[float, Loaded]] = {}


def clear_cache() -> None:
    _cache.clear()


def drop_cache(owner_id: str) -> None:
    """Forget one owner's loaded X-Ray, so an input they just changed shows at once."""
    _cache.pop(owner_id, None)


async def load_cached(pool: asyncpg.Pool, owner_id: str) -> Loaded:
    """`load`, reused for `CACHE_SECONDS` per owner: the six X-Ray tabs read the same data, so a
    visit loads once, not once per tab. Holdings change at most daily; a minute is stale enough."""
    now = time.monotonic()
    hit = _cache.get(owner_id)
    if hit is not None and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    loaded = await load(pool, owner_id)
    _cache[owner_id] = (now, loaded)
    return loaded


def aggregate(loaded: Loaded, *, top: int = 100_000) -> AggregateReport:
    d = loaded.data
    return aggregate_exposure(
        d.portfolio,
        loaded.settings,
        d.snapshots,
        as_of=loaded.day,
        register=d.register,
        expense_ratios=d.expense_ratios,
        top=top,
    )


def benchmark_for(loaded: Loaded) -> tuple[bench_mod.BenchmarkWeights | None, str | None]:
    settings = loaded.settings
    symbols = loaded.config.prices.benchmark_symbols
    primary = symbols[0] if symbols else None
    proxy = next((p for p in settings.benchmark_proxies if p.symbol == primary), None)
    if proxy is None and settings.benchmark_proxies:
        proxy = settings.benchmark_proxies[0]
    if proxy is None:
        return None, "No benchmark proxy fund is configured (look_through.benchmark_proxies)."
    snap = loaded.data.snapshots.get(proxy.fund_isin)
    if snap is None:
        return None, f"The proxy fund for {proxy.label} has no stored holdings yet."
    groups: dict[str, IssuerRef] = config_groups(settings)
    fund = settings.fund(proxy.fund_isin)
    return (
        bench_mod.benchmark_weights(
            snap,
            label=proxy.label,
            symbol=proxy.symbol,
            fund_label=fund.label if fund else proxy.fund_isin,
            groups=groups,
            register=loaded.data.register,
        ),
        None,
    )


def benchmark_result(
    loaded: Loaded,
) -> tuple[bench_mod.XrayBenchmark, dict[str, tuple[list[Any], list[Any], str | None]]]:
    weights, reason = benchmark_for(loaded)
    if weights is None:
        return bench_mod.unavailable(reason or "No benchmark."), {}
    return bench_mod.compare(loaded.leafset, weights, today=loaded.day)


# ----------------------------------------------------------------------------- candidates


class XrayCandidate(BaseModel):
    ticker: str
    name: str | None
    isin: str | None
    asset_type: str | None
    country: str | None
    currency: str | None
    source: str
    list_name: str | None = None
    held: bool
    looked_through: bool | None


async def candidates(
    pool: asyncpg.Pool,
    owner_id: str,
    query: str | None,
    held: set[str],
    fund_isins: dict[str, bool],
    limit: int = 20,
) -> list[XrayCandidate]:
    out: dict[str, XrayCandidate] = {}

    def add(r: asyncpg.Record, source: str, list_name: str | None = None) -> None:
        if r["ticker"] in out:
            return
        isin = r["isin"]
        is_fund = discovery_read_is_fund(r["asset_type"]) or isin in fund_isins
        out[r["ticker"]] = XrayCandidate(
            ticker=r["ticker"],
            name=r["name"],
            isin=isin,
            asset_type=r["asset_type"],
            country=r["country"],
            currency=r["currency"],
            source=source,
            list_name=list_name,
            held=r["ticker"] in held,
            looked_through=fund_isins.get(isin, False) if is_fund else None,
        )

    watch = await pool.fetch(
        """select distinct on (w.ticker) w.ticker, w.list_name, u.name, u.isin, u.asset_type, u.country, u.currency
           from public.watchlist_item w join public.investment_universe u on u.ticker = w.ticker
           where w.owner_id = $1 order by w.ticker, w.list_name""",
        owner_id,
    )
    for r in watch:
        add(r, "watchlist", r["list_name"])
    if query and query.strip():
        like = f"%{query.strip()}%"
        rows = await pool.fetch(
            """select distinct on (ticker) ticker, name, isin, asset_type, country, currency
               from public.investment_universe
               where status = 'active' and (ticker ilike $1 or name ilike $1)
               order by ticker limit $2""",
            like,
            limit,
        )
        for r in rows:
            add(r, "universe")
    return list(out.values())[:limit]


def discovery_read_is_fund(asset_type: str | None) -> bool:
    from steward.lookthrough.read import is_fund_type

    return is_fund_type(asset_type)


async def load_candidates(pool: asyncpg.Pool, tickers: list[str]) -> dict[str, Candidate]:
    if not tickers:
        return {}
    rows = await pool.fetch(
        """select distinct on (ticker) ticker, name, isin, asset_type, country, currency
           from public.investment_universe where ticker = any($1::text[]) order by ticker""",
        tickers,
    )
    return {
        r["ticker"]: Candidate(
            ticker=r["ticker"],
            name=r["name"],
            isin=r["isin"],
            asset_type=r["asset_type"],
            country=r["country"],
            currency=r["currency"],
        )
        for r in rows
    }


def view_context(loaded: Loaded) -> ViewContext:
    return loaded.analysis.ctx


async def propose(
    pool: asyncpg.Pool,
    owner_id: str,
    request: ProposedRequest,
    *,
    today: dt.date | None = None,
    loaded: Loaded | None = None,
) -> XrayProposal:
    """Before and after X-Ray of hypothetical weights. Reads only; the copy is discarded."""
    if loaded is None:
        loaded = await load(pool, owner_id, today=today)
    portfolio = loaded.data.portfolio
    if portfolio is None:
        raise ProposalError("There is no portfolio snapshot to propose changes to.")
    cands = await load_candidates(pool, [a.ticker for a in request.additions])
    new_portfolio, notes = apply_proposal(portfolio, request, cands)
    data = replace(loaded.data, portfolio=new_portfolio)
    added_isins = sorted({c.isin for c in cands.values() if c.isin})
    if added_isins:
        extra = await load_issuer_register(pool, added_isins)
        data.register = {**data.register, **extra}
        data.profiles = {**data.profiles, **await _profiles(pool, added_isins)}
    leafset = build_leaves(data)
    weight: dict[str, float] = defaultdict(float)
    first_isin: dict[str, str] = {}
    for x in leafset.leaves:
        if x.is_equity and x.issuer_key:
            weight[x.issuer_key] += x.weight
            if x.isin:
                first_isin.setdefault(x.issuer_key, x.isin)
    ranked = [
        (k, first_isin[k]) for k in sorted(weight, key=lambda k: -weight[k]) if k in first_isin
    ]
    fresh = [(k, i) for k, i in ranked[:SCORED_ISSUERS] if k not in loaded.facts]
    try:
        facts = {**loaded.facts, **await _facts(pool, fresh, loaded.day)}
    except FactsTimeout:
        facts = dict(loaded.facts)
        notes.append(
            "Statements for the added securities took too long to read; they are unscored."
        )
    after = analyse(data, facts)
    looked: dict[str, bool] = {}
    for p in [*portfolio.positions, *new_portfolio.positions]:
        if is_fund_type(p.security_type) or loaded.settings.fund(p.isin) is not None:
            looked[p.ticker] = bool(
                p.isin and loaded.settings.fund(p.isin) is not None and p.isin in data.snapshots
            )
    return compare(
        loaded.analysis,
        after,
        portfolio,
        new_portfolio,
        as_of=loaded.day.isoformat(),
        notes=[*loaded.notes, *notes],
        held_funds_looked_through=looked,
    )
