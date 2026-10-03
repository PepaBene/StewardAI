"""Point-in-time macro context and mapped holding effects."""

import datetime as dt
import itertools
import statistics
from decimal import Decimal
from typing import Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.macro.exposure import Exposure, exposure_summary, weighted_effect
from steward.macro.release_calendar import NextRelease, next_releases

HISTORY_MAX = 60
# Fewer stored first-release timestamps than this and no cadence is claimed.
MIN_RELEASES_FOR_CADENCE = 4


class SeriesVintage(BaseModel):
    """One period of a series with the value first published and the latest known by `as_of`.

    `value_first_release` is null when the first publication was not archived (the oldest
    stored row has no release date: a backfill or a provider that keeps no history); then
    `vintage_unavailable` is true and nothing may be drawn as a "first release"."""

    period: dt.date
    value_latest: Decimal
    value_first_release: Decimal | None
    vintage_unavailable: bool
    released_at: dt.datetime | None  # when the first release was published
    revised_at: dt.datetime | None  # when the latest value became known; null if never revised
    revision_count: int  # stored revisions after the first row (0: never revised)


class NextReleaseSource(BaseModel):
    """Where a `next_release` date comes from: the provider's own published calendar."""

    provider: str = Field(
        description="Provider whose calendar names the date (fred, eurostat, ecb, bis, cnb)."
    )
    url: str = Field(
        description="The provider's calendar page the date was read from (for a "
        "`published_rule`, the page that states the rule)."
    )
    title: str | None = Field(default=None, description="The calendar entry's own label.")


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
    # Oldest first, at most `history` periods (request parameter; empty when 0). Only records
    # known by `as_of`. `release_calendar` is the provider's published schedule as text: no
    # machine-readable next-release date is stored.
    history: list[SeriesVintage] = Field(default_factory=list)
    release_calendar: str | None = None
    next_release: dt.date | None = Field(
        default=None,
        description="Date of the next release of this series as announced in the provider's own "
        "published release calendar: the earliest stored calendar date on or after the UTC date "
        "of `as_of`, from calendar rows known by `as_of` and not superseded (rescheduled) by "
        "then. A date, not a promise: the provider may still reschedule it. Null when no "
        "calendar entry or published rule is stored for the series (then see "
        "`next_release_estimate`). `next_release_basis` is `provider_calendar` or "
        "`published_rule` whenever this is set.",
    )
    next_release_time: dt.time | None = Field(
        default=None,
        description="Wall-clock time of day of `next_release` exactly as the provider publishes "
        "it, never converted. The zone it is a time in is `next_release_timezone`. Null when "
        "the calendar gives only a date (FRED, Eurostat, BIS, ECB meeting days) or "
        "`next_release` is null.",
    )
    next_release_timezone: str | None = Field(
        default=None,
        description="IANA time zone of `next_release_time` (for example Europe/Prague). Set only "
        "when `next_release_time` is set; null for a date-only release. "
        "`next_release_timezone_source` says whether the provider stated it or it was assumed "
        "from where the publishing agency is located: show an assumed zone as assumed.",
    )
    next_release_timezone_source: Literal["stated_by_provider", "agency_location"] | None = Field(
        default=None,
        description="`stated_by_provider`: the provider's calendar names the zone (BLS iCal "
        "TZID US-Eastern; ECB yield curves 'CET'). `agency_location`: the provider states a "
        "time but no zone, so the zone is that of the publishing agency's seat (ECB Frankfurt, "
        "ČNB Prague, DG ECFIN Brussels, ...), an assumption. Null with `next_release_timezone`.",
    )
    next_release_source: NextReleaseSource | None = Field(
        default=None,
        description="Provider and calendar URL of `next_release`; null when it is null.",
    )
    next_release_estimate: dt.date | None = Field(
        default=None,
        description="FALLBACK, set only when `next_release` is null (no calendar entry stored "
        "for the series). Estimated date (UTC) of the next release of this series: the latest "
        "stored first-release timestamp plus the median gap between the stored first-release "
        "timestamps of its observations (all periods known by `as_of`, independent of "
        "`history`). Null with fewer than 4 stored first-release timestamps, with a "
        "non-positive median gap, or when the estimate is already before `as_of` (the series "
        "is overdue or no longer published). An estimate, not a provider announcement.",
    )
    next_release_basis: (
        Literal["provider_calendar", "published_rule", "observed_cadence"] | None
    ) = Field(
        default=None,
        description="Where the returned next-release date comes from: `provider_calendar` "
        "(`next_release`, a date in the provider's published schedule), `published_rule` "
        "(`next_release`, derived from a release rule and a holiday list the provider "
        "itself publishes, for example 'every TARGET business day' with the ECB's closing "
        "days; the date can still move when the provider postpones) or `observed_cadence` "
        "(`next_release_estimate`, the median gap of stored first releases; a fallback only "
        "when no calendar entry or rule exists). Null when both are null.",
    )


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


async def context_for_holding(
    pool: asyncpg.Pool, ticker: str, as_of: dt.datetime, history: int = 0
) -> MacroContext:
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
             and isl.status='confirmed'
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
                      coalesce(pa.state,'access_unverified') access_state
               from newest e join ranges r using(series_id,source,geography)
               -- One row per provider, whatever endpoints it has: the most recently probed
               -- one (never-probed last), then by endpoint name. No row: never probed.
               left join lateral (
                 select a.state from public.provider_access a
                 where a.provider=e.source and a.endpoint in ('indicators','sdmx')
                 order by a.tested_at desc nulls last, a.endpoint limit 1
               ) pa on true
               order by e.series_id,e.geography""",
            geographies,
            as_of,
        )
        if geographies
        else []
    )
    histories = await _vintage_history(pool, rows, as_of, history) if history > 0 else {}
    cadences = await _release_cadence(pool, rows, as_of)
    calendar = await next_releases(pool, [(r["source"], r["series_id"]) for r in rows], as_of)
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
        observations=[
            ContextPoint(
                **dict(row),
                **histories.get((row["series_id"], row["source"], row["geography"]), {}),
                **_with_calendar(
                    cadences.get((row["series_id"], row["source"], row["geography"]), {}),
                    calendar.get((row["source"], row["series_id"])),
                ),
            )
            for row in rows
        ],
        coverage=[MacroCoverage(**dict(row)) for row in coverage],
        effects=[HoldingEffect(**row) for row in decorated_mapping],
        comparisons=[MacroComparison(**dict(row)) for row in comparisons],
        exposure_unknown=not mapping,
        exposure=summary,
        series_effects=series_effects,
    )


def observed_next_release(
    releases: list[dt.datetime], as_of: dt.datetime
) -> tuple[dt.date | None, Literal["observed_cadence"] | None]:
    """(estimate, basis) from stored first-release timestamps only: the latest one plus the
    median gap between consecutive distinct ones. (None, None) when fewer than
    MIN_RELEASES_FOR_CADENCE are stored, the median gap is not positive, or the estimate lies
    before `as_of`."""
    stamps = sorted({r if r.tzinfo else r.replace(tzinfo=dt.UTC) for r in releases})
    if len(stamps) < MIN_RELEASES_FOR_CADENCE:
        return None, None
    gap = statistics.median((b - a).total_seconds() for a, b in itertools.pairwise(stamps))
    if gap <= 0:
        return None, None
    estimate = (stamps[-1] + dt.timedelta(seconds=gap)).astimezone(dt.UTC)
    if estimate < as_of:
        return None, None
    return estimate.date(), "observed_cadence"


def _with_calendar(cadence: dict[str, object], entry: NextRelease | None) -> dict[str, object]:
    """The provider's calendar date when one is stored; the observed-cadence estimate only as
    the fallback (never both: the basis names the one returned)."""
    if entry is None:
        return cadence
    return {
        "next_release": entry.release_date,
        "next_release_time": entry.release_time,
        "next_release_timezone": entry.release_timezone if entry.release_time else None,
        "next_release_timezone_source": entry.release_timezone_source
        if entry.release_time
        else None,
        "next_release_source": NextReleaseSource(
            provider=entry.provider, url=entry.source_url, title=entry.title
        ),
        "next_release_estimate": None,
        "next_release_basis": entry.basis,
    }


async def _release_cadence(
    pool: asyncpg.Pool, rows: list[asyncpg.Record], as_of: dt.datetime
) -> dict[tuple[str, str, str], dict[str, object]]:
    """Per series, `next_release_estimate` / `next_release_basis` from the first-release
    timestamps (release_at of each period's first stored row) known by `as_of`."""
    if not rows:
        return {}
    keys = {(r["series_id"], r["source"], r["geography"]) for r in rows}
    stored = await pool.fetch(
        """with known as (
             select m.series_id,m.source,m.geography,m.period,m.release_at,
               row_number() over (partition by m.series_id,m.source,m.geography,m.period
                 order by m.revision,m.known_at,m.id) first_rn
             from public.macro_observation m
             where m.known_at <= $2 and m.series_id = any($1::text[])
               and m.geography = any($3::text[]) and m.source = any($4::text[])
               and m.value is not null
           )
           select series_id,source,geography,array_agg(release_at order by release_at) releases
           from known where first_rn=1 and release_at is not null
           group by 1,2,3""",
        sorted({k[0] for k in keys}),
        as_of,
        sorted({k[2] for k in keys}),
        sorted({k[1] for k in keys}),
    )
    out: dict[tuple[str, str, str], dict[str, object]] = {}
    for row in stored:
        key = (row["series_id"], row["source"], row["geography"])
        if key not in keys:
            continue
        estimate, basis = observed_next_release(list(row["releases"]), as_of)
        out[key] = {"next_release_estimate": estimate, "next_release_basis": basis}
    return out


async def _vintage_history(
    pool: asyncpg.Pool, rows: list[asyncpg.Record], as_of: dt.datetime, limit: int
) -> dict[tuple[str, str, str], dict[str, object]]:
    """Per series, the last `limit` periods (oldest first) with first-release and latest value,
    from the rows known by `as_of` only."""
    if not rows:
        return {}
    keys = {(r["series_id"], r["source"], r["geography"]) for r in rows}
    stored = await pool.fetch(
        """with known as (
             select m.*,
               row_number() over (partition by m.series_id,m.source,m.geography,m.period
                 order by m.revision desc,m.known_at desc,m.id desc) latest_rn,
               row_number() over (partition by m.series_id,m.source,m.geography,m.period
                 order by m.revision,m.known_at,m.id) first_rn,
               count(*) over (partition by m.series_id,m.source,m.geography,m.period) n
             from public.macro_observation m
             where m.known_at <= $2 and m.series_id = any($1::text[])
               and m.geography = any($3::text[]) and m.source = any($4::text[])
               and m.value is not null
           ), paired as (
             select l.series_id,l.source,l.geography,l.period,l.value value_latest,
                    l.known_at latest_known_at,f.value first_value,f.release_at first_release_at,
                    l.n,
                    row_number() over (partition by l.series_id,l.source,l.geography
                      order by l.period desc) recent
             from known l join known f on f.series_id=l.series_id and f.source=l.source
               and f.geography=l.geography and f.period=l.period and f.first_rn=1
             where l.latest_rn=1
           )
           select p.series_id,p.source,p.geography,p.period,p.value_latest,p.first_value,
                  p.first_release_at,p.latest_known_at,p.n,md.release_calendar
           from paired p
           left join public.macro_series_metadata md on md.series_id=p.series_id
             and md.source=p.source
           where p.recent <= $5
           order by p.series_id,p.source,p.geography,p.period""",
        sorted({k[0] for k in keys}),
        as_of,
        sorted({k[2] for k in keys}),
        sorted({k[1] for k in keys}),
        min(limit, HISTORY_MAX),
    )
    out: dict[tuple[str, str, str], dict[str, object]] = {}
    for row in stored:
        key = (row["series_id"], row["source"], row["geography"])
        if key not in keys:
            continue
        archived = row["first_release_at"] is not None
        entry = out.setdefault(key, {"history": [], "release_calendar": row["release_calendar"]})
        history = entry["history"]
        assert isinstance(history, list)
        history.append(
            SeriesVintage(
                period=row["period"],
                value_latest=row["value_latest"],
                value_first_release=row["first_value"] if archived else None,
                vintage_unavailable=not archived,
                released_at=row["first_release_at"],
                revised_at=row["latest_known_at"] if row["n"] > 1 else None,
                revision_count=row["n"] - 1,
            )
        )
    return out


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
