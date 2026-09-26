"""Read models for the holding page's identity, structure, price and risk sections, the
reference-data gaps (Activity page) and the exchange-closure suggestions."""

import datetime as dt
import json
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.config.holding_profiles import HoldingProfile, HoldingProfilesConfig
from steward.config.jobs import ExchangeClosure, JobsConfig
from steward.config.store import StoredConfig, load_active, save_config
from steward.reference import risk
from steward.reference.calendar import closure_suggestions
from steward.reference.prices import CHECKS_KEY, GAPS_KEY
from steward.sources.esma import MIC_COUNTRY

RelationshipType = Literal[
    "parent", "ultimate_parent", "subsidiary", "owner", "competitor", "supplier", "customer"
]


class IdentifierOut(BaseModel):
    figi: str
    composite_figi: str | None
    listing_ticker: str | None
    exch_code: str | None
    security_type: str | None
    name: str | None


class ListingOut(BaseModel):
    mic: str
    full_name: str | None
    cfi: str | None
    currency: str | None
    first_trade_at: dt.datetime | None
    terminated_at: dt.datetime | None
    status: str | None
    home: bool


class LiquidityOut(BaseModel):
    liquid: bool | None
    avg_daily_turnover: float | None
    turnover_currency: str | None
    avg_daily_trades: float | None
    most_relevant_market: str | None
    period_to: dt.date | None


class LegalEntityOut(BaseModel):
    lei: str
    legal_name: str
    other_names: list[str]
    jurisdiction: str | None
    legal_form: str | None
    hq_country: str | None
    status: str | None
    registration_status: str | None
    next_renewal: dt.datetime | None
    successor_lei: str | None
    fetched_at: dt.datetime


class RelationshipOut(BaseModel):
    id: int
    type: RelationshipType
    object_name: str
    object_lei: str | None
    source: str
    sources: list[str] = Field(description="Every source that reported it.")
    source_ref: str | None
    as_of: dt.date | None
    status: Literal["confirmed", "suggested", "rejected", "disputed"]
    tier: int = Field(description="1 GLEIF or a filing, 3 Wikidata; owner decisions win.")
    conflict: dict[str, Any] | None = Field(
        default=None,
        description="Disputed: the contradicting source's claim (source, tier, object_name, "
        "source_ref).",
    )


class ReportingExceptionOut(BaseModel):
    category: str
    reason: str


class AliasOut(BaseModel):
    alias: str
    kind: str
    source: str


class WikidataSummary(BaseModel):
    qid: str
    industry: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    ceo: list[str] = Field(default_factory=list)
    chair: list[str] = Field(default_factory=list)
    website: str | None = None
    part_of: list[str] = Field(default_factory=list, description="Indexes and groups.")
    fetched_at: dt.datetime


class HoldingIdentity(BaseModel):
    ticker: str
    isin: str | None
    share_class_figi: str | None
    security_type: str | None
    home_mic: str | None
    home_country: str | None
    price_symbol: str | None
    identity_resolved_at: dt.datetime | None
    identity_error: str | None = Field(description="Why identity is incomplete; null when fine.")
    identifiers: list[IdentifierOut] = Field(description="Composite (per-country) listings.")
    identifier_count: int = Field(description="All OpenFIGI listings of the share class.")
    listings: list[ListingOut]
    liquidity: LiquidityOut | None
    legal_entity: LegalEntityOut | None
    parents: list[RelationshipOut] = Field(description="Direct and ultimate consolidating parents.")
    parent_exceptions: list[ReportingExceptionOut] = Field(
        description="GLEIF's stated reasons that no parent is reported."
    )
    subsidiaries: list[RelationshipOut]
    subsidiary_count: int
    owners: list[RelationshipOut]
    suggested: list[RelationshipOut] = Field(description="Waiting for the owner to confirm.")
    disputed: list[RelationshipOut] = Field(
        default_factory=list,
        description="A tier-3 claim a tier-1 source contradicts; `conflict` holds the other "
        "source's version. Waiting for the owner.",
    )
    related: list[RelationshipOut] = Field(
        description="Confirmed competitors, suppliers and customers."
    )
    aliases: list[AliasOut]
    wikidata: WikidataSummary | None = Field(description="Tier 3: community-edited.")


SOURCE_TIER = {"gleif": 1, "edgar": 1, "owner": 1, "wikidata": 3}


def _rel(row: asyncpg.Record) -> RelationshipOut:
    return RelationshipOut(
        id=row["id"],
        type=row["type"],
        object_name=row["object_name"],
        object_lei=row["object_lei"],
        source=row["source"],
        sources=list(row["sources"]),
        source_ref=row["source_ref"],
        as_of=row["as_of"],
        status=row["status"],
        tier=min(SOURCE_TIER.get(s, 3) for s in [row["source"], *row["sources"]]),
        conflict=json.loads(row["conflict"]) if row["conflict"] else None,
    )


async def holding_identity(pool: asyncpg.Pool, ticker: str) -> HoldingIdentity | None:
    inst = await pool.fetchrow(
        "select t212_ticker, isin, share_class_figi, security_type, home_mic, home_country,"
        " price_symbol, identity_resolved_at, identity_error, issuer_lei, wikidata"
        " from public.instrument where t212_ticker = $1",
        ticker,
    )
    if inst is None:
        return None
    ids = await pool.fetch(
        "select figi, composite_figi, listing_ticker, exch_code, security_type, name"
        " from public.instrument_identifier where ticker = $1 order by exch_code, figi",
        ticker,
    )
    listings = await pool.fetch(
        "select mic, full_name, cfi, currency, first_trade_at, terminated_at, status"
        " from public.listing where isin = $1 order by (terminated_at is not null), mic",
        inst["isin"],
    )
    liquidity = await pool.fetchrow(
        "select liquid, avg_daily_turnover, turnover_currency, avg_daily_trades,"
        " most_relevant_market, period_to from public.listing_liquidity where isin = $1",
        inst["isin"],
    )
    entity = (
        await pool.fetchrow(
            "select lei, legal_name, other_names, jurisdiction, legal_form, hq_country, status,"
            " registration_status, next_renewal, successor_lei, fetched_at"
            " from public.legal_entity where lei = $1",
            inst["issuer_lei"],
        )
        if inst["issuer_lei"]
        else None
    )
    rels = [
        _rel(r)
        for r in await pool.fetch(
            "select * from public.entity_relationship where ticker = $1"
            " order by type, status, object_name",
            ticker,
        )
    ]
    exceptions = (
        await pool.fetch(
            "select category, reason from public.entity_reporting_exception where lei = $1",
            inst["issuer_lei"],
        )
        if inst["issuer_lei"]
        else []
    )
    aliases = await pool.fetch(
        "select alias, kind, source from public.entity_alias where ticker = $1"
        " order by kind, alias",
        ticker,
    )
    confirmed = [r for r in rels if r.status == "confirmed"]
    subsidiaries = [r for r in confirmed if r.type == "subsidiary"]
    return HoldingIdentity(
        ticker=ticker,
        isin=inst["isin"],
        share_class_figi=inst["share_class_figi"],
        security_type=inst["security_type"],
        home_mic=inst["home_mic"],
        home_country=inst["home_country"],
        price_symbol=inst["price_symbol"],
        identity_resolved_at=inst["identity_resolved_at"],
        identity_error=inst["identity_error"],
        identifiers=[IdentifierOut(**dict(r)) for r in ids if r["figi"] == r["composite_figi"]],
        identifier_count=len(ids),
        listings=[ListingOut(**dict(r), home=r["mic"] == inst["home_mic"]) for r in listings],
        liquidity=LiquidityOut(**dict(liquidity)) if liquidity else None,
        legal_entity=LegalEntityOut(**dict(entity)) if entity else None,
        parents=[r for r in confirmed if r.type in ("parent", "ultimate_parent")],
        parent_exceptions=[ReportingExceptionOut(**dict(e)) for e in exceptions],
        subsidiaries=subsidiaries[:100],
        subsidiary_count=len(subsidiaries),
        owners=[r for r in confirmed if r.type == "owner"],
        suggested=[r for r in rels if r.status == "suggested"],
        disputed=[r for r in rels if r.status == "disputed"],
        related=[r for r in confirmed if r.type in ("competitor", "supplier", "customer")],
        aliases=[AliasOut(**dict(a)) for a in aliases],
        wikidata=WikidataSummary.model_validate(json.loads(inst["wikidata"]))
        if inst["wikidata"]
        else None,
    )


# ---------------------------------------------------------------------------
# Relationship decisions
# ---------------------------------------------------------------------------


class RelationshipDecisionError(Exception):
    def __init__(self, message: str, *, not_found: bool = False) -> None:
        super().__init__(message)
        self.not_found = not_found


PROFILE_FIELD = {"competitor": "competitors", "supplier": "suppliers", "customer": "customers"}


async def decide_relationship(
    pool: asyncpg.Pool, relationship_id: int, confirm: bool, owner_id: str | None
) -> tuple[RelationshipOut, int | None]:
    """Confirm or reject a suggestion. Confirming a competitor, supplier or customer writes a
    new holding-profiles version with it (so the next news run matches on it); confirming a
    subsidiary adds its name to the holding's news aliases. A rejection is never suggested
    again. Returns the relationship and the new profile version id, if any."""
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "select * from public.entity_relationship where id = $1 for update", relationship_id
        )
        if row is None:
            raise RelationshipDecisionError(f"No relationship {relationship_id}", not_found=True)
        if row["status"] not in ("suggested", "disputed"):
            raise RelationshipDecisionError(
                f"Relationship {relationship_id} is {row['status']}, not a suggestion"
            )
        updated = await conn.fetchrow(
            "update public.entity_relationship set status = $2, decided_at = now(),"
            " decided_by = $3::uuid, sources = (select array_agg(distinct s) from"
            " unnest(sources || case when $2 = 'confirmed' then array['owner'] else"
            " array[]::text[] end) s) where id = $1 returning *",
            relationship_id,
            "confirmed" if confirm else "rejected",
            owner_id,
        )
        version_id = None
        if confirm and row["type"] in PROFILE_FIELD:
            version_id = await _add_to_profile(conn, row, owner_id)
        if confirm and row["type"] == "subsidiary":
            await conn.execute(
                "insert into public.entity_alias (ticker, alias, kind, source) values"
                " ($1, $2, 'subsidiary', 'owner') on conflict (ticker, alias) do nothing",
                row["ticker"],
                row["object_name"],
            )
    return _rel(updated), version_id


async def _add_to_profile(
    conn: asyncpg.Connection, row: asyncpg.Record, owner_id: str | None
) -> int:
    stored = await load_active(conn, "holding_profiles", HoldingProfilesConfig)
    config = stored.body if stored else HoldingProfilesConfig()
    field = PROFILE_FIELD[row["type"]]
    profiles = list(config.profiles)
    current = config.for_ticker(row["ticker"]) or HoldingProfile(ticker=row["ticker"])
    names: list[str] = list(getattr(current, field))
    if row["object_name"] not in names:
        names.append(row["object_name"][:200])
    updated = current.model_copy(update={field: names})
    profiles = [p for p in profiles if p.ticker != row["ticker"]] + [updated]
    saved: StoredConfig[HoldingProfilesConfig] = await save_config(
        conn,
        "holding_profiles",
        HoldingProfilesConfig(profiles=profiles),
        created_by=owner_id,
        note=f"Confirmed suggested {row['type']} {row['object_name']} ({row['source']})",
    )
    return saved.id


# ---------------------------------------------------------------------------
# Prices and risk
# ---------------------------------------------------------------------------


class PricePoint(BaseModel):
    date: dt.date
    close: float


class RiskCard(BaseModel):
    returns: dict[str, float | None] = Field(description="1m, 3m, 6m, 1y, 2y (fractions).")
    volatility: float | None = Field(description="Annualised, last 252 trading days.")
    max_drawdown: float | None = Field(description="Worst fall from a peak, stored history.")
    beta: float | None = Field(description="Against the first benchmark, last 252 days.")
    observations: int


class Correlation(BaseModel):
    ticker: str
    correlation: float | None


class PlausibilityCheck(BaseModel):
    date: dt.date
    stooq_close: float
    trading212_price: float
    difference_pct: float
    flagged: bool = Field(description="Beyond the configured tolerance.")


class PriceHistory(BaseModel):
    ticker: str
    symbol: str | None
    source: Literal["Stooq"] = "Stooq"
    as_of: dt.date | None = Field(description="Date of the latest stored close.")
    fetched_at: dt.datetime | None
    bars: list[PricePoint]
    benchmark_symbol: str | None
    benchmark: list[PricePoint]
    risk: RiskCard | None
    correlations: list[Correlation] = Field(description="With each other holding, last 252 days.")
    gap: str | None = Field(description="Why there is no price history, when there is none.")
    plausibility: PlausibilityCheck | None = Field(
        description="Latest Stooq close vs Trading212's price on a sync day."
    )


async def load_series(
    pool: asyncpg.Pool, symbol: str, since: dt.date | None = None
) -> list[tuple[dt.date, float]]:
    rows = await pool.fetch(
        "select date, close from public.price_bar where symbol = $1 and source = 'stooq'"
        " and ($2::date is null or date >= $2) order by date",
        symbol,
        since,
    )
    return [(r["date"], float(r["close"])) for r in rows]


async def _state(pool: asyncpg.Pool, key: str) -> dict[str, Any]:
    raw = await pool.fetchval("select value from public.sync_state where key = $1", key)
    return dict(json.loads(raw)) if raw else {}


async def price_history(pool: asyncpg.Pool, ticker: str, days: int) -> PriceHistory:
    jobs = await load_active(pool, "jobs", JobsConfig)
    profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    benchmarks = (jobs.body if jobs else JobsConfig()).prices.benchmark_symbols
    benchmark_symbol = benchmarks[0] if benchmarks else None
    profile = profiles.body.for_ticker(ticker) if profiles else None
    symbol = (profile.price_symbol if profile else None) or await pool.fetchval(
        "select price_symbol from public.instrument where t212_ticker = $1", ticker
    )
    gaps = (await _state(pool, GAPS_KEY)).get("gaps", {})
    checks = (await _state(pool, CHECKS_KEY)).get("checks", {})
    series = await load_series(pool, symbol) if symbol else []
    bench = await load_series(pool, benchmark_symbol) if benchmark_symbol else []
    since = (series[-1][0] if series else dt.date.today()) - dt.timedelta(days=days)
    fetched = (
        await pool.fetchval(
            "select max(fetched_at) from public.price_bar where symbol = $1", symbol
        )
        if symbol
        else None
    )
    correlations = []
    if series:
        others = await pool.fetch(
            "select distinct t212_ticker, price_symbol from public.instrument"
            " where price_symbol is not null and t212_ticker <> $1 and t212_ticker in"
            " (select ticker from public.position_snapshot where snapshot_date ="
            " (select max(snapshot_date) from public.position_snapshot))",
            ticker,
        )
        for other in others:
            other_series = await load_series(pool, other["price_symbol"])
            correlations.append(
                Correlation(
                    ticker=other["t212_ticker"],
                    correlation=risk.correlation(series, other_series),
                )
            )
    figures = risk.figures(series, bench or None) if series else None
    gap = None
    if not series:
        gap = (gaps.get(ticker) or {}).get("reason") or (
            "no Stooq symbol (set price_symbol in the holding profile)"
            if not symbol
            else "no prices stored yet"
        )
    return PriceHistory(
        ticker=ticker,
        symbol=symbol,
        as_of=series[-1][0] if series else None,
        fetched_at=fetched,
        bars=[PricePoint(date=d, close=c) for d, c in series if d >= since],
        benchmark_symbol=benchmark_symbol,
        benchmark=[PricePoint(date=d, close=c) for d, c in bench if d >= since],
        risk=RiskCard(**figures.__dict__) if figures else None,
        correlations=correlations,
        gap=gap,
        plausibility=PlausibilityCheck.model_validate(checks[ticker]) if ticker in checks else None,
    )


# ---------------------------------------------------------------------------
# Gaps and calendars
# ---------------------------------------------------------------------------


class IdentityGap(BaseModel):
    ticker: str
    name: str | None
    problem: str


class PriceGap(BaseModel):
    key: str = Field(description="Ticker, or the benchmark symbol.")
    kind: Literal["holding", "benchmark"]
    symbol: str | None
    reason: str


class ReferenceGaps(BaseModel):
    identity: list[IdentityGap]
    prices: list[PriceGap]
    prices_checked_at: str | None


async def reference_gaps(pool: asyncpg.Pool) -> ReferenceGaps:
    rows = await pool.fetch(
        "select t212_ticker, name, identity_error from public.instrument"
        " where identity_error is not null order by t212_ticker"
    )
    state = await _state(pool, GAPS_KEY)
    return ReferenceGaps(
        identity=[
            IdentityGap(ticker=r["t212_ticker"], name=r["name"], problem=r["identity_error"])
            for r in rows
        ],
        prices=[
            PriceGap(
                key=k,
                kind="benchmark" if k == v.get("symbol") else "holding",
                symbol=v.get("symbol"),
                reason=v.get("reason", ""),
            )
            for k, v in sorted(state.get("gaps", {}).items())
        ],
        prices_checked_at=state.get("at"),
    )


class ClosureSuggestion(BaseModel):
    mic: str
    date: dt.date
    name: str


class ExchangeCalendar(BaseModel):
    exchanges: dict[str, str] = Field(description="Home MIC of a holding -> its country.")
    closures: list[ExchangeClosure] = Field(description="Confirmed (in the Jobs config).")
    suggestions: list[ClosureSuggestion] = Field(
        description="Public holidays in an exchange's country not yet confirmed as closures."
    )


async def exchange_calendar(pool: asyncpg.Pool, today: dt.date) -> ExchangeCalendar:
    jobs = await load_active(pool, "jobs", JobsConfig)
    settings = (jobs.body if jobs else JobsConfig()).calendar
    rows = await pool.fetch(
        "select distinct home_mic, home_country from public.instrument where home_mic is not null"
    )
    mics = {r["home_mic"]: r["home_country"] or MIC_COUNTRY.get(r["home_mic"], "") for r in rows}
    mics = {m: c for m, c in mics.items() if c}
    suggestions = await closure_suggestions(
        pool, settings, mics, today, today + dt.timedelta(days=400)
    )
    return ExchangeCalendar(
        exchanges=mics,
        closures=settings.exchange_closures,
        suggestions=[ClosureSuggestion(mic=m, date=d, name=n) for m, d, n in suggestions],
    )


async def confirm_closures(
    pool: asyncpg.Pool, closures: list[ExchangeClosure], owner_id: str | None
) -> int:
    """Add closures to the Jobs config as a new version; returns its id."""
    async with pool.acquire() as conn:
        stored = await load_active(conn, "jobs", JobsConfig)
        config = stored.body if stored else JobsConfig()
        existing = {(c.mic, c.date) for c in config.calendar.exchange_closures}
        merged = config.calendar.exchange_closures + [
            c for c in closures if (c.mic, c.date) not in existing
        ]
        merged.sort(key=lambda c: (c.date, c.mic))
        updated = config.model_copy(
            update={"calendar": config.calendar.model_copy(update={"exchange_closures": merged})}
        )
        saved = await save_config(
            conn,
            "jobs",
            JobsConfig.model_validate(updated.model_dump()),
            created_by=owner_id,
            note=f"Confirmed {len(closures)} exchange closure(s)",
        )
    return saved.id
