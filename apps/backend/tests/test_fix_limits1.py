"""Regression tests for group LIMITS-1: every source pages or widens its window until it has
all the history it intends to (Trading212, Stooq, FX, macro, holidays)."""

import datetime as dt
import json
from decimal import Decimal
from typing import Any

import asyncpg
import httpx
import pytest

from steward.config.jobs import EcbSeries, EurostatSeries, FredSeries, JobsConfig
from steward.config.store import save_config
from steward.jobs.runner import JobContext, run_job
from steward.macro.collect import (
    Observation,
    collect_window,
    fetch_ecb_series,
    fetch_eurostat,
    fetch_fred,
)
from steward.reference.prices import PriceDeps, collect_symbol, fx_history, run_holiday_refresh
from steward.sources.market import MarketDataError, stooq_daily
from steward.trading212.client import HISTORY_PAGE_SIZE, Trading212Client

TODAY = dt.date(2026, 9, 25)

# ---------------------------------------------------------------------------
# Trading212: follow nextPagePath until it is null, 50 items a page
# ---------------------------------------------------------------------------


async def test_trading212_history_pages_until_next_page_path_is_null() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url.query, "ascii"))
        cursor = request.url.params.get("cursor")
        item = {"reference": cursor or "first", "type": "DEPOSIT", "amount": 1,
                "dateTime": "2026-09-01T10:00:00Z"}  # fmt: skip
        nxt = {None: "/api/v0/equity/history/transactions?limit=50&cursor=2", "2": None}
        return httpx.Response(200, json={"items": [item], "nextPagePath": nxt[cursor]})

    async def no_sleep(_: float) -> None:
        return None

    async with Trading212Client(
        "k", "s", transport=httpx.MockTransport(handler), sleep=no_sleep
    ) as t212:
        pages = [page async for page in t212.iter_cash_history()]
    assert HISTORY_PAGE_SIZE == 50  # the documented maximum `limit`
    assert seen == ["limit=50", "limit=50&cursor=2"]
    assert [p[0].reference for p in pages] == ["first", "2"]


# ---------------------------------------------------------------------------
# Macro: request shapes for recent / backfill windows, FRED offset paging
# ---------------------------------------------------------------------------

FRED = FredSeries(key="fed_funds", label="Fed funds", series_id="DFF", policy_rate=True)


def _fred_obs(days: list[str]) -> list[dict[str, str]]:
    return [{"date": d, "value": "4.33", "realtime_start": "2026-09-25"} for d in days]


async def test_fred_recent_asks_for_the_newest_few() -> None:
    params: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params.append(dict(request.url.params))
        return httpx.Response(200, json={"count": 9000, "observations": _fred_obs(["2026-09-24"])})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        obs = await fetch_fred(http, FRED, "key")
    assert len(obs) == 1 and len(params) == 1
    assert params[0]["sort_order"] == "desc" and params[0]["limit"] == "6"
    assert "observation_start" not in params[0]


async def test_fred_backfill_pages_by_offset_until_count() -> None:
    params: list[dict[str, str]] = []
    pages = {None: ["2016-01-01", "2016-01-02"], "2": ["2016-01-03"]}

    def handler(request: httpx.Request) -> httpx.Response:
        params.append(dict(request.url.params))
        rows = pages[request.url.params.get("offset")]
        return httpx.Response(200, json={"count": 3, "observations": _fred_obs(rows)})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        obs = await fetch_fred(http, FRED, "key", since=dt.date(2016, 1, 1))
    assert [o.period_label for o in obs] == ["2016-01-01", "2016-01-02", "2016-01-03"]
    assert params[0]["observation_start"] == "2016-01-01"
    assert params[0]["sort_order"] == "asc" and params[0]["limit"] == "100000"
    assert [p.get("offset") for p in params] == [None, "2"]


def _jsonstat(periods: list[str]) -> dict[str, Any]:
    return {
        "id": ["geo", "time"],
        "size": [1, len(periods)],
        "dimension": {
            "geo": {"category": {"index": {"CZ": 0}}},
            "time": {"category": {"index": {p: i for i, p in enumerate(periods)}}},
        },
        "value": {str(i): 2.0 for i in range(len(periods))},
        "updated": "2026-09-17T11:00:00+0200",
    }


async def test_eurostat_backfill_uses_since_time_period_year() -> None:
    series = EurostatSeries(key="hicp", label="HICP", dataset="prc_hicp_minr")
    queries: list[list[tuple[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        queries.append(list(request.url.params.multi_items()))
        return httpx.Response(200, json=_jsonstat(["2016-01", "2016-02"]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await fetch_eurostat(http, series, ["CZ"], since=dt.date(2016, 1, 1))
        await fetch_eurostat(http, series, ["CZ"])
    assert ("sinceTimePeriod", "2016") in queries[0]
    assert not any(k == "lastTimePeriod" for k, _ in queries[0])
    assert ("lastTimePeriod", "3") in queries[1]


async def test_ecb_backfill_uses_start_period() -> None:
    series = EcbSeries(key="dfr", label="DFR", flow_key="FM/D.U2.EUR.4F.KR.DFR.LEV")
    params: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params.append(dict(request.url.params))
        return httpx.Response(200, text="TIME_PERIOD,OBS_VALUE\n2016-01-01,-0.3\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await fetch_ecb_series(http, series, TODAY, since=dt.date(2016, 1, 1))
        await fetch_ecb_series(http, series, TODAY)
    assert params[0] == {"startPeriod": "2016-01-01", "format": "csvdata"}
    assert params[1] == {"lastNObservations": "5", "format": "csvdata"}


def _obs(series_id: str, period: dt.date) -> Observation:
    return Observation(
        series_id, series_id, "fred", period, period.isoformat(), Decimal(1),
        "x", "%", TODAY, "https://fred.stlouisfed.org/series/DFF",
    )  # fmt: skip


@pytest.mark.db
async def test_collect_window_backfills_first_then_recent_then_fills_gaps(
    backend_pool: asyncpg.Pool,
) -> None:
    calls: list[dt.date | None] = []
    recent = [_obs("ff", TODAY - dt.timedelta(days=n)) for n in (2, 1, 0)]

    async def fetch(since: dt.date | None) -> list[Observation]:
        calls.append(since)
        return recent

    detail: dict[str, Any] = {}
    # Nothing stored: one request from 1 January ten years back.
    await collect_window(backend_pool, "fred", ["ff"], fetch, TODAY, 10, detail)
    assert calls == [dt.date(2016, 1, 1)] and detail["backfill_from"] == "2016-01-01"

    async def store(series_id: str, period: dt.date) -> None:
        await backend_pool.execute(
            "insert into public.macro_series (series_id, source, period, value, label, unit, url)"
            " values ($1, 'fred', $2, 1, 'x', '%', 'https://x')",
            series_id,
            period,
        )

    # Stored up to the recent window: only the recent request.
    await store("ff", TODAY - dt.timedelta(days=2))
    calls.clear()
    await collect_window(backend_pool, "fred", ["ff"], fetch, TODAY, 10, {})
    assert calls == [None]

    # Stored only up to three weeks ago: the recent window leaves a gap, fetched from there.
    recent = [_obs("gg", TODAY - dt.timedelta(days=n)) for n in (2, 1, 0)]
    await store("gg", TODAY - dt.timedelta(days=21))
    calls.clear()
    detail = {}
    await collect_window(backend_pool, "fred", ["gg"], fetch, TODAY, 10, detail)
    assert calls == [None, TODAY - dt.timedelta(days=21)]
    assert detail["gap_from"] == (TODAY - dt.timedelta(days=21)).isoformat()


# ---------------------------------------------------------------------------
# Stooq: refusals are errors, not "no data"; a longer history_years is backfilled
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Get your apikey:\n1. Open https://stooq.com/q/d/?s=aapl.us&get_apikey",
        "Exceeded the daily hits limit",
    ],
)
async def test_stooq_refusal_is_an_error(text: str) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, text=text))
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(MarketDataError, match="refused"):
            await stooq_daily(http, "aapl.us", TODAY, TODAY)


async def test_stooq_passes_the_api_key() -> None:
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("apikey"))
        return httpx.Response(200, text="Date,Open,High,Low,Close\n2026-09-25,1,1,1,1\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        bars = await stooq_daily(http, "aapl.us", TODAY, TODAY, api_key="abc")
    assert seen == ["abc"] and len(bars) == 1


def _stooq_handler(requests: list[dt.date]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        d1 = dt.datetime.strptime(request.url.params["d1"], "%Y%m%d").date()
        requests.append(d1)
        days = [d1 + dt.timedelta(days=n) for n in range(0, (TODAY - d1).days + 1, 7)]
        rows = "\n".join(f"{d.isoformat()},10,10,10,10,100" for d in days)
        return httpx.Response(200, text="Date,Open,High,Low,Close,Volume\n" + rows)

    return httpx.MockTransport(handler)


@pytest.mark.db
async def test_stooq_backfill_extends_when_history_years_grows(
    backend_pool: asyncpg.Pool,
) -> None:
    requests: list[dt.date] = []
    async with httpx.AsyncClient(transport=_stooq_handler(requests)) as http:
        deps = PriceDeps(http=http, today=TODAY)
        await collect_symbol(backend_pool, deps, "xyz.us", 2)  # first run: 2 years
        await collect_symbol(backend_pool, deps, "xyz.us", 2)  # recent days only
        await collect_symbol(backend_pool, deps, "xyz.us", 5)  # owner asked for 5 years
        await collect_symbol(backend_pool, deps, "xyz.us", 5)  # recent days only
    two, five = TODAY - dt.timedelta(days=735), TODAY - dt.timedelta(days=1830)
    assert requests[0] == two and requests[2] == five
    assert requests[1] > TODAY - dt.timedelta(days=30) and requests[3] > requests[2]
    oldest = await backend_pool.fetchval("select min(date) from public.price_bar")
    assert oldest == five


@pytest.mark.db
async def test_stooq_refusal_is_a_visible_gap(backend_pool: asyncpg.Pool) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, text="Get your apikey: ..."))
    async with httpx.AsyncClient(transport=transport) as http:
        count, gap = await collect_symbol(
            backend_pool, PriceDeps(http=http, today=TODAY), "xyz.us", 2
        )
    assert count == 0 and gap is not None and "refused" in gap


# ---------------------------------------------------------------------------
# FX history: re-backfilled when history_years grows
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_fx_history_backfill_extends_when_history_years_grows(
    backend_pool: asyncpg.Pool,
) -> None:
    starts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        start, _ = request.url.path.rsplit("/", 1)[1].split("..")
        starts.append(start)
        return httpx.Response(200, json={"base": "EUR", "rates": {start: {"USD": 1.1}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        deps = PriceDeps(http=http, today=TODAY)
        for years in (2, 2, 4):
            await fx_history(backend_pool, deps, years, {})
    assert starts == [
        (TODAY - dt.timedelta(days=735)).isoformat(),
        (TODAY - dt.timedelta(days=1465)).isoformat(),
    ]
    state = json.loads(
        await backend_pool.fetchval(
            "select value from public.sync_state where key = 'fx_history_backfill'"
        )
    )
    assert state["from"] == starts[-1]


# ---------------------------------------------------------------------------
# Holidays: last, this and next year
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_holiday_refresh_loads_last_this_and_next_year(backend_pool: asyncpg.Pool) -> None:
    years: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        year = request.url.path.split("/")[-2]
        years.append(year)
        return httpx.Response(200, json=[{"date": f"{year}-01-01", "name": "New Year"}])

    async with backend_pool.acquire() as conn:
        await save_config(
            conn,
            "jobs",
            JobsConfig.model_validate({"calendar": {"holiday_countries": ["CZ"]}}),
            created_by=None,
        )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:

        async def job(ctx: JobContext) -> None:
            await run_holiday_refresh(ctx, http, TODAY)

        assert await run_job(backend_pool, "holiday_refresh", job, trigger="manual") == "succeeded"
    assert years == ["2025", "2026", "2027"]
    stored = await backend_pool.fetchval(
        "select count(*) from public.holiday where calendar = 'CZ'"
    )
    assert stored == 3


async def test_the_stooq_key_reaches_every_download() -> None:
    import datetime as dt

    import httpx

    from steward.sources.market import stooq_daily

    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("apikey"))
        return httpx.Response(200, text="Date,Open,High,Low,Close,Volume\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await stooq_daily(http, "aapl.us", dt.date(2026, 1, 1), dt.date(2026, 1, 5), api_key="k1")
        await stooq_daily(http, "aapl.us", dt.date(2026, 1, 1), dt.date(2026, 1, 5))
    assert seen == ["k1", None]
