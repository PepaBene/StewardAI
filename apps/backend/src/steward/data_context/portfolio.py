"""Portfolio additions (D7.3): macro sensitivities, country/product concentration,
adjusted-return coverage and fund overlap.

Rules: only holdings with a confirmed issuer link and an active exposure mapping are rolled
up; every ratio is returned as a `Share` with its numerator and denominator; a holding with
no mapping contributes to the unmapped residual and never to an effect; reported and inferred
(estimated or user-assigned) mappings are kept apart; nothing is distributed over unknown
weight.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.data_context.common import Gap, Share
from steward.funds.aggregate import (
    FundOverlapReport,
    Unknown,
    aggregate_portfolio,
    fund_overlap,
)

Basis = Literal["reported", "inferred"]


class MixedCurrencyError(ValueError):
    pass


@dataclass
class Position:
    ticker: str
    value: Decimal
    issuer_id: int | None
    symbol: str


@dataclass
class Mapping:
    issuer_id: int
    geography: str | None
    hs_revision: str | None
    hs_code: str | None
    basis: str
    weight: Decimal


@dataclass
class _Acc:
    value: Decimal = Decimal(0)
    by_basis: dict[str, Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    tickers: set[str] = field(default_factory=set)


def basis_class(basis: str) -> Basis:
    return "reported" if basis == "reported" else "inferred"


class ExposureRow(BaseModel):
    key: str
    hs_revision: str | None = None
    exposed_value: Decimal
    share_of_portfolio: Share
    share_of_mapped: Share | None
    by_basis: dict[str, Decimal]
    holdings: int


class PortfolioConcentration(BaseModel):
    dimension: Literal["country", "product"]
    mapped: Share = Field(description="Mapped value over total portfolio value.")
    unmapped_value: Decimal
    unmapped_tickers: list[str]
    rows: list[ExposureRow]
    hhi: Decimal | None = Field(
        description="Herfindahl index of exposed value shares within the mapped value; null "
        "without a mapped value."
    )
    hhi_denominator: Decimal
    note: str


class SensitivityBasis(BaseModel):
    exposed_value_total: Decimal
    exposed_value_covered: Decimal
    coverage: Share
    weighted_change: Decimal | None = Field(
        description="Exposure-weighted average change over covered geographies only; null "
        "when no geography with exposure has two observations."
    )
    geographies: list[str]


class MacroSensitivity(BaseModel):
    series_id: str
    source: str
    unit: str
    reported: SensitivityBasis
    inferred: SensitivityBasis
    latest_period_min: dt.date | None
    latest_period_max: dt.date | None
    portfolio_value: Decimal


class ReturnBasisCoverage(BaseModel):
    adjustment_basis: str
    complete: Share
    partial_value: Decimal
    missing_value: Decimal
    complete_tickers: list[str]
    partial_tickers: list[str]
    missing_tickers: list[str]


class AdjustedReturnCoverage(BaseModel):
    bases: list[ReturnBasisCoverage]
    note: str


class PortfolioDataContext(BaseModel):
    snapshot_date: dt.date | None
    known_as_of: dt.datetime
    base_currency: str | None
    total_value: Decimal
    position_count: int
    identity_coverage: Share | None
    country_concentration: PortfolioConcentration | None
    product_concentration: PortfolioConcentration | None
    macro_sensitivities: list[MacroSensitivity]
    adjusted_return_coverage: AdjustedReturnCoverage | None
    fund_overlap: FundOverlapReport | None
    fund_unknown: Unknown | None
    fund_total_value: float | None
    gaps: list[Gap]


def concentration(
    dimension: Literal["country", "product"],
    positions: list[Position],
    mappings: list[Mapping],
    total: Decimal,
) -> PortfolioConcentration:
    by_issuer: dict[int, list[Mapping]] = defaultdict(list)
    for m in mappings:
        key = m.geography if dimension == "country" else m.hs_code
        if key is not None and m.weight > 0:
            by_issuer[m.issuer_id].append(m)
    accs: dict[tuple[str, str | None], _Acc] = {}
    mapped_value = Decimal(0)
    unmapped = Decimal(0)
    unmapped_tickers: list[str] = []
    for p in positions:
        rows = by_issuer.get(p.issuer_id, []) if p.issuer_id is not None else []
        weight_sum = sum((m.weight for m in rows), Decimal(0))
        if not rows:
            unmapped += p.value
            unmapped_tickers.append(p.ticker)
            continue
        scale = Decimal(1) / weight_sum if weight_sum > 1 else Decimal(1)
        mapped_value += p.value * min(weight_sum, Decimal(1))
        unmapped += p.value * max(Decimal(1) - weight_sum, Decimal(0))
        for m in rows:
            key = (m.geography if dimension == "country" else m.hs_code) or ""
            acc = accs.setdefault((key, None if dimension == "country" else m.hs_revision), _Acc())
            exposed = p.value * m.weight * scale
            acc.value += exposed
            acc.by_basis[m.basis] += exposed
            acc.tickers.add(p.ticker)
    rows_out = [
        ExposureRow(
            key=key,
            hs_revision=revision,
            exposed_value=acc.value,
            share_of_portfolio=Share.of(acc.value, total, "total portfolio value"),
            share_of_mapped=Share.of(acc.value, mapped_value, "mapped value")
            if mapped_value > 0
            else None,
            by_basis=dict(acc.by_basis),
            holdings=len(acc.tickers),
        )
        for (key, revision), acc in accs.items()
    ]
    rows_out.sort(key=lambda r: (-r.exposed_value, r.key))
    hhi: Decimal | None = None
    if mapped_value > 0:
        hhi = sum(((r.exposed_value / mapped_value) ** 2 for r in rows_out), Decimal(0))
    return PortfolioConcentration(
        dimension=dimension,
        mapped=Share.of(mapped_value, total, "total portfolio value"),
        unmapped_value=unmapped,
        unmapped_tickers=sorted(set(unmapped_tickers)),
        rows=rows_out,
        hhi=hhi,
        hhi_denominator=mapped_value,
        note="Only holdings with an active exposure mapping are rolled up. The unmapped value "
        "is reported beside the percentages, never spread over the mapped rows.",
    )


def sensitivity(
    positions: list[Position],
    mappings: list[Mapping],
    observations: dict[tuple[str, str, str], dict[str, tuple[Decimal | None, dt.date]]],
    total: Decimal,
    limit: int,
) -> list[MacroSensitivity]:
    """`observations[(series, source, unit)][geography] = (change, latest_period)`."""
    exposed: dict[tuple[str, Basis], Decimal] = defaultdict(Decimal)
    values: dict[int, Decimal] = defaultdict(Decimal)
    for p in positions:
        if p.issuer_id is not None:
            values[p.issuer_id] += p.value
    for m in mappings:
        value = values.get(m.issuer_id)
        if value is not None and m.geography and m.weight > 0:
            exposed[(m.geography, basis_class(m.basis))] += value * m.weight
    out: list[MacroSensitivity] = []
    for (series_id, source, unit), by_geo in observations.items():

        def build(
            basis: Basis,
            by_geo: dict[str, tuple[Decimal | None, dt.date]] = by_geo,
        ) -> SensitivityBasis:
            total_exposed = sum((v for (g, b), v in exposed.items() if b == basis), Decimal(0))
            covered = {
                g: v
                for (g, b), v in exposed.items()
                if b == basis and g in by_geo and by_geo[g][0] is not None
            }
            covered_value = sum(covered.values(), Decimal(0))
            weighted: Decimal | None = None
            if covered_value > 0:
                weighted = (
                    sum(
                        (v * (by_geo[g][0] or Decimal(0)) for g, v in covered.items()),
                        Decimal(0),
                    )
                    / covered_value
                )
            return SensitivityBasis(
                exposed_value_total=total_exposed,
                exposed_value_covered=covered_value,
                coverage=Share.of(covered_value, total_exposed, f"{basis} exposed value"),
                weighted_change=weighted,
                geographies=sorted(covered),
            )

        reported, inferred = build("reported"), build("inferred")
        if not reported.geographies and not inferred.geographies:
            continue
        periods = [by_geo[g][1] for g in {*reported.geographies, *inferred.geographies}]
        out.append(
            MacroSensitivity(
                series_id=series_id,
                source=source,
                unit=unit,
                reported=reported,
                inferred=inferred,
                latest_period_min=min(periods),
                latest_period_max=max(periods),
                portfolio_value=total,
            )
        )
    out.sort(
        key=lambda s: (
            -(s.reported.exposed_value_covered + s.inferred.exposed_value_covered),
            s.series_id,
            s.source,
        )
    )
    return out[:limit]


async def _positions(pool: asyncpg.Pool, day: dt.date) -> list[Position]:
    rows = await pool.fetch(
        """select p.ticker,p.value_base,p.base_currency,
                  coalesce(i.price_symbol,i.t212_ticker) symbol,s.issuer_id
           from public.position_snapshot p
           join public.instrument i on i.t212_ticker=p.ticker
           left join public.instrument_security_link l
             on l.ticker=p.ticker and l.status='confirmed'
           left join public.security s on s.id=l.security_id
           where p.snapshot_date=$1 order by p.ticker""",
        day,
    )
    if len({r["base_currency"] for r in rows}) > 1:
        raise MixedCurrencyError("Position snapshot mixes base currencies; totals are incomparable")
    return [
        Position(r["ticker"], Decimal(r["value_base"]), r["issuer_id"], r["symbol"]) for r in rows
    ]


async def _mappings(pool: asyncpg.Pool, day: dt.date, when: dt.datetime) -> list[Mapping]:
    rows = await pool.fetch(
        """select issuer_id,geography,hs_revision,hs_code,basis,weight
           from public.exposure_mapping
           where weight>0 and valid_from <= $1 and (valid_to is null or valid_to >= $1)
             and created_at <= $2""",
        day,
        when,
    )
    return [Mapping(**dict(r)) for r in rows]


async def _observation_changes(
    pool: asyncpg.Pool, geographies: list[str], when: dt.datetime
) -> dict[tuple[str, str, str], dict[str, tuple[Decimal | None, dt.date]]]:
    if not geographies:
        return {}
    rows = await pool.fetch(
        """with eligible as (
             select distinct on (series_id,source,geography,unit,period)
                    series_id,source,geography,unit,period,value
             from public.macro_observation
             where geography=any($1::text[]) and known_at <= $2 and value is not null
             order by series_id,source,geography,unit,period,known_at desc
           ), ranked as (
             select *,row_number() over (
               partition by series_id,source,geography,unit order by period desc) rn
             from eligible
           )
           select series_id,source,geography,unit,period,value,rn
           from ranked where rn <= 2 order by series_id,source,geography,unit,rn""",
        geographies,
        when,
    )
    grouped: dict[tuple[str, str, str, str], list[asyncpg.Record]] = defaultdict(list)
    for r in rows:
        grouped[(r["series_id"], r["source"], r["unit"], r["geography"])].append(r)
    out: dict[tuple[str, str, str], dict[str, tuple[Decimal | None, dt.date]]] = defaultdict(dict)
    for (series_id, source, unit, geography), pair in grouped.items():
        latest = pair[0]
        change = latest["value"] - pair[1]["value"] if len(pair) == 2 else None
        out[(series_id, source, unit)][geography] = (change, latest["period"])
    return dict(out)


async def _return_coverage(
    pool: asyncpg.Pool, positions: list[Position], when: dt.datetime
) -> AdjustedReturnCoverage:
    rows = await pool.fetch(
        """select distinct on (symbol,adjustment_basis)
                  symbol,adjustment_basis,value,coverage,missing_actions
           from public.price_return_series where symbol=any($1::text[]) and known_at <= $2
           order by symbol,adjustment_basis,end_date desc,known_at desc""",
        [p.symbol for p in positions],
        when,
    )
    latest = {(r["symbol"], r["adjustment_basis"]): r for r in rows}
    total = sum((p.value for p in positions), Decimal(0))
    bases: list[ReturnBasisCoverage] = []
    for basis in ("total_return", "price_adjusted", "price_raw"):
        complete: list[Position] = []
        partial: list[Position] = []
        missing: list[Position] = []
        for p in positions:
            r = latest.get((p.symbol, basis))
            if r is None:
                missing.append(p)
            elif r["value"] is not None and r["coverage"] >= 1 and not r["missing_actions"]:
                complete.append(p)
            else:
                partial.append(p)
        bases.append(
            ReturnBasisCoverage(
                adjustment_basis=basis,
                complete=Share.of(
                    sum((p.value for p in complete), Decimal(0)), total, "total portfolio value"
                ),
                partial_value=sum((p.value for p in partial), Decimal(0)),
                missing_value=sum((p.value for p in missing), Decimal(0)),
                complete_tickers=[p.ticker for p in complete],
                partial_tickers=[p.ticker for p in partial],
                missing_tickers=[p.ticker for p in missing],
            )
        )
    return AdjustedReturnCoverage(
        bases=bases,
        note="Coverage of stored return series, not a return. A series counts as complete only "
        "with full exchange-session coverage and no missing corporate actions; total return "
        "needs verified action coverage. No portfolio return is derived from partial series.",
    )


async def portfolio_data_context(
    pool: asyncpg.Pool,
    as_of: dt.date | None = None,
    *,
    series_limit: int = 20,
) -> PortfolioDataContext:
    when = dt.datetime.combine(as_of, dt.time.max, dt.UTC) if as_of else dt.datetime.now(dt.UTC)
    day = await pool.fetchval(
        "select max(snapshot_date) from public.position_snapshot"
        " where ($1::date is null or snapshot_date <= $1)",
        as_of,
    )
    if day is None:
        return PortfolioDataContext(
            snapshot_date=None,
            known_as_of=when,
            base_currency=None,
            total_value=Decimal(0),
            position_count=0,
            identity_coverage=None,
            country_concentration=None,
            product_concentration=None,
            macro_sensitivities=[],
            adjusted_return_coverage=None,
            fund_overlap=None,
            fund_unknown=None,
            fund_total_value=None,
            gaps=[
                Gap(
                    domain="portfolio",
                    state="missing",
                    reason="No position snapshot exists on or before the requested date.",
                )
            ],
        )
    positions = await _positions(pool, day)
    base = await pool.fetchval(
        "select base_currency from public.position_snapshot where snapshot_date=$1 limit 1", day
    )
    total = sum((p.value for p in positions), Decimal(0))
    mappings = await _mappings(pool, day, when)
    identified = sum((p.value for p in positions if p.issuer_id is not None), Decimal(0))
    geographies = sorted({m.geography for m in mappings if m.geography})
    observations = await _observation_changes(pool, geographies, when)
    overlap = await fund_overlap(pool, as_of=as_of)
    aggregate = await aggregate_portfolio(pool, as_of=as_of, top=1)
    gaps: list[Gap] = []
    unresolved = [p.ticker for p in positions if p.issuer_id is None]
    if unresolved:
        gaps.append(
            Gap(
                domain="identity",
                state="unresolved",
                reason="No confirmed issuer link, so exposure cannot be attached: "
                + ", ".join(unresolved),
            )
        )
    if not mappings:
        gaps.append(
            Gap(
                domain="exposure_mapping",
                state="missing",
                reason="No active country or product exposure mapping; concentration and "
                "sensitivities are empty rather than assumed.",
            )
        )
    elif not observations:
        gaps.append(
            Gap(
                domain="macro_sensitivities",
                state="missing",
                reason="No stored country series covers a mapped geography.",
            )
        )
    if not overlap.funds:
        gaps.append(
            Gap(
                domain="fund_overlap",
                state="missing",
                reason="No fund holdings snapshot was known by the requested date.",
            )
        )
    return PortfolioDataContext(
        snapshot_date=day,
        known_as_of=when,
        base_currency=base,
        total_value=total,
        position_count=len(positions),
        identity_coverage=Share.of(identified, total, "total portfolio value"),
        country_concentration=concentration("country", positions, mappings, total),
        product_concentration=concentration("product", positions, mappings, total),
        macro_sensitivities=sensitivity(positions, mappings, observations, total, series_limit),
        adjusted_return_coverage=await _return_coverage(pool, positions, when),
        fund_overlap=overlap,
        fund_unknown=aggregate.unknown,
        fund_total_value=aggregate.total_value,
        gaps=gaps,
    )
