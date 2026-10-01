"""Holding data context (D7.2): global financial history, country/trade exposure, business
network, patents, source disagreements, explicit gaps and primary-versus-vendor provenance.

It composes the existing read functions (`free_source_reads`, `macro.read`) and adds no
tables. Unknown stays null, every row keeps its `known_at`, and a cutoff (`as_of`) hides
anything learned later, including later revisions.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import asyncpg
from pydantic import BaseModel, Field

from steward.data_context.common import (
    Gap,
    Provenance,
    as_json,
    cutoff,
    provenance_counts,
    provenance_of,
    resolve_identity,
)
from steward.free_source_reads import (
    business_network_payload,
    company_research_rows,
    financial_history_rows,
    forward_rows,
    patent_trend_rows,
)
from steward.macro.read import MacroContext, context_for_holding

ROW_LIMIT = 300


class FinancialFactOut(BaseModel):
    id: int
    concept: str
    label: str | None
    statement: str
    period_start: dt.date | None
    period_end: dt.date
    fiscal_period: str | None
    basis: str
    value: Decimal | None
    unit: str
    currency: str | None
    dimensions: Any
    source: str
    provenance: Provenance
    source_url: str
    known_at: dt.datetime
    revision: int
    has_text_block: bool
    evidence_id: int | None


class FinancialHistory(BaseModel):
    total_rows: int
    returned_rows: int
    statements: dict[str, int]
    earliest_period: dt.date | None
    latest_period: dt.date | None
    provenance: dict[str, int]
    facts: list[FinancialFactOut]


class TradeRow(BaseModel):
    reporter: str
    partner: str
    flow: str
    hs_revision: str
    hs_code: str
    period: dt.date
    trade_value: Decimal | None
    quantity: Decimal | None
    quantity_unit: str | None
    known_at: dt.datetime
    source_url: str


class TradeExposure(BaseModel):
    hs_code: str
    hs_revision: str | None
    geography: str | None
    exposure_weight: Decimal
    exposure_basis: str
    latest_period: dt.date | None
    rows: list[TradeRow]
    notice: str = (
        "National product trade statistics. They do not identify the issuer's customers or "
        "suppliers, and rows for different partners are listed, never summed."
    )
    gap: str | None = None


class Disagreement(BaseModel):
    conflict_id: int
    metric: str
    status: str
    tolerance: Decimal | None
    difference: Decimal | None
    preferred_source: str | None
    preferred_provenance: Provenance
    preferred_value: Decimal | None
    preferred_known_at: dt.datetime | None
    alternative_source: str
    alternative_provenance: Provenance
    alternative_value: Decimal | None
    alternative_known_at: dt.datetime
    period_end: dt.date
    resolution: str | None


class CoverageEntry(BaseModel):
    provider: str
    dataset: str
    entity_key: str
    state: str
    latest_period: dt.date | None
    expected_periods: int | None
    available_periods: int | None
    checked_at: dt.datetime
    reason: str | None


class HoldingDataContext(BaseModel):
    ticker: str
    known_as_of: dt.datetime
    identity: dict[str, object]
    financial_history: FinancialHistory
    country_exposure: MacroContext | None
    trade_exposure: list[TradeExposure]
    business_network: dict[str, Any]
    patents: dict[str, Any]
    forward_data_provenance: dict[str, int] = Field(
        description="Source classes of forward observations (consensus, guidance, scenarios)."
    )
    research_observation_provenance: dict[str, int]
    source_disagreements: list[Disagreement]
    coverage: list[CoverageEntry]
    gaps: list[Gap]
    provenance_note: str = (
        "primary = regulator, central bank, statistics office or issuer; vendor = commercial "
        "aggregator. A vendor value never replaces a primary value; disagreements are listed."
    )


def _fact(row: dict[str, Any]) -> FinancialFactOut:
    return FinancialFactOut(
        id=row["id"],
        concept=row["concept"],
        label=row["label"],
        statement=row["statement"],
        period_start=row["period_start"],
        period_end=row["period_end"],
        fiscal_period=row["fiscal_period"],
        basis=row["basis"],
        value=row["value"],
        unit=row["unit"],
        currency=row["currency"],
        dimensions=as_json(row["dimensions"]),
        source=row["source"],
        provenance=provenance_of(row["source"]),
        source_url=row["source_url"],
        known_at=row["known_at"],
        revision=row["revision"],
        has_text_block=row["text_block"] is not None,
        evidence_id=row["evidence_id"],
    )


def build_financial_history(rows: list[dict[str, Any]], limit: int = ROW_LIMIT) -> FinancialHistory:
    # Rows are ordered newest period first; a restatement keeps its own revision and known_at.
    statements: dict[str, int] = {}
    for row in rows:
        statements[row["statement"]] = statements.get(row["statement"], 0) + 1
    periods = [row["period_end"] for row in rows]
    return FinancialHistory(
        total_rows=len(rows),
        returned_rows=min(len(rows), limit),
        statements=statements,
        earliest_period=min(periods, default=None),
        latest_period=max(periods, default=None),
        provenance=provenance_counts([row["source"] for row in rows]),
        facts=[_fact(row) for row in rows[:limit]],
    )


async def trade_exposure(
    pool: asyncpg.Pool, effects: list[Any], as_of: dt.datetime
) -> list[TradeExposure]:
    out: list[TradeExposure] = []
    seen: set[tuple[str | None, str]] = set()
    for effect in effects:
        if not effect.hs_code or (effect.geography, effect.hs_code) in seen:
            continue
        seen.add((effect.geography, effect.hs_code))
        rows = await pool.fetch(
            """with latest as (
                 select distinct on (reporter,partner,flow,hs_revision,hs_code,period)
                        reporter,partner,flow,hs_revision,hs_code,period,trade_value,quantity,
                        quantity_unit,known_at,source_url
                 from public.trade_observation
                 where hs_code=$1 and ($2::text is null or reporter=$2) and known_at <= $3
                 order by reporter,partner,flow,hs_revision,hs_code,period,known_at desc
               )
               select * from latest
               where period=(select max(period) from latest)
               order by trade_value desc nulls last,reporter,partner,flow limit 20""",
            effect.hs_code,
            effect.geography,
            as_of,
        )
        trade_rows = [TradeRow(**dict(r)) for r in rows]
        out.append(
            TradeExposure(
                hs_code=effect.hs_code,
                hs_revision=trade_rows[0].hs_revision if trade_rows else None,
                geography=effect.geography,
                exposure_weight=effect.weight,
                exposure_basis=effect.basis,
                latest_period=max((r.period for r in trade_rows), default=None),
                rows=trade_rows,
                gap=None
                if trade_rows
                else "No trade observations were known for this product by the cutoff.",
            )
        )
    return out


async def source_disagreements(
    pool: asyncpg.Pool, issuer_id: int | None, security_id: int | None, as_of: dt.datetime
) -> list[Disagreement]:
    if issuer_id is None and security_id is None:
        return []
    rows = await pool.fetch(
        """select c.id,c.metric,c.status,c.tolerance,c.difference,c.resolution,
                  p.source preferred_source,p.value preferred_value,p.known_at preferred_known_at,
                  a.source alternative_source,a.value alternative_value,
                  a.known_at alternative_known_at,a.period_end
           from public.source_conflict c
           join public.source_observation a on a.id=c.alternative_observation_id
           left join public.source_observation p on p.id=c.preferred_observation_id
           where ((a.issuer_id is not null and a.issuer_id=$1)
               or (a.security_id is not null and a.security_id=$2))
             and a.known_at <= $3 and (p.id is null or p.known_at <= $3)
           order by a.period_end desc,c.id desc""",
        issuer_id,
        security_id,
        as_of,
    )
    return [
        Disagreement(
            conflict_id=r["id"],
            metric=r["metric"],
            status=r["status"],
            tolerance=r["tolerance"],
            difference=r["difference"],
            preferred_source=r["preferred_source"],
            preferred_provenance=provenance_of(r["preferred_source"]),
            preferred_value=r["preferred_value"],
            preferred_known_at=r["preferred_known_at"],
            alternative_source=r["alternative_source"],
            alternative_provenance=provenance_of(r["alternative_source"]),
            alternative_value=r["alternative_value"],
            alternative_known_at=r["alternative_known_at"],
            period_end=r["period_end"],
            resolution=r["resolution"],
        )
        for r in rows
    ]


async def coverage_entries(pool: asyncpg.Pool, keys: list[str]) -> list[CoverageEntry]:
    rows = await pool.fetch(
        """select provider,dataset,entity_key,state,latest_period,expected_periods,
                  available_periods,checked_at,reason
           from public.coverage_status where entity_key=any($1::text[])
           order by provider,dataset,entity_key""",
        keys,
    )
    return [CoverageEntry(**dict(r)) for r in rows]


def find_gaps(
    identity: dict[str, object],
    history: FinancialHistory,
    macro: MacroContext | None,
    trade: list[TradeExposure],
    network: dict[str, Any],
    patents: list[dict[str, Any]],
    forward: list[dict[str, Any]],
    coverage: list[CoverageEntry],
) -> list[Gap]:
    gaps: list[Gap] = []
    if not identity["known"]:
        gaps.append(Gap(domain="identity", state="missing", reason="Not a known instrument."))
    elif identity["issuer_id"] is None:
        gaps.append(
            Gap(
                domain="identity",
                state="unresolved",
                reason="No confirmed issuer/security link (status: "
                f"{identity['status'] or 'none'}); issuer-level data cannot be attached.",
            )
        )
    if history.total_rows == 0:
        gaps.append(
            Gap(
                domain="financial_history",
                state="missing",
                reason="No financial statement facts were known by the cutoff.",
            )
        )
    if macro is None or not macro.effects:
        gaps.append(
            Gap(
                domain="country_exposure",
                state="missing",
                reason="No active country or product exposure mapping exists for the issuer; "
                "no country effect is assumed.",
            )
        )
    elif not macro.observations:
        gaps.append(
            Gap(
                domain="country_exposure",
                state="partial",
                reason="Exposure is mapped but no country indicator was known by the cutoff.",
            )
        )
    for item in trade:
        if item.gap:
            gaps.append(
                Gap(
                    domain="trade_exposure",
                    state="missing",
                    reason=f"HS {item.hs_code}: {item.gap}",
                )
            )
    if not any(e["status"] == "confirmed" for e in network["edges"]):
        gaps.append(
            Gap(
                domain="business_network",
                state="missing",
                reason="No confirmed company-graph edge; suggested edges are candidates only.",
            )
        )
    if not patents:
        gaps.append(
            Gap(
                domain="patents",
                state="missing",
                reason="No patent family is attributed to the issuer by the cutoff.",
            )
        )
    if not forward:
        gaps.append(
            Gap(
                domain="forward_data",
                state="missing",
                reason="No guidance, consensus or scenario observation was known by the cutoff.",
            )
        )
    for entry in coverage:
        if entry.state != "available":
            gaps.append(
                Gap(
                    domain=f"{entry.provider}:{entry.dataset}",
                    state="not_covered" if entry.state == "not_covered" else "partial",
                    reason=f"Coverage ledger state {entry.state}: "
                    f"{entry.reason or 'no reason stored'}",
                )
            )
    return gaps


async def holding_data_context(
    pool: asyncpg.Pool, ticker: str, as_of: dt.datetime | None = None
) -> HoldingDataContext:
    when = cutoff(as_of)
    identity = await resolve_identity(pool, ticker)
    rows = await financial_history_rows(pool, ticker, when)
    history = build_financial_history(rows)
    macro = await context_for_holding(pool, ticker, when)
    if not macro.effects:
        # Without an active mapping no country is attributable to this issuer; the macro
        # reader's unfiltered fallback is not shown as the holding's exposure.
        macro = macro.model_copy(update={"observations": [], "comparisons": []})
    trade = await trade_exposure(pool, macro.effects, when)
    network = await business_network_payload(pool, ticker, when)
    patents = await patent_trend_rows(pool, ticker)
    forward = await forward_rows(pool, ticker, when)
    research = await company_research_rows(pool, ticker, when)
    issuer_id = identity["issuer_id"]
    security_id = identity["security_id"]
    disagreements = await source_disagreements(
        pool,
        issuer_id if isinstance(issuer_id, int) else None,
        security_id if isinstance(security_id, int) else None,
        when,
    )
    keys = [ticker]
    symbol = identity.get("price_symbol")
    if isinstance(symbol, str):
        keys.append(symbol)
    coverage = await coverage_entries(pool, keys)
    # `identity` carries internal ids only; `price_symbol` is a public symbol.
    return HoldingDataContext(
        ticker=ticker,
        known_as_of=when,
        identity={k: v for k, v in identity.items()},
        financial_history=history,
        country_exposure=macro,
        trade_exposure=trade,
        business_network=network,
        patents={"families": network["patents"], "trend": patents},
        forward_data_provenance=provenance_counts([r["provider"] for r in forward]),
        research_observation_provenance=provenance_counts([r["provider"] for r in research]),
        source_disagreements=disagreements,
        coverage=coverage,
        gaps=find_gaps(
            identity, history, macro, trade, network, network["patents"], forward, coverage
        ),
    )
