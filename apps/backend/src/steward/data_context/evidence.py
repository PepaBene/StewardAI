"""Stable evidence read models (D7.7).

Filed-note evidence, control / institutional / fund positions (three separate channels, never
combined), classified insider activity, validated dependencies (confirmed edges only), deal
states and public consensus vintages. Every item carries its source URL, the date it became
known (`known_at`), the source passage where one is stored (else `passage` is null with a
`passage_reason`), and each section states a coverage reason. A cutoff (`as_of`) hides every
record learned after it, including later revisions and amendments.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.data_context.common import Gap, as_json, cutoff, to_date
from steward.free_source_reads import business_network_payload, has_table, status_as_of_sql
from steward.geo_codes import has_column
from steward.ownership.control_stakes_read import (
    ControlStakeOut,
    holding_control_stakes,
)
from steward.ownership.insider_read import InsiderActivityOut, holding_insider_activity
from steward.ownership.nport_read import HoldingFundPositions, holding_fund_positions
from steward.ownership.read import HoldingOwnership, holding_ownership

PASSAGE_CHARS = 2000


class SectionCoverage(BaseModel):
    state: Literal["covered", "partial", "not_covered"]
    reason: str


class FiledNote(BaseModel):
    fact_id: int
    concept: str
    label: str | None
    period_end: dt.date
    fiscal_period: str | None
    accession: str | None
    form: str | None
    source: str
    source_url: str
    passage: str | None
    passage_basis: Literal["source_evidence_quote", "filed_text_block"] | None
    passage_reason: str | None
    known_at: dt.datetime
    revision: int
    evidence_id: int | None


class FiledNotes(BaseModel):
    coverage: SectionCoverage
    notes: list[FiledNote]


class StakeItem(BaseModel):
    known_at: dt.date = Field(description="Filing date: when the stake became public.")
    source_passage: str | None = Field(description="Item 4 purpose text; 13G has none.")
    passage_reason: str | None
    coverage_reason: str
    superseded: bool
    stake: ControlStakeOut


class ControlChannel(BaseModel):
    channel: Literal["beneficial_ownership_13dg"] = "beneficial_ownership_13dg"
    coverage: SectionCoverage
    stakes: list[StakeItem]


class InstitutionalChannel(BaseModel):
    channel: Literal["institutional_13f"] = "institutional_13f"
    coverage: SectionCoverage
    ownership: HoldingOwnership | None
    pit_note: str | None


class FundChannel(BaseModel):
    channel: Literal["fund_positions_nport"] = "fund_positions_nport"
    coverage: SectionCoverage
    positions: HoldingFundPositions


class Positions(BaseModel):
    not_additive: str = (
        "Control stakes (13D/G), institutional 13F positions and N-PORT fund positions measure "
        "different things at different dates and overlap; no total or combined percentage is "
        "computed anywhere."
    )
    control: ControlChannel
    institutional: InstitutionalChannel
    funds: FundChannel


class InsiderSource(BaseModel):
    accession: str
    filed: dt.date
    source_url: str
    economic_class: str | None
    shares: Decimal | None
    passage: str | None
    passage_reason: str | None


class Insider(BaseModel):
    coverage: SectionCoverage
    known_by: dt.date
    activity: InsiderActivityOut
    net_discretionary_derived_from: list[InsiderSource]


class Dependency(BaseModel):
    kind: Literal["graph_edge", "customer_supplier_disclosure"]
    edge_id: int
    counterparty_key: str | None
    counterparty_name: str | None
    anonymous_counterparty: bool = False
    relationship: str
    direction: str
    reported_share: Decimal | None
    share_basis: str | None
    fiscal_period: str | None
    valid_from: dt.date | None
    valid_to: dt.date | None
    evidence_tier: str | None
    source: str
    source_url: str
    passage: str | None
    passage_reason: str | None
    evidence_id: int | None
    known_at: dt.datetime | None


class Dependencies(BaseModel):
    coverage: SectionCoverage
    dependencies: list[Dependency]
    excluded_unconfirmed: int
    note: str = (
        "Only confirmed relationships are listed. Suggested and disputed candidates, and "
        "legacy edges without a known-at date, are excluded; an anonymous counterparty is "
        "never given a name and a missing share stays null."
    )


class DealEvent(BaseModel):
    event_id: int
    event_type: str
    event_state: str
    status: str
    announced_at: dt.date | None
    effective_at: dt.date | None
    known_at: dt.datetime
    counterparty_key: str | None
    source: str
    source_url: str
    passage: str | None
    passage_reason: str | None
    terms: dict[str, Any]


class DealState(BaseModel):
    deal_key: str
    counterparty_key: str | None
    current_state: str | None = Field(
        description="State of the latest confirmed event by the cutoff; null when none is."
    )
    state_basis: str
    events: list[DealEvent]


class Deals(BaseModel):
    coverage: SectionCoverage
    deals: list[DealState]


class ConsensusVintage(BaseModel):
    observation_id: int
    provider: str | None
    vintage: str | None
    value: Decimal | None
    low: Decimal | None
    high: Decimal | None
    analyst_count: int | None
    currency: str | None
    published_at: dt.datetime | None
    known_at: dt.datetime
    source_url: str
    passage: str | None
    passage_reason: str | None


class ConsensusSeries(BaseModel):
    provider: str | None = Field(
        default=None, description="One series per provider: providers are never mixed."
    )
    currency: str | None = None
    metric: str
    fiscal_period: str
    vintages: list[ConsensusVintage] = Field(description="Oldest first; none overwritten.")
    revision_since_previous: Decimal | None = Field(
        description="Latest value minus the previous vintage's; null unless both are known."
    )
    revision_inputs: list[int]


class IrConsensusRelease(BaseModel):
    release_id: int
    release_type: str
    published_at: dt.datetime | None
    known_at: dt.datetime
    analyst_count: int | None
    basis: str | None
    status: str
    source_url: str


class Consensus(BaseModel):
    coverage: SectionCoverage
    series: list[ConsensusSeries]
    issuer_published: list[IrConsensusRelease]
    note: str = (
        "Public analyst consensus as published, one row per vintage. Provider earnings-"
        "calendar estimates are a different kind and are not shown here."
    )


class EvidenceSummary(BaseModel):
    ticker: str
    known_as_of: dt.datetime
    filed_notes: FiledNotes
    positions: Positions
    insider_activity: Insider
    dependencies: Dependencies
    deals: Deals
    consensus: Consensus
    gaps: list[Gap]


def _cov(count: int, none_reason: str, ok_reason: str) -> SectionCoverage:
    return SectionCoverage(
        state="covered" if count else "not_covered", reason=ok_reason if count else none_reason
    )


async def filed_notes(
    pool: asyncpg.Pool, ticker: str, as_of: dt.datetime | None = None, limit: int = 100
) -> FiledNotes:
    when = cutoff(as_of)
    rows = await pool.fetch(
        """select * from (
             select distinct on (f.concept,f.period_end,f.dimensions::text)
                    f.id,f.concept,f.label,f.period_end,f.fiscal_period,f.accession,f.form,
                    f.source,f.source_url,f.text_block,f.known_at,f.revision,f.evidence_id,
                    e.quote
             from public.financial_statement_fact f
             left join public.source_evidence e on e.id=f.evidence_id
             where f.ticker=$1 and f.statement='note' and f.known_at <= $2
             order by f.concept,f.period_end,f.dimensions::text,f.known_at desc,f.id desc
           ) latest order by period_end desc,concept limit $3""",
        ticker,
        when,
        limit,
    )
    notes: list[FiledNote] = []
    for r in rows:
        passage: str | None = None
        basis: Literal["source_evidence_quote", "filed_text_block"] | None = None
        reason: str | None = None
        if r["quote"]:
            passage, basis = r["quote"][:PASSAGE_CHARS], "source_evidence_quote"
        elif r["text_block"]:
            passage, basis = r["text_block"][:PASSAGE_CHARS], "filed_text_block"
        else:
            reason = "The note fact has neither a stored evidence quote nor a text block."
        notes.append(
            FiledNote(
                fact_id=r["id"],
                concept=r["concept"],
                label=r["label"],
                period_end=r["period_end"],
                fiscal_period=r["fiscal_period"],
                accession=r["accession"],
                form=r["form"],
                source=r["source"],
                source_url=r["source_url"],
                passage=passage,
                passage_basis=basis,
                passage_reason=reason,
                known_at=r["known_at"],
                revision=r["revision"],
                evidence_id=r["evidence_id"],
            )
        )
    return FiledNotes(
        coverage=_cov(
            len(notes),
            "No filed note (text block or evidence quote) was known by the cutoff. "
            "Notes are loaded from XBRL text blocks of US filings only.",
            f"{len(notes)} latest-revision filed notes known by the cutoff.",
        ),
        notes=notes,
    )


def _control_items(all_stakes: list[ControlStakeOut], known_by: dt.date) -> list[StakeItem]:
    known = [s for s in all_stakes if s.filed <= known_by]
    # A chain is superseded only by a filing that was itself public by the cutoff.
    replaced = {s.prior_accession for s in known if s.prior_accession}
    items: list[StakeItem] = []
    for s in sorted(known, key=lambda x: (x.filed, x.accession), reverse=True):
        reasons = [s.reconciliation.note or s.reconciliation.class_status]
        reasons.append(f"percent check {s.reconciliation.percent_check}")
        items.append(
            StakeItem(
                known_at=s.filed,
                source_passage=s.intent_text,
                passage_reason=None
                if s.intent_text
                else (
                    "Schedule 13G is a passive filing and states no purpose."
                    if s.schedule == "13G"
                    else "No Item 4 text was parsed from this filing."
                ),
                coverage_reason="; ".join(r for r in reasons if r),
                superseded=s.accession in replaced,
                stake=s,
            )
        )
    return items


async def positions(pool: asyncpg.Pool, ticker: str, as_of: dt.datetime | None = None) -> Positions:
    when = cutoff(as_of)
    day = to_date(when) or dt.date.today()
    pit = as_of is not None
    control_all = await holding_control_stakes(pool, ticker)
    items = _control_items([*control_all.stakes, *control_all.superseded], day)
    ownership = await holding_ownership(pool, ticker)
    pit_note: str | None = None
    if pit:
        ownership = ownership.model_copy(
            update={
                "periods": [
                    p for p in ownership.periods if p.latest_filed and p.latest_filed <= day
                ],
                "top_holders": [],
                "top_holders_previous_period": None,
                "pct_of_shares_outstanding": None,
            }
        )
        pit_note = (
            "Point-in-time request: only periods whose latest filing was public by the cutoff "
            "are listed; top holders and the aggregate percentage are not reconstructed."
        )
    funds = await holding_fund_positions(pool, ticker, as_of=day)
    if not ownership.applicable:
        inst_cov = SectionCoverage(
            state="not_covered",
            reason=ownership.not_applicable_reason or "Form 13F cannot cover this holding.",
        )
    else:
        inst_cov = _cov(
            len(ownership.periods),
            "No Form 13F period is loaded for this holding by the cutoff.",
            f"{len(ownership.periods)} Form 13F periods loaded; managers under USD 100 M are "
            "not counted.",
        )
    return Positions(
        control=ControlChannel(
            coverage=_cov(
                len(items),
                "No Schedule 13D/G structured filing is stored. Only XML filings from "
                "December 2024 are parsed, so absence is not evidence of no stake.",
                f"{len(items)} 13D/G filings known by the cutoff.",
            ),
            stakes=items,
        ),
        institutional=InstitutionalChannel(
            coverage=inst_cov, ownership=ownership, pit_note=pit_note
        ),
        funds=FundChannel(
            coverage=SectionCoverage(
                state="covered"
                if funds.coverage.state == "available"
                else "not_covered"
                if funds.coverage.state in ("no_funds_loaded", "no_positions")
                else "partial",
                reason=f"N-PORT coverage state {funds.coverage.state}: "
                f"{funds.coverage.funds_loaded} tracked funds loaded, "
                f"{funds.coverage.funds_holding} hold the security. {funds.coverage.scope}",
            ),
            positions=funds,
        ),
    )


async def insider(pool: asyncpg.Pool, ticker: str, as_of: dt.datetime | None = None) -> Insider:
    day = to_date(cutoff(as_of)) or dt.date.today()
    activity = await holding_insider_activity(pool, ticker, until=day, known_by=day)
    derived = [
        InsiderSource(
            accession=t.accession,
            filed=t.filed,
            source_url=t.source_url,
            economic_class=t.economic_class,
            shares=t.shares,
            passage=t.footnote_text,
            passage_reason=None
            if t.footnote_text
            else "The filing states no footnote for this row; the class rests on the code.",
        )
        for t in activity.transactions
        if t.counts_toward_net_discretionary
    ]
    return Insider(
        coverage=_cov(
            len(activity.transactions),
            "No Form 3/4/5 transaction was known in the window. Coverage is SEC Section 16 "
            "filers; UK PDMR notices are not retrievable yet.",
            f"{len(activity.transactions)} transactions classified; "
            f"{len(derived)} count toward net discretionary activity.",
        ),
        known_by=day,
        activity=activity,
        net_discretionary_derived_from=derived,
    )


async def dependencies(
    pool: asyncpg.Pool, ticker: str, as_of: dt.datetime | None = None
) -> Dependencies:
    payload = await business_network_payload(
        pool, ticker, cutoff(as_of), historical=as_of is not None
    )
    out: list[Dependency] = []
    excluded = 0
    for e in payload["edges"]:
        if isinstance(e["id"], str):  # legacy edge: no known-at date, cannot be point-in-time
            excluded += 1
            continue
        if e["status"] != "confirmed":
            excluded += 1
            continue
        out.append(
            Dependency(
                kind="graph_edge",
                edge_id=e["id"],
                counterparty_key=e["object_key"],
                counterparty_name=e["object_name"],
                relationship=e["edge_type"],
                direction=e["direction"],
                reported_share=None,
                share_basis=None,
                fiscal_period=None,
                valid_from=e["valid_from"],
                valid_to=e["valid_to"],
                evidence_tier=e["evidence_tier"],
                source=e["source"],
                source_url=e["source_url"],
                passage=e["passage"],
                passage_reason=None
                if e["passage"]
                else "Confirmed through linked evidence; no passage is stored on the edge.",
                evidence_id=e["evidence_id"],
                known_at=e["known_at"],
            )
        )
    for d in payload["disclosures"]:
        if d["status"] != "confirmed":
            excluded += 1
            continue
        out.append(
            Dependency(
                kind="customer_supplier_disclosure",
                edge_id=d["id"],
                counterparty_key=d["counterparty_key"],
                counterparty_name=d["counterparty_name"],
                anonymous_counterparty=d["anonymous_counterparty"],
                relationship=d["relationship"],
                direction=d["direction"],
                reported_share=d["reported_share"],
                share_basis=d["share_basis"],
                fiscal_period=d["fiscal_period"],
                valid_from=d["valid_from"],
                valid_to=d["valid_to"],
                evidence_tier=None,
                source=d["source"],
                source_url=d["source_url"],
                passage=d["passage"],
                passage_reason=None,
                evidence_id=d["evidence_id"],
                known_at=d["known_at"],
            )
        )
    return Dependencies(
        coverage=_cov(
            len(out),
            "No confirmed relationship is stored by the cutoff"
            + (f" ({excluded} unconfirmed candidates excluded)." if excluded else "."),
            f"{len(out)} confirmed relationships; {excluded} unconfirmed candidates excluded.",
        ),
        dependencies=out,
        excluded_unconfirmed=excluded,
    )


def _deal_order(e: DealEvent) -> tuple[dt.date, dt.datetime, int]:
    """Event time first (effective, else announced, else the day it became known), then the
    time it became known, then the id: an undated event is placed by when it was learned
    instead of before every dated one."""
    return (e.effective_at or e.announced_at or e.known_at.date(), e.known_at, e.event_id)


async def deals(pool: asyncpg.Pool, ticker: str, as_of: dt.datetime | None = None) -> Deals:
    when = cutoff(as_of)
    log = await has_table(pool, "graph_status_log")
    decided = await has_column(pool, "company_event", "decided_at")
    status = status_as_of_sql("company_event", "ev", "$2", log=log, decided=decided)
    rows = await pool.fetch(
        f"""select ev.id,ev.event_type,ev.event_state,{status} as status,ev.announced_at,
                  ev.effective_at,ev.known_at,ev.counterparty_key,ev.source,ev.source_url,
                  ev.terms,ev.detail,e.quote
           from public.company_event ev
           left join public.source_evidence e on e.id=ev.evidence_id
           where ev.ticker=$1 and ev.event_type in ('deal','financing') and ev.known_at <= $2
           order by coalesce(ev.counterparty_key,ev.source_url),ev.known_at,ev.id""",  # noqa: S608 - the status fragment is built from constants
        ticker,
        when,
    )
    grouped: dict[str, list[DealEvent]] = {}
    keys: dict[str, str | None] = {}
    for r in rows:
        detail = as_json(r["detail"])
        detail = detail if isinstance(detail, dict) else {}
        passage = r["quote"] or detail.get("passage")
        key = r["counterparty_key"] or r["source_url"]
        keys[key] = r["counterparty_key"]
        terms = as_json(r["terms"])
        terms = terms if isinstance(terms, dict) else {}
        grouped.setdefault(key, []).append(
            DealEvent(
                event_id=r["id"],
                event_type=r["event_type"],
                event_state=r["event_state"],
                status=r["status"],
                announced_at=r["announced_at"],
                effective_at=r["effective_at"],
                known_at=r["known_at"],
                counterparty_key=r["counterparty_key"],
                source=r["source"],
                source_url=r["source_url"],
                passage=str(passage)[:PASSAGE_CHARS] if passage else None,
                passage_reason=None if passage else "No source passage is stored for this event.",
                terms=terms,
            )
        )
    states: list[DealState] = []
    for key, events in grouped.items():
        events.sort(key=_deal_order)
        confirmed = [e for e in events if e.status == "confirmed"]
        current = max(confirmed, key=_deal_order) if confirmed else None
        states.append(
            DealState(
                deal_key=key,
                counterparty_key=keys[key],
                current_state=current.event_state if current else None,
                state_basis=(
                    f"latest confirmed event {current.event_id} known {current.known_at:%Y-%m-%d}"
                    if current
                    else "no confirmed event; suggested events are candidates, no state is given"
                ),
                events=events,
            )
        )
    return Deals(
        coverage=_cov(
            len(states),
            "No deal or financing event was known by the cutoff.",
            f"{len(states)} deals/financings; state shown only from confirmed events.",
        ),
        deals=states,
    )


async def consensus(pool: asyncpg.Pool, ticker: str, as_of: dt.datetime | None = None) -> Consensus:
    when = cutoff(as_of)
    rows = await pool.fetch(
        """select o.id,o.provider,o.metric,o.fiscal_period,o.currency,o.value,o.low,o.high,
                  o.analyst_count,o.vintage,o.published_at,o.known_at,o.source_url,e.quote
           from public.forward_observation o
           left join public.source_evidence e on e.id=o.evidence_id
           where o.ticker=$1 and o.kind='published_analyst_consensus' and o.known_at <= $2
           order by o.metric,o.fiscal_period,o.known_at,o.id""",
        ticker,
        when,
    )
    # A series is one provider's estimates in one currency: a revision is never computed
    # across providers (or currencies).
    grouped: dict[tuple[str | None, str | None, str, str], list[ConsensusVintage]] = {}
    for r in rows:
        grouped.setdefault(
            (r["provider"], r["currency"], r["metric"], r["fiscal_period"]), []
        ).append(
            ConsensusVintage(
                observation_id=r["id"],
                provider=r["provider"],
                vintage=r["vintage"],
                value=r["value"],
                low=r["low"],
                high=r["high"],
                analyst_count=r["analyst_count"],
                currency=r["currency"],
                published_at=r["published_at"],
                known_at=r["known_at"],
                source_url=r["source_url"],
                passage=r["quote"],
                passage_reason=None if r["quote"] else "No source passage is stored.",
            )
        )
    series: list[ConsensusSeries] = []
    for (provider, currency, metric, period), vintages in grouped.items():
        revision: Decimal | None = None
        inputs: list[int] = []
        if len(vintages) >= 2:
            prev, last = vintages[-2], vintages[-1]
            inputs = [prev.observation_id, last.observation_id]
            if prev.value is not None and last.value is not None:
                revision = last.value - prev.value
        series.append(
            ConsensusSeries(
                provider=provider,
                currency=currency,
                metric=metric,
                fiscal_period=period,
                vintages=vintages,
                revision_since_previous=revision,
                revision_inputs=inputs,
            )
        )
    releases = await pool.fetch(
        """select id,release_type,published_at,known_at,analyst_count,basis,status,source_url
           from public.issuer_ir_release
           where ticker=$1 and release_type in ('consensus','segment_consensus') and known_at <= $2
           order by known_at desc,id desc""",
        ticker,
        when,
    )
    issuer = [
        IrConsensusRelease(
            release_id=r["id"],
            release_type=r["release_type"],
            published_at=r["published_at"],
            known_at=r["known_at"],
            analyst_count=r["analyst_count"],
            basis=r["basis"],
            status=r["status"],
            source_url=r["source_url"],
        )
        for r in releases
    ]
    count = len(series) + len(issuer)
    return Consensus(
        coverage=_cov(
            count,
            "No public analyst consensus vintage was known by the cutoff. Free vendor "
            "endpoints for consensus may be paid-only; see /sources/access.",
            f"{len(series)} consensus series with preserved vintages and "
            f"{len(issuer)} issuer-published consensus releases.",
        ),
        series=series,
        issuer_published=issuer,
    )


async def evidence_summary(
    pool: asyncpg.Pool, ticker: str, as_of: dt.datetime | None = None
) -> EvidenceSummary:
    notes = await filed_notes(pool, ticker, as_of)
    pos = await positions(pool, ticker, as_of)
    ins = await insider(pool, ticker, as_of)
    deps = await dependencies(pool, ticker, as_of)
    dls = await deals(pool, ticker, as_of)
    cons = await consensus(pool, ticker, as_of)
    sections: dict[str, SectionCoverage] = {
        "filed_notes": notes.coverage,
        "control_stakes": pos.control.coverage,
        "institutional_13f": pos.institutional.coverage,
        "fund_positions": pos.funds.coverage,
        "insider_activity": ins.coverage,
        "dependencies": deps.coverage,
        "deals": dls.coverage,
        "consensus": cons.coverage,
    }
    gaps = [
        Gap(
            domain=name,
            state="not_covered" if cov.state == "not_covered" else "partial",
            reason=cov.reason,
        )
        for name, cov in sections.items()
        if cov.state != "covered"
    ]
    return EvidenceSummary(
        ticker=ticker,
        known_as_of=cutoff(as_of),
        filed_notes=notes,
        positions=pos,
        insider_activity=ins,
        dependencies=deps,
        deals=dls,
        consensus=cons,
        gaps=gaps,
    )
