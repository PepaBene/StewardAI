"""Point-in-time macro context and mapped holding effects."""

import datetime as dt
from decimal import Decimal

import asyncpg
from pydantic import BaseModel


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


class MacroContext(BaseModel):
    as_of: dt.datetime
    observations: list[ContextPoint]
    coverage: list[MacroCoverage]
    effects: list[HoldingEffect]
    comparisons: list[MacroComparison]


async def context_for_holding(pool: asyncpg.Pool, ticker: str, as_of: dt.datetime) -> MacroContext:
    rows = await pool.fetch(
        """with eligible as (
             select m.*, row_number() over (partition by m.series_id,m.source,m.geography
               order by m.period desc,m.known_at desc) rn
             from public.macro_observation m where m.known_at <= $2
           ), ranges as (
             select series_id,source,geography,
               min(value) filter(where period >= ($2::date-interval '5 years')) low5,
               max(value) filter(where period >= ($2::date-interval '5 years')) high5,
               min(value) filter(where period >= ($2::date-interval '10 years')) low10,
               max(value) filter(where period >= ($2::date-interval '10 years')) high10
             from public.macro_observation where known_at <= $2 group by 1,2,3
           )
           select e.series_id,e.source,e.geography,e.value,e.unit,e.period,e.release_at,e.known_at,
                  e.source_url,r.low5 five_year_low,r.high5 five_year_high,
                  r.low10 ten_year_low,r.high10 ten_year_high,
                  coalesce(pa.state,'available') access_state
           from eligible e join ranges r using(series_id,source,geography)
           left join public.provider_access pa on pa.provider=e.source
             and pa.endpoint in ('indicators','sdmx')
           where e.rn=1 and (e.geography in (
             select em.geography from public.exposure_mapping em
             join public.instrument_security_link isl on isl.security_id in (
               select id from public.security where issuer_id=em.issuer_id)
             where isl.ticker=$1 and em.weight > 0 and em.valid_from <= $2::date
               and (em.valid_to is null or em.valid_to >= $2::date)
           ) or not exists(select 1 from public.exposure_mapping em
             join public.security s on s.issuer_id=em.issuer_id
             join public.instrument_security_link isl on isl.security_id=s.id where isl.ticker=$1))
           order by e.series_id,e.geography""",
        ticker,
        as_of,
    )
    mapping = await pool.fetch(
        """with active as (
             select em.*,sum(em.weight) over(partition by em.issuer_id) mapped_weight
             from public.exposure_mapping em
             where em.weight > 0 and em.valid_from <= $2::date
               and (em.valid_to is null or em.valid_to >= $2::date)
           )
           select isl.ticker,em.issuer_id,em.segment,em.basis,em.weight,em.geography,em.hs_code,
                  greatest(0,1-em.mapped_weight) unknown_weight
           from active em
           join public.security s on s.issuer_id=em.issuer_id
           join public.instrument_security_link isl on isl.security_id=s.id
           where isl.ticker=$1
           order by em.segment""",
        ticker,
        as_of,
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
               from public.macro_observation where series_id=any($1::text[]) and known_at <= $2
               order by series_id,source,geography,period desc,known_at desc""",
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
    )
