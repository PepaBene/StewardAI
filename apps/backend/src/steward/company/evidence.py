"""Evidence-linked read models for the company workbench (plan M2.1, M2.7, M2.8, M2.10, M2.11):
overview, estimates, business graph and patents, capital allocation and governance.

Everything here is read from stored, sourced rows. Nothing is inferred: an estimate without a
metadata record is not shown as consensus, a suggested relationship is labelled suggested, a
13F position is not called ownership, and an absent source is named.
"""

# ruff: noqa: E501
from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlsplit

import asyncpg
from pydantic import BaseModel, Field, field_validator

from steward.analytics.contract import SourceRef, rounded
from steward.analytics.versions import version
from steward.company.ratios import figures_by_column
from steward.company.statements import StatementsView
from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.store import load_active
from steward.reference.price_series import choose_series, holding_symbols

PROFILE_METRICS = ("sector", "industry", "description", "employees", "ceo", "website", "ipo_date")


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


# ------------------------------------------------------------------------------ overview


class Listing(BaseModel):
    ticker: str
    name: str | None
    mic: str | None
    currency: str | None
    status: str | None
    selected: bool = False


class ProfileFact(BaseModel):
    metric: str
    value: str
    provider: str
    source_url: str | None
    known_at: dt.datetime


class SegmentLine(BaseModel):
    label: str
    value: Decimal | None
    share: Decimal | None
    period_end: dt.date | None
    basis: str | None = None


class UpcomingEvent(BaseModel):
    kind: str
    date: dt.date
    detail: str
    source: str | None = None


class CoverageScore(BaseModel):
    have: int
    of: int


class CoverageItem(BaseModel):
    area: str
    present: bool
    detail: str


class LegalLink(BaseModel):
    relation: Literal["parent", "ultimate_parent", "subsidiary"]
    name: str
    lei: str | None
    status: str = Field(description="confirmed, or suggested until you confirm it")
    source: str
    as_of: dt.date | None


class OperationLine(BaseModel):
    label: str
    revenue: Decimal | None
    share: Decimal | None


class OperationBreakdown(BaseModel):
    kind: str = Field(description="geographic, product or segment, as the company reports it")
    label: str
    period: str | None
    currency: str | None
    lines: list[OperationLine]
    note: str | None = None


class Overview(BaseModel):
    ticker: str
    name: str | None
    legal_name: str | None
    isin: str | None
    cik: str | None
    lei: str | None
    website: str | None
    country: str | None
    asset_type: str | None
    status: str | None
    currency: str | None
    listing_currency: str | None
    listings: list[Listing]
    profile: list[ProfileFact]
    segments: list[SegmentLine]
    operations: list[OperationBreakdown] = Field(default_factory=list)
    legal_group: list[LegalLink] = Field(default_factory=list)
    legal_group_note: str = ""
    competitors: list[str]
    peers_confirmed: list[str]
    upcoming: list[UpcomingEvent]
    last_close: Decimal | None
    last_close_date: dt.date | None
    coverage: list[CoverageItem]
    coverage_score: CoverageScore
    sources: list[SourceRef]
    method_version: str


def _single_axis(dimensions: Any) -> tuple[str, str] | None:
    """(axis, member) of a fact broken out on exactly one axis; None for a total or a cross-cut."""
    dims = _json(dimensions)
    members = dims.get("members") or dims.get("typed") or {}
    if isinstance(members, list):
        members = {str(p[0]): p[1] for p in members if isinstance(p, (list, tuple)) and len(p) == 2}
    if not isinstance(members, dict) or len(members) != 1:
        return None
    ((axis, member),) = members.items()
    return str(axis), str(member)


_AXIS_PREFERENCE = ("BusinessSegments", "OperatingSegments", "ProductOrService", "Geograph")


def segment_lines(rows: list[dict[str, Any]]) -> list[SegmentLine]:
    """Revenue of the latest full-year period on one axis: annual-length facts only, the latest
    filing's value when a figure was restated, and a single axis so that geography, products and
    segments (each of which adds up to the same revenue) are never summed together."""
    latest: dict[tuple[str, str, dt.date, dt.date], dict[str, Any]] = {}
    for r in rows:
        axis = _single_axis(r["dimensions"])
        start, end = r["period_start"], r["period_end"]
        if axis is None or start is None or not 330 <= (end - start).days <= 380:
            continue
        key = (*axis, start, end)
        if key not in latest or (r["known_at"], r["id"]) > (
            latest[key]["known_at"],
            latest[key]["id"],
        ):
            latest[key] = r
    if not latest:
        return []
    period_end = max(end for _, _, _, end in latest)
    by_axis: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for (axis_name, member, _start, end), r in latest.items():
        if end == period_end:
            by_axis.setdefault(axis_name, []).append((member, r))

    def rank(axis: str) -> tuple[int, Decimal]:
        order = next(
            (i for i, p in enumerate(_AXIS_PREFERENCE) if p in axis), len(_AXIS_PREFERENCE)
        )
        return order, -sum((r["value"] for _, r in by_axis[axis] if r["value"] > 0), Decimal(0))

    chosen = by_axis[min(by_axis, key=rank)]
    chosen.sort(key=lambda m: -m[1]["value"])
    total = sum((r["value"] for _, r in chosen if r["value"] > 0), Decimal(0))
    return [
        SegmentLine(
            label=member.split(":")[-1],
            value=r["value"],
            share=rounded(r["value"] / total, "fraction") if total > 0 else None,
            period_end=period_end,
        )
        for member, r in chosen[:12]
    ]


async def overview(pool: asyncpg.Pool, ticker: str) -> Overview | None:
    u = await pool.fetchrow(
        """select u.ticker, u.name, u.isin, u.country, u.mic, u.exchange, u.currency, u.status,
                  u.asset_type, u.issuer_id, u.price_symbol, u.has_statements, u.has_prices,
                  i.next_report_date, i.cik as instrument_cik,
                  iss.legal_name, iss.lei, iss.cik as issuer_cik, iss.website
           from public.investment_universe u
           left join public.instrument i on i.t212_ticker = u.ticker
           left join public.issuer iss on iss.id = u.issuer_id
           where u.ticker = $1 limit 1""",
        ticker,
    )
    if u is None:
        return None
    listings: list[Listing] = []
    if u["issuer_id"] is not None:
        rows = await pool.fetch(
            "select ticker, name, mic, currency, status from public.investment_universe where issuer_id=$1 order by ticker",
            u["issuer_id"],
        )
        listings = [Listing(**dict(r), selected=r["ticker"] == ticker) for r in rows]
    if not listings:
        listings = [
            Listing(
                ticker=ticker,
                name=u["name"],
                mic=u["mic"],
                currency=u["currency"],
                status=u["status"],
                selected=True,
            )
        ]
    prof = await pool.fetch(
        """select distinct on (metric) metric, coalesce(text_value, value::text) as value, provider, source_url, known_at
           from public.company_research_observation
           where ticker=$1 and dataset='profile' and metric = any($2::text[])
           order by metric, known_at desc""",
        ticker,
        list(PROFILE_METRICS),
    )
    profile = [ProfileFact(**dict(r)) for r in prof if r["value"]]
    seg_rows = await pool.fetch(
        """select dimensions, period_start, period_end, value, known_at, id
           from public.financial_statement_fact
           where ticker=$1 and statement='segment' and value is not null
             and period_start is not null
             and concept in ('us-gaap:Revenues','us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax','ifrs-full:Revenue')""",
        ticker,
    )
    segments = segment_lines([dict(r) for r in seg_rows])
    comp = await pool.fetch(
        """select distinct object_name from public.company_graph_edge
           where subject_key=$1 and edge_type='competitor' and status='confirmed' order by object_name limit 12""",
        ticker,
    )
    peers = await pool.fetch(
        "select distinct peer_ticker from public.peer_set where ticker=$1 and status='confirmed' order by peer_ticker limit 20",
        ticker,
    )
    upcoming: list[UpcomingEvent] = []
    today = dt.date.today()
    if u["next_report_date"] and u["next_report_date"] >= today:
        upcoming.append(
            UpcomingEvent(
                kind="results",
                date=u["next_report_date"],
                detail="Next reported results date",
                source="instrument calendar",
            )
        )
    actions = await pool.fetch(
        """select a.action_type, a.ex_date, a.cash_amount, a.currency, a.source_url
           from public.corporate_action a join public.security_listing l on l.id=a.listing_id
           join public.instrument_security_link k on k.listing_id=l.id
           where k.ticker=$1 and a.ex_date >= $2 and a.status='confirmed' order by a.ex_date limit 5""",
        ticker,
        today,
    )
    for a in actions:
        amount = f" {a['cash_amount']} {a['currency']}" if a["cash_amount"] is not None else ""
        upcoming.append(
            UpcomingEvent(
                kind=a["action_type"],
                date=a["ex_date"],
                detail=f"Ex-date{amount}",
                source=a["source_url"],
            )
        )
    # The instrument's symbol is Stooq style; the stored bars may be Yahoo style, so the series
    # is chosen the way every other reader does (newest stored bar, Stooq on a tie).
    bar = None
    profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    symbols = await holding_symbols(
        pool, [ticker], profiles.body if profiles else HoldingProfilesConfig()
    )
    ref = (await choose_series(pool, symbols))[ticker]
    if ref is not None:
        bar = await pool.fetchrow(
            """select date, close from public.price_bar where symbol=$1 and source=$2
               and bar_type='adjusted' and access_state='available'
               order by date desc limit 1""",
            ref.symbol,
            ref.source,
        )
    years = await pool.fetchval(
        "select count(distinct period_end) from public.financial_statement_fact where ticker=$1 and statement='income' and period_start is not null and period_end-period_start between 330 and 380",
        ticker,
    )
    est = await pool.fetchval(
        "select count(*) from public.forward_observation where ticker=$1", ticker
    )
    edges = await pool.fetchval(
        "select count(*) from public.company_graph_edge where subject_key=$1 and status='confirmed'",
        ticker,
    )
    patents = await pool.fetchval(
        "select count(*) from public.patent_family_owner where ticker=$1", ticker
    )
    insiders = await pool.fetchval(
        "select count(*) from public.insider_filing where ticker=$1", ticker
    )
    coverage = [
        CoverageItem(
            area="Filed statements",
            present=bool(years),
            detail=f"{years or 0} fiscal years of income-statement facts",
        ),
        CoverageItem(
            area="Price history",
            present=bar is not None,
            detail=f"latest close {bar['date']}" if bar else "no stored close",
        ),
        CoverageItem(
            area="Provider profile",
            present=bool(profile),
            detail=f"{len(profile)} profile fields" if profile else "no provider profile fetched",
        ),
        CoverageItem(
            area="Segments",
            present=bool(segments),
            detail=f"{len(segments)} segment lines" if segments else "no dimensional segment facts",
        ),
        CoverageItem(
            area="Estimates",
            present=bool(est),
            detail=f"{est} stored observations"
            if est
            else "no stored estimates (see the Estimates tab)",
        ),
        CoverageItem(
            area="Business graph",
            present=bool(edges),
            detail=f"{edges or 0} confirmed relationships",
        ),
        CoverageItem(
            area="Patents", present=bool(patents), detail=f"{patents or 0} owned patent families"
        ),
        CoverageItem(
            area="Insider filings", present=bool(insiders), detail=f"{insiders or 0} filings"
        ),
    ]
    got = sum(1 for c in coverage if c.present)
    legal = await pool.fetch(
        """select type, object_name, object_lei, status, source, as_of from public.entity_relationship
           where ticker=$1 and type in ('parent','ultimate_parent','subsidiary') and status in ('confirmed','suggested')
           order by case type when 'ultimate_parent' then 0 when 'parent' then 1 else 2 end,
                    (status='confirmed') desc, object_name limit 40""",
        ticker,
    )
    exception = await pool.fetchval(
        """select e.reason from public.entity_reporting_exception e
           join public.issuer i on i.lei = e.lei where i.id=$1 limit 1""",
        u["issuer_id"],
    )
    legal_note = (
        "Parents are accounting-consolidation parents, not shareholders. Suggested links come from "
        "Wikidata or filings and stay suggested until you confirm them."
        if legal
        else (
            f"GLEIF reports no parent: {exception}."
            if exception
            else "No parent or subsidiary relationship is stored for this company."
        )
    )
    return Overview(
        legal_group=[
            LegalLink(
                relation=r["type"],
                name=r["object_name"],
                lei=r["object_lei"],
                status=r["status"],
                source=r["source"],
                as_of=r["as_of"],
            )
            for r in legal
        ],
        legal_group_note=legal_note,
        ticker=ticker,
        name=u["name"],
        legal_name=u["legal_name"],
        isin=u["isin"],
        cik=u["issuer_cik"] or u["instrument_cik"],
        lei=u["lei"],
        website=u["website"],
        country=u["country"],
        asset_type=u["asset_type"],
        status=u["status"],
        currency=u["currency"],
        listing_currency=u["currency"],
        listings=listings,
        profile=profile,
        segments=segments,
        competitors=[r["object_name"] for r in comp],
        peers_confirmed=[r["peer_ticker"] for r in peers],
        upcoming=upcoming,
        last_close=bar["close"] if bar else None,
        last_close_date=bar["date"] if bar else None,
        coverage=coverage,
        coverage_score=CoverageScore(have=got, of=len(coverage)),
        sources=[SourceRef(kind="provider", ref=p.provider, url=p.source_url) for p in profile[:6]],
        method_version=version("peer_compare"),
    )


# ----------------------------------------------------------------------------- estimates


class EstimatePoint(BaseModel):
    kind: str
    kind_label: str
    metric: str
    fiscal_period: str
    period_end: dt.date | None
    provider: str | None
    basis: str | None
    currency: str | None
    value: Decimal | None
    low: Decimal | None
    high: Decimal | None
    analyst_count: int | None
    vintage: str | None
    known_at: dt.datetime
    source_url: str
    metadata_complete: bool
    surprise: Decimal | None = None
    surprise_note: str | None = None


class Estimates(BaseModel):
    ticker: str
    access: str
    access_note: str
    points: list[EstimatePoint]
    notes: list[str]
    method_version: str


KIND_LABEL = {
    "published_analyst_consensus": "Forward consensus (published analysts)",
    "event_calendar_estimate": "Event-calendar estimate (not consensus)",
    "company_guidance": "Company guidance",
    "owner_scenario": "Your scenario",
}


def _complete(r: Mapping[str, Any]) -> bool:
    """A consensus needs provider, fiscal period, currency, basis and an analyst count."""
    return (
        all(r.get(k) not in (None, "") for k in ("provider", "fiscal_period", "currency", "basis"))
        and r.get("analyst_count") is not None
    )


def _eps_known_at(annual: StatementsView | None) -> dict[dt.date, dt.datetime | None]:
    """When each fiscal year's diluted EPS was first filed, by period end."""
    if annual is None:
        return {}
    ends = {c.id: c.end for c in annual.columns if c.kind == "annual"}
    out: dict[dt.date, dt.datetime | None] = {}
    for table in annual.statements:
        for line in table.lines:
            if line.key == "eps_diluted":
                out.update(
                    {ends[cid]: cell.known_at for cid, cell in line.cells.items() if cid in ends}
                )
    return out


async def estimates(pool: asyncpg.Pool, ticker: str, annual: StatementsView | None) -> Estimates:
    state = await pool.fetchval(
        "select state from public.provider_access where provider='fmp' and endpoint='analyst_estimates'"
    )
    note = {
        "available": "Analyst estimates are available on the configured plan.",
        None: "No provider access record: estimates have not been probed.",
    }.get(
        state,
        f"Analyst-estimate access is '{state}': forward consensus is not shown, and an estimate filter stays disabled.",
    )
    rows = await pool.fetch(
        """select kind, metric, fiscal_period, period_end, provider, basis, currency, value, low, high, analyst_count,
                  vintage, known_at, source_url
           from public.forward_observation where ticker=$1 order by period_end nulls last, known_at desc limit 200""",
        ticker,
    )
    actuals: dict[dt.date, Mapping[str, Decimal | None]] = {}
    if annual is not None:
        fig = figures_by_column(annual)
        actuals = {c.end: fig[c.id] for c in annual.columns if c.kind == "annual"}
    actual_known = _eps_known_at(annual)
    scored: set[tuple[dt.date, str | None, str | None]] = set()
    points: list[EstimatePoint] = []
    for r in rows:
        d = dict(r)
        complete = _complete(d)
        if d["kind"] == "published_analyst_consensus" and state != "available":
            continue
        surprise, why = None, None
        if (
            d["kind"] == "published_analyst_consensus"
            and d["metric"] in ("earnings_per_share", "eps")
            and d["period_end"]
            and d["value"]
        ):
            hit_end, hit = next(
                ((end, a) for end, a in actuals.items() if abs((end - d["period_end"]).days) <= 10),
                (None, None),
            )
            if hit is None or hit_end is None:
                why = "no filed fiscal year ends near this estimate's period"
            elif not complete:
                why = "estimate metadata is incomplete (basis, currency or analyst count), so no surprise is computed"
            elif (d["basis"] or "").lower() not in ("diluted", "gaap", "reported") or hit.get(
                "eps_diluted"
            ) is None:
                why = f"estimate basis '{d['basis']}' is not the filed diluted EPS basis"
            elif (
                annual is None
                or not annual.currency
                or (d["currency"] or "").upper() != annual.currency.upper()
            ):
                why = f"estimate currency {d['currency']} differs from the filed {annual.currency if annual else None}, so no surprise is computed"
            elif (published := actual_known.get(hit_end)) is None or d["known_at"] > published:
                why = "estimate was not known before the actual was filed, so it is not a forecast of it"
            elif (key := (hit_end, d["provider"], d["basis"])) in scored:
                why = "a later estimate of this period from the same provider is the one scored"
            elif d["value"] == 0:
                why = "estimate is zero"
            else:
                scored.add(key)
                surprise = rounded((hit["eps_diluted"] - d["value"]) / abs(d["value"]), "fraction")
        points.append(
            EstimatePoint(
                **{
                    k: d[k]
                    for k in (
                        "kind",
                        "metric",
                        "fiscal_period",
                        "period_end",
                        "provider",
                        "basis",
                        "currency",
                        "value",
                        "low",
                        "high",
                        "analyst_count",
                        "vintage",
                        "known_at",
                        "source_url",
                    )
                },
                kind_label=KIND_LABEL.get(d["kind"], d["kind"]),
                metadata_complete=complete,
                surprise=surprise,
                surprise_note=why,
            )
        )
    notes = [
        "Event-calendar estimates (for example Finnhub) are a date and a single number for a report event; they are not forward consensus.",
        "Company guidance is the company's own statement and is shown separately from analyst estimates.",
        "A surprise percentage is shown only when the estimate's metric, fiscal period and basis match the filed actual.",
    ]
    if not any(p.kind == "published_analyst_consensus" for p in points):
        notes.append(
            "No forward consensus is stored with a full metadata record, so none is shown."
        )
    return Estimates(
        ticker=ticker,
        access=state or "unprobed",
        access_note=note,
        points=points,
        notes=notes,
        method_version=version("peer_compare"),
    )


async def forward_eps(
    pool: asyncpg.Pool, ticker: str, price_currency: str | None
) -> Decimal | None:
    """The next fiscal year's published EPS consensus, only with access and full metadata, and
    only in the currency the price is quoted in (a forward P/E divides one by the other)."""
    state = await pool.fetchval(
        "select state from public.provider_access where provider='fmp' and endpoint='analyst_estimates'"
    )
    if state != "available":
        return None
    r = await pool.fetchrow(
        """select value, provider, basis, currency, fiscal_period, analyst_count from public.forward_observation
           where ticker=$1 and kind='published_analyst_consensus' and metric='earnings_per_share' and value is not null
             and period_end > current_date order by period_end, known_at desc limit 1""",
        ticker,
    )
    if r is None or not _complete(dict(r)) or not price_currency or r["currency"] != price_currency:
        return None
    value: Decimal | None = r["value"]
    return value


# ------------------------------------------------------------------------------ network


class GraphEdge(BaseModel):
    edge_type: str
    counterparty: str
    direction: str
    status: str
    evidence_tier: str
    valid_from: dt.date | None
    valid_to: dt.date | None
    known_at: dt.datetime
    source: str
    source_url: str
    passage: str | None
    new_since: bool = False


class GraphEvent(BaseModel):
    event_type: str
    event_state: str
    counterparty: str | None
    announced_at: dt.date | None
    effective_at: dt.date | None
    status: str
    source_url: str
    known_at: dt.datetime
    terms: dict[str, Any]
    new_since: bool = False


class PatentYear(BaseModel):
    year: int
    families: int


class PatentTech(BaseModel):
    cpc: str
    families: int


class Network(BaseModel):
    ticker: str
    since: dt.date
    since_basis: Literal["chosen", "last_review", "last_journal_entry", "default_90_days"] = (
        "chosen"
    )
    edges: list[GraphEdge]
    suggested_count: int
    events: list[GraphEvent]
    patent_total: int
    patent_by_year: list[PatentYear]
    patent_by_tech: list[PatentTech]
    ownership_confidence_min: Decimal | None
    ownership_note: str
    patent_status: dict[str, int]
    caveats: list[str]
    method_version: str


async def network(
    pool: asyncpg.Pool, ticker: str, since: dt.date | None = None, owner_id: Any = None
) -> Network:
    basis = "chosen"
    if since is None:
        # The monthly review report carries no ticker, so the last review is the newer of the
        # owner's latest journal entry for this ticker and the latest monthly review report.
        journal = (
            await pool.fetchval(
                "select max(created_at)::date from public.decision_journal where owner_id=$1 and ticker=$2",
                owner_id,
                ticker,
            )
            if owner_id is not None
            else None
        )
        report = await pool.fetchval(
            "select max(created_at)::date from public.report where type='review'"
        )
        reviewed = max((d for d in (journal, report) if d is not None), default=None)
        if reviewed is not None:
            since = reviewed
            basis = "last_review" if reviewed == report else "last_journal_entry"
        else:
            since, basis = dt.date.today() - dt.timedelta(days=90), "default_90_days"
    cutoff = dt.datetime.combine(since, dt.time.min, tzinfo=dt.UTC)
    edges = await pool.fetch(
        """select edge_type, object_name as counterparty, direction, status, evidence_tier, valid_from, valid_to,
                  known_at, source, source_url, passage
           from public.company_graph_edge
           where subject_key=$1 and status in ('confirmed','suggested','disputed')
           order by (status='confirmed') desc, known_at desc limit 120""",
        ticker,
    )
    events = await pool.fetch(
        """select event_type, event_state, counterparty_key as counterparty, announced_at, effective_at, status,
                  source_url, known_at, terms
           from public.company_event where ticker=$1 and status in ('confirmed','suggested','disputed')
           order by coalesce(announced_at, known_at::date) desc limit 60""",
        ticker,
    )
    fam = await pool.fetch(
        """select f.priority_date, f.cpc_codes, f.legal_status, o.ownership_confidence
           from public.patent_family_owner o join public.patent_family f on f.id=o.family_id
           where o.ticker=$1""",
        ticker,
    )
    by_year: Counter[int] = Counter()
    by_tech: Counter[str] = Counter()
    status: Counter[str] = Counter()
    for r in fam:
        if r["priority_date"]:
            by_year[r["priority_date"].year] += 1
        for code in {c[:4] for c in (r["cpc_codes"] or []) if c}:
            by_tech[code] += 1
        status[r["legal_status"]] += 1
    shown = [
        GraphEdge(**dict(e), new_since=e["known_at"] >= cutoff)
        for e in edges
        if e["status"] != "suggested"
    ]
    suggested = sum(1 for e in edges if e["status"] == "suggested")
    conf = [r["ownership_confidence"] for r in fam if r["ownership_confidence"] is not None]
    return Network(
        ticker=ticker,
        since=since,
        since_basis=basis,
        edges=shown,
        suggested_count=suggested,
        events=[
            GraphEvent(**{**dict(e), "terms": _json(e["terms"])}, new_since=e["known_at"] >= cutoff)
            for e in events
        ],
        patent_total=len(fam),
        patent_by_year=[PatentYear(year=y, families=n) for y, n in sorted(by_year.items())],
        patent_by_tech=[PatentTech(cpc=c, families=n) for c, n in by_tech.most_common(10)],
        ownership_confidence_min=min(conf) if conf else None,
        ownership_note="Patent families are mapped to this issuer by applicant name or a dated subsidiary path; the lowest mapping confidence is shown, and suggested mappings are not confirmed ownership.",
        patent_status=dict(status),
        caveats=[
            "Only confirmed and disputed relationships are listed; suggested ones are counted, not shown as fact.",
            "'New' marks rows first seen on or after the date shown: the last review report for this "
            "company when one exists, otherwise 90 days back, unless you pick a date.",
        ],
        method_version=version("peer_compare"),
    )


# -------------------------------------------------------------------- capital allocation


class AllocationYear(BaseModel):
    column: str
    label: str
    fcf: Decimal | None
    capex: Decimal | None
    dividends: Decimal | None
    buybacks: Decimal | None
    debt_issued: Decimal | None
    debt_repaid: Decimal | None
    net_debt_change: Decimal | None
    acquisitions: Decimal | None = None
    divestitures: Decimal | None = None
    returned_share_of_fcf: Decimal | None
    share_count: Decimal | None
    share_change: Decimal | None
    fcf_per_share: Decimal | None
    fcf_per_share_growth: Decimal | None
    fcf_growth: Decimal | None
    reconciliation: str | None = None


class StatedAction(BaseModel):
    kind: str
    date: dt.date | None
    detail: str
    state: str | None
    source_url: str


class CapitalAllocation(BaseModel):
    ticker: str
    years: list[AllocationYear]
    stated: list[StatedAction]
    observed_note: str
    method_version: str
    warnings: list[str]


def _growth(now: Decimal | None, before: Decimal | None) -> Decimal | None:
    if now is None or before is None or before <= 0 or now <= 0:
        return None
    return rounded(now / before - 1, "fraction")


def announced_by_fiscal_year(
    rows: Sequence[Mapping[str, Any]],
    period_ends: Sequence[dt.date],
    currency: str | None,
    primary_listing: int | None = None,
) -> tuple[dict[dt.date, Decimal], list[str]]:
    """Confirmed cash dividends per share, summed by the fiscal year (keyed by its period end) that
    contains the ex-date, to sit beside that year's cash paid, plus notes on what was left out.

    Only the statement currency counts (an unlabelled amount is kept). With the company's primary
    listing known, only that listing counts. Without it, identical ex-date and amount rows are
    de-duplicated across listings and sources; if the listings still disagree, the announced figure
    is withheld and the ambiguity is flagged rather than one listing silently chosen."""
    notes: list[str] = []
    usable = [
        r
        for r in rows
        if r.get("currency") is None
        or currency is None
        or str(r["currency"]).upper() == currency.upper()
    ]
    if primary_listing is not None:
        usable = [r for r in usable if r.get("listing_id") == primary_listing]
    else:
        per_listing: dict[Any, set[tuple[dt.date, Decimal]]] = {}
        for r in usable:
            per_listing.setdefault(r.get("listing_id"), set()).add((r["ex_date"], r["cash_amount"]))
        if len({frozenset(s) for s in per_listing.values()}) > 1:
            notes.append(
                "Announced dividends differ between listings and no primary listing is recorded, "
                "so no announced figure is shown."
            )
            return {}, notes
    seen: set[tuple[dt.date, Decimal]] = set()
    ends = sorted(period_ends)
    out: dict[dt.date, Decimal] = {}
    for r in sorted(usable, key=lambda r: r["ex_date"]):
        key = (r["ex_date"], r["cash_amount"])
        if key in seen:  # the same dividend carried by another listing or source
            continue
        seen.add(key)
        ex = r["ex_date"]
        for i, end in enumerate(ends):
            start = ends[i - 1] if i else end - dt.timedelta(days=366)
            if start < ex <= end:
                out[end] = out.get(end, Decimal(0)) + r["cash_amount"]
                break
    return out, notes


async def capital_allocation(
    pool: asyncpg.Pool, ticker: str, annual: StatementsView
) -> CapitalAllocation:
    fig = figures_by_column(annual)
    cols = [c for c in annual.columns if c.kind == "annual"]
    div_rows = await pool.fetch(
        """select a.ex_date, a.cash_amount, a.currency, a.listing_id, k.listing_id as primary_listing
           from public.corporate_action a join public.security_listing l on l.id=a.listing_id
           join public.instrument_security_link k on k.listing_id=l.id
           where k.ticker=$1 and a.action_type='cash_dividend' and a.status='confirmed' and a.cash_amount is not null""",
        ticker,
    )
    # `instrument_security_link` holds one listing per ticker: it is the company's own (primary) one.
    primary = next(
        (r["primary_listing"] for r in div_rows if r["primary_listing"] is not None), None
    )
    paid_per_share, dividend_notes = announced_by_fiscal_year(
        [dict(r) for r in div_rows], [c.end for c in cols], annual.currency, primary
    )
    # Repurchases the equity note reports as made in the year, to set beside the cash paid.
    note_repurchased: dict[dt.date, Decimal] = {}
    for r in await pool.fetch(
        """select distinct on (period_end) period_end, value from public.financial_statement_fact
           where ticker=$1 and statement='note' and value is not null and coalesce(dimensions->'members', '[]'::jsonb) = '[]'::jsonb
             and concept in ('us-gaap:StockRepurchasedAndRetiredDuringPeriodValue','us-gaap:StockRepurchasedDuringPeriodValue')
             and period_start is not null and period_end - period_start between 330 and 380
           order by period_end, known_at desc""",
        ticker,
    ):
        note_repurchased[r["period_end"]] = abs(r["value"])
    years: list[AllocationYear] = []
    prev: AllocationYear | None = None
    for c in cols:
        f = fig[c.id]
        cfo, capex = f.get("cash_from_operations"), f.get("capex")
        fcf = None if cfo is None or capex is None else cfo - abs(capex)
        div, buy = f.get("dividends_paid"), f.get("share_repurchases_paid")
        issued, repaid = f.get("debt_issued"), f.get("debt_repaid")
        acq, divest = f.get("acquisitions_paid"), f.get("divestiture_proceeds")
        net_debt = None if issued is None or repaid is None else abs(issued) - abs(repaid)
        shares = f.get("weighted_shares_diluted") or f.get("weighted_shares_basic")
        returned = None
        if fcf is not None and fcf > 0 and (div is not None or buy is not None):
            returned = rounded((abs(div or Decimal(0)) + abs(buy or Decimal(0))) / fcf, "fraction")
        fps = None if fcf is None or not shares else rounded(fcf / shares, "per_share")
        recon = None
        announced = paid_per_share.get(c.end)
        if announced is not None and div is not None and shares:
            paid = abs(div) / shares
            gap = abs(paid - announced) / announced if announced else None
            recon = f"announced {announced:.4f} per share vs paid {paid:.4f}: {'consistent' if gap is not None and gap <= Decimal('0.15') else 'differs by more than 15%, check timing or the share count'}"
        noted = note_repurchased.get(c.end)
        if noted is not None and buy is not None and abs(buy) > 0:
            gap_b = abs(abs(buy) - noted) / abs(buy)
            line = f"repurchases per the equity note {noted:,.0f} vs cash paid {abs(buy):,.0f}: {'consistent' if gap_b <= Decimal('0.15') else 'differ by more than 15%, usually timing of settlement or excise tax'}"
            recon = f"{recon}; {line}" if recon else line
        years.append(
            AllocationYear(
                column=c.id,
                label=c.label,
                fcf=fcf,
                capex=None if capex is None else abs(capex),
                dividends=None if div is None else abs(div),
                buybacks=None if buy is None else abs(buy),
                debt_issued=None if issued is None else abs(issued),
                debt_repaid=None if repaid is None else abs(repaid),
                net_debt_change=net_debt,
                acquisitions=None if acq is None else abs(acq),
                divestitures=None if divest is None else abs(divest),
                returned_share_of_fcf=returned,
                share_count=shares,
                share_change=_growth(shares, prev.share_count)
                if prev and prev.share_count and shares
                else None,
                fcf_per_share=fps,
                fcf_per_share_growth=_growth(fps, prev.fcf_per_share) if prev else None,
                fcf_growth=_growth(fcf, prev.fcf) if prev else None,
                reconciliation=recon,
            )
        )
        prev = years[-1]
    stated: list[StatedAction] = []
    for r in await pool.fetch(
        """select event_type, event_state, announced_at, effective_at, source_url, counterparty_key, terms
           from public.company_event where ticker=$1 and event_type in ('deal','financing') and status='confirmed'
           order by coalesce(announced_at, known_at::date) desc limit 30""",
        ticker,
    ):
        stated.append(
            StatedAction(
                kind=r["event_type"],
                date=r["announced_at"] or r["effective_at"],
                detail=f"{r['event_type']} with {r['counterparty_key'] or 'a counterparty'}",
                state=r["event_state"],
                source_url=r["source_url"],
            )
        )
    for r in await pool.fetch(
        "select source_url, published_at, metadata from public.issuer_ir_release where ticker=$1 and release_type='capital_return' order by published_at desc nulls last limit 10",
        ticker,
    ):
        meta = r["metadata"]
        meta = json.loads(meta) if isinstance(meta, str) else (meta or {})
        said = [
            f"{x.get('type', 'return')} {x.get('amount')} {x.get('currency') or ''}".strip()
            for x in (meta.get("capital_return") or [])
            if isinstance(x, dict) and x.get("amount")
        ][:3]
        stated.append(
            StatedAction(
                kind="capital_return",
                date=r["published_at"].date() if r["published_at"] else None,
                detail="Stated capital-return policy or programme (company release)"
                + (f": {'; '.join(said)}" if said else ""),
                state=None,
                source_url=r["source_url"],
            )
        )
    warnings = list(dividend_notes)
    if not any(y.fcf is not None for y in years):
        warnings.append(
            "No filed operating cash flow and capital expenditure, so free cash flow is not available."
        )
    return CapitalAllocation(
        ticker=ticker,
        years=years,
        stated=stated,
        observed_note="Observed results are computed from the filed cash-flow statement and share counts; the stated policy and announced actions are the company's own words and are listed separately. Acquisitions and divestitures are the cash the filed statement reports for them; a year with no figure had no such line filed (or was collected before these lines were kept), which is unknown rather than none. Deal events are listed separately where one is confirmed.",
        method_version=version("capital_allocation"),
        warnings=warnings,
    )


# --------------------------------------------------------------------------- governance


class Role(BaseModel):
    person: str
    edge_type: str
    valid_from: dt.date | None
    valid_to: dt.date | None
    status: str
    source_url: str
    passage: str | None


class Compensation(BaseModel):
    person: str = Field(description="Name and position as the proxy statement gives them.")
    fiscal_year: int
    total: Decimal
    currency: str = "USD"
    components: dict[str, Decimal] = Field(
        description="Only the components the provider sent; an absent one is not zero."
    )
    filed: dt.date | None
    source_url: str
    provider: str


class RelatedPartyFigure(BaseModel):
    label: str
    value: Decimal
    currency: str | None
    period_end: dt.date
    concept: str = Field(description="The filed XBRL element, so the figure can be checked.")


RELATED_PARTY_LABEL = {
    "us-gaap:RelatedPartyTransactionAmountsOfTransaction": "Related-party transactions, total",
    "us-gaap:RevenueFromRelatedParties": "Revenue from related parties",
    "us-gaap:CostsAndExpensesRelatedParty": "Costs and expenses with related parties",
    "us-gaap:RelatedPartyTransactionExpensesFromTransactionsWithRelatedParty": "Costs and expenses with related parties",
    "us-gaap:DueFromRelatedParties": "Due from related parties",
    "us-gaap:AccountsReceivableRelatedParties": "Due from related parties",
    "us-gaap:DueToRelatedParties": "Due to related parties",
    "us-gaap:AccountsPayableRelatedParties": "Due to related parties",
}


class Governance(BaseModel):
    ticker: str
    roles: list[Role]
    related_party: list[dict[str, Any]]
    related_party_figures: list[RelatedPartyFigure] = Field(default_factory=list)
    compensation: list[Compensation] = Field(default_factory=list)
    compensation_note: str
    caveats: list[str]
    method_version: str


async def governance(pool: asyncpg.Pool, ticker: str) -> Governance:
    rows = await pool.fetch(
        """select object_name as person, edge_type, valid_from, valid_to, status, source_url, passage
           from public.company_graph_edge
           where subject_key=$1 and edge_type='executive' and status in ('confirmed','disputed')
           order by coalesce(valid_to, date '9999-12-31') desc, valid_from desc nulls last limit 60""",
        ticker,
    )
    rp = await pool.fetch(
        """select relationship, counterparty_name, reported_share, fiscal_period, source_url, passage
           from public.relationship_disclosure where ticker=$1 and status='confirmed' and relationship in ('customer','supplier')
           order by known_at desc limit 20""",
        ticker,
    )
    rp_facts = await pool.fetch(
        """select distinct on (concept) concept, value, currency, period_end
           from public.financial_statement_fact
           where ticker=$1 and statement='note' and value is not null and coalesce(dimensions->'members', '[]'::jsonb) = '[]'::jsonb
             and concept = any($2::text[])
           order by concept, period_end desc, known_at desc""",
        ticker,
        list(RELATED_PARTY_LABEL),
    )
    comp = await pool.fetch(
        """select distinct on (text_value, metric) text_value as person, metric, value as total, currency,
                  published_at, source_url, provider, raw
           from public.company_research_observation
           where ticker=$1 and dataset='executive_compensation' and value is not null and text_value is not null
           order by text_value, metric, known_at desc""",
        ticker,
    )
    compensation: list[Compensation] = []
    for r in comp:
        raw = json.loads(r["raw"]) if isinstance(r["raw"], str) else (r["raw"] or {})
        year = raw.get("fiscal_year")
        if not isinstance(year, int):
            continue
        compensation.append(
            Compensation(
                person=r["person"],
                fiscal_year=year,
                total=r["total"],
                currency=r["currency"] or "USD",
                components={k: Decimal(v) for k, v in (raw.get("components") or {}).items()},
                filed=r["published_at"].date() if r["published_at"] else None,
                source_url=r["source_url"],
                provider=r["provider"],
            )
        )
    compensation.sort(key=lambda c: (-c.fiscal_year, -c.total))
    return Governance(
        ticker=ticker,
        roles=[Role(**dict(r)) for r in rows],
        related_party=[dict(r) for r in rp],
        related_party_figures=[
            RelatedPartyFigure(
                label=RELATED_PARTY_LABEL[r["concept"]],
                value=r["value"],
                currency=r["currency"],
                period_end=r["period_end"],
                concept=r["concept"],
            )
            for r in rp_facts
        ],
        compensation=compensation,
        compensation_note=(
            "Summary compensation as reported in the proxy statement, from Financial Modeling Prep; "
            "the filing link on each row is the primary source. Grant-date values, not what was realised."
            if compensation
            else "No executive compensation is stored for this company. It is collected for holdings "
            "mapped to a US listing, and only when the FMP key is set; absence is not a finding."
        ),
        caveats=[
            "Roles are dated from filings and confirmed graph relationships; an undated role is shown as undated.",
            "An institutional manager's 13F position is a reported holding, not beneficial ownership or an endorsement.",
            "Related-party figures are the company-wide totals tagged in the filing's notes; counterparty-by-counterparty detail is not collected, and an absent total means none was tagged, not that none exists.",
        ],
        method_version=version("peer_compare"),
    )


# ---------------------------------------------------------------------------- case items

CASE_KINDS = ("guidance", "moat", "bull", "bear", "concentration", "capital_allocation", "risk")
MOAT_TYPES = ("switching_costs", "scale", "ip", "network_effects", "regulation", "none")


class Citation(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    passage: str | None = Field(default=None, max_length=1000)

    @field_validator("url")
    @classmethod
    def _web_address(cls, value: str) -> str:
        """Only an http(s) address with a host: a javascript: or data: URL would run when clicked."""
        parts = urlsplit(value.strip())
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
            raise ValueError("a citation URL must be an http or https address")
        return value.strip()


class CaseItemIn(BaseModel):
    kind: str
    moat_type: str | None = None
    statement: str = Field(min_length=3, max_length=2000)
    evidence: list[Citation] = Field(default_factory=list, max_length=10)
    confidence: str = "low"
    status: str = "draft"


class CaseItem(CaseItemIn):
    id: int
    created_at: dt.datetime
    updated_at: dt.datetime


class CaseView(BaseModel):
    ticker: str
    items: list[CaseItem]
    concentration: list[dict[str, Any]]
    note: str


def case_input_problem(item: CaseItemIn) -> str | None:
    if item.kind not in CASE_KINDS:
        return f"kind must be one of {', '.join(CASE_KINDS)}"
    if item.kind == "moat" and item.moat_type not in MOAT_TYPES:
        return f"a moat item needs a moat_type: {', '.join(MOAT_TYPES)}"
    if item.kind != "moat" and item.moat_type is not None:
        return "moat_type applies to moat items only"
    if item.confidence not in ("low", "medium", "high"):
        return "confidence must be low, medium or high"
    if item.status == "confirmed" and not item.evidence:
        return "a confirmed assessment needs at least one citation"
    if item.status not in ("draft", "confirmed"):
        return "status must be draft or confirmed"
    return None


def _item(r: asyncpg.Record) -> CaseItem:
    ev = r["evidence"]
    ev = json.loads(ev) if isinstance(ev, str) else ev
    return CaseItem(
        id=r["id"],
        kind=r["kind"],
        moat_type=r["moat_type"],
        statement=r["statement"],
        evidence=[Citation(**e) for e in ev],
        confidence=r["confidence"],
        status=r["status"],
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


async def case_view(pool: asyncpg.Pool, owner_id: Any, ticker: str) -> CaseView:
    rows = await pool.fetch(
        """select * from public.company_case_item where owner_id=$1 and ticker=$2 and not archived
           order by kind, created_at desc""",
        owner_id,
        ticker,
    )
    conc = await pool.fetch(
        """select relationship, counterparty_name, anonymous_counterparty, reported_share, share_basis, fiscal_period, source_url, passage
           from public.relationship_disclosure where ticker=$1 and status='confirmed' and reported_share is not null
           order by reported_share desc limit 10""",
        ticker,
    )
    return CaseView(
        ticker=ticker,
        items=[_item(r) for r in rows],
        concentration=[dict(r) for r in conc],
        note="These are cited assessments by you, kept apart from reported numbers. A confirmed item needs at least one source; a moat is a type and evidence, not a rating.",
    )


async def save_case_item(
    pool: asyncpg.Pool, owner_id: Any, ticker: str, item: CaseItemIn, item_id: int | None = None
) -> CaseItem | None:
    evidence = json.dumps([e.model_dump() for e in item.evidence])
    if item_id is None:
        r = await pool.fetchrow(
            """insert into public.company_case_item (owner_id, ticker, kind, moat_type, statement, evidence, confidence, status)
               values ($1,$2,$3,$4,$5,$6::jsonb,$7,$8) returning *""",
            owner_id,
            ticker,
            item.kind,
            item.moat_type,
            item.statement,
            evidence,
            item.confidence,
            item.status,
        )
    else:
        r = await pool.fetchrow(
            """update public.company_case_item set kind=$4, moat_type=$5, statement=$6, evidence=$7::jsonb,
                      confidence=$8, status=$9, updated_at=now()
               where id=$1 and owner_id=$2 and ticker=$3 and not archived returning *""",
            item_id,
            owner_id,
            ticker,
            item.kind,
            item.moat_type,
            item.statement,
            evidence,
            item.confidence,
            item.status,
        )
    return _item(r) if r else None


async def archive_case_item(pool: asyncpg.Pool, owner_id: Any, ticker: str, item_id: int) -> bool:
    n = await pool.fetchval(
        "update public.company_case_item set archived=true, updated_at=now() where id=$1 and owner_id=$2 and ticker=$3 and not archived returning 1",
        item_id,
        owner_id,
        ticker,
    )
    return n is not None
