"""Read-only research context (D7.4): country, industry (traded product) and company claims
that deep research and chat can cite.

Every claim is a stored observation or a deterministic computation over stored observations
(a latest-versus-previous change, a latest value per revision chain). Each carries
`known_as_of` (the date the record became known) and the point-in-time cutoff hides records
learned later, so a historical report cannot be written with later knowledge. No model output
is stored or computed here; explanations and uncertainty wording belong to the writer.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.data_context.common import Provenance, cutoff, provenance_of, to_date
from steward.free_source_reads import (
    company_research_rows,
    financial_history_rows,
    forward_rows,
    patent_trend_rows,
)

Domain = Literal[
    "macro",
    "trade",
    "financial",
    "forward",
    "company_research",
    "business_network",
    "patents",
]


class Claim(BaseModel):
    claim_id: str = Field(description="Stable id such as macro:123; cite it, never the number.")
    domain: Domain
    subject: str
    metric: str
    label: str
    value: Decimal | None
    text_value: str | None = None
    unit: str | None
    currency: str | None = None
    period: dt.date | None
    known_as_of: dt.date = Field(description="Date the record became known to the app.")
    source: str
    provenance: Provenance
    source_url: str | None
    note: str | None = None


class ContextGap(BaseModel):
    domain: str
    reason: str


class ResearchContext(BaseModel):
    scope: Literal["country", "industry", "company"]
    subject: str
    as_of: dt.datetime
    claims: list[Claim]
    gaps: list[ContextGap]
    method: str = (
        "Stored observations only, latest record known by the cutoff; changes are latest "
        "minus the previous period of the same series. No figure is estimated here."
    )


def _claim(**kw: Any) -> Claim:
    kw["provenance"] = provenance_of(kw["source"])
    return Claim(**kw)


async def country_context(
    pool: asyncpg.Pool, geography: str, as_of: dt.datetime | None = None, limit: int = 40
) -> ResearchContext:
    when = cutoff(as_of)
    rows = await pool.fetch(
        """with eligible as (
             select distinct on (series_id,source,unit,period)
                    id,series_id,source,geography,unit,period,value,known_at,source_url
             from public.macro_observation
             where geography=$1 and known_at <= $2
             order by series_id,source,unit,period,known_at desc
           ), ranked as (
             select *,row_number() over (partition by series_id,source,unit
                                         order by period desc) rn from eligible
           )
           select * from ranked where rn <= 2 order by series_id,source,unit,rn""",
        geography,
        when,
    )
    claims: list[Claim] = []
    by_series: dict[tuple[str, str, str], list[asyncpg.Record]] = {}
    for r in rows:
        by_series.setdefault((r["series_id"], r["source"], r["unit"]), []).append(r)
    for (series_id, source, unit), pair in sorted(by_series.items())[:limit]:
        latest = pair[0]
        change = (
            latest["value"] - pair[1]["value"]
            if len(pair) == 2 and latest["value"] is not None and pair[1]["value"] is not None
            else None
        )
        claims.append(
            _claim(
                claim_id=f"macro:{latest['id']}",
                domain="macro",
                subject=geography,
                metric=series_id,
                label=f"{series_id} ({geography})",
                value=latest["value"],
                unit=unit,
                period=latest["period"],
                known_as_of=to_date(latest["known_at"]),
                source=source,
                source_url=latest["source_url"],
                note=(
                    f"Change from {pair[1]['period']}: {change}"
                    if change is not None
                    else "No earlier period known, so no change is stated."
                ),
            )
        )
    trade = await pool.fetch(
        """with latest as (
             select distinct on (reporter,partner,flow,hs_revision,hs_code,period)
                    id,reporter,partner,flow,hs_revision,hs_code,period,trade_value,known_at,
                    source_url
             from public.trade_observation where reporter=$1 and known_at <= $2
             order by reporter,partner,flow,hs_revision,hs_code,period,known_at desc
           )
           select * from latest where period=(select max(period) from latest)
           order by trade_value desc nulls last,hs_code,partner,flow limit 10""",
        geography,
        when,
    )
    for r in trade:
        claims.append(
            _claim(
                claim_id=f"trade:{r['id']}",
                domain="trade",
                subject=geography,
                metric=f"trade_{r['flow']}",
                label=f"{r['flow']} of HS {r['hs_code']} with {r['partner']} ({geography})",
                value=r["trade_value"],
                unit="USD",
                period=r["period"],
                known_as_of=to_date(r["known_at"]),
                source="un_comtrade",
                source_url=r["source_url"],
                note="National product trade; not an issuer contract. Partners are not summed.",
            )
        )
    gaps: list[ContextGap] = []
    if not by_series:
        gaps.append(
            ContextGap(
                domain="macro",
                reason="No macro series for this geography was known by the cutoff.",
            )
        )
    if not trade:
        gaps.append(
            ContextGap(
                domain="trade",
                reason="No trade observation with this reporter was known by the cutoff.",
            )
        )
    return ResearchContext(scope="country", subject=geography, as_of=when, claims=claims, gaps=gaps)


async def industry_context(
    pool: asyncpg.Pool, hs_code: str, as_of: dt.datetime | None = None
) -> ResearchContext:
    when = cutoff(as_of)
    trade = await pool.fetch(
        """with latest as (
             select distinct on (reporter,partner,flow,hs_revision,hs_code,period)
                    id,reporter,partner,flow,hs_revision,hs_code,period,trade_value,known_at,
                    source_url
             from public.trade_observation where hs_code=$1 and known_at <= $2
             order by reporter,partner,flow,hs_revision,hs_code,period,known_at desc
           )
           select * from latest where period=(select max(period) from latest)
           order by trade_value desc nulls last,reporter,partner,flow limit 20""",
        hs_code,
        when,
    )
    claims = [
        _claim(
            claim_id=f"trade:{r['id']}",
            domain="trade",
            subject=f"HS {hs_code}",
            metric=f"trade_{r['flow']}",
            label=f"{r['reporter']} {r['flow']} with {r['partner']} (HS {r['hs_revision']})",
            value=r["trade_value"],
            unit="USD",
            period=r["period"],
            known_as_of=to_date(r["known_at"]),
            source="un_comtrade",
            source_url=r["source_url"],
            note="Rows are separate reporter/partner pairs and are never summed here.",
        )
        for r in trade
    ]
    exposed = await pool.fetch(
        """select count(distinct issuer_id)::int issuers,
                  count(*) filter (where basis='reported')::int reported
           from public.exposure_mapping
           where hs_code=$1 and weight>0 and created_at <= $2
             and valid_from <= $3 and (valid_to is null or valid_to >= $3)""",
        hs_code,
        when,
        when.date(),
    )
    gaps = []
    if not claims:
        gaps.append(
            ContextGap(
                domain="trade",
                reason="No trade observation for this product was known by the cutoff.",
            )
        )
    context = ResearchContext(
        scope="industry", subject=f"HS {hs_code}", as_of=when, claims=claims, gaps=gaps
    )
    row = exposed[0]
    context.method += (
        f" {row['issuers']} issuers have an active exposure mapping to this product "
        f"({row['reported']} reported)."
    )
    return context


async def company_context(
    pool: asyncpg.Pool, ticker: str, as_of: dt.datetime | None = None, limit: int = 60
) -> ResearchContext:
    when = cutoff(as_of)
    claims: list[Claim] = []
    facts = await financial_history_rows(pool, ticker, when)
    seen: set[tuple[str, dt.date]] = set()
    for f in facts:  # newest period first, latest revision first within a period
        if f["statement"] == "note" or f["value"] is None or f["dimensions"]:
            continue
        key = (f["concept"], f["period_end"])
        if key in seen:
            continue
        seen.add(key)
        if len([c for c in claims if c.domain == "financial"]) >= limit // 2:
            continue
        claims.append(
            _claim(
                claim_id=f"financial:{f['id']}",
                domain="financial",
                subject=ticker,
                metric=f["concept"],
                label=f["label"] or f["concept"],
                value=f["value"],
                unit=f["unit"],
                currency=f["currency"],
                period=f["period_end"],
                known_as_of=to_date(f["known_at"]),
                source=f["source"],
                source_url=f["source_url"],
                note=f"{f['basis']}, revision {f['revision']}",
            )
        )
    forward = await forward_rows(pool, ticker, when)
    latest_forward: dict[tuple[str, str, str], dict[str, Any]] = {}
    for fr in forward:  # known_at descending within metric, period
        latest_forward.setdefault((fr["kind"], fr["metric"], fr["fiscal_period"]), fr)
    for (kind, metric, period), fr in sorted(latest_forward.items())[:10]:
        claims.append(
            _claim(
                claim_id=f"forward:{fr['id']}",
                domain="forward",
                subject=ticker,
                metric=metric,
                label=f"{kind.replace('_', ' ')} {metric} {period}",
                value=fr["value"],
                unit=None,
                currency=fr["currency"],
                period=fr["period_end"],
                known_as_of=to_date(fr["known_at"]),
                source=fr["provider"] or kind,
                source_url=fr["source_url"],
                note=f"{kind}; low {fr['low']}, high {fr['high']}, analysts {fr['analyst_count']}",
            )
        )
    research = await company_research_rows(pool, ticker, when)
    seen_metric: set[tuple[str, str]] = set()
    for r in research:
        mk = (r["dataset"], r["metric"])
        if mk in seen_metric or r["match_confidence"] is None or r["match_confidence"] < 0.8:
            continue
        seen_metric.add(mk)
        claims.append(
            _claim(
                claim_id=f"company_research:{r['id']}",
                domain="company_research",
                subject=ticker,
                metric=r["metric"],
                label=f"{r['dataset']}: {r['metric']}",
                value=r["value"],
                text_value=r["text_value"],
                unit=r["unit"],
                currency=r["currency"],
                period=r["period_end"],
                known_as_of=to_date(r["known_at"]),
                source=r["provider"],
                source_url=r["source_url"],
                note=f"entity match confidence {r['match_confidence']}",
            )
        )
        if len(seen_metric) >= 10:
            break
    edges = await pool.fetch(
        """select id,object_key,object_name,edge_type,source,source_url,known_at
           from public.company_graph_edge
           where subject_key=$1 and status='confirmed' and known_at <= $2
             and (valid_from is null or valid_from <= $3)
             and (valid_to is null or valid_to >= $3)
           order by known_at desc,id limit 10""",
        ticker,
        when,
        when.date(),
    )
    for e in edges:
        claims.append(
            _claim(
                claim_id=f"edge:{e['id']}",
                domain="business_network",
                subject=ticker,
                metric=e["edge_type"],
                label=f"confirmed {e['edge_type']}: {e['object_name']}",
                value=None,
                text_value=e["object_name"],
                unit=None,
                period=None,
                known_as_of=to_date(e["known_at"]),
                source=e["source"],
                source_url=e["source_url"],
            )
        )
    patent_rows = await patent_trend_rows(pool, ticker, when)
    patent_known = await pool.fetchval(
        """select max(p.known_at) from public.patent_family p
           join public.patent_family_owner o on o.family_id=p.id
           where o.ticker=$1 and p.known_at <= $2""",
        ticker,
        when,
    )
    for pr in sorted(patent_rows, key=lambda r: (-int(r["year"]), str(r["cpc"])))[:5]:
        claims.append(
            _claim(
                claim_id=f"patents:{ticker}:{pr['cpc']}:{pr['year']}",
                domain="patents",
                subject=ticker,
                metric="patent_families",
                label=f"patent families in CPC {pr['cpc']}, priority year {pr['year']}",
                value=Decimal(int(pr["families"])),
                unit="families",
                period=None,
                known_as_of=to_date(patent_known),
                source="epo_ops",
                source_url=None,
                note="Counts are a signal, not a valuation; direct owners only.",
            )
        )
    present = {c.domain for c in claims}
    gaps = [
        ContextGap(domain=d, reason=f"No {d.replace('_', ' ')} record was known by the cutoff.")
        for d in ("financial", "forward", "company_research", "business_network", "patents")
        if d not in present
    ]
    return ResearchContext(scope="company", subject=ticker, as_of=when, claims=claims, gaps=gaps)


def claim_sentence(claim: Claim) -> str:
    """One deterministic sentence for a report: label, value, period, source and known date."""
    if claim.value is not None:
        shown = (
            f"{claim.value:,.2f}".rstrip("0").rstrip(".")
            if claim.value % 1
            else f"{claim.value:,.0f}"
        )
        value = f"{shown} {claim.unit or claim.currency or ''}".strip()
    else:
        value = claim.text_value or "no value"
    when = f" for {claim.period}" if claim.period else ""
    return f"{claim.label}{when}: {value} ({claim.source}; known as of {claim.known_as_of})."
