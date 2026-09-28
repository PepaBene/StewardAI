"""ČNB rates (steward.sources.cnb) and their macro-job steps: CZEONIA daily (the Sharpe
ratio's risk-free rate, never a surprise) and the 2W repo rate (a policy rate: a new change
is a central-bank event). Fixture files are real ČNB responses saved on 2026-09-28."""

import datetime as dt
from decimal import Decimal
from itertools import pairwise
from pathlib import Path

import asyncpg
import httpx
import pytest

from steward.config.jobs import CnbSeries, JobsConfig, MacroSettings
from steward.config.store import save_config
from steward.jobs.runner import JobContext, run_job
from steward.macro.collect import MacroDeps, fetch_cnb_series, run_macro_collect
from steward.sources import cnb

FIXTURES = Path(__file__).parent / "fixtures" / "cnb"


def _text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_czeonia_yearly() -> None:
    points = cnb.parse_czeonia_yearly(_text("czeonia_2026.txt"))
    assert points[0] == cnb.RatePoint(dt.date(2026, 1, 2), Decimal("3.07"), Decimal("11671"))
    assert points[-1] == cnb.RatePoint(dt.date(2026, 9, 24), Decimal("3.56"), Decimal("20740"))
    assert all(a.date < b.date for a, b in pairwise(points))
    assert cnb.parse_czeonia_yearly("") == []  # a year with no fixing yet
    # A decimal comma (the Czech files) is read too.
    comma = cnb.parse_czeonia_yearly("Date|Volume (CZK mio)|Rate\n02 Jan 2026|1|3,07")
    assert comma[0].rate == Decimal("3.07")


def test_parse_czeonia_daily_and_errors() -> None:
    assert cnb.parse_czeonia_daily(_text("czeonia_daily.txt")) == [
        cnb.RatePoint(dt.date(2026, 9, 24), Decimal("3.56"), Decimal("20740"))
    ]
    with pytest.raises(cnb.CnbError, match="HTML"):
        cnb.parse_czeonia_yearly("<!DOCTYPE html><html></html>")
    with pytest.raises(cnb.CnbError, match="header"):
        cnb.parse_czeonia_yearly("Foo|Bar\n1|2")
    with pytest.raises(cnb.CnbError, match="unexpected date"):
        cnb.parse_czeonia_yearly("Date|Volume (CZK mio)|Rate\n2026-01-02|1|3.0")
    with pytest.raises(cnb.CnbError, match="too short"):
        cnb.parse_czeonia_daily("24 Sep 2026\n")


def test_parse_repo_rate_history_with_bom() -> None:
    text = _text("repo_2w.txt")
    assert text.startswith("﻿")
    points = cnb.parse_repo_rate(text)
    assert points[0] == cnb.RatePoint(dt.date(1995, 12, 8), Decimal("11.30"))
    assert points[-1] == cnb.RatePoint(dt.date(2026, 6, 19), Decimal("3.75"))
    with pytest.raises(cnb.CnbError, match="unexpected header"):
        cnb.parse_repo_rate("DATE|RATE\n20260619|3.75")


def _transport(
    files: dict[tuple[str, str | None], tuple[int, str]], seen: list[str] | None = None
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        base = f"{request.url.scheme}://{request.url.host}{request.url.path}"
        status, body = files.get((base, request.url.params.get("calendarYear")), (200, ""))
        return httpx.Response(status, text=body)

    return httpx.MockTransport(handler)


FILES: dict[tuple[str, str | None], tuple[int, str]] = {
    (cnb.CZEONIA_YEARLY_URL, "2025"): (200, _text("czeonia_2025.txt")),
    (cnb.CZEONIA_YEARLY_URL, "2026"): (200, _text("czeonia_2026.txt")),
    (cnb.CZEONIA_DAILY_URL, None): (200, _text("czeonia_daily.txt")),
    (cnb.REPO_RATE_URL, None): (200, _text("repo_2w.txt")),
}
TODAY = dt.date(2026, 9, 28)
CZEONIA = CnbSeries(key="czeonia", label="CZEONIA", series="czeonia")
REPO = CnbSeries(key="cnb_repo_2w", label="ČNB 2W repo rate", series="repo_2w", policy_rate=True)


async def test_fetch_czeonia_backfill_and_recent() -> None:
    seen: list[str] = []
    async with httpx.AsyncClient(transport=_transport(FILES, seen)) as http:
        backfill = await fetch_cnb_series(http, CZEONIA, TODAY, since=dt.date(2025, 1, 1))
        assert backfill[0].period == dt.date(2025, 1, 2)
        assert backfill[-1].period == dt.date(2026, 9, 24)
        assert backfill[-1].value == Decimal("3.56") and backfill[-1].source == "cnb"
        # Published the next business day: Thursday 24 Sep -> Friday 25 Sep.
        assert backfill[-1].release_date == dt.date(2026, 9, 25)
        assert not backfill[-1].policy_rate
        assert [u for u in seen if "calendarYear" in u] == [
            f"{cnb.CZEONIA_YEARLY_URL}?calendarYear=2025",
            f"{cnb.CZEONIA_YEARLY_URL}?calendarYear=2026",
        ]
        recent = await fetch_cnb_series(http, CZEONIA, TODAY)
        assert recent[0].period.year == 2026
        repo = await fetch_cnb_series(http, REPO, TODAY)
        assert len(repo) == 5 and repo[-1].value == Decimal("3.75") and repo[-1].policy_rate


async def test_transient_errors_are_retried_and_4xx_fail() -> None:
    calls = 0

    def flaky(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("reset")
        if calls == 2:
            return httpx.Response(503)
        return httpx.Response(200, text=_text("repo_2w.txt"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(flaky)) as http:
        assert (await cnb.fetch_repo_rate(http))[-1].rate == Decimal("3.75")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(404))
    ) as http:
        with pytest.raises(cnb.CnbError, match="HTTP 404"):
            await cnb.fetch_repo_rate(http)


@pytest.mark.db
async def test_macro_job_stores_cnb_rates_and_raises_only_repo_decisions(
    backend_pool: asyncpg.Pool,
) -> None:
    async with backend_pool.acquire() as conn:
        await save_config(
            conn,
            "jobs",
            JobsConfig(macro=MacroSettings(eurostat=[], fred=[], ecb=[], history_years=1)),
            created_by=None,
        )
    files = dict(FILES)

    async def collect(today: dt.date) -> str:
        now = dt.datetime.combine(today, dt.time(10), tzinfo=dt.UTC)
        async with httpx.AsyncClient(transport=_transport(files)) as http:

            async def handler(ctx: JobContext) -> None:
                await run_macro_collect(ctx, MacroDeps(http=http, today=today, now=now))

            return await run_job(backend_pool, "macro_collect", handler, trigger="manual")

    assert await collect(TODAY) == "succeeded"
    stored = await backend_pool.fetch(
        "select series_id, count(*) as n, min(period) as first, max(period) as last"
        " from macro_series where source = 'cnb' group by series_id order by series_id"
    )
    assert [(r["series_id"], r["first"], r["last"]) for r in stored] == [
        ("cnb_repo_2w", dt.date(2025, 2, 7), dt.date(2026, 6, 19)),
        ("czeonia", dt.date(2025, 1, 2), dt.date(2026, 9, 24)),
    ]
    assert await backend_pool.fetchval("select count(*) from macro_series where source='cnb'") > 400

    # Next day: a new CZEONIA fixing (no event) and a new repo decision (a central-bank event).
    files[(cnb.CZEONIA_YEARLY_URL, "2026")] = (
        200,
        _text("czeonia_2026.txt").rstrip("\n") + "\n25 Sep 2026|19000|3.31\n",
    )
    files[(cnb.REPO_RATE_URL, None)] = (200, _text("repo_2w.txt").rstrip() + "\n20261001|3.50\n")
    assert await collect(TODAY + dt.timedelta(days=1)) == "succeeded"
    assert await backend_pool.fetchval(
        "select value from macro_series where series_id = 'czeonia' and period = '2026-09-25'"
    ) == Decimal("3.31")
    events = await backend_pool.fetch("select event_type, headline from story_cluster")
    assert [e["event_type"] for e in events] == ["central_bank"]
    assert "3.5%" in events[0]["headline"] and "previous 3.75%" in events[0]["headline"]
    item = await backend_pool.fetchrow("select outlet, outlet_name from news_item")
    assert (item["outlet"], item["outlet_name"]) == ("cnb.cz", "Czech National Bank")


@pytest.mark.db
async def test_a_cnb_outage_does_not_fail_the_macro_job(backend_pool: asyncpg.Pool) -> None:
    async with backend_pool.acquire() as conn:
        await save_config(
            conn,
            "jobs",
            JobsConfig(macro=MacroSettings(eurostat=[], fred=[], ecb=[])),
            created_by=None,
        )
    down = httpx.MockTransport(lambda _: httpx.Response(503))
    async with httpx.AsyncClient(transport=down) as http:

        async def handler(ctx: JobContext) -> None:
            await run_macro_collect(ctx, MacroDeps(http=http, today=TODAY))

        assert await run_job(backend_pool, "macro_collect", handler, trigger="manual") == (
            "succeeded"
        )
    errors = await backend_pool.fetch(
        "select name, detail->>'error' as error from job_step where name like 'cnb:%'"
    )
    assert {e["name"] for e in errors} == {"cnb:czeonia", "cnb:cnb_repo_2w"}
    assert all("503" in e["error"] for e in errors)
