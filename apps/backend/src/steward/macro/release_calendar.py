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
from typing import Any

import asyncpg
import httpx
from pydantic import BaseModel

from steward import __version__
from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.jobs.runner import JobContext
from steward.settings import get_settings
from steward.sources.base import AccessResult, AccessState
from steward.sources.governor import Budget, PostgresRequestGovernor
from steward.sources.release_calendar import (
    CalendarEntry,
    CalendarFetch,
    fetch_bis,
    fetch_ecb,
    fetch_eurostat,
    fetch_fred,
)
from steward.sources.store import CoverageUpdate, provider_gate, record_access, update_coverage

log = logging.getLogger(__name__)

ENDPOINT = "release_calendar"
COVERAGE_PROVIDER = "macro_release_calendar"
CALENDAR_PROVIDERS = ("fred", "eurostat", "ecb", "bis")

# The collector's own hard stop per provider (the ledger rows in provider_access declare FRED's
# published 120 requests per minute; the others publish no limit). A daily run needs 1 request
# (Eurostat, BIS), 3 (ECB) or 2 per series (FRED), so these leave room for a few manual runs.
CALENDAR_BUDGETS: dict[str, Budget] = {
    "fred": Budget(per_minute=Decimal(30), daily=Decimal(60)),
    "eurostat": Budget(daily=Decimal(6)),
    "ecb": Budget(daily=Decimal(15)),
    "bis": Budget(daily=Decimal(6)),
}


@dataclass(slots=True)
class CalendarDeps:
    http: httpx.AsyncClient
    fred_key: str | None = None
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
                    fetch.provider,
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
                        reference_period,source_url,retrieved_at,known_at)
                       values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$9)""",
                    entry.provider,
                    entry.series_id,
                    entry.calendar_key,
                    entry.release_date,
                    entry.release_time,
                    entry.title,
                    entry.reference_period,
                    entry.source_url,
                    now,
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
    step: Any,
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
    elif provider == "eurostat":
        fetch = await fetch_eurostat(deps.http, today, before_call)
    elif provider == "ecb":
        fetch = await fetch_ecb(deps.http, today, before_call)
    else:
        fetch = await fetch_bis(deps.http, today, before_call)
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


async def run_release_calendar(ctx: JobContext, deps: CalendarDeps) -> None:
    stored = await load_active(ctx.pool, "jobs", JobsConfig)
    await ctx.record_config_version(stored.id if stored else None)
    jobs = stored.body if stored else JobsConfig()
    now = deps.now or dt.datetime.now(dt.UTC)
    governor = PostgresRequestGovernor(ctx.pool)
    fred_series = {s.key: s.series_id for s in jobs.macro.fred}
    for provider in CALENDAR_PROVIDERS:
        async with ctx.step(f"{provider}:{ENDPOINT}") as step:
            await _provider_step(ctx, deps, governor, now, provider, fred_series, step)


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


def series_base(series_id: str) -> str:
    """The calendar key of an observation series: Eurostat series are '<key>:<geo>'."""
    return series_id.split(":", 1)[0]


async def next_releases(
    pool: asyncpg.Pool, keys: Iterable[tuple[str, str]], as_of: dt.datetime
) -> dict[tuple[str, str], NextRelease]:
    """Per (source, observation series id): the earliest stored release on or after the UTC date
    of `as_of`, from calendar rows known by `as_of` and not yet superseded at `as_of`."""
    pairs = sorted({(source, series_base(series_id)) for source, series_id in keys})
    if not pairs:
        return {}
    as_of_utc = as_of.astimezone(dt.UTC) if as_of.tzinfo else as_of.replace(tzinfo=dt.UTC)
    rows = await pool.fetch(
        """select distinct on (c.provider,c.series_id)
                  c.provider,c.series_id,c.release_date,c.release_time,c.source_url,c.title
           from public.macro_release_calendar c
           join unnest($1::text[],$2::text[]) k(provider,series_id)
             on k.provider=c.provider and k.series_id=c.series_id
           where c.known_at <= $3 and (c.superseded_at is null or c.superseded_at > $3)
             and c.release_date >= $4
           order by c.provider,c.series_id,c.release_date,c.release_time nulls last,c.id""",
        [p[0] for p in pairs],
        [p[1] for p in pairs],
        as_of_utc,
        as_of_utc.date(),
    )
    found = {(r["provider"], r["series_id"]): NextRelease(**dict(r)) for r in rows}
    return {
        (source, series_id): found[(source, series_base(series_id))]
        for source, series_id in keys
        if (source, series_base(series_id)) in found
    }
