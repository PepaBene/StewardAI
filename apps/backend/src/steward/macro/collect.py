"""The `macro_collect` job: macro series into `macro_series`, and macro surprises.

- Eurostat (dissemination API, JSON-stat 2.0, no key): HICP, unemployment, GDP, industrial
  production and sentiment for the configured areas (CZ, DE, the euro area). The dataset's
  `updated` timestamp is the release date.
- FRED (key: FRED_API_KEY): US series. The request asks for the current vintage only, so
  FRED's `realtime_start` is the day of the fetch (the vintage's start), not the day the
  value was first published; it is stored as the release date because the job runs daily and
  a new period is first seen on (or just after) its release day.
- ECB Data Portal (no key): the policy rates; a new daily value is dated the day it is seen.

A series' first run backfills `history_years` of history; later runs fetch only the recent
observations (and the gap since the last stored one after an outage).

A surprise is evaluated only for an observation seen for the first time (its release day),
never for revisions of old periods: the new value against the previous period's, beyond the
series' band in the event rules config (0 means any change, used for policy rates, which then
count as central-bank decisions). Each surprise becomes a tier-1 macro event citing the
release date and the source URL; digests take every macro number from `macro_series`.
"""

import csv
import datetime as dt
import io
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import partial
from typing import Any

import asyncpg
import httpx

from steward import __version__
from steward.config.event_rules import EventRulesConfig
from steward.config.jobs import EcbSeries, EurostatSeries, FredSeries, JobsConfig, MacroSettings
from steward.config.store import load_active
from steward.jobs.runner import JobContext
from steward.news.synthetic import SyntheticEvent, SyntheticSource, raise_events
from steward.settings import get_settings

EUROSTAT_URL = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{dataset}"
EUROSTAT_PAGE = "https://ec.europa.eu/eurostat/databrowser/view/{dataset}/default/table"
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
FRED_PAGE = "https://fred.stlouisfed.org/series/{series_id}"
FRED_MAX_LIMIT = 100000  # the documented maximum `limit` of series/observations
ECB_URL = "https://data-api.ecb.europa.eu/service/data/{flow_key}"
ECB_PAGE = "https://data.ecb.europa.eu/data/datasets/{flow}/{key}"

MACRO_SOURCE = SyntheticSource("macro-data", "Macro releases (FRED, ECB, Eurostat)", "macro")
AREA_NAMES = {"EA21": "Euro area", "EA20": "Euro area", "EA": "Euro area", "EU27_2020": "EU"}


class MacroError(Exception):
    pass


@dataclass(frozen=True)
class Observation:
    series_id: str  # "<key>:<geo>" or "<key>"
    key: str
    source: str
    period: dt.date
    period_label: str
    value: Decimal
    label: str
    unit: str
    release_date: dt.date | None
    url: str
    policy_rate: bool = False


def parse_period(text: str) -> dt.date:
    """'2026-08' -> 1 Aug 2026; '2026-Q2' -> 1 Apr 2026; '2026' -> 1 Jan 2026; dates as-is."""
    if "-Q" in text:
        year, quarter = text.split("-Q")
        return dt.date(int(year), 3 * int(quarter) - 2, 1)
    parts = text.split("-")
    if len(parts) == 1:
        return dt.date(int(parts[0]), 1, 1)
    if len(parts) == 2:
        return dt.date(int(parts[0]), int(parts[1]), 1)
    return dt.date.fromisoformat(text)


def area_name(geo: str) -> str:
    return AREA_NAMES.get(geo, geo)


def parse_jsonstat(body: dict[str, Any], series: EurostatSeries) -> list[Observation]:
    """Every (geo, period) value of a JSON-stat dataset filtered to one series."""
    if "error" in body:
        raise MacroError(f"Eurostat: {body['error']}")
    ids: list[str] = body["id"]
    sizes: list[int] = body["size"]
    dims = body["dimension"]
    index = {d: dims[d]["category"]["index"] for d in ids}
    for d in ids:
        if isinstance(index[d], list):
            index[d] = {code: i for i, code in enumerate(index[d])}
    updated = body.get("updated")
    release = dt.date.fromisoformat(updated[:10]) if updated else None
    values: dict[str, float] = {str(k): v for k, v in (body.get("value") or {}).items()}
    strides = [1] * len(ids)
    for i in range(len(ids) - 2, -1, -1):
        strides[i] = strides[i + 1] * sizes[i + 1]
    geo_pos, time_pos = ids.index("geo"), ids.index("time")
    out = []
    for geo, gi in index["geo"].items():
        for period, ti in index["time"].items():
            flat = gi * strides[geo_pos] + ti * strides[time_pos]
            raw = values.get(str(flat))
            if raw is None:
                continue
            out.append(
                Observation(
                    series_id=f"{series.key}:{geo}",
                    key=series.key,
                    source="eurostat",
                    period=parse_period(period),
                    period_label=period,
                    value=Decimal(str(raw)),
                    label=f"{area_name(geo)} {series.label}",
                    unit=series.unit,
                    release_date=release,
                    url=EUROSTAT_PAGE.format(dataset=series.dataset),
                )
            )
    return out


async def fetch_eurostat(
    http: httpx.AsyncClient,
    series: EurostatSeries,
    geos: list[str],
    last_periods: int = 3,
    since: dt.date | None = None,
) -> list[Observation]:
    """The last `last_periods` periods, or with `since` every period from that year on
    (`sinceTimePeriod` takes a bare year for monthly, quarterly and annual datasets alike)."""
    params: list[tuple[str, str | int | float | bool | None]] = [
        *(("geo", g) for g in geos),
        *series.filters.items(),
        ("sinceTimePeriod", str(since.year)) if since else ("lastTimePeriod", last_periods),
    ]
    response = await http.get(EUROSTAT_URL.format(dataset=series.dataset), params=params)
    if response.status_code != 200:
        raise MacroError(f"Eurostat {series.dataset} returned HTTP {response.status_code}")
    return parse_jsonstat(response.json(), series)


def parse_fred(body: dict[str, Any], series: FredSeries) -> list[Observation]:
    out = []
    for row in body.get("observations", []):
        try:
            value = Decimal(row["value"])
        except (InvalidOperation, KeyError):
            continue  # FRED writes "." for a missing value
        out.append(
            Observation(
                series_id=series.key,
                key=series.key,
                source="fred",
                period=dt.date.fromisoformat(row["date"]),
                period_label=row["date"],
                value=value,
                label=series.label,
                unit=series.unit,
                # The fetch date for a current-vintage request (see the module docstring),
                # which is the release day only when the job runs on that day.
                release_date=dt.date.fromisoformat(row["realtime_start"])
                if row.get("realtime_start")
                else None,
                url=FRED_PAGE.format(series_id=series.series_id),
                policy_rate=series.policy_rate,
            )
        )
    return out


async def fetch_fred(
    http: httpx.AsyncClient,
    series: FredSeries,
    api_key: str,
    limit: int = 6,
    since: dt.date | None = None,
) -> list[Observation]:
    """The newest `limit` observations, or with `since` every observation from that date,
    paged by `offset` (FRED returns at most 100000 per request and reports `count`)."""
    params = {
        "series_id": series.series_id,
        "api_key": api_key,
        "file_type": "json",
        "units": series.units,
        "sort_order": "desc",
        "limit": str(limit),
    }
    if since is not None:
        params.update(
            observation_start=since.isoformat(), sort_order="asc", limit=str(FRED_MAX_LIMIT)
        )
    out: list[Observation] = []
    offset = 0
    while True:
        response = await http.get(FRED_URL, params=params)
        if response.status_code != 200:
            # Never echo the URL: it carries the API key.
            raise MacroError(f"FRED {series.series_id} returned HTTP {response.status_code}")
        body = response.json()
        out += parse_fred(body, series)
        if since is None:
            return out
        got = len(body.get("observations") or [])
        offset += got
        if got == 0 or offset >= int(body.get("count") or 0):
            return out
        params["offset"] = str(offset)


def parse_ecb_series(text: str, series: EcbSeries, seen_on: dt.date) -> list[Observation]:
    flow, key = series.flow_key.split("/", 1)
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            value = Decimal(row["OBS_VALUE"])
            period = dt.date.fromisoformat(row["TIME_PERIOD"])
        except (InvalidOperation, KeyError, ValueError):
            continue
        out.append(
            Observation(
                series_id=series.key,
                key=series.key,
                source="ecb",
                period=period,
                period_label=row["TIME_PERIOD"],
                value=value,
                label=series.label,
                unit=series.unit,
                release_date=seen_on,
                url=ECB_PAGE.format(flow=flow, key=f"{flow}.{key}"),
                policy_rate=series.policy_rate,
            )
        )
    return out


async def fetch_ecb_series(
    http: httpx.AsyncClient, series: EcbSeries, today: dt.date, since: dt.date | None = None
) -> list[Observation]:
    """The last 5 observations, or with `since` every observation from that date on."""
    window = {"startPeriod": since.isoformat()} if since else {"lastNObservations": "5"}
    response = await http.get(
        ECB_URL.format(flow_key=series.flow_key), params={**window, "format": "csvdata"}
    )
    if response.status_code != 200:
        raise MacroError(f"ECB {series.flow_key} returned HTTP {response.status_code}")
    return parse_ecb_series(response.text, series, today)


# Called with `since=` a date (fetch from there) or None (only the recent observations).
Fetch = Callable[..., Awaitable[list[Observation]]]


async def collect_window(
    pool: asyncpg.Pool,
    source: str,
    series_ids: list[str],
    fetch: Fetch,
    today: dt.date,
    history_years: int,
    detail: dict[str, Any],
) -> list[Observation]:
    """Fetch one configured series (one request may cover several geos' series ids).

    A series with nothing stored yet is backfilled from 1 January `history_years` ago (its
    history is stored as the baseline, never evaluated). Otherwise only the recent
    observations are fetched; when even the oldest of those is newer than the newest stored
    one (the job did not run for a while), the gap is fetched from the last stored period.
    """
    rows = await pool.fetch(
        "select series_id, max(period) as last from public.macro_series where source = $1"
        " and series_id = any($2::text[]) group by series_id",
        source,
        series_ids,
    )
    latest: dict[str, dt.date] = {r["series_id"]: r["last"] for r in rows}
    if any(sid not in latest for sid in series_ids):
        start = dt.date(today.year - history_years, 1, 1)
        detail["backfill_from"] = start.isoformat()
        return await fetch(since=start)
    obs = await fetch(since=None)
    gaps = []
    for sid in series_ids:
        first = min((o.period for o in obs if o.series_id == sid), default=None)
        if first is not None and first > latest[sid]:
            gaps.append(latest[sid])
    if gaps:
        detail["gap_from"] = min(gaps).isoformat()
        obs = await fetch(since=min(gaps))
    return obs


# ---------------------------------------------------------------------------
# Store and evaluate
# ---------------------------------------------------------------------------


def _fmt(value: Decimal, unit: str) -> str:
    text = f"{value.normalize():f}"
    return f"{text}%" if unit == "%" else f"{text} {unit}"


def surprise(
    new: Observation, previous: Observation | None, band: float | None
) -> SyntheticEvent | None:
    """The macro event for a newly released value, when it moves beyond the band."""
    if previous is None or band is None:
        return None
    change = new.value - previous.value
    if abs(change) <= Decimal(str(band)) and not (band == 0 and change != 0):
        return None
    if band == 0 and change == 0:
        return None
    event_type = "central_bank" if new.policy_rate else "macro_surprise"
    released = new.release_date.isoformat() if new.release_date else "unknown date"
    verb = "changed" if new.policy_rate else "released"
    headline = (
        f"{new.label}: {_fmt(new.value, new.unit)} for {new.period_label} "
        f"(previous {_fmt(previous.value, previous.unit)})"
    )
    summary = (
        f"{new.label} {verb} on {released}: {_fmt(new.value, new.unit)} for "
        f"{new.period_label}, against {_fmt(previous.value, previous.unit)} for "
        f"{previous.period_label}. Source: {new.url}"
    )
    return SyntheticEvent(
        ticker=None,
        event_type=event_type,
        headline=headline,
        summary=summary,
        url=f"{new.url}#{new.series_id}:{new.period_label}",
        event_date=new.release_date,
        outlet={"eurostat": "ec.europa.eu", "fred": "stlouisfed.org", "ecb": "ecb.europa.eu"}[
            new.source
        ],
        outlet_name={"eurostat": "Eurostat", "fred": "FRED", "ecb": "ECB Data Portal"}[new.source],
    )


async def store_and_evaluate(
    pool: asyncpg.Pool, observations: list[Observation], bands: dict[str, float]
) -> tuple[int, list[SyntheticEvent]]:
    """Insert new observations (revisions update the value but are never evaluated) and
    return (new count, surprise events). A series with nothing stored yet is only stored."""
    events: list[SyntheticEvent] = []
    new_count = 0
    by_series: dict[str, list[Observation]] = {}
    for o in observations:
        by_series.setdefault(o.series_id, []).append(o)
    for series_id, obs in by_series.items():
        obs.sort(key=lambda o: o.period)
        # A series' first run stores the backfilled history as the baseline: comparing
        # those old observations with each other would raise false surprises.
        baseline = not await pool.fetchval(
            "select exists (select 1 from public.macro_series where series_id = $1"
            " and source = $2)",
            series_id,
            obs[0].source,
        )
        for o in obs:
            inserted = await pool.fetchval(
                "insert into public.macro_series (series_id, source, period, value, label, unit,"
                " release_date, url) values ($1, $2, $3, $4, $5, $6, $7, $8)"
                " on conflict (series_id, source, period) do update set value = excluded.value,"
                " fetched_at = now() returning (xmax = 0)",
                series_id,
                o.source,
                o.period,
                o.value,
                o.label,
                o.unit,
                o.release_date,
                o.url,
            )
            if not inserted:
                continue
            new_count += 1
            if baseline:
                continue
            prev = await pool.fetchrow(
                "select period, value, unit from public.macro_series where series_id = $1"
                " and source = $2 and period < $3 order by period desc limit 1",
                series_id,
                o.source,
                o.period,
            )
            previous = (
                Observation(
                    series_id,
                    o.key,
                    o.source,
                    prev["period"],
                    _label(prev["period"], o),
                    prev["value"],
                    o.label,
                    prev["unit"] or o.unit,
                    None,
                    o.url,
                    o.policy_rate,
                )
                if prev
                else None
            )
            event = surprise(o, previous, bands.get(o.key))
            if event is not None:
                events.append(event)
    return new_count, events


def _label(period: dt.date, like: Observation) -> str:
    if "-Q" in like.period_label:
        return f"{period.year}-Q{(period.month - 1) // 3 + 1}"
    if len(like.period_label) == 7:
        return f"{period.year}-{period.month:02d}"
    return period.isoformat()


@dataclass
class MacroDeps:
    http: httpx.AsyncClient
    fred_key: str | None = None
    today: dt.date | None = None
    now: dt.datetime | None = None


async def run_macro_collect(ctx: JobContext, deps: MacroDeps) -> None:
    pool = ctx.pool
    today = deps.today or dt.datetime.now(dt.UTC).date()
    now = deps.now or dt.datetime.now(dt.UTC)
    async with ctx.step("load_config") as step:
        jobs = await load_active(pool, "jobs", JobsConfig)
        rules = await load_active(pool, "event_rules", EventRulesConfig)
        await ctx.record_config_version(jobs.id if jobs else None)
        await ctx.record_config_version(rules.id if rules else None)
        settings: MacroSettings = (jobs.body if jobs else JobsConfig()).macro
        bands = (rules.body if rules else EventRulesConfig()).macro_surprise_bands
        step.detail["series"] = (
            [s.key for s in settings.eurostat]
            + [s.key for s in settings.fred]
            + [s.key for s in settings.ecb]
        )
    events: list[SyntheticEvent] = []
    for series in settings.eurostat:
        async with ctx.step(f"eurostat:{series.key}") as step:
            try:
                obs = await collect_window(
                    pool,
                    "eurostat",
                    [f"{series.key}:{geo}" for geo in settings.geos],
                    partial(fetch_eurostat, deps.http, series, settings.geos),
                    today,
                    settings.history_years,
                    step.detail,
                )
            except (MacroError, httpx.HTTPError, KeyError, ValueError) as exc:
                step.detail["error"] = str(exc)[:300]
                continue
            count, found = await store_and_evaluate(pool, obs, bands)
            events += found
            step.detail.update(observations=len(obs), new=count, surprises=len(found))
    for fred in settings.fred:
        async with ctx.step(f"fred:{fred.key}") as step:
            if not deps.fred_key:
                step.detail["skipped"] = "FRED_API_KEY is not set"
                continue
            try:
                obs = await collect_window(
                    pool,
                    "fred",
                    [fred.key],
                    partial(fetch_fred, deps.http, fred, deps.fred_key),
                    today,
                    settings.history_years,
                    step.detail,
                )
            except (MacroError, httpx.HTTPError, KeyError, ValueError) as exc:
                step.detail["error"] = str(exc)[:300]
                continue
            count, found = await store_and_evaluate(pool, obs, bands)
            events += found
            step.detail.update(observations=len(obs), new=count, surprises=len(found))
    for ecb in settings.ecb:
        async with ctx.step(f"ecb:{ecb.key}") as step:
            try:
                obs = await collect_window(
                    pool,
                    "ecb",
                    [ecb.key],
                    partial(fetch_ecb_series, deps.http, ecb, today),
                    today,
                    settings.history_years,
                    step.detail,
                )
            except (MacroError, httpx.HTTPError) as exc:
                step.detail["error"] = str(exc)[:300]
                continue
            count, found = await store_and_evaluate(pool, obs, bands)
            events += found
            step.detail.update(observations=len(obs), new=count, surprises=len(found))
    async with ctx.step("surprises") as step:
        created = await raise_events(pool, MACRO_SOURCE, events, now, ctx.job_id)
        step.detail.update(events=[e.headline for e in events], created=created)


async def macro_collect_handler(ctx: JobContext) -> None:
    settings = get_settings()
    async with httpx.AsyncClient(
        timeout=60.0, headers={"User-Agent": f"StewardAI/{__version__}"}
    ) as http:
        await run_macro_collect(
            ctx,
            MacroDeps(
                http=http,
                fred_key=settings.fred_api_key.get_secret_value()
                if settings.fred_api_key
                else None,
            ),
        )
