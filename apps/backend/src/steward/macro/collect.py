"""The `macro_collect` job: macro series into `macro_series`, and macro surprises.

- Eurostat (dissemination API, JSON-stat 2.0, no key): HICP, unemployment, GDP, industrial
  production and sentiment for the configured areas (CZ, DE, the euro area). The dataset's
  `updated` timestamp is the release date.
- FRED (key: FRED_API_KEY): US series. The request asks for the current vintage only, so
  FRED's `realtime_start` is the day of the fetch (the vintage's start), not the day the
  value was first published; it is stored as the release date because the job runs daily and
  a new period is first seen on (or just after) its release day.
- ECB Data Portal (no key): the policy rates; a new daily value is dated the day it is seen.
- ČNB (no key, steward.sources.cnb): CZEONIA, the CZK overnight rate (daily, released the
  next business day; the risk-free rate of the portfolio's Sharpe ratio, never evaluated
  for surprises), and the 2-week repo rate's change history (a policy rate: each new
  change is a central-bank decision).

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
import hashlib
import io
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from decimal import Decimal
from functools import partial
from typing import Any

import asyncpg
import httpx

from steward import __version__
from steward.config.event_rules import EventRulesConfig
from steward.config.jobs import (
    CnbSeries,
    EcbSeries,
    EurostatSeries,
    FredSeries,
    JobsConfig,
    MacroSettings,
)
from steward.config.store import load_active
from steward.jobs.runner import JobContext
from steward.macro.providers import (
    finite_decimal,
    frequency_of,
    parse_fred_vintages,
    parse_period,
    sdmx_unit_multiplier,
)
from steward.macro.store import MacroRow, store_macro_rows, upsert_series_metadata
from steward.news.synthetic import SyntheticEvent, SyntheticSource, raise_events
from steward.settings import get_settings
from steward.sources import cnb
from steward.sources.base import AccessResult, AccessState
from steward.sources.store import CoverageUpdate, update_coverage

EUROSTAT_URL = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{dataset}"
EUROSTAT_PAGE = "https://ec.europa.eu/eurostat/databrowser/view/{dataset}/default/table"
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
FRED_PAGE = "https://fred.stlouisfed.org/series/{series_id}"
FRED_MAX_LIMIT = 100000  # the documented maximum `limit` of series/observations
ECB_URL = "https://data-api.ecb.europa.eu/service/data/{flow_key}"
ECB_PAGE = "https://data.ecb.europa.eu/data/datasets/{flow}/{key}"

MACRO_SOURCE = SyntheticSource("macro-data", "Macro releases (FRED, ECB, Eurostat, ČNB)", "macro")
# Per `Observation.source`: the outlet host and name a macro event cites.
OUTLETS: dict[str, tuple[str, str]] = {
    "eurostat": ("ec.europa.eu", "Eurostat"),
    "fred": ("stlouisfed.org", "FRED"),
    "ecb": ("ecb.europa.eu", "ECB Data Portal"),
    "cnb": ("cnb.cz", "Czech National Bank"),
}
AREA_NAMES = {"EA21": "Euro area", "EA20": "Euro area", "EA": "Euro area", "EU27_2020": "EU"}
log = logging.getLogger(__name__)

# D2.1 semantics, written beside every observation. Release calendars name the publisher's
# calendar (the exact dates are the provider's); nothing here is inferred from the data.
RELEASE_CALENDARS = {
    "eurostat": "Eurostat euro indicators release calendar; dataset `updated` = release date",
    "fred": "FRED release calendar for the series' release; fetch date = first sight",
    "ecb": "ECB statistical calendar; release date = day first seen (unknown for backfills)",
    "cnb": "CNB: CZEONIA on the next business day (about 09:30); repo rate on decision days",
}
EUROSTAT_TRANSFORMATION = {
    "RCH_A": "annual rate of change",
    "RCH_M": "monthly rate of change",
    "CLV_PCH_PRE": "chain-linked volumes, change on previous period",
    "PCH_PRE": "change on previous period",
    "PCH_SM": "change on same period of previous year",
}
EUROSTAT_ADJUSTMENT = {
    "SA": "seasonally adjusted",
    "NSA": "not seasonally adjusted",
    "SCA": "seasonally and calendar adjusted",
    "CA": "calendar adjusted",
}
FRED_TRANSFORMATION = {
    "pch": "percent change",
    "pc1": "percent change from year ago",
    "chg": "change",
}
GEOGRAPHY_DEFINITIONS = {
    "EA21": "Euro area, 21 members (Eurostat aggregate, from 2026)",
    "EA20": "Euro area, 20 members (Eurostat aggregate)",
    "EU27_2020": "European Union, 27 members (Eurostat aggregate)",
    "US": "United States (national series)",
    "CZ": "Czechia (national series)",
    "WLD": "World price (a global benchmark, not a country series)",
}


def is_commodity(key: str) -> bool:
    """Commodity price series are world benchmarks: they must never be read as US data."""
    return key.startswith("commodity_")


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
    # The provider's own vintage label (Eurostat `updated`, FRED `realtime_start`), if any.
    vintage: str | None = None
    unit_multiplier: Decimal = Decimal(1)
    seasonal_adjustment: str | None = None
    transformation: str | None = None
    release_calendar: str | None = None
    geography_definition: str | None = None


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
    filters = series.filters
    values: dict[str, float] = {str(k): v for k, v in (body.get("value") or {}).items()}
    strides = [1] * len(ids)
    for i in range(len(ids) - 2, -1, -1):
        strides[i] = strides[i + 1] * sizes[i + 1]
    geo_pos, time_pos = ids.index("geo"), ids.index("time")
    out = []
    for geo, gi in index["geo"].items():
        for period, ti in index["time"].items():
            flat = gi * strides[geo_pos] + ti * strides[time_pos]
            value = finite_decimal(values.get(str(flat)))
            if value is None:
                continue
            out.append(
                Observation(
                    series_id=f"{series.key}:{geo}",
                    key=series.key,
                    source="eurostat",
                    period=parse_period(period),
                    period_label=period,
                    value=value,
                    label=f"{area_name(geo)} {series.label}",
                    unit=series.unit,
                    release_date=release,
                    url=EUROSTAT_PAGE.format(dataset=series.dataset),
                    vintage=updated,
                    seasonal_adjustment=EUROSTAT_ADJUSTMENT.get(filters.get("s_adj", "")),
                    transformation=EUROSTAT_TRANSFORMATION.get(filters.get("unit", "")),
                    release_calendar=RELEASE_CALENDARS["eurostat"],
                    geography_definition=GEOGRAPHY_DEFINITIONS.get(geo, f"Eurostat geo code {geo}"),
                )
            )
    return out


async def fetch_eurostat(
    http: httpx.AsyncClient,
    series: EurostatSeries,
    geos: list[str],
    last_periods: int = 3,
    since: dt.date | None = None,
    pool: asyncpg.Pool | None = None,
    known_at: dt.datetime | None = None,
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
    body = response.json()
    retrieved_at = known_at or dt.datetime.now(dt.UTC)
    if pool is not None:
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        await pool.execute(
            """insert into public.macro_payload_archive
               (source,dataset,content_hash,known_at,source_url,payload)
               values('eurostat',$1,$2,$3,$4,$5::jsonb) on conflict do nothing""",
            series.dataset,
            hashlib.sha256(encoded).hexdigest(),
            retrieved_at,
            str(response.url),
            encoded.decode(),
        )
    return parse_jsonstat(body, series)


def parse_fred(body: dict[str, Any], series: FredSeries) -> list[Observation]:
    out = []
    for row in body.get("observations", []):
        value = finite_decimal(row.get("value"))
        if value is None:
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
                vintage=row.get("realtime_start"),
                transformation=FRED_TRANSFORMATION.get(series.units),
                release_calendar=RELEASE_CALENDARS["fred"],
                geography_definition=GEOGRAPHY_DEFINITIONS[
                    "WLD" if is_commodity(series.key) else "US"
                ],
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


# Under FRED's 2,000 vintage dates for a daily series (about 260 a year).
ALFRED_WINDOW_YEARS = 4


def join_vintage_windows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One real-time window per observation and value. FRED clips each window's rows to the
    requested range, so a value that held across two requests comes back as two rows meeting
    at the boundary; joined, it is not mistaken for a revision on the boundary day."""
    out: list[dict[str, Any]] = []
    last: dict[str, dict[str, Any]] = {}
    for row in sorted(rows, key=lambda r: (r.get("date", ""), r.get("realtime_start", ""))):
        prev = last.get(row.get("date", ""))
        if (
            prev is not None
            and prev.get("value") == row.get("value")
            and prev.get("realtime_end")
            and row.get("realtime_start")
            and dt.date.fromisoformat(prev["realtime_end"]) + dt.timedelta(days=1)
            == dt.date.fromisoformat(row["realtime_start"])
        ):
            prev["realtime_end"] = row.get("realtime_end")
            continue
        joined = dict(row)
        out.append(joined)
        last[row.get("date", "")] = joined
    return out


async def store_fred_vintages(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    series: FredSeries,
    api_key: str,
    today: dt.date,
    history_years: int,
) -> int | None:
    """Append actual ALFRED real-time windows; never rewrite an earlier vintage.

    `output_type=1` returns one row per observation and real-time window (value, start, end),
    which is what holds revisions; `output_type=4` is *initial release only* and would never
    show one. FRED refuses a real-time range for a transformed series (`us_cpi`, `pc1`), so
    those keep no revision history (None) rather than index levels under a `%` unit. A daily
    series has more than FRED's 2,000 vintage dates in ten years; it is read in shorter windows."""
    if series.units != "lin":
        return None
    start = dt.date(today.year - history_years, 1, 1)

    async def window(lo: dt.date, hi: dt.date) -> httpx.Response:
        return await http.get(
            FRED_URL,
            params={
                "series_id": series.series_id,
                "api_key": api_key,
                "file_type": "json",
                "output_type": "1",
                "units": series.units,
                "observation_start": start.isoformat(),
                "realtime_start": lo.isoformat(),
                "realtime_end": hi.isoformat(),
                "limit": str(FRED_MAX_LIMIT),
            },
        )

    response = await window(start, today)
    rows: list[dict[str, Any]]
    if response.status_code == 400 and "vintage dates" in response.text:
        rows = []
        lo = start
        while lo <= today:
            hi = min(dt.date(lo.year + ALFRED_WINDOW_YEARS, lo.month, lo.day), today)
            part = await window(lo, hi)
            if part.status_code != 200:
                raise MacroError(f"ALFRED {series.series_id} returned HTTP {part.status_code}")
            rows += part.json().get("observations") or []
            lo = hi + dt.timedelta(days=1)
        rows = join_vintage_windows(rows)
    elif response.status_code != 200:
        raise MacroError(f"ALFRED {series.series_id} returned HTTP {response.status_code}")
    else:
        rows = response.json().get("observations") or []
    skipped: list[str] = []
    points = parse_fred_vintages(
        {"observations": rows},
        series_id=series.key,
        unit=series.unit,
        source_url=FRED_PAGE.format(series_id=series.series_id),
        skipped=skipped,
    )
    if skipped:
        log.warning("ALFRED %s: skipped %d unparseable rows", series.series_id, len(skipped))
    result = await store_macro_rows(
        pool,
        [
            MacroRow.from_point(
                replace(point, transformation=FRED_TRANSFORMATION.get(series.units)),
                point.release_at or dt.datetime.now(dt.UTC),
            )
            for point in points
        ],
        vintage_keyed=True,
    )
    return result.inserted


ECB_ADJUSTMENT_COLUMNS = ("SEASONAL_ADJUSTMENT", "ADJUSTMENT", "ADJUSTMENT_INDICATOR")


def parse_ecb_series(
    text: str,
    series: EcbSeries,
    seen_on: dt.date | None,
    skipped: list[str] | None = None,
) -> list[Observation]:
    """Rows of the Data Portal's `csvdata` format. TIME_PERIOD may be daily (`2026-07-01`),
    monthly (`2026-07`), quarterly (`2026-Q2`), half-yearly, weekly or annual. A row whose
    period or value cannot be read is appended to `skipped` instead of vanishing.
    `seen_on` is the release date to record (None: unknown, as for a backfill)."""
    flow, key = series.flow_key.split("/", 1)
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        label = (row.get("TIME_PERIOD") or "").strip()
        value = finite_decimal(row.get("OBS_VALUE"))
        try:
            period = parse_period(label)
        except ValueError:
            period = None
        if period is None or value is None:
            if skipped is not None:
                skipped.append(f"{label or '?'}: {row.get('OBS_VALUE')!r}")
            continue
        adjustment = next((row[c] for c in ECB_ADJUSTMENT_COLUMNS if row.get(c)), None)
        out.append(
            Observation(
                series_id=series.key,
                key=series.key,
                source="ecb",
                period=period,
                period_label=label,
                value=value,
                label=series.label,
                unit=series.unit,
                release_date=seen_on,
                url=ECB_PAGE.format(flow=flow, key=f"{flow}.{key}"),
                policy_rate=series.policy_rate,
                unit_multiplier=sdmx_unit_multiplier(row.get("UNIT_MULT")),
                seasonal_adjustment=adjustment,
                transformation=row.get("TRANSFORMATION") or None,
                release_calendar=RELEASE_CALENDARS["ecb"],
                geography_definition="Euro area (ECB REF_AREA U2: changing composition)",
            )
        )
    return out


async def fetch_ecb_series(
    http: httpx.AsyncClient, series: EcbSeries, today: dt.date, since: dt.date | None = None
) -> list[Observation]:
    """The last 5 observations, or with `since` every observation from that date on.

    The Data Portal gives no release date. A new observation is dated the day it is first
    seen, but a backfill or gap fill is history already published: its release date is
    unknown (None), not the day the job happened to run."""
    window = {"startPeriod": since.isoformat()} if since else {"lastNObservations": "5"}
    response = await http.get(
        ECB_URL.format(flow_key=series.flow_key), params={**window, "format": "csvdata"}
    )
    if response.status_code != 200:
        raise MacroError(f"ECB {series.flow_key} returned HTTP {response.status_code}")
    skipped: list[str] = []
    out = parse_ecb_series(response.text, series, None if since else today, skipped)
    if skipped:
        log.warning(
            "ECB %s: skipped %d row(s), kept %d (first skipped: %s)",
            series.flow_key,
            len(skipped),
            len(out),
            skipped[0],
        )
        if not out:
            raise MacroError(
                f"ECB {series.flow_key}: all {len(skipped)} rows were skipped "
                f"(first: {skipped[0]}); the payload format is not understood"
            )
    return out


def _next_business_day(day: dt.date) -> dt.date:
    nxt = day + dt.timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += dt.timedelta(days=1)
    return nxt


def cnb_observations(
    series: CnbSeries, points: list[cnb.RatePoint], today: dt.date
) -> list[Observation]:
    """ČNB rate points as observations. CZEONIA is published the business day after the
    day it is for (ČNB publishes D+1 by about 9:30), which is its release date. A repo
    rate row is the day a new rate became valid; its release date is that day, or the day
    it was first seen when the file lists it in advance."""
    url = cnb.CZEONIA_PAGE if series.series == "czeonia" else cnb.REPO_RATE_URL
    return [
        Observation(
            series_id=series.key,
            key=series.key,
            source="cnb",
            period=p.date,
            period_label=p.date.isoformat(),
            value=p.rate,
            label=series.label,
            unit=series.unit,
            release_date=_next_business_day(p.date)
            if series.series == "czeonia"
            else min(p.date, today),
            url=url,
            policy_rate=series.policy_rate,
        )
        for p in points
    ]


async def fetch_cnb_series(
    http: httpx.AsyncClient, series: CnbSeries, today: dt.date, since: dt.date | None = None
) -> list[Observation]:
    """CZEONIA: the current year (or every year from `since`). Repo rate: the one history
    file, cut to `since`, or its last 5 changes."""
    if series.series == "czeonia":
        points = await cnb.fetch_czeonia(http, today, since)
    else:
        points = await cnb.fetch_repo_rate(http)
        points = [p for p in points if p.date >= since] if since else points[-5:]
    return cnb_observations(series, points, today)


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
        outlet=OUTLETS[new.source][0],
        outlet_name=OUTLETS[new.source][1],
    )


def _geography(series_id: str, source: str) -> str:
    if ":" in series_id:
        return series_id.rsplit(":", 1)[1]
    if source == "fred" and is_commodity(series_id):
        return "WLD"
    return {"fred": "US", "ecb": "EA", "cnb": "CZ"}.get(source, "unknown")


async def store_and_evaluate(
    pool: asyncpg.Pool,
    observations: list[Observation],
    bands: dict[str, float],
    known_at: dt.datetime | None = None,
) -> tuple[int, list[SyntheticEvent]]:
    """Store observations and return (new count, surprise events).

    `macro_series` keeps the current value per period; `macro_observation` keeps every
    distinct value (a repeated fetch of the same value inserts nothing, a changed one is the
    next revision). A surprise is evaluated only for a period newer than everything stored for
    the series: a revision never is, nor is an older period filled in late. A series with
    nothing stored yet is only stored (its backfill is the baseline)."""
    events: list[SyntheticEvent] = []
    new_count = 0
    known_at = known_at or dt.datetime.now(dt.UTC)
    by_series: dict[str, list[Observation]] = {}
    for o in observations:
        by_series.setdefault(o.series_id, []).append(o)
    for series_id, obs in by_series.items():
        obs.sort(key=lambda o: o.period)
        source = obs[0].source
        geography = _geography(series_id, source)
        await update_coverage(
            pool,
            CoverageUpdate(
                provider=source,
                dataset=obs[0].key,
                entity_key=geography,
                access=AccessResult(AccessState.AVAILABLE),
                available_periods=len({item.period for item in obs}),
                earliest_period=min(item.period for item in obs),
                latest_period=max(item.period for item in obs),
            ),
        )
        stored = {
            r["period"]: r
            for r in await pool.fetch(
                "select period, value, unit from public.macro_series where series_id = $1"
                " and source = $2",
                series_id,
                source,
            )
        }
        # A series' first run stores the backfilled history as the baseline: comparing
        # those old observations with each other would raise false surprises.
        baseline = not stored
        newest_stored = max(stored) if stored else None
        await store_macro_rows(
            pool,
            [
                MacroRow(
                    o.key,
                    o.source,
                    _geography(o.series_id, o.source),
                    o.period,
                    o.value,
                    o.unit,
                    frequency_of(o.period_label),
                    dt.datetime.combine(o.release_date, dt.time(), dt.UTC)
                    if o.release_date is not None and o.source != "fred"
                    else None,
                    known_at,
                    o.vintage or known_at.date().isoformat(),
                    o.url,
                    {"label": o.label, "period_label": o.period_label},
                    o.unit_multiplier,
                    o.seasonal_adjustment,
                    o.transformation,
                )
                for o in obs
            ],
        )
        await pool.executemany(
            "insert into public.macro_series (series_id, source, period, value, label, unit,"
            " release_date, url) values ($1, $2, $3, $4, $5, $6, $7, $8)"
            " on conflict (series_id, source, period) do update set value = excluded.value,"
            " fetched_at = now()",
            [
                (series_id, o.source, o.period, o.value, o.label, o.unit, o.release_date, o.url)
                for o in obs
            ],
        )
        await upsert_series_metadata(
            pool,
            source=source,
            series_id=obs[0].key,
            label=obs[0].label,
            definition=obs[0].label,
            frequency=frequency_of(obs[0].period_label),
            unit=obs[0].unit,
            consumer="macro digests and surprise events",
            unit_multiplier=obs[0].unit_multiplier,
            seasonal_adjustment=obs[0].seasonal_adjustment,
            transformation=obs[0].transformation,
            geography_definition=obs[0].geography_definition,
            release_calendar=obs[0].release_calendar,
        )
        history = sorted((p, r["value"], r["unit"]) for p, r in stored.items())
        for o in obs:
            if o.period in stored:
                continue  # a revision of a stored period
            new_count += 1
            earlier = [h for h in history if h[0] < o.period]
            history.append((o.period, o.value, o.unit))
            history.sort()
            if baseline or (newest_stored is not None and o.period < newest_stored):
                continue
            prev = earlier[-1] if earlier else None
            previous = (
                Observation(
                    series_id,
                    o.key,
                    o.source,
                    prev[0],
                    _label(prev[0], o),
                    prev[1],
                    o.label,
                    prev[2] or o.unit,
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


async def mark_macro_coverage(
    pool: asyncpg.Pool,
    provider: str,
    dataset: str,
    geographies: list[str],
    state: AccessState,
    reason: str,
) -> None:
    for geography in geographies:
        await update_coverage(
            pool,
            CoverageUpdate(
                provider=provider,
                dataset=dataset,
                entity_key=geography,
                access=AccessResult(state, reason),
            ),
        )


def _label(period: dt.date, like: Observation) -> str:
    frequency = frequency_of(like.period_label)
    if frequency == "quarterly":
        return f"{period.year}-Q{(period.month - 1) // 3 + 1}"
    if frequency == "semiannual":
        return f"{period.year}-S{1 if period.month < 7 else 2}"
    if frequency == "weekly":
        iso = period.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if frequency == "monthly":
        return f"{period.year}-{period.month:02d}"
    if frequency == "annual":
        return str(period.year)
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
            + [s.key for s in settings.cnb]
        )
    events: list[SyntheticEvent] = []
    created = 0

    async def raise_now(found: list[SyntheticEvent]) -> None:
        """Raise a series' events right after it is stored: a later failure (another series,
        ALFRED) can then never lose them - they are not re-derived on the next run."""
        nonlocal created
        created += len(await raise_events(pool, MACRO_SOURCE, found, now, ctx.job_id))

    for series in settings.eurostat:
        async with ctx.step(f"eurostat:{series.key}") as step:
            try:
                obs = await collect_window(
                    pool,
                    "eurostat",
                    [f"{series.key}:{geo}" for geo in settings.geos],
                    partial(
                        fetch_eurostat,
                        deps.http,
                        series,
                        settings.geos,
                        pool=pool,
                        known_at=now,
                    ),
                    today,
                    settings.history_years,
                    step.detail,
                )
            except (MacroError, httpx.HTTPError, KeyError, ValueError) as exc:
                step.detail["error"] = str(exc)[:300]
                await mark_macro_coverage(
                    pool,
                    "eurostat",
                    series.key,
                    settings.geos,
                    AccessState.TEMPORARILY_FAILED,
                    type(exc).__name__,
                )
                continue
            count, found = await store_and_evaluate(pool, obs, bands, known_at=now)
            events += found
            await raise_now(found)
            step.detail.update(observations=len(obs), new=count, surprises=len(found))
    for fred in settings.fred:
        async with ctx.step(f"fred:{fred.key}") as step:
            if not deps.fred_key:
                step.detail["skipped"] = "FRED_API_KEY is not set"
                await mark_macro_coverage(
                    pool,
                    "fred",
                    fred.key,
                    ["WLD" if is_commodity(fred.key) else "US"],
                    AccessState.ACCESS_UNVERIFIED,
                    "FRED_API_KEY is not set",
                )
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
                await mark_macro_coverage(
                    pool,
                    "fred",
                    fred.key,
                    ["WLD" if is_commodity(fred.key) else "US"],
                    AccessState.TEMPORARILY_FAILED,
                    type(exc).__name__,
                )
                continue
            count, found = await store_and_evaluate(pool, obs, bands, known_at=now)
            events += found
            await raise_now(found)
            step.detail.update(observations=len(obs), new=count, surprises=len(found))
            try:
                kept = await store_fred_vintages(
                    pool, deps.http, fred, deps.fred_key, today, settings.history_years
                )
                step.detail["vintages"] = (
                    kept
                    if kept is not None
                    else "none: FRED keeps no revision history of a change series"
                )
            except (
                MacroError,
                httpx.HTTPError,
                ValueError,
                KeyError,
                asyncpg.PostgresError,
            ) as exc:
                # The current-vintage numbers and their events are already stored and
                # raised; an ALFRED failure is reported on the step, not as a job failure.
                step.detail["vintage_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
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
                await mark_macro_coverage(
                    pool,
                    "ecb",
                    ecb.key,
                    ["EA"],
                    AccessState.TEMPORARILY_FAILED,
                    type(exc).__name__,
                )
                continue
            count, found = await store_and_evaluate(pool, obs, bands, known_at=now)
            events += found
            await raise_now(found)
            step.detail.update(observations=len(obs), new=count, surprises=len(found))
    for cnb_series in settings.cnb:
        async with ctx.step(f"cnb:{cnb_series.key}") as step:
            try:
                obs = await collect_window(
                    pool,
                    "cnb",
                    [cnb_series.key],
                    partial(fetch_cnb_series, deps.http, cnb_series, today),
                    today,
                    settings.history_years,
                    step.detail,
                )
            except (cnb.CnbError, httpx.HTTPError) as exc:
                # A ČNB outage never fails the job: the step says so and the rest runs.
                step.detail["error"] = str(exc)[:300]
                await mark_macro_coverage(
                    pool,
                    "cnb",
                    cnb_series.key,
                    ["CZ"],
                    AccessState.TEMPORARILY_FAILED,
                    type(exc).__name__,
                )
                continue
            count, found = await store_and_evaluate(pool, obs, bands, known_at=now)
            events += found
            await raise_now(found)
            step.detail.update(observations=len(obs), new=count, surprises=len(found))
            if obs:
                step.detail["latest"] = {"period": obs[-1].period, "value": obs[-1].value}
    async with ctx.step("surprises") as step:
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
