"""The stored release calendar: parsers on recorded provider responses, schema-drift quarantine,
quota / paid-only handling, the append-only store with rescheduling, the `macro_release_calendar`
job, the zero-cost gate and the macro read path (`next_release`, provider calendar before the
observed-cadence fallback)."""

import datetime as dt
import json
from collections.abc import AsyncIterator, Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool, get_pool_or_error
from api.main import create_app
from steward.config.jobs import COVERAGE_DEFAULT_REFRESH_HOURS, JobsConfig, coverage_stale_policy
from steward.jobs.runner import JobContext, run_job
from steward.macro.read import context_for_holding
from steward.macro.release_calendar import (
    CALENDAR_BUDGETS,
    CalendarDeps,
    next_releases,
    run_release_calendar,
    store_calendar,
)
from steward.sources.base import AccessState
from steward.sources.governor import QuotaExceeded
from steward.sources.release_calendar import (
    CalendarDrift,
    CalendarEntry,
    CalendarFetch,
    fetch_bis,
    fetch_cnb,
    fetch_ecb,
    fetch_ecfin,
    fetch_eurostat,
    fetch_fred,
    parse_bis_calendar,
    parse_ecb_calendar_html,
    parse_eurostat_ics,
    parse_fred_release_dates,
    parse_fred_series_release,
)
from steward.verify_sources.zero_cost import audit_zero_cost
from tests.test_macro_fixes import _holding, _issuer, _map

FIXTURES = Path(__file__).parent / "fixtures" / "release_calendar"
TODAY = dt.date(2026, 10, 3)
NOW = dt.datetime(2026, 10, 3, 16, 0, tzinfo=dt.UTC)
OWNER = "7f0c3d4e-0000-4000-8000-000000000001"
SECRET = "0123456789abcdef0123456789abcdef"


def _text(name: str) -> str:
    return (FIXTURES / name).read_text()


def _json(name: str) -> Any:
    return json.loads(_text(name))


async def _allow() -> None:
    return None


def _utc(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text).replace(tzinfo=dt.UTC)


# ---------------------------------------------------------------------------------------
# Parsers on the recorded responses
# ---------------------------------------------------------------------------------------
def test_fred_parsers_read_the_recorded_release_and_future_dates() -> None:
    assert parse_fred_series_release(_json("fred_series_release_cpiaucsl.json")) == (
        10,
        "Consumer Price Index",
    )
    assert parse_fred_series_release(_json("fred_series_release_unrate.json"))[0] == 50
    assert parse_fred_series_release(_json("fred_series_release_dff.json"))[0] == 18
    dates = parse_fred_release_dates(_json("fred_release_dates_cpi_10.json"), 10)
    assert dt.date(2026, 10, 14) in dates and dates == sorted(dates)
    assert parse_fred_release_dates(_json("fred_release_dates_employment_50.json"), 50) == [
        dt.date(2026, 11, 6),
        dt.date(2026, 12, 4),
    ]


def test_eurostat_ics_events_have_titles_and_all_day_dates() -> None:
    events = parse_eurostat_ics(_text("eurostat_euro_indicators.ics"))
    assert ("Inflation (HICP)", dt.date(2026, 10, 16)) in events
    assert ("Unemployment", dt.date(2026, 10, 30)) in events
    # An escaped comma in the iCalendar text is unescaped.
    assert ("GDP main aggregates and employment", dt.date(2026, 12, 7)) in events


def test_ecb_calendar_page_has_date_time_dataset_and_reference_period() -> None:
    rows = parse_ecb_calendar_html(_text("ecb_stprmp.html"), "BSI")
    assert rows[0] == (
        dt.date(2026, 9, 25),
        dt.time(10, 0),
        "Aug-2026",
        "Monetary developments in the euro area (Dataset: BSI)",
    )
    assert [r[0] for r in rows][1] == dt.date(2026, 10, 27)
    assert len(parse_ecb_calendar_html(_text("ecb_stprmf.html"), "MIR")) == 5


def test_bis_calendar_rows_parse_with_slash_dates() -> None:
    rows = parse_bis_calendar(_json("bis_releasecalendar.json"))
    assert ("TOTAL_CREDIT", "Release", dt.date(2026, 12, 7), "2026-Q2") in {
        (c, t, d, p) for c, t, d, p in rows
    } or any(c == "TOTAL_CREDIT" and t == "Release" for c, t, _d, _p in rows)
    assert {c for c, *_ in rows} >= {"TOTAL_CREDIT", "DSR", "LBS", "DSS", "IDS", "GLI", "CPI"}


# ---------------------------------------------------------------------------------------
# Schema drift: a payload that is not in the verified shape raises and stores nothing
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "call",
    [
        lambda: parse_fred_series_release({"releases": []}),
        lambda: parse_fred_series_release({"releases": [{"id": "10"}]}),
        lambda: parse_fred_release_dates({"dates": []}, 10),
        lambda: parse_fred_release_dates({"release_dates": [{"release_id": 10, "date": "x"}]}, 10),
        lambda: parse_fred_release_dates(
            {"release_dates": [{"release_id": 11, "date": "2026-10-14"}]}, 10
        ),
        lambda: parse_eurostat_ics("<html>maintenance</html>"),
        lambda: parse_eurostat_ics("BEGIN:VCALENDAR\nPRODID:-//Eurostat//EN\nEND:VCALENDAR\n"),
        lambda: parse_eurostat_ics(
            "BEGIN:VCALENDAR\nPRODID:-//Eurostat//EN\nBEGIN:VEVENT\nSUMMARY:x\nEND:VEVENT\nEND:VCALENDAR"
        ),
        lambda: parse_ecb_calendar_html("<html></html>", "BSI"),
        lambda: parse_ecb_calendar_html(_text("ecb_stprmf.html"), "BSI"),  # other dataset
        lambda: parse_bis_calendar({"error": "x"}),
        lambda: parse_bis_calendar([{"category_id": "DSR"}]),
        lambda: parse_bis_calendar(
            [
                {
                    "category_id": "DSR",
                    "release_type": "Release",
                    "release_date": "07.12.2026",
                    "time_period": "q",
                }
            ]
        ),
    ],
)
def test_unexpected_shapes_are_schema_drift(call: Callable[[], object]) -> None:
    with pytest.raises(CalendarDrift):
        call()


async def test_drifted_payloads_come_back_quarantined_with_no_entries() -> None:
    def serve(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "api.stlouisfed.org":
            return httpx.Response(200, json={"releases": []})
        if host == "ec.europa.eu":
            return httpx.Response(200, text="<html>not a calendar</html>")
        if host in ("www.ecb.europa.eu", "www.cnb.cz"):
            return httpx.Response(200, text="<html>redesigned</html>")
        return httpx.Response(200, json={"unexpected": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as http:
        fetched = [
            await fetch_fred(http, SECRET, {"us_cpi": "CPIAUCSL"}, TODAY, _allow),
            await fetch_eurostat(http, TODAY, _allow),
            await fetch_ecb(http, TODAY, _allow),
            await fetch_bis(http, TODAY, _allow),
            await fetch_ecfin(http, TODAY, _allow),
            await fetch_cnb(http, TODAY, _allow),
        ]
    for result in fetched:
        assert result.entries == [] and result.covered == set(), result.provider
        assert result.drift and result.raw is not None, result.provider
        assert result.access.state == AccessState.TEMPORARILY_FAILED
        assert "schema drift" in (result.access.detail or "")


async def test_a_calendar_that_lost_a_mapped_release_is_drift_not_silence() -> None:
    ics = _text("eurostat_euro_indicators.ics").replace("Inflation (HICP)", "Inflation")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=ics))
    ) as http:
        result = await fetch_eurostat(http, TODAY, _allow)
    assert result.drift and "Inflation (HICP)" in result.drift and result.entries == []


# ---------------------------------------------------------------------------------------
# Quota, paid-only and secrets
# ---------------------------------------------------------------------------------------
async def test_a_refused_reservation_means_no_request_and_a_typed_quota_result() -> None:
    calls: list[str] = []

    def serve(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={})

    async def refuse() -> None:
        raise QuotaExceeded("daily free quota exhausted")

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as http:
        for result in (
            await fetch_fred(http, SECRET, {"us_cpi": "CPIAUCSL"}, TODAY, refuse),
            await fetch_eurostat(http, TODAY, refuse),
            await fetch_ecb(http, TODAY, refuse),
            await fetch_bis(http, TODAY, refuse),
            await fetch_ecfin(http, TODAY, refuse),
            await fetch_cnb(http, TODAY, refuse),
        ):
            assert result.access.state == AccessState.QUOTA_EXHAUSTED
            assert result.entries == [] and result.calls == 0
    assert calls == []


@pytest.mark.parametrize(
    ("status", "state"),
    [
        (402, AccessState.PAID_ONLY),
        (403, AccessState.ACCESS_UNVERIFIED),
        (429, AccessState.QUOTA_EXHAUSTED),
        (503, AccessState.TEMPORARILY_FAILED),
    ],
)
async def test_http_denials_are_typed_and_never_leak_the_api_key(
    status: int, state: AccessState
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(status, text="no"))
    ) as http:
        result = await fetch_fred(http, SECRET, {"us_cpi": "CPIAUCSL"}, TODAY, _allow)
    assert result.access.state == state and result.entries == []
    assert SECRET not in repr(result)


async def test_a_transport_error_is_reported_without_the_url() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as http:
        result = await fetch_fred(http, SECRET, {"us_cpi": "CPIAUCSL"}, TODAY, _allow)
    assert result.access.state == AccessState.TEMPORARILY_FAILED
    assert SECRET not in (result.access.detail or "") and "ConnectError" in (
        result.access.detail or ""
    )


async def test_httpx_request_logging_does_not_carry_the_fred_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger="httpx")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, text=_text("fred_series_release_unrate.json"))
        )
    ) as http:
        await fetch_fred(http, SECRET, {"us_unemployment": "UNRATE"}, TODAY, _allow)
    assert "series/release" in caplog.text  # the request was logged ...
    assert SECRET not in caplog.text  # ... without the key


async def test_fred_without_a_key_makes_no_request() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("no request expected"))
    ) as http:
        result = await fetch_fred(http, "", {"us_cpi": "CPIAUCSL"}, TODAY, _allow)
    assert result.access.state == AccessState.ACCESS_UNVERIFIED and result.calls == 0


# ---------------------------------------------------------------------------------------
# End to end on the recorded responses (no database): entries per series
# ---------------------------------------------------------------------------------------
def _provider_transport() -> httpx.MockTransport:
    def serve(request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.host == "api.stlouisfed.org":
            if url.path.endswith("/series/release"):
                sid = url.params["series_id"]
                name = {"CPIAUCSL": "cpiaucsl", "UNRATE": "unrate", "DFF": "dff"}[sid]
                return httpx.Response(200, text=_text(f"fred_series_release_{name}.json"))
            rid = url.params["release_id"]
            name = {"10": "cpi_10", "50": "employment_50", "18": "h15_18"}[rid]
            assert url.params["include_release_dates_with_no_data"] == "true"
            return httpx.Response(200, text=_text(f"fred_release_dates_{name}.json"))
        if url.host == "ec.europa.eu" and "economy_finance" in url.path:
            if "2026" not in url.path:  # next year's PDF is not published yet
                return httpx.Response(404, text="not found")
            return httpx.Response(
                200, content=(FIXTURES / "ecfin_publication_dates_2026.pdf").read_bytes()
            )
        if url.host == "ec.europa.eu":
            return httpx.Response(200, text=_text("eurostat_euro_indicators.ics"))
        if url.host == "www.ecb.europa.eu":
            page = url.path.rsplit("/", 1)[1].split(".")[0]
            if "/mgcgc/" in url.path:
                page = "mgcgc"
            return httpx.Response(200, text=_text(f"ecb_{page}.html"))
        if url.host == "data.bis.org":
            return httpx.Response(200, text=_text("bis_releasecalendar.json"))
        if url.host == "www.cnb.cz":
            return httpx.Response(200, text=_text("cnb_bank_board.html"))
        return httpx.Response(404)

    return httpx.MockTransport(serve)


async def test_every_provider_maps_to_its_series_and_keeps_only_upcoming_dates() -> None:
    fred_series = {"us_cpi": "CPIAUCSL", "us_unemployment": "UNRATE", "fed_funds": "DFF"}
    async with httpx.AsyncClient(transport=_provider_transport()) as http:
        fred = await fetch_fred(http, SECRET, fred_series, TODAY, _allow)
        eurostat = await fetch_eurostat(http, TODAY, _allow)
        ecb = await fetch_ecb(http, TODAY, _allow)
        bis = await fetch_bis(http, TODAY, _allow)
        ecfin = await fetch_ecfin(http, TODAY, _allow)
        cnb = await fetch_cnb(http, TODAY, _allow)
    for result in (fred, eurostat, ecb, bis, ecfin, cnb):
        assert result.access.state == AccessState.AVAILABLE and result.drift is None
        assert result.entries and all(e.release_date >= TODAY for e in result.entries)
    assert fred.calls == 6  # 3 series/release + 3 release/dates
    by_series = {
        s: min(e.release_date for e in fred.entries if e.series_id == s) for s in fred_series
    }
    assert by_series == {
        "us_cpi": dt.date(2026, 10, 14),
        "us_unemployment": dt.date(2026, 11, 6),
        "fed_funds": dt.date(2026, 10, 5),
    }
    assert {e.series_id for e in eurostat.entries} >= {
        "hicp",
        "hicp:EA21",
        "hicp:DE",
        "unemployment",
        "industrial_production",
        "gdp",
    }
    assert not any(e.series_id == "hicp:CZ" for e in eurostat.entries)
    gdp = sorted(e.release_date for e in eurostat.entries if e.series_id == "gdp")
    # The preliminary flash (t+30) already updates namq_10_gdp: it is the next GDP release.
    assert gdp[0] == dt.date(2026, 10, 30)
    assert {e.series_id for e in ecb.entries} == {
        "euro_area_m3_growth",
        "euro_area_private_credit",
        "euro_area_household_loan_rate",
        "euro_area_corporate_loan_rate",
        "ea_household_overnight_deposit_rate",
        "ea_corporate_term_deposit_rate",
        "ea_bls_enterprise_credit_standards",
        "ecb_deposit_rate",
    }
    m3 = min(
        (e for e in ecb.entries if e.series_id == "euro_area_m3_growth"),
        key=lambda e: e.release_date,
    )
    assert (m3.release_date, m3.release_time) == (dt.date(2026, 10, 27), dt.time(10, 0))
    assert {e.series_id for e in bis.entries} == {
        "total_credit",
        "debt_service",
        "banking_claims",
        "debt_securities",
        "global_liquidity",
        "property_prices",  # collected from WS_LONG_CPI: BIS category CPI, not residential
    }
    decisions = sorted(e.release_date for e in ecb.entries if e.series_id == "ecb_deposit_rate")
    assert decisions[0] == dt.date(2026, 10, 29)  # Day 2 of the meeting, not Day 1


# ---------------------------------------------------------------------------------------
# The append-only store: a rescheduled date is a new row, the old one is kept
# ---------------------------------------------------------------------------------------
def _fetch(
    provider: str, *days: dt.date, series: str = "us_cpi", key: str = "release:10"
) -> CalendarFetch:
    fetch = CalendarFetch(provider, access=None)  # type: ignore[arg-type]
    fetch.covered.add((series, key))
    fetch.entries = [
        CalendarEntry(provider, series, key, day, "https://cal.test/", title="CPI") for day in days
    ]
    return fetch


@pytest.mark.db
async def test_a_rescheduled_release_keeps_the_old_row_and_supersedes_it(
    backend_pool: asyncpg.Pool,
) -> None:
    first = _utc("2026-10-03T07:00")
    second = _utc("2026-10-05T07:00")
    r1 = await store_calendar(
        backend_pool, _fetch("fred", dt.date(2026, 10, 14), dt.date(2026, 11, 10)), first
    )
    assert (r1.inserted, r1.superseded) == (2, 0)
    # Same calendar again: nothing changes.
    again = await store_calendar(
        backend_pool, _fetch("fred", dt.date(2026, 10, 14), dt.date(2026, 11, 10)), first
    )
    assert (again.inserted, again.superseded, again.unchanged) == (0, 0, 2)
    # 14 Oct is moved to 15 Oct.
    r2 = await store_calendar(
        backend_pool, _fetch("fred", dt.date(2026, 10, 15), dt.date(2026, 11, 10)), second
    )
    assert (r2.inserted, r2.superseded, r2.unchanged) == (1, 1, 1)
    rows = await backend_pool.fetch(
        "select release_date, known_at, superseded_at from macro_release_calendar"
        " order by release_date, id"
    )
    assert [(r["release_date"], r["superseded_at"]) for r in rows] == [
        (dt.date(2026, 10, 14), second),
        (dt.date(2026, 10, 15), None),
        (dt.date(2026, 11, 10), None),
    ]
    # The old row is history: it cannot be deleted or edited, only superseded.
    with pytest.raises(asyncpg.RestrictViolationError):
        await backend_pool.execute("delete from macro_release_calendar")
    with pytest.raises(asyncpg.RestrictViolationError):
        await backend_pool.execute("update macro_release_calendar set release_date='2027-01-01'")
    # A past date that vanishes was not rescheduled: it happened.
    third = _utc("2026-10-16T07:00")
    r3 = await store_calendar(backend_pool, _fetch("fred", dt.date(2026, 11, 10)), third)
    assert (r3.inserted, r3.superseded) == (0, 0)
    # Back to the superseded date: a new live row, the superseded one stays.
    await store_calendar(
        backend_pool, _fetch("fred", dt.date(2026, 11, 10), dt.date(2026, 12, 1)), third
    )
    count = await backend_pool.fetchval("select count(*) from macro_release_calendar")
    assert count == 4


# ---------------------------------------------------------------------------------------
# The macro read path
# ---------------------------------------------------------------------------------------
async def _obs(
    pool: asyncpg.Pool,
    source: str,
    series: str,
    geography: str,
    period: str,
    known: str,
    release: str | None = None,
) -> None:
    await pool.execute(
        "insert into macro_observation (series_id,source,geography,period,value,unit,frequency,"
        " release_at,known_at,vintage,revision,source_url) values ($1,$2,$3,$4,$5,'%',"
        "'monthly',$6,$7,'v0',0,'u')",
        series,
        source,
        geography,
        dt.date.fromisoformat(period),
        Decimal("1.5"),
        _utc(release) if release else None,
        _utc(known),
    )


async def _seed_context(pool: asyncpg.Pool) -> None:
    issuer = await _issuer(pool)
    await _holding(pool, "MAPPED", issuer)
    await _map(pool, issuer, "0.5", geography="US")
    await _map(pool, issuer, "0.5", geography="DE")
    await pool.execute("update exposure_mapping set created_at='2026-02-01'")
    # us_cpi (FRED): five monthly first releases -> an observed cadence exists as fallback.
    for period, release in (
        ("2026-01-01", "2026-02-10"),
        ("2026-02-01", "2026-03-10"),
        ("2026-03-01", "2026-04-10"),
        ("2026-04-01", "2026-05-10"),
        ("2026-05-01", "2026-06-10"),
    ):
        await _obs(pool, "fred", "us_cpi", "US", period, release, release)
    # hicp:DE (Eurostat) and an OECD series that no calendar covers.
    await _obs(pool, "eurostat", "hicp:DE", "DE", "2026-05-01", "2026-06-17", "2026-06-17")
    for period, release in (
        ("2026-01-01", "2026-02-10"),
        ("2026-02-01", "2026-03-10"),
        ("2026-03-01", "2026-04-10"),
        ("2026-04-01", "2026-05-10"),
        ("2026-05-01", "2026-06-10"),
    ):
        await _obs(pool, "oecd", "inflation", "DE", period, release, release)


def _stored(
    provider: str, series: str, key: str, day: str, known: str, superseded: str | None = None
) -> tuple[Any, ...]:
    return (
        provider,
        series,
        key,
        dt.date.fromisoformat(day),
        f"https://{provider}.test/calendar",
        f"{series} release",
        _utc(known),
        _utc(superseded) if superseded else None,
    )


async def _insert_calendar(pool: asyncpg.Pool, *rows: tuple[Any, ...]) -> None:
    for row in rows:
        await pool.execute(
            "insert into macro_release_calendar (provider,series_id,calendar_key,release_date,"
            "source_url,title,known_at,retrieved_at,superseded_at)"
            " values ($1,$2,$3,$4,$5,$6,$7,$7,$8)",
            *row,
        )


@pytest.mark.db
async def test_context_returns_the_provider_calendar_and_keeps_the_estimate_as_fallback(
    backend_pool: asyncpg.Pool,
) -> None:
    await _seed_context(backend_pool)
    as_of = _utc("2026-07-01")
    # Before any calendar is stored: the observed-cadence estimate, labelled as such.
    before = await context_for_holding(backend_pool, "MAPPED", as_of)
    cpi = next(o for o in before.observations if o.series_id == "us_cpi")
    assert cpi.next_release is None and cpi.next_release_source is None
    assert cpi.next_release_estimate == dt.date(2026, 7, 10)
    assert cpi.next_release_basis == "observed_cadence"

    await _insert_calendar(
        backend_pool,
        # FRED CPI: the calendar says 14 July (not the cadence's 10 July); a later date too.
        _stored("fred", "us_cpi", "release:10", "2026-07-14", "2026-06-12"),
        _stored("fred", "us_cpi", "release:10", "2026-08-12", "2026-06-12"),
        # Eurostat HICP is keyed without the geography suffix.
        _stored("eurostat", "hicp", "Inflation (HICP)", "2026-07-17", "2026-06-12"),
    )
    context = await context_for_holding(backend_pool, "MAPPED", as_of)
    got = {o.series_id: o for o in context.observations}
    cpi, hicp, oecd = got["us_cpi"], got["hicp:DE"], got["inflation"]
    assert cpi.next_release == dt.date(2026, 7, 14)
    assert cpi.next_release_basis == "provider_calendar"
    assert cpi.next_release_source is not None
    assert (cpi.next_release_source.provider, cpi.next_release_source.url) == (
        "fred",
        "https://fred.test/calendar",
    )
    assert cpi.next_release_estimate is None  # the estimate is only the fallback
    assert (
        hicp.next_release == dt.date(2026, 7, 17) and hicp.next_release_basis == "provider_calendar"
    )
    # A provider with no calendar still falls back to the estimate.
    assert oecd.next_release is None and oecd.next_release_source is None
    assert oecd.next_release_estimate == dt.date(2026, 7, 10)
    assert oecd.next_release_basis == "observed_cadence"


@pytest.mark.db
async def test_context_respects_as_of_known_at_and_rescheduling(
    backend_pool: asyncpg.Pool,
) -> None:
    await _seed_context(backend_pool)
    await _insert_calendar(
        backend_pool,
        # Announced 12 June for 14 July; on 3 July moved to 16 July.
        _stored(
            "fred", "us_cpi", "release:10", "2026-07-14", "2026-06-12", superseded="2026-07-03"
        ),
        _stored("fred", "us_cpi", "release:10", "2026-07-16", "2026-07-03"),
        # Announced only on 5 July: invisible earlier.
        _stored("fred", "us_cpi", "release:10", "2026-07-02", "2026-07-05"),
    )

    async def basis(as_of: str) -> tuple[dt.date | None, str | None, dt.date | None]:
        ctx = await context_for_holding(backend_pool, "MAPPED", _utc(as_of))
        cpi = next(o for o in ctx.observations if o.series_id == "us_cpi")
        return cpi.next_release, cpi.next_release_basis, cpi.next_release_estimate

    # 20 June: only the 14 July announcement was known.
    assert (await basis("2026-06-20"))[:2] == (dt.date(2026, 7, 14), "provider_calendar")
    # 4 July: the move to 16 July is known, the old date is superseded (and 2 July is past).
    assert (await basis("2026-07-04"))[:2] == (dt.date(2026, 7, 16), "provider_calendar")
    # 6 July: the 2 July row (announced 5 July) is a past date; still 16 July.
    assert (await basis("2026-07-06"))[:2] == (dt.date(2026, 7, 16), "provider_calendar")
    # On the release date itself the release still counts as "next" (>= as_of's date).
    assert (await basis("2026-07-16T08:00"))[:2] == (dt.date(2026, 7, 16), "provider_calendar")
    # After the last stored date no calendar entry applies: back to the observed-cadence
    # estimate (here none either: it lies before as_of).
    assert await basis("2026-07-17") == (None, None, None)


@pytest.mark.db
async def test_next_releases_is_empty_without_calendar_rows(backend_pool: asyncpg.Pool) -> None:
    assert await next_releases(backend_pool, [("fred", "us_cpi")], NOW) == {}
    assert await next_releases(backend_pool, [], NOW) == {}


# ---------------------------------------------------------------------------------------
# The job: free and gated, quota ledger, coverage, quarantine
# ---------------------------------------------------------------------------------------
async def _run(pool: asyncpg.Pool, transport: httpx.MockTransport, key: str | None = SECRET) -> str:
    async with httpx.AsyncClient(transport=transport) as http:

        async def handler(ctx: JobContext) -> None:
            await run_release_calendar(ctx, CalendarDeps(http, fred_key=key, now=NOW))

        return await run_job(pool, "macro_release_calendar", handler, trigger="manual")


@pytest.mark.db
async def test_job_stores_every_provider_calendar_and_writes_coverage_and_usage(
    backend_pool: asyncpg.Pool,
) -> None:
    assert await _run(backend_pool, _provider_transport()) == "succeeded"
    stored = {
        r["provider"]: r["n"]
        for r in await backend_pool.fetch(
            "select provider, count(*) n from macro_release_calendar group by 1"
        )
    }
    assert set(stored) == {"fred", "eurostat", "ecb", "bis", "cnb"} and all(stored.values())
    # Known-at is the retrieval time of this run; the source is always the provider calendar.
    row = await backend_pool.fetchrow(
        "select known_at, retrieved_at, source from macro_release_calendar limit 1"
    )
    assert row is not None and row["known_at"] == NOW and row["source"] == "provider_calendar"
    coverage = {
        r["dataset"]: r
        for r in await backend_pool.fetch(
            "select * from coverage_status where provider='macro_release_calendar'"
        )
    }
    assert set(coverage) == {"fred", "eurostat", "ecb", "bis", "ecfin", "cnb"}
    assert all(c["state"] == "available" and c["available_periods"] for c in coverage.values())
    usage = {
        r["provider"]: r["credits"]
        for r in await backend_pool.fetch(
            "select provider, sum(credits) credits from provider_quota_usage"
            " where endpoint='release_calendar' and window_kind='day'"
            " and window_start >= '2026-10-03'"
            " group by provider"
        )
    }
    assert usage["fred"] == 6 and usage["eurostat"] == 1 and usage["ecb"] == 4 and usage["bis"] == 1
    assert usage["ecfin"] == 2 and usage["cnb"] == 1  # this year's PDF and the unpublished next
    # The key is nowhere in the audit trail.
    audit = await backend_pool.fetchval(
        "select string_agg(coalesce(error_redacted,''),' ') from provider_fetch_audit"
    )
    assert SECRET not in (audit or "")
    # The coverage dataset has a documented default refresh and a stale basis.
    assert coverage_stale_policy(JobsConfig(), "macro_release_calendar") == (
        48,
        "documented_default",
    )


@pytest.mark.db
async def test_job_reschedule_flows_through_to_the_context(backend_pool: asyncpg.Pool) -> None:
    await _seed_context(backend_pool)
    assert await _run(backend_pool, _provider_transport()) == "succeeded"
    ctx = await context_for_holding(backend_pool, "MAPPED", NOW)
    cpi = next(o for o in ctx.observations if o.series_id == "us_cpi")
    assert (cpi.next_release, cpi.next_release_basis) == (
        dt.date(2026, 10, 14),
        "provider_calendar",
    )

    # FRED now reports the October CPI on the 15th.
    moved = json.loads(_text("fred_release_dates_cpi_10.json"))
    for item in moved["release_dates"]:
        if item["date"] == "2026-10-14":
            item["date"] = "2026-10-15"
    base = _provider_transport()

    def serve(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/release/dates") and request.url.params["release_id"] == "10":
            return httpx.Response(200, json=moved)
        return base.handler(request)  # type: ignore[return-value]

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as http:

        async def handler(ctx: JobContext) -> None:
            later = NOW + dt.timedelta(days=1)
            await run_release_calendar(ctx, CalendarDeps(http, fred_key=SECRET, now=later))

        assert (
            await run_job(backend_pool, "macro_release_calendar", handler, trigger="manual")
            == "succeeded"
        )
    now_view = await context_for_holding(backend_pool, "MAPPED", NOW + dt.timedelta(days=2))
    cpi = next(o for o in now_view.observations if o.series_id == "us_cpi")
    assert cpi.next_release == dt.date(2026, 10, 15)
    # An as_of between the two collections still sees the first announcement.
    old_view = await context_for_holding(backend_pool, "MAPPED", NOW + dt.timedelta(hours=1))
    cpi = next(o for o in old_view.observations if o.series_id == "us_cpi")
    assert cpi.next_release == dt.date(2026, 10, 14)
    assert (
        await backend_pool.fetchval(
            "select count(*) from macro_release_calendar where series_id='us_cpi'"
            " and release_date='2026-10-14' and superseded_at is not null"
        )
        == 1
    )


@pytest.mark.db
async def test_job_quarantines_drift_and_stores_nothing_for_that_provider(
    backend_pool: asyncpg.Pool,
) -> None:
    base = _provider_transport()

    def serve(request: httpx.Request) -> httpx.Response:
        if request.url.host == "data.bis.org":
            return httpx.Response(200, json={"redesigned": True})
        return base.handler(request)  # type: ignore[return-value]

    assert await _run(backend_pool, httpx.MockTransport(serve)) == "succeeded"
    assert not await backend_pool.fetchval(
        "select count(*) from macro_release_calendar where provider='bis'"
    )
    assert await backend_pool.fetchval(
        "select count(*) from macro_release_calendar where provider='fred'"
    )
    quarantined = await backend_pool.fetchrow(
        "select source, dataset from macro_payload_archive where source='bis'"
    )
    assert quarantined is not None and quarantined["dataset"] == "release_calendar:quarantine"
    cov = await backend_pool.fetchrow(
        "select state, reason from coverage_status"
        " where provider='macro_release_calendar' and dataset='bis'"
    )
    assert (
        cov is not None and cov["state"] == "temporarily_failed" and "schema drift" in cov["reason"]
    )


@pytest.mark.db
async def test_job_paid_only_response_hard_blocks_the_endpoint_and_stops_calling(
    backend_pool: asyncpg.Pool,
) -> None:
    hits: list[str] = []
    base = _provider_transport()

    def serve(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.stlouisfed.org":
            hits.append(request.url.path)
            return httpx.Response(402, text="payment required")
        return base.handler(request)  # type: ignore[return-value]

    assert await _run(backend_pool, httpx.MockTransport(serve)) == "succeeded"
    assert len(hits) == 1  # the first denial stops the provider's remaining calls
    access = await backend_pool.fetchrow(
        "select state, hard_blocked from provider_access where provider='fred'"
        " and endpoint='release_calendar'"
    )
    assert access is not None and access["state"] == "paid_only" and access["hard_blocked"]
    # The next run does not call FRED at all.
    hits.clear()
    assert await _run(backend_pool, httpx.MockTransport(serve)) == "succeeded"
    assert hits == []
    cov = await backend_pool.fetchrow(
        "select state from coverage_status"
        " where provider='macro_release_calendar' and dataset='fred'"
    )
    assert cov is not None and cov["state"] == "paid_only"


@pytest.mark.db
async def test_job_hard_stops_at_the_daily_budget(backend_pool: asyncpg.Pool) -> None:
    hits: list[str] = []
    base = _provider_transport()

    def serve(request: httpx.Request) -> httpx.Response:
        if request.url.host == "data.bis.org":
            hits.append("bis")
        return base.handler(request)  # type: ignore[return-value]

    daily = int(CALENDAR_BUDGETS["bis"].daily or 0)
    for _ in range(daily + 2):
        await _run(backend_pool, httpx.MockTransport(serve))
    assert len(hits) == daily
    cov = await backend_pool.fetchrow(
        "select state from coverage_status"
        " where provider='macro_release_calendar' and dataset='bis'"
    )
    assert cov is not None and cov["state"] == "quota_exhausted"


@pytest.mark.db
async def test_job_without_a_fred_key_skips_fred_and_still_reads_the_keyless_calendars(
    backend_pool: asyncpg.Pool,
) -> None:
    assert await _run(backend_pool, _provider_transport(), key=None) == "succeeded"
    providers = {
        r["provider"]
        for r in await backend_pool.fetch("select distinct provider from macro_release_calendar")
    }
    assert providers == {"eurostat", "ecb", "bis", "cnb"}


# ---------------------------------------------------------------------------------------
# Zero-cost gate, coverage default, registry
# ---------------------------------------------------------------------------------------
@pytest.fixture
async def client(backend_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[get_pool_or_error] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(OWNER, None, "aal2")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.mark.db
async def test_zero_cost_gate_passes_with_the_calendar_endpoints_and_fails_if_one_is_paid(
    backend_pool: asyncpg.Pool, client: httpx.AsyncClient
) -> None:
    registered = {
        (r["provider"], r["state"], r["hard_blocked"], r["account_tier"])
        for r in await backend_pool.fetch(
            "select provider,state,hard_blocked,account_tier from provider_access"
            " where endpoint='release_calendar'"
        )
    }
    assert registered == {
        ("fred", "available", False, "free"),
        ("eurostat", "available", False, "free"),
        ("ecb", "available", False, "free"),
        ("bis", "available", False, "free"),
        ("ecfin", "available", False, "free"),
        ("cnb", "available", False, "free"),
    }
    manifest = await backend_pool.fetchval(
        "select count(*) from provider_endpoint_manifest where endpoint='release_calendar'"
    )
    assert manifest == 6
    gate = (await client.get("/verification/zero-cost-gate")).json()
    assert gate["passed"] is True
    assert not [
        f
        for f in gate["findings"]
        if f["endpoint"] == "release_calendar" and f["severity"] == "violation"
    ]
    # FRED's published free limit is on the ledger; the others publish none.
    quota = await backend_pool.fetchrow(
        "select quota, quota_period from provider_access where provider='fred'"
        " and endpoint='release_calendar'"
    )
    assert quota is not None and (quota["quota"], quota["quota_period"]) == (120, "minute")

    # One of them marked paid and left reachable: the gate fails.
    await backend_pool.execute(
        "update provider_access set state='paid_only', hard_blocked=false"
        " where provider='eurostat' and endpoint='release_calendar'"
    )
    report = await audit_zero_cost(backend_pool)
    assert not report.passed
    assert {(v.provider, v.endpoint, v.code) for v in report.violations} == {
        ("eurostat", "release_calendar", "paid_reachable")
    }
    assert (await client.get("/verification/zero-cost-gate")).json()["passed"] is False


def test_calendar_dataset_has_a_documented_default_refresh() -> None:
    hours, basis = COVERAGE_DEFAULT_REFRESH_HOURS["macro_release_calendar"]
    assert hours == 24 and basis.strip()
    assert JobsConfig().macro_release_calendar.enabled  # free and verified: on by default


def test_the_collector_budgets_stay_inside_the_published_free_limit() -> None:
    assert CALENDAR_BUDGETS["fred"].per_minute is not None
    assert CALENDAR_BUDGETS["fred"].per_minute <= 120
    assert all(b.daily is not None for b in CALENDAR_BUDGETS.values())
