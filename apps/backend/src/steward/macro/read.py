"""Point-in-time macro context and mapped holding effects."""

import datetime as dt
from decimal import Decimal

import asyncpg
from pydantic import BaseModel, Field

from steward.macro.exposure import Exposure, exposure_summary, weighted_effect


class ContextPoint(BaseModel):
    series_id: str
    source: str
    geography: str
    value: Decimal | None
    unit: str
    period: dt.date
    release_at: dt.datetime | None
    known_at: dt.datetime
    source_url: str
    five_year_low: Decimal | None
    five_year_high: Decimal | None
    ten_year_low: Decimal | None
    ten_year_high: Decimal | None
    access_state: str
    # The period before `period` (latest vintage of each), for the change a mapping weights.
    previous_period: dt.date | None = None
    previous_value: Decimal | None = None


class HoldingEffect(BaseModel):
    ticker: str
    issuer_id: int
    segment: str
    basis: str
    weight: Decimal
    geography: str | None
    hs_code: str | None
    unknown_weight: Decimal
    related_series: list[str]
    explanation: str
    dimension: str = "geography"


class MacroCoverage(BaseModel):
    provider: str
    dataset: str
    geography: str
    state: str
    checked_at: dt.datetime
    reason: str | None


class MacroComparison(BaseModel):
    series_id: str
    source: str
    geography: str
    value: Decimal | None
    unit: str
    period: dt.date
    release_at: dt.datetime | None
    known_at: dt.datetime
    source_url: str


class SeriesEffect(BaseModel):
    """Exposure-weighted latest change of one country series: sum of weight x (latest minus
    previous period) over the mapped geographies that have both. `covered_weight` is the share
    of the issuer those geographies represent; the rest is unknown, not unchanged."""

    series_id: str
    source: str
    unit: str
    weighted_change: Decimal
    covered_weight: Decimal
    geographies: list[str]


class MacroContext(BaseModel):
    as_of: dt.datetime
    observations: list[ContextPoint]
    coverage: list[MacroCoverage]
    effects: list[HoldingEffect]
    comparisons: list[MacroComparison]
    # True when no exposure mapping was known by `as_of`: no country can be attributed to the
    # issuer, so no country indicator is returned as its context.
    exposure_unknown: bool = False
    exposure: dict[str, object] = Field(default_factory=dict)
    series_effects: list[SeriesEffect] = Field(default_factory=list)


async def context_for_holding(pool: asyncpg.Pool, ticker: str, as_of: dt.datetime) -> MacroContext:
    # A mapping counts from the day it was entered (created_at), not from its valid_from, so a
    # mapping entered after `as_of` can never leak into an earlier view.
    mapping = await pool.fetch(
        """with active as (
             select em.*,
                    public.exposure_dimension(em.geography,em.hs_code) dimension,
                    sum(em.weight) over(partition by em.issuer_id,
                      public.exposure_dimension(em.geography,em.hs_code)) mapped_weight
             from public.exposure_mapping em
             where em.weight > 0 and em.valid_from <= $2::date and em.created_at <= $2
               and (em.valid_to is null or em.valid_to >= $2::date)
           )
           select isl.ticker,em.issuer_id,em.segment,em.basis,em.weight,em.geography,em.hs_code,
                  em.dimension,greatest(0,1-em.mapped_weight) unknown_weight
           from active em
           join public.security s on s.issuer_id=em.issuer_id
           join public.instrument_security_link isl on isl.security_id=s.id
           where isl.ticker=$1
           order by em.segment""",
        ticker,
        as_of,
    )
    geographies = sorted({row["geography"] for row in mapping if row["geography"]})
    rows = (
        await pool.fetch(
            """with eligible as (
                 select m.*, row_number() over (partition by m.series_id,m.source,m.geography,
                   m.period order by m.revision desc,m.known_at desc,m.id desc) vrn
                 from public.macro_observation m
                 where m.known_at <= $2 and m.geography = any($1::text[]) and m.value is not null
               ), latest as (
                 select *, lag(value) over w previous_value, lag(period) over w previous_period
                 from eligible where vrn=1
                 window w as (partition by series_id,source,geography order by period)
               ), ranges as (
                 select series_id,source,geography,
                   min(value) filter(where period >= ($2::date-interval '5 years')) low5,
                   max(value) filter(where period >= ($2::date-interval '5 years')) high5,
                   min(value) filter(where period >= ($2::date-interval '10 years')) low10,
                   max(value) filter(where period >= ($2::date-interval '10 years')) high10
                 from latest group by 1,2,3
               ), newest as (
                 select distinct on (series_id,source,geography) *
                 from latest order by series_id,source,geography,period desc
               )
               select e.series_id,e.source,e.geography,e.value,e.unit,e.period,e.release_at,
                      e.known_at,e.source_url,e.previous_period,e.previous_value,
                      r.low5 five_year_low,r.high5 five_year_high,
                      r.low10 ten_year_low,r.high10 ten_year_high,
                      coalesce(pa.state,'available') access_state
               from newest e join ranges r using(series_id,source,geography)
               left join public.provider_access pa on pa.provider=e.source
                 and pa.endpoint in ('indicators','sdmx')
               order by e.series_id,e.geography""",
            geographies,
            as_of,
        )
        if geographies
        else []
    )
    observed_geographies = {row["geography"] for row in rows}
    decorated_mapping = []
    for row in mapping:
        item = dict(row)
        geography = item["geography"]
        item["related_series"] = sorted(
            observation["series_id"]
            for observation in rows
            if geography and observation["geography"] == geography
        )
        if geography and geography in observed_geographies:
            item["explanation"] = (
                f"{item['basis']} mapping assigns {item['weight']:.1%} of {item['segment']} "
                f"to {geography}; the returned country indicators provide dated context. "
                "No company-specific sensitivity is configured."
            )
        elif geography:
            item["explanation"] = (
                f"{item['basis']} mapping assigns {item['weight']:.1%} of {item['segment']} "
                f"to {geography}, but no eligible country indicator is available as of the "
                "requested date."
            )
        else:
            item["explanation"] = (
                f"{item['basis']} mapping assigns {item['weight']:.1%} of {item['segment']} "
                f"to HS {item['hs_code']}; country macro indicators do not establish company "
                "supply contracts or sensitivities."
            )
        decorated_mapping.append(item)
    exposures = [
        Exposure(m["segment"], m["weight"], m["basis"], m["geography"], m["hs_code"])
        for m in mapping
    ]
    summary: dict[str, object] = {}
    if exposures:
        try:
            summary = exposure_summary(exposures)
        except ValueError:  # a legacy over-cap mapping: report it as unsummarisable
            summary = {}
    series_effects = _series_effects(exposures, rows)
    coverage = await pool.fetch(
        """select c.provider,c.dataset,c.entity_key geography,
                  case when c.provider='eurostat'
                    and not exists(select 1 from public.macro_observation m
                      where m.series_id=c.dataset and m.geography=c.entity_key
                        and m.known_at <= $1)
                    and exists(select 1 from public.macro_observation m
                      where m.series_id=c.dataset and m.geography=c.entity_key
                        and m.known_at > $1)
                    then 'vintage_unavailable' else c.state end state,
                  c.checked_at,
                  case when c.provider='eurostat'
                    and not exists(select 1 from public.macro_observation m
                      where m.series_id=c.dataset and m.geography=c.entity_key
                        and m.known_at <= $1)
                    and exists(select 1 from public.macro_observation m
                      where m.series_id=c.dataset and m.geography=c.entity_key
                        and m.known_at > $1)
                    then 'Eurostat release history was not archived before collection began'
                    else c.reason end reason
           from public.coverage_status c
           where c.provider in ('world_bank','oecd','bis','ecb','fred','eurostat','cnb')
           order by c.provider,c.dataset,c.entity_key""",
        as_of,
    )
    series_ids = sorted({row["series_id"] for row in rows})
    comparisons = (
        await pool.fetch(
            """select distinct on(series_id,source,geography) series_id,source,geography,
                      value,unit,period,release_at,known_at,source_url
               from public.macro_observation
               where series_id=any($1::text[]) and known_at <= $2 and value is not null
               order by series_id,source,geography,period desc,revision desc,known_at desc""",
            series_ids,
            as_of,
        )
        if series_ids
        else []
    )
    return MacroContext(
        as_of=as_of,
        observations=[ContextPoint(**dict(row)) for row in rows],
        coverage=[MacroCoverage(**dict(row)) for row in coverage],
        effects=[HoldingEffect(**row) for row in decorated_mapping],
        comparisons=[MacroComparison(**dict(row)) for row in comparisons],
        exposure_unknown=not mapping,
        exposure=summary,
        series_effects=series_effects,
    )


def _series_effects(exposures: list[Exposure], rows: list[asyncpg.Record]) -> list[SeriesEffect]:
    """Per (series, source): the exposure-weighted latest change across mapped geographies."""
    out: list[SeriesEffect] = []
    keys = sorted({(r["series_id"], r["source"], r["unit"]) for r in rows})
    for series_id, source, unit in keys:
        changes = {
            r["geography"]: r["value"] - r["previous_value"]
            for r in rows
            if r["series_id"] == series_id
            and r["source"] == source
            and r["unit"] == unit
            and r["previous_value"] is not None
        }
        effect = weighted_effect(exposures, changes)
        if effect is None:
            continue
        used = sorted(
            {e.geography for e in exposures if e.geography is not None and e.geography in changes}
        )
        covered = sum(
            (e.weight for e in exposures if e.dimension == "geography" and e.geography in changes),
            Decimal(0),
        )
        out.append(
            SeriesEffect(
                series_id=series_id,
                source=source,
                unit=unit,
                weighted_change=effect,
                covered_weight=covered,
                geographies=used,
            )
        )
    return out
