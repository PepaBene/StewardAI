"""The `macro_release_calendar` job and the stored release calendar.

The job reads each provider's own published release calendar (steward.sources.release_calendar)
into `public.macro_release_calendar`, append-only: a rescheduled release is a new row and the
old one keeps its place with `superseded_at`. `next_releases` answers "the earliest stored
release date on or after `as_of`" for the macro context, from rows known by `as_of` and not yet
superseded then.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

import asyncpg
import httpx
from pydantic import BaseModel

from steward import __version__
from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.jobs.runner import JobContext
from steward.macro.sdmx_keys import SdmxKeyError, pin_key
from steward.settings import get_settings
from steward.sources.base import AccessResult, AccessState
from steward.sources.governor import Budget, PostgresRequestGovernor
from steward.sources.release_calendar import (
    CalendarEntry,
    CalendarFetch,
    fetch_bis,
    fetch_bls,
    fetch_cnb,
    fetch_ecb,
    fetch_ecfin,
    fetch_eurostat,
    fetch_fred,
)
from steward.sources.store import CoverageUpdate, provider_gate, record_access, update_coverage

log = logging.getLogger(__name__)

ENDPOINT = "release_calendar"
COVERAGE_PROVIDER = "macro_release_calendar"
# `ecfin` is the DG ECFIN survey calendar and `bls` the BLS release schedule: each is called and
# gated as its own endpoint, but its rows date the `eurostat` sentiment series / the `fred` series
# (the observation source), see CalendarFetch.rows_provider. `bls` runs after `fred` so that the
# two can be compared.
CALENDAR_PROVIDERS = ("fred", "bls", "eurostat", "ecb", "bis", "ecfin", "cnb")

# The collector's own hard stop per provider (the ledger rows in provider_access declare FRED's
# published 120 requests per minute; the others publish no limit). A daily run needs 1 request
# (Eurostat, BIS, BLS), 2 (ECFIN: this and next year's PDF), 3 (ČNB: meetings, holidays, CZEONIA
# rule), 6 (ECB: three statistical calendars, the meeting calendar, the yield-curve rule's two
# pages) or 2 per series (FRED), so these leave room for a few manual runs.
CALENDAR_BUDGETS: dict[str, Budget] = {
    "fred": Budget(per_minute=Decimal(30), daily=Decimal(60)),
    "bls": Budget(daily=Decimal(6)),
    "eurostat": Budget(daily=Decimal(6)),
    "ecb": Budget(daily=Decimal(15)),
    "bis": Budget(daily=Decimal(6)),
    "ecfin": Budget(daily=Decimal(6)),
    "cnb": Budget(daily=Decimal(6)),
}


@dataclass(slots=True)
class CalendarDeps:
    http: httpx.AsyncClient
    fred_key: str | None = None
    # A User-Agent with a contact address: BLS refuses requests without one.
    bls_user_agent: str | None = None
    now: dt.datetime | None = None


@dataclass(frozen=True, slots=True)
class StoreResult:
    inserted: int
    superseded: int
    unchanged: int


async def store_calendar(pool: asyncpg.Pool, fetch: CalendarFetch, now: dt.datetime) -> StoreResult:
    """Append the new dates and supersede (never delete or rewrite) the live future dates a
    completely-read calendar no longer lists. Past dates are never superseded: they happened."""
    today = now.astimezone(dt.UTC).date()
    inserted = superseded = unchanged = 0
    provider = fetch.rows_provider
    fresh: dict[tuple[str, str], dict[dt.date, CalendarEntry]] = {}
    for entry in fetch.entries:
        fresh.setdefault((entry.series_id, entry.calendar_key), {})[entry.release_date] = entry
    async with pool.acquire() as conn, conn.transaction():
        for series_id, calendar_key in sorted(fetch.covered):
            wanted = fresh.get((series_id, calendar_key), {})
            live = {
                r["release_date"]: r["id"]
                for r in await conn.fetch(
                    """select id,release_date from public.macro_release_calendar
                       where provider=$1 and series_id=$2 and calendar_key=$3
                         and superseded_at is null""",
                    provider,
                    series_id,
                    calendar_key,
                )
            }
            gone = [i for day, i in live.items() if day >= today and day not in wanted]
            if gone:
                await conn.execute(
                    "update public.macro_release_calendar set superseded_at=$2"
                    " where id = any($1::bigint[])",
                    gone,
                    now,
                )
                superseded += len(gone)
            for day, entry in sorted(wanted.items()):
                if day in live:
                    unchanged += 1
                    continue
                await conn.execute(
                    """insert into public.macro_release_calendar
                       (provider,series_id,calendar_key,release_date,release_time,title,
                        reference_period,source_url,retrieved_at,known_at,source,
                        release_timezone,release_timezone_source)
                       values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$9,$10,$11,$12)""",
                    entry.provider,
                    entry.series_id,
                    entry.calendar_key,
                    entry.release_date,
                    entry.release_time,
                    entry.title,
                    entry.reference_period,
                    entry.source_url,
                    now,
                    entry.basis,
                    entry.release_timezone,
                    entry.release_timezone_source,
                )
                inserted += 1
    return StoreResult(inserted, superseded, unchanged)


async def _quarantine(pool: asyncpg.Pool, provider: str, raw: Any, now: dt.datetime) -> None:
    """Keep a rejected payload (schema drift) for inspection; it is never read as a calendar."""
    encoded = json.dumps(raw, sort_keys=True, default=str, separators=(",", ":")).encode()
    await pool.execute(
        """insert into public.macro_payload_archive
           (source,dataset,content_hash,known_at,source_url,payload)
           values($1,$2,$3,$4,$5,$6::jsonb) on conflict do nothing""",
        provider,
        f"{ENDPOINT}:quarantine",
        hashlib.sha256(encoded).hexdigest(),
        now,
        f"quarantine:{provider}",
        encoded.decode(),
    )


async def _provider_step(
    ctx: JobContext,
    deps: CalendarDeps,
    governor: PostgresRequestGovernor,
    now: dt.datetime,
    provider: str,
    fred_series: dict[str, str],
    bis_flows: dict[str, str],
    step: Any,
    bls_user_agent: str | None = None,
) -> None:
    decision = await provider_gate(ctx.pool, provider, ENDPOINT)
    if not decision.open:
        step.detail["skipped"] = f"access gate closed: {decision.reason}"
        await _coverage(
            ctx.pool,
            provider,
            AccessResult(AccessState(decision.state or "access_unverified"), decision.reason),
        )
        return

    async def before_call() -> None:
        await governor.reserve(provider, ENDPOINT, Decimal(1), CALENDAR_BUDGETS[provider], now=now)

    today = now.astimezone(dt.UTC).date()
    if provider == "fred":
        fetch = await fetch_fred(deps.http, deps.fred_key or "", fred_series, today, before_call)
    elif provider == "bls":
        fetch = await fetch_bls(deps.http, today, before_call, fred_series, bls_user_agent)
    elif provider == "eurostat":
        fetch = await fetch_eurostat(deps.http, today, before_call)
    elif provider == "ecb":
        fetch = await fetch_ecb(deps.http, today, before_call)
    elif provider == "ecfin":
        fetch = await fetch_ecfin(deps.http, today, before_call)
    elif provider == "cnb":
        fetch = await fetch_cnb(deps.http, today, before_call)
    else:
        fetch = await fetch_bis(deps.http, today, before_call, bis_flows)
    if fetch.calls:
        await record_access(
            ctx.pool,
            provider,
            ENDPOINT,
            fetch.access,
            status_code=fetch.status_code,
            response_bytes=fetch.response_bytes,
            credits=fetch.calls,
            job_id=ctx.job_id,
        )
    step.detail.update(
        calls=fetch.calls, entries=len(fetch.entries), access=fetch.access.state.value
    )
    if fetch.drift is not None:
        await _quarantine(ctx.pool, provider, fetch.raw, now)
        step.detail["quarantined"] = fetch.drift[:300]
    result = await store_calendar(ctx.pool, fetch, now)
    step.detail.update(
        inserted=result.inserted, superseded=result.superseded, unchanged=result.unchanged
    )
    if provider == "bls" and fetch.entries:
        mismatches = await bls_fred_mismatches(ctx.pool, fetch)
        if mismatches:
            # The BLS is the agency that publishes these releases: its date is the one the read
            # path returns. A disagreement with FRED is reported, never silently resolved.
            step.detail["bls_fred_date_mismatch"] = mismatches
            log.warning("BLS and FRED release dates disagree: %s", mismatches)
    access = fetch.access
    if access.state == AccessState.AVAILABLE and not fetch.entries:
        access = AccessResult(AccessState.NOT_COVERED, "calendar lists no upcoming release")
    await _coverage(ctx.pool, provider, access, [e.release_date for e in fetch.entries])


async def _coverage(
    pool: asyncpg.Pool, provider: str, access: AccessResult, upcoming: Iterable[dt.date] = ()
) -> None:
    days = sorted(set(upcoming))
    await update_coverage(
        pool,
        CoverageUpdate(
            provider=COVERAGE_PROVIDER,
            dataset=provider,
            entity_key="calendar",
            access=access,
            available_periods=len(days) if days else None,
            earliest_period=days[0] if days else None,
            latest_period=days[-1] if days else None,
        ),
    )


def _effective_bis_flow(series: Any) -> str:
    """The dataflow id ("WS_TC") a BIS series is really collected from, after key corrections."""
    flow: str = series.flow
    try:
        flow = pin_key("bis", series.flow, series.series_key, series.geography).flow
    except SdmxKeyError:
        pass  # the collector reports an unusable key; the calendar maps the configured flow
    parts = flow.split(",")
    return parts[1] if len(parts) >= 2 else flow


async def bls_fred_mismatches(
    pool: asyncpg.Pool, bls: CalendarFetch
) -> dict[str, dict[str, list[str]]]:
    """Per series, the upcoming dates that only one of BLS and FRED lists, compared inside the
    window both calendars cover (BLS lists a year ahead, FRED fewer dates)."""
    out: dict[str, dict[str, list[str]]] = {}
    for series_id, _calendar_key in sorted(bls.covered):
        theirs = {e.release_date for e in bls.entries if e.series_id == series_id}
        rows = await pool.fetch(
            """select release_date from public.macro_release_calendar
               where provider='fred' and series_id=$1 and calendar_key like 'release:%'
                 and superseded_at is null""",
            series_id,
        )
        fred = {r["release_date"] for r in rows}
        if not fred or not theirs:
            continue
        low = min(min(fred), min(theirs))
        high = min(max(fred), max(theirs))
        only_bls = sorted(d for d in theirs if low <= d <= high and d not in fred)
        only_fred = sorted(d for d in fred if low <= d <= high and d not in theirs)
        if only_bls or only_fred:
            out[series_id] = {
                "only_bls": [d.isoformat() for d in only_bls],
                "only_fred": [d.isoformat() for d in only_fred],
            }
    return out


async def run_release_calendar(ctx: JobContext, deps: CalendarDeps) -> None:
    stored = await load_active(ctx.pool, "jobs", JobsConfig)
    await ctx.record_config_version(stored.id if stored else None)
    jobs = stored.body if stored else JobsConfig()
    now = deps.now or dt.datetime.now(dt.UTC)
    governor = PostgresRequestGovernor(ctx.pool)
    fred_series = {s.key: s.series_id for s in jobs.macro.fred}
    # "BIS,WS_TC,2.0" -> "WS_TC": the dataflow a BIS series is really collected from.
    # The flow is the one actually requested: a stored config may still hold a key that
    # `pin_key` corrects to another dataflow (property_prices: WS_LONG_CPI -> WS_SPP).
    bis_flows = {
        s.key: _effective_bis_flow(s) for s in jobs.public_data.sdmx if s.provider == "bis"
    }
    for provider in CALENDAR_PROVIDERS:
        async with ctx.step(f"{provider}:{ENDPOINT}") as step:
            await _provider_step(
                ctx,
                deps,
                governor,
                now,
                provider,
                fred_series,
                bis_flows,
                step,
                bls_user_agent=deps.bls_user_agent,
            )


async def macro_release_calendar_handler(ctx: JobContext) -> None:
    settings = get_settings()
    async with httpx.AsyncClient(
        timeout=60.0, headers={"User-Agent": f"StewardAI/{__version__}"}
    ) as http:
        await run_release_calendar(
            ctx,
            CalendarDeps(
                http=http,
                fred_key=settings.fred_api_key.get_secret_value()
                if settings.fred_api_key
                else None,
                bls_user_agent=settings.bls_user_agent or settings.sec_user_agent,
            ),
        )


# ------------------------------------------------------------------------------------- reading
class NextRelease(BaseModel):
    provider: str
    series_id: str
    release_date: dt.date
    release_time: dt.time | None
    source_url: str
    title: str | None
    release_timezone: str | None = None
    release_timezone_source: Literal["stated_by_provider", "agency_location"] | None = None
    basis: Literal["provider_calendar", "published_rule"] = "provider_calendar"


def series_base(series_id: str) -> str:
    """The calendar key of an observation series: Eurostat series are '<key>:<geo>'."""
    return series_id.split(":", 1)[0]


async def next_releases(
    pool: asyncpg.Pool, keys: Iterable[tuple[str, str]], as_of: dt.datetime
) -> dict[tuple[str, str], NextRelease]:
    """Per (source, observation series id): the earliest stored release on or after the UTC date
    of `as_of`, from calendar rows known by `as_of` and not yet superseded at `as_of`. A row
    stored for the exact series id ('hicp:EA21': the euro area also has flash estimates) wins over
    the row of its dataset ('hicp'). For the BLS series (CPI, Employment Situation) the BLS's own
    rows (calendar key 'bls:...', with the 08:30 time) win over FRED's date-only rows: the agency
    that publishes the release is the authority on its date."""
    wanted = sorted(set(keys))
    ids = sorted(
        {(source, series_id) for source, series_id in wanted}
        | {(source, series_base(series_id)) for source, series_id in wanted}
    )
    if not ids:
        return {}
    as_of_utc = as_of.astimezone(dt.UTC) if as_of.tzinfo else as_of.replace(tzinfo=dt.UTC)
    rows = await pool.fetch(
        """select distinct on (c.provider,c.series_id)
                  c.provider,c.series_id,c.release_date,c.release_time,c.source_url,c.title,
                  c.release_timezone,c.release_timezone_source,c.source as basis
           from public.macro_release_calendar c
           join unnest($1::text[],$2::text[]) k(provider,series_id)
             on k.provider=c.provider and k.series_id=c.series_id
           where c.known_at <= $3 and (c.superseded_at is null or c.superseded_at > $3)
             and c.release_date >= $4
           order by c.provider,c.series_id,
                    (c.calendar_key like 'bls:%') desc,
                    c.release_date,c.release_time nulls last,c.id""",
        [i[0] for i in ids],
        [i[1] for i in ids],
        as_of_utc,
        as_of_utc.date(),
    )
    found = {(r["provider"], r["series_id"]): NextRelease(**dict(r)) for r in rows}
    out: dict[tuple[str, str], NextRelease] = {}
    for source, series_id in wanted:
        hit = found.get((source, series_id)) or found.get((source, series_base(series_id)))
        if hit is not None:
            out[(source, series_id)] = hit
    return out
