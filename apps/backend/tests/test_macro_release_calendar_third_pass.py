"""Third pass of the macro release calendar and the SDMX verification (2026-10-03):

* time zones of published release times (one constant table, stated vs assumed);
* the BLS iCal calendar (08:30 US Eastern) and its cross-check against FRED;
* published rules: ECB yield curves (every TARGET business day) and ČNB CZEONIA (every Czech
  working day), each with the holiday list the provider itself publishes;
* the BIS property_prices defect (WS_LONG_CPI -> WS_SPP) incl. the migration's data handling;
* every configured OECD / BIS series: dimension order and key against recorded live responses.
"""

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest

from steward.config.jobs import PublicDataSettings
from steward.macro.providers import parse_sdmx_json
from steward.macro.read import ContextPoint, context_for_holding
from steward.macro.release_calendar import (
    _effective_bis_flow,
    bls_fred_mismatches,
    next_releases,
    store_calendar,
)
from steward.macro.sdmx_keys import (
    BIS_ACCEPT,
    DOCUMENTED_DSD,
    KEY_CORRECTIONS,
    OECD_ACCEPT,
    SdmxKeyError,
    data_request,
    pin_key,
)
from steward.sources.base import AccessState
from steward.sources.release_calendar import (
    AGENCY_TIMEZONES,
    BLS_RELEASES,
    CalendarDrift,
    CalendarEntry,
    agency_timezone,
    cnb_czeonia_rule_entries,
    easter_sunday,
    ecb_yield_rule_entries,
    fetch_bis,
    fetch_bls,
    fetch_cnb,
    fetch_ecb,
    is_target_business_day,
    parse_bls_ics,
    parse_cnb_holidays,
    target_closing_days,
    verify_cnb_czeonia_rule,
    verify_ecb_target_rule,
)
from tests.test_macro_release_calendar import (
    BLS_AGENT,
    NOW,
    TODAY,
    _allow,
    _insert_calendar,
    _provider_transport,
    _run,
    _seed_context,
    _stored,
    _text,
    _utc,
)

FIXTURES = Path(__file__).parent / "fixtures"
LIVE = FIXTURES / "sdmx_live"


# ------------------------------------------------------------------------------- time zones
def test_the_timezone_table_is_one_constant_with_a_valid_zone_per_agency() -> None:
    expected = {
        "ecb": "Europe/Berlin",
        "cnb": "Europe/Prague",
        "ecfin": "Europe/Brussels",
        "eurostat": "Europe/Luxembourg",
        "bis": "Europe/Zurich",
        "bls": "America/New_York",
        "fred": "America/New_York",
        "bea": "America/New_York",
        "oecd": "Europe/Paris",
        "world_bank": "America/New_York",
        "imf": "America/New_York",
        "un_comtrade": "America/New_York",
    }
    assert AGENCY_TIMEZONES == expected
    for agency, zone in expected.items():
        assert agency_timezone(agency) == zone, agency  # ZoneInfo(zone) must exist
    with pytest.raises(KeyError):
        agency_timezone("nobody")


@pytest.mark.parametrize(("agency", "zone"), sorted(AGENCY_TIMEZONES.items()))
def test_a_timed_entry_without_a_stated_zone_gets_the_agency_zone_as_assumed(
    agency: str, zone: str
) -> None:
    entry = CalendarEntry(
        "fred", "s", "k", dt.date(2026, 11, 5), "https://x.test", dt.time(14, 30), agency=agency
    )
    assert (entry.release_timezone, entry.release_timezone_source) == (zone, "agency_location")


def test_a_stated_zone_wins_and_a_date_only_release_has_no_zone() -> None:
    stated = CalendarEntry(
        "fred",
        "s",
        "k",
        dt.date(2026, 11, 5),
        "https://x.test",
        dt.time(8, 30),
        agency="oecd",
        stated_timezone="America/New_York",
    )
    assert (stated.release_timezone, stated.release_timezone_source) == (
        "America/New_York",
        "stated_by_provider",
    )
    dated = CalendarEntry("ecb", "s", "k", dt.date(2026, 11, 5), "https://x.test")
    assert (dated.release_timezone, dated.release_timezone_source) == (None, None)
    with pytest.raises(ValueError, match="basis"):
        CalendarEntry("ecb", "s", "k", dt.date(2026, 11, 5), "https://x.test", basis="guess")


async def test_every_timed_calendar_of_the_job_carries_the_right_zone() -> None:
    async with httpx.AsyncClient(transport=_provider_transport()) as http:
        ecb = await fetch_ecb(http, TODAY, _allow)
        cnb = await fetch_cnb(http, TODAY, _allow)
    stats = next(e for e in ecb.entries if e.series_id == "euro_area_m3_growth")
    assert (stats.release_time, stats.release_timezone, stats.release_timezone_source) == (
        dt.time(10, 0),
        "Europe/Berlin",
        "agency_location",  # the ECB statistical calendar names no zone
    )
    meeting = next(e for e in ecb.entries if e.series_id == "ecb_deposit_rate")
    assert meeting.release_time is None and meeting.release_timezone is None  # dates only
    repo = next(e for e in cnb.entries if e.series_id == "cnb_repo_2w")
    assert (repo.release_time, repo.release_timezone, repo.release_timezone_source) == (
        dt.time(14, 30),
        "Europe/Prague",
        "agency_location",
    )
    yield_row = next(e for e in ecb.entries if e.series_id == "euro_area_10y_yield")
    assert (yield_row.release_time, yield_row.release_timezone) == (
        dt.time(12, 0),
        "Europe/Berlin",
    )
    assert yield_row.release_timezone_source == "stated_by_provider"  # "12:00 CET" on the page


# ------------------------------------------------------------------------------------- BLS
def test_the_bls_ics_gives_cpi_and_employment_situation_at_0830_us_eastern() -> None:
    events = parse_bls_ics(_text("bls_schedule.ics"))
    cpi = sorted(d for t, d, _ in events if t == "Consumer Price Index" and d >= TODAY)
    emp = sorted(d for t, d, _ in events if t == "Employment Situation" and d >= TODAY)
    assert cpi[:3] == [dt.date(2026, 10, 14), dt.date(2026, 11, 10), dt.date(2026, 12, 10)]
    assert emp[:2] == [dt.date(2026, 11, 6), dt.date(2026, 12, 4)]
    assert {w for t, _, w in events if t in ("Consumer Price Index", "Employment Situation")} == {
        dt.time(8, 30)
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t.replace("BEGIN:VCALENDAR", "BEGIN:VXX", 1),
        lambda t: t.replace("Bureau of Labor Statistics", "Somebody", 1),
        lambda t: t.replace("DTSTART;TZID=US-Eastern:20261014T083000", "DTSTART:20261014T083000"),
        lambda t: t.replace("TZID=US-Eastern", "TZID=Pacific"),
        lambda t: "BEGIN:VCALENDAR\nPRODID:-//Bureau of Labor Statistics//EN\nEND:VCALENDAR",
    ],
)
def test_a_changed_bls_calendar_is_drift(mutate: Any) -> None:
    with pytest.raises(CalendarDrift):
        parse_bls_ics(mutate(_text("bls_schedule.ics")))


FRED_SERIES = {"us_cpi": "CPIAUCSL", "us_unemployment": "UNRATE", "fed_funds": "DFF"}


async def test_bls_rows_are_stored_for_the_fred_series_with_the_stated_zone() -> None:
    seen: list[httpx.Request] = []

    def serve(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=_text("bls_schedule.ics"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as http:
        fetch = await fetch_bls(http, TODAY, _allow, FRED_SERIES, BLS_AGENT)
    assert fetch.access.state == AccessState.AVAILABLE and fetch.drift is None
    assert fetch.rows_provider == "fred" and fetch.calls == 1
    assert seen[0].headers["user-agent"] == BLS_AGENT  # the contact address goes to BLS only
    assert {(e.provider, e.series_id, e.calendar_key) for e in fetch.entries} == {
        ("fred", "us_cpi", "bls:cpi"),
        ("fred", "us_unemployment", "bls:empsit"),
    }  # fed_funds (H.15) is the Federal Reserve's, not BLS's
    assert all(
        (e.release_time, e.release_timezone, e.release_timezone_source)
        == (dt.time(8, 30), "America/New_York", "stated_by_provider")
        for e in fetch.entries
    )
    assert fetch.covered == {("us_cpi", "bls:cpi"), ("us_unemployment", "bls:empsit")}
    assert set(BLS_RELEASES) == {"CPIAUCSL", "UNRATE"}


@pytest.mark.parametrize("agent", [None, "", "StewardAI/1.0"])
async def test_bls_without_a_contact_address_makes_no_request(agent: str | None) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("no request expected"))
    ) as http:
        fetch = await fetch_bls(http, TODAY, _allow, FRED_SERIES, agent)
    assert fetch.access.state == AccessState.ACCESS_UNVERIFIED and fetch.calls == 0
    assert not fetch.entries


async def test_a_bls_403_is_typed_and_a_missing_event_is_drift() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(403, text="Forbidden"))
    ) as http:
        denied = await fetch_bls(http, TODAY, _allow, FRED_SERIES, BLS_AGENT)
    assert denied.access.state != AccessState.AVAILABLE and not denied.entries
    text = _text("bls_schedule.ics").replace("SUMMARY:Consumer Price Index", "SUMMARY:CPI renamed")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=text))
    ) as http:
        drift = await fetch_bls(http, TODAY, _allow, FRED_SERIES, BLS_AGENT)
    assert drift.drift is not None and "missing" in drift.drift and not drift.entries


@pytest.mark.db
async def test_bls_dates_win_over_fred_and_a_disagreement_is_reported(
    backend_pool: asyncpg.Pool,
) -> None:
    await _seed_context(backend_pool)
    moved = json.loads(_text("fred_release_dates_cpi_10.json"))
    for item in moved["release_dates"]:
        if item["date"] == "2026-10-14":
            item["date"] = "2026-10-15"  # FRED disagrees with the BLS for October
    base = _provider_transport()

    def serve(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/release/dates") and request.url.params["release_id"] == "10":
            return httpx.Response(200, json=moved)
        return base.handler(request)  # type: ignore[return-value]

    assert await _run(backend_pool, httpx.MockTransport(serve)) == "succeeded"
    step = await backend_pool.fetchval(
        "select detail::text from job_step where name like 'bls:%' order by id desc limit 1"
    )
    assert step is not None and "bls_fred_date_mismatch" in step
    mismatch = json.loads(step)["bls_fred_date_mismatch"]["us_cpi"]
    assert mismatch == {"only_bls": ["2026-10-14"], "only_fred": ["2026-10-15"]}
    ctx = await context_for_holding(backend_pool, "MAPPED", NOW)
    cpi = next(o for o in ctx.observations if o.series_id == "us_cpi")
    # the agency that publishes the release is the authority: its date and time are returned
    assert cpi.next_release == dt.date(2026, 10, 14)
    assert cpi.next_release_time == dt.time(8, 30)
    assert cpi.next_release_timezone == "America/New_York"
    assert cpi.next_release_timezone_source == "stated_by_provider"
    assert cpi.next_release_basis == "provider_calendar"
    # fed_funds has no BLS row: FRED's date-only row, no time and no zone
    ff = next((o for o in ctx.observations if o.series_id == "fed_funds"), None)
    assert ff is None or (ff.next_release_time, ff.next_release_timezone) == (None, None)


@pytest.mark.db
async def test_the_comparison_is_empty_when_both_calendars_agree(
    backend_pool: asyncpg.Pool,
) -> None:
    assert await _run(backend_pool, _provider_transport()) == "succeeded"
    async with httpx.AsyncClient(transport=_provider_transport()) as http:
        fetch = await fetch_bls(http, TODAY, _allow, FRED_SERIES, BLS_AGENT)
    assert await bls_fred_mismatches(backend_pool, fetch) == {}


@pytest.mark.db
async def test_the_job_skips_bls_without_a_contact_address_and_keeps_fred(
    backend_pool: asyncpg.Pool,
) -> None:
    assert await _run(backend_pool, _provider_transport(), bls_agent=None) == "succeeded"
    keys = {
        r["calendar_key"]
        for r in await backend_pool.fetch(
            "select distinct calendar_key from macro_release_calendar where provider='fred'"
        )
    }
    assert keys == {"release:10", "release:50", "release:18"}
    cov = await backend_pool.fetchrow(
        "select state from coverage_status where provider='macro_release_calendar'"
        " and dataset='bls'"
    )
    assert cov is not None and cov["state"] == "access_unverified"


# ------------------------------------------------------------------- published rule: TARGET
def test_easter_and_the_target_closing_days() -> None:
    assert easter_sunday(2026) == dt.date(2026, 4, 5)
    assert easter_sunday(2027) == dt.date(2027, 3, 28)
    assert easter_sunday(2038) == dt.date(2038, 4, 25)
    assert target_closing_days(2026) == {
        dt.date(2026, 1, 1),
        dt.date(2026, 4, 3),  # Good Friday
        dt.date(2026, 4, 6),  # Easter Monday
        dt.date(2026, 5, 1),
        dt.date(2026, 12, 25),
        dt.date(2026, 12, 26),
    }
    assert not is_target_business_day(dt.date(2026, 10, 3))  # a Saturday
    assert not is_target_business_day(dt.date(2026, 12, 25))
    assert is_target_business_day(dt.date(2026, 12, 24))  # not a TARGET holiday
    assert is_target_business_day(dt.date(2026, 10, 5))


def _yield_pages() -> tuple[str, str]:
    return _text("ecb_yield_curve.html"), _text("ecb_t2_opening_hours.html")


def test_the_target_rule_is_applied_only_to_the_verified_wording() -> None:
    verify_ecb_target_rule(*_yield_pages())
    page, t2 = _yield_pages()
    for bad_page, bad_t2 in (
        (page.replace("every TARGET business day", "every second day"), t2),
        (page.replace("12:00 CET", "13:00 CET"), t2),
        (page, t2.replace("Easter Monday", "Whit Monday")),
        (page, t2.replace("26 December (Boxing Day)", "")),
        (page, t2.replace("closed on Saturday and Sunday", "closed on Sunday")),
        ("", t2),
        (page, ""),
    ):
        with pytest.raises(CalendarDrift):
            verify_ecb_target_rule(bad_page, bad_t2)


def test_yield_curve_dates_are_the_target_business_days_at_noon_cet() -> None:
    rows = ecb_yield_rule_entries(dt.date(2026, 12, 20))
    days = [e.release_date for e in rows]
    # 20 Dec is a Sunday; 25 and 26 December are TARGET closing days
    assert days[:5] == [
        dt.date(2026, 12, 21),
        dt.date(2026, 12, 22),
        dt.date(2026, 12, 23),
        dt.date(2026, 12, 24),
        dt.date(2026, 12, 28),
    ]
    assert dt.date(2026, 12, 25) not in days and dt.date(2027, 1, 1) not in days
    assert all(e.basis == "published_rule" and e.release_time == dt.time(12, 0) for e in rows)
    assert {e.release_timezone_source for e in rows} == {"stated_by_provider"}
    assert "postponed" in (rows[0].title or "") and rows[0].source_url.endswith(
        "euro_area_yield_curves/html/index.en.html"
    )
    # Easter 2027: Good Friday 26 March and Easter Monday 29 March are skipped
    spring = [e.release_date for e in ecb_yield_rule_entries(dt.date(2027, 3, 25))]
    assert spring[:3] == [dt.date(2027, 3, 25), dt.date(2027, 3, 30), dt.date(2027, 3, 31)]


async def test_the_ecb_fetch_adds_the_yield_rule_and_reads_two_more_pages() -> None:
    async with httpx.AsyncClient(transport=_provider_transport()) as http:
        fetch = await fetch_ecb(http, TODAY, _allow)
    assert fetch.calls == 6  # three calendars, the meetings, the yield page, the T2 page
    rows = [e for e in fetch.entries if e.series_id == "euro_area_10y_yield"]
    assert rows[0].release_date == dt.date(2026, 10, 5)  # TODAY is a Saturday
    assert ("euro_area_10y_yield", "rule:target-business-day") in fetch.covered
    assert max(e.release_date for e in rows) <= TODAY + dt.timedelta(days=14)


async def test_a_changed_yield_curve_statement_is_drift_and_leaves_the_other_pages() -> None:
    base = _provider_transport()

    def serve(request: httpx.Request) -> httpx.Response:
        if "euro_area_yield_curves" in request.url.path:
            return httpx.Response(200, text="<p>Updated sometimes.</p>")
        return base.handler(request)  # type: ignore[return-value]

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as http:
        fetch = await fetch_ecb(http, TODAY, _allow)
    assert fetch.drift is not None and "TARGET business day rule" in fetch.drift
    assert not [e for e in fetch.entries if e.series_id == "euro_area_10y_yield"]
    assert [e for e in fetch.entries if e.series_id == "euro_area_m3_growth"]  # still usable


# --------------------------------------------------------------------- published rule: ČNB
def test_the_cnb_holiday_list_is_parsed_strictly() -> None:
    holidays = parse_cnb_holidays(_text("cnb_bank_holidays.html"))
    assert set(holidays) == {2025, 2026} and all(len(v) == 13 for v in holidays.values())
    assert dt.date(2026, 10, 28) in holidays[2026] and dt.date(2026, 11, 17) in holidays[2026]
    # the page says the sliding-holiday concept is not applied: 28 Sep 2025 (Sunday) is just listed
    assert dt.date(2025, 9, 28) in holidays[2025]
    page = _text("cnb_bank_holidays.html")
    for broken in (
        page.replace(
            "<p>28 October \u2013 Establishment of the Czechoslovak Republic (Wednesday)</p>", ""
        ),
        page.replace("(Wednesday)", "(Thursday)", 1),
        page.replace('The "sliding holiday" concept is not applied', "x"),
        page.replace("6 April \u2013 Easter (Monday)", "7 April \u2013 Easter (Tuesday)"),
        "<html></html>",
    ):
        with pytest.raises(CalendarDrift):
            parse_cnb_holidays(broken)


def test_the_czeonia_rule_needs_the_stated_publication_sentence() -> None:
    verify_cnb_czeonia_rule(_text("cnb_czeonia_methodology.html"))
    for bad in (
        "",
        "<p>published when ready</p>",
        _text("cnb_czeonia_methodology.html").replace("D+1", "D+2"),
    ):
        with pytest.raises(CalendarDrift):
            verify_cnb_czeonia_rule(bad)


def test_czeonia_dates_are_czech_working_days_inside_the_published_holiday_lists() -> None:
    holidays = parse_cnb_holidays(_text("cnb_bank_holidays.html"))
    rows = cnb_czeonia_rule_entries(holidays, dt.date(2026, 10, 26))
    days = [e.release_date for e in rows]
    # 28 Oct (Wed) is a bank holiday; 31 Oct / 1 Nov are the weekend; 17 Nov is a bank holiday
    assert days[:5] == [
        dt.date(2026, 10, 26),
        dt.date(2026, 10, 27),
        dt.date(2026, 10, 29),
        dt.date(2026, 10, 30),
        dt.date(2026, 11, 2),
    ]
    assert dt.date(2026, 11, 17) not in [
        e.release_date for e in cnb_czeonia_rule_entries(holidays, dt.date(2026, 11, 16))
    ]
    assert all(e.basis == "published_rule" for e in rows)
    # No time (the ČNB says "usually by 9.30 a.m."), hence no zone
    assert all(e.release_time is None and e.release_timezone is None for e in rows)
    assert "9.30" in (rows[0].title or "")
    # 2027 has no published list yet: the dates stop at the end of 2026
    end = cnb_czeonia_rule_entries(holidays, dt.date(2026, 12, 28))
    assert [e.release_date for e in end] == [
        dt.date(2026, 12, 28),
        dt.date(2026, 12, 29),
        dt.date(2026, 12, 30),
        dt.date(2026, 12, 31),
    ]
    assert cnb_czeonia_rule_entries(holidays, dt.date(2027, 1, 4)) == []


async def test_the_cnb_fetch_dates_czeonia_and_the_repo_decisions() -> None:
    async with httpx.AsyncClient(transport=_provider_transport()) as http:
        fetch = await fetch_cnb(http, TODAY, _allow)
    assert fetch.calls == 3 and fetch.drift is None
    assert ("czeonia", "rule:czech-working-day") in fetch.covered
    assert ("cnb_repo_2w", "bank-board-monetary-policy") in fetch.covered
    czeonia = [e for e in fetch.entries if e.series_id == "czeonia"]
    assert czeonia[0].release_date == dt.date(2026, 10, 5) and czeonia[0].basis == "published_rule"


async def test_a_failing_holiday_page_keeps_the_repo_dates() -> None:
    base = _provider_transport()

    def serve(request: httpx.Request) -> httpx.Response:
        if "bank-holidays" in request.url.path:
            return httpx.Response(404, text="gone")
        return base.handler(request)  # type: ignore[return-value]

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as http:
        fetch = await fetch_cnb(http, TODAY, _allow)
    assert [e for e in fetch.entries if e.series_id == "cnb_repo_2w"]
    assert not [e for e in fetch.entries if e.series_id == "czeonia"]
    assert fetch.access.state != AccessState.AVAILABLE


@pytest.mark.db
async def test_rule_rows_are_stored_read_and_labelled_published_rule(
    backend_pool: asyncpg.Pool,
) -> None:
    async with httpx.AsyncClient(transport=_provider_transport()) as http:
        ecb = await fetch_ecb(http, TODAY, _allow)
        cnb = await fetch_cnb(http, TODAY, _allow)
    await store_calendar(backend_pool, ecb, NOW)
    await store_calendar(backend_pool, cnb, NOW)
    got = await next_releases(
        backend_pool,
        [("ecb", "euro_area_10y_yield"), ("cnb", "czeonia"), ("cnb", "cnb_repo_2w")],
        NOW,
    )
    y, z, repo = (
        got[("ecb", "euro_area_10y_yield")],
        got[("cnb", "czeonia")],
        got[("cnb", "cnb_repo_2w")],
    )
    assert (y.basis, y.release_date, y.release_time, y.release_timezone) == (
        "published_rule",
        dt.date(2026, 10, 5),
        dt.time(12, 0),
        "Europe/Berlin",
    )
    assert y.release_timezone_source == "stated_by_provider"
    assert (z.basis, z.release_date, z.release_time, z.release_timezone) == (
        "published_rule",
        dt.date(2026, 10, 5),
        None,
        None,
    )
    assert (repo.basis, repo.release_timezone, repo.release_timezone_source) == (
        "provider_calendar",
        "Europe/Prague",
        "agency_location",
    )
    # the rule slides forward: a later run adds the new day and keeps the old rows
    later = NOW + dt.timedelta(days=3)
    async with httpx.AsyncClient(transport=_provider_transport()) as http:
        again = await fetch_ecb(http, later.date(), _allow)
    result = await store_calendar(backend_pool, again, later)
    assert result.inserted >= 1 and result.superseded == 0


@pytest.mark.db
async def test_the_database_refuses_a_zone_without_a_time_or_an_unknown_basis(
    backend_pool: asyncpg.Pool,
) -> None:
    insert = (
        "insert into macro_release_calendar(provider,series_id,calendar_key,release_date,"
        "release_time,source_url,known_at,source,release_timezone,release_timezone_source)"
        " values ('ecb','s','k','2026-11-05',$1,'https://x.test',now(),$2,$3,$4)"
    )
    ok_time = dt.time(10, 0)
    await backend_pool.execute(
        insert, ok_time, "provider_calendar", "Europe/Berlin", "agency_location"
    )
    with pytest.raises(asyncpg.CheckViolationError):  # a zone needs a time
        await backend_pool.execute(
            insert, None, "provider_calendar", "Europe/Berlin", "agency_location"
        )
    with pytest.raises(asyncpg.CheckViolationError):  # zone and source come as a pair
        await backend_pool.execute(insert, ok_time, "provider_calendar", "Europe/Berlin", None)
    with pytest.raises(asyncpg.CheckViolationError):
        await backend_pool.execute(insert, ok_time, "provider_calendar", "Europe/Berlin", "guess")
    with pytest.raises(asyncpg.CheckViolationError):
        await backend_pool.execute(insert, ok_time, "estimate", None, None)
    await backend_pool.execute(
        insert.replace("'s','k'", "'t','k'"), None, "published_rule", None, None
    )


@pytest.mark.db
async def test_context_exposes_zone_and_source_only_with_a_time(
    backend_pool: asyncpg.Pool,
) -> None:
    await _seed_context(backend_pool)
    as_of = _utc("2026-07-01")
    await _insert_calendar(
        backend_pool,
        _stored("fred", "us_cpi", "release:10", "2026-07-14", "2026-06-12"),
    )
    ctx = await context_for_holding(backend_pool, "MAPPED", as_of)
    cpi = next(o for o in ctx.observations if o.series_id == "us_cpi")
    assert (cpi.next_release_time, cpi.next_release_timezone, cpi.next_release_timezone_source) == (
        None,
        None,
        None,
    )
    fields = ContextPoint.model_fields
    assert fields["next_release_timezone"].default is None
    assert "stated_by_provider" in str(fields["next_release_timezone_source"].annotation)
    assert "published_rule" in str(fields["next_release_basis"].annotation)
    assert "never converted" in (fields["next_release_time"].description or "")


def test_the_openapi_document_carries_the_zone_fields_and_the_new_basis() -> None:
    schema = json.loads((Path(__file__).parents[1] / "openapi.json").read_text())
    point = schema["components"]["schemas"]["ContextPoint"]["properties"]
    assert {"next_release_timezone", "next_release_timezone_source"} <= set(point)
    assert "published_rule" in json.dumps(point["next_release_basis"])
    assert "agency_location" in json.dumps(point["next_release_timezone_source"])


# ---------------------------------------------------------------- BIS property_prices defect
async def test_property_prices_is_dated_from_the_residential_category_not_cpi() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, text=_text("bis_releasecalendar.json"))
        )
    ) as http:
        shipped = await fetch_bis(http, TODAY, _allow)
        stored_config = await fetch_bis(http, TODAY, _allow, {"property_prices": "WS_SPP"})
    for fetch in (shipped, stored_config):
        rows = [e for e in fetch.entries if e.series_id == "property_prices"]
        assert {e.calendar_key for e in rows} == {"RPP"}
        assert min(e.release_date for e in rows) == dt.date(2026, 10, 29)
        # only the "Selected residential" releases update WS_SPP, not the detailed WS_DPP ones
        assert {e.reference_period for e in rows} >= {"2026-Q2"}
        assert not any(e.reference_period and e.reference_period.startswith("2026-0") for e in rows)


def test_a_stored_config_with_the_old_flow_is_dated_through_the_corrected_flow() -> None:
    old = next(s for s in PublicDataSettings().sdmx if s.key == "property_prices").model_copy(
        update={"flow": "BIS,WS_LONG_CPI,1.0", "series_key": ".Q.R"}
    )
    assert _effective_bis_flow(old) == "WS_SPP"
    unusable = old.model_copy(update={"series_key": "Q.R"})  # the collector reports this one
    assert _effective_bis_flow(unusable) == "WS_LONG_CPI"


def test_the_shipped_property_prices_series_is_the_residential_dataflow() -> None:
    spp = next(s for s in PublicDataSettings().sdmx if s.key == "property_prices")
    assert spp.flow == "BIS,WS_SPP,1.0" and "residential" in spp.definition.lower()
    pinned = pin_key("bis", spp.flow, spp.series_key, spp.geography)
    assert (pinned.flow, pinned.key, pinned.corrected) == ("BIS,WS_SPP,1.0", "Q.US.R.628", False)
    # the old stored key is corrected, with a unit that says what the series now is
    old = pin_key("bis", "BIS,WS_LONG_CPI,1.0", ".Q.R", "US")
    assert old.corrected and old.flow == "BIS,WS_SPP,1.0" and "2010 = 100" in (old.unit or "")


MIGRATION = "20261007140000_macro_release_calendar_third_pass"
ROOT = Path(__file__).resolve().parents[3] / "supabase"


async def _property_rows(pool: Any) -> None:
    insert = (
        "insert into macro_observation (series_id,source,geography,period,value,unit,frequency,"
        " known_at,vintage,revision,source_url,metadata) values ($1,'bis','US',$2,$3,'index',"
        "'quarterly','2026-10-01','v0',0,$4,$5::jsonb)"
    )
    cpi_url = "https://stats.bis.org/api/v2/data/dataflow/BIS/WS_LONG_CPI/1.0/Q.US.?x=1"
    spp_url = "https://stats.bis.org/api/v2/data/dataflow/BIS/WS_SPP/1.0/Q.US.R.628"
    await pool.execute(  # consumer prices stored as property prices (the defect)
        insert,
        "property_prices",
        dt.date(2026, 1, 1),
        Decimal("153.6"),
        cpi_url,
        json.dumps({"dimensions": {"FREQ": "Q", "UNIT_MEASURE": "628"}}),
    )
    await pool.execute(  # the same shape with no URL evidence: no VALUE dimension, not SPP
        insert,
        "property_prices",
        dt.date(2026, 4, 1),
        Decimal("154.1"),
        "https://x.test/",
        json.dumps({"dimensions": {"FREQ": "Q"}}),
    )
    await pool.execute(  # a genuine WS_SPP row stays
        insert,
        "property_prices",
        dt.date(2026, 7, 1),
        Decimal("154.2"),
        spp_url,
        json.dumps({"dimensions": {"VALUE": "R", "UNIT_MEASURE": "628"}}),
    )
    await pool.execute(  # another series is never touched
        insert,
        "total_credit",
        dt.date(2026, 1, 1),
        Decimal("139.7"),
        cpi_url,
        "{}",
    )
    await pool.execute(
        "insert into macro_release_calendar(provider,series_id,calendar_key,release_date,"
        "source_url,known_at) values ('bis','property_prices','CPI','2026-10-29','https://x.test',"
        "'2026-10-03'),('bis','property_prices','RPP','2026-11-26','https://x.test','2026-10-03')"
    )


@pytest.mark.db
async def test_the_migration_rekeys_mislabelled_cpi_rows_and_the_rollback_restores_them(
    backend_pool: asyncpg.Pool, migrated_db: Any
) -> None:
    admin = await asyncpg.connect(migrated_db.admin_url)
    up = (ROOT / "migrations" / f"{MIGRATION}.sql").read_text()
    down = (ROOT / "rollback" / f"{MIGRATION}.down.sql").read_text()
    # the fixture database already has the migration applied: undo it, seed the defect, redo it
    await admin.execute(down)
    await _property_rows(admin)
    before = await admin.fetch("select series_id,period,value from macro_observation order by id")
    await admin.execute(up)

    rows = await admin.fetch(
        "select series_id,period,value,metadata->>'rekeyed_from' src from macro_observation"
        " order by id"
    )
    assert [(r["series_id"], r["period"], r["value"], r["src"]) for r in rows] == [
        ("bis_long_cpi", dt.date(2026, 1, 1), Decimal("153.6"), "property_prices"),
        ("bis_long_cpi", dt.date(2026, 4, 1), Decimal("154.1"), "property_prices"),
        ("property_prices", dt.date(2026, 7, 1), Decimal("154.2"), None),
        ("total_credit", dt.date(2026, 1, 1), Decimal("139.7"), None),
    ]
    # nothing readable as property prices came from the consumer-price flow
    assert (
        await admin.fetchval(
            "select count(*) from macro_observation where series_id='property_prices'"
            " and source_url like '%WS_LONG_CPI%'"
        )
        == 0
    )
    meta = await admin.fetchrow(
        "select label,frequency,unit from macro_series_metadata where series_id='bis_long_cpi'"
    )
    assert meta is not None and meta["frequency"] == "quarterly" and meta["unit"] == "index"
    # the CPI-category calendar rows are superseded (kept), the residential ones stay live
    cal = {
        r["calendar_key"]: r["superseded_at"] is not None
        for r in await admin.fetch(
            "select calendar_key, superseded_at from macro_release_calendar"
            " where series_id='property_prices'"
        )
    }
    assert cal == {"CPI": True, "RPP": False}
    got = await next_releases(backend_pool, [("bis", "property_prices")], _utc("2026-10-04"))
    assert got[("bis", "property_prices")].release_date == dt.date(2026, 11, 26)

    # rollback: the exact original rows come back, nothing is lost
    await admin.execute(down)
    after = await admin.fetch("select series_id,period,value from macro_observation order by id")
    assert [tuple(r) for r in after] == [tuple(r) for r in before]
    assert (
        await admin.fetchval(
            "select count(*) from macro_observation where metadata ? 'rekeyed_from'"
        )
        == 0
    )
    assert (
        await admin.fetchval(
            "select count(*) from macro_release_calendar where superseded_at is not null"
        )
        == 0
    )
    await admin.execute(up)  # and the migration can be applied again
    await admin.close()


@pytest.mark.db
async def test_the_migration_backfills_zones_only_for_rows_with_a_time(
    backend_pool: asyncpg.Pool, migrated_db: Any
) -> None:
    admin = await asyncpg.connect(migrated_db.admin_url)
    up = (ROOT / "migrations" / f"{MIGRATION}.sql").read_text()
    down = (ROOT / "rollback" / f"{MIGRATION}.down.sql").read_text()
    await admin.execute(down)
    insert = (
        "insert into macro_release_calendar(provider,series_id,calendar_key,release_date,"
        "release_time,source_url,known_at) values ($1,$2,'k','2026-11-05',$3,'https://x.test',now())"
    )
    t = dt.time(10, 0)
    for provider, series, when in (
        ("ecb", "euro_area_m3_growth", t),
        ("ecb", "ecb_deposit_rate", None),
        ("cnb", "cnb_repo_2w", dt.time(14, 30)),
        ("eurostat", "sentiment", dt.time(11, 0)),
        ("eurostat", "hicp", None),
        ("fred", "us_cpi", None),
        ("bis", "total_credit", None),
    ):
        await admin.execute(insert, provider, series, when)
    await admin.execute(up)
    got = {
        r["series_id"]: (r["release_timezone"], r["release_timezone_source"])
        for r in await admin.fetch(
            "select series_id,release_timezone,release_timezone_source from macro_release_calendar"
        )
    }
    assert got == {
        "euro_area_m3_growth": ("Europe/Berlin", "agency_location"),
        "ecb_deposit_rate": (None, None),
        "cnb_repo_2w": ("Europe/Prague", "agency_location"),
        "sentiment": ("Europe/Brussels", "agency_location"),
        "hicp": (None, None),
        "us_cpi": (None, None),
        "total_credit": (None, None),
    }
    # the table is still append-only after the deliberate backfill
    with pytest.raises(asyncpg.PostgresError):
        await admin.execute("update macro_release_calendar set title='x'")
    await admin.close()


# ------------------------------------------------------ SDMX dimension orders (live-verified)
# series key -> (recorded live response, flow id the response came from). The responses were
# requested on 2026-10-03 with the keys the configuration pins (see sdmx_keys.KEY_CORRECTIONS).
LIVE_SERIES = {
    "bis": [
        "total_credit",
        "debt_service",
        "banking_claims",
        "debt_securities",
        "global_liquidity",
        "property_prices",
    ],
    "oecd": [
        "inflation",
        "unemployment",
        "productivity",
        "wages",
        "consumer_confidence",
        "business_confidence",
        "housing",
    ],
}
SHIPPED = {(s.provider, s.key): s for s in PublicDataSettings().sdmx}


def test_every_shipped_series_has_a_recorded_live_response() -> None:
    assert set(SHIPPED) == {(p, k) for p, keys in LIVE_SERIES.items() for k in keys}
    for provider, keys in LIVE_SERIES.items():
        for key in keys:
            assert (LIVE / f"{provider}_{key}.json").exists()


@pytest.mark.parametrize(
    ("provider", "key"), [(p, k) for p, keys in LIVE_SERIES.items() for k in keys]
)
def test_the_pinned_key_matches_the_live_dimension_order_and_selects_one_series(
    provider: str, key: str
) -> None:
    series = SHIPPED[(provider, key)]
    pinned = pin_key(provider, series.flow, series.series_key, series.geography)
    assert pinned.validated and not pinned.corrected  # the shipped defaults need no correction
    body = json.loads((LIVE / f"{provider}_{key}.json").read_text())
    data = body["data"]
    structure = data["structures"][0] if "structures" in data else data["structure"]
    dims = [d["id"] for d in structure["dimensions"]["observation"] if d["id"] != "TIME_PERIOD"]
    flow_id = ",".join(pinned.flow.split(",")[:2])
    assert tuple(dims) == DOCUMENTED_DSD[flow_id], f"{flow_id}: dimension order differs from live"
    slots = pinned.key.split(".")
    assert len(slots) == len(dims)
    points = parse_sdmx_json(
        body,
        source=provider,
        series_id=key,
        geography=series.geography,
        unit=series.unit,
        frequency=series.frequency,
        source_url="https://example.test/live",
        vintage="2026-10-03",
        expect_geography=pinned.area_code,
        geography_map={"USA": "US"} if provider == "oecd" else None,
    )
    assert len(points) >= 1 and {p.geography for p in points} == {"US"}
    for point in points:
        # every slot of the pinned key is the value of that dimension in the response
        assert [point.metadata["dimensions"][d] for d in dims] == slots
        assert point.value is not None
    assert len({p.period for p in points}) == len(points)  # one series: one value per period


def test_bis_values_carry_their_unit_multiplier_and_oecd_periods_parse() -> None:
    lbs = json.loads((LIVE / "bis_banking_claims.json").read_text())
    points = parse_sdmx_json(
        lbs,
        source="bis",
        series_id="banking_claims",
        geography="US",
        unit="USD (millions)",
        frequency="quarterly",
        source_url="u",
        vintage="v",
    )
    assert {p.unit_multiplier for p in points} == {Decimal(1_000_000)}
    spp = json.loads((LIVE / "bis_property_prices.json").read_text())
    got = parse_sdmx_json(
        spp,
        source="bis",
        series_id="p",
        geography="US",
        unit="i",
        frequency="quarterly",
        source_url="u",
        vintage="v",
    )
    assert max(p.period for p in got) == dt.date(2026, 4, 1)  # 2026-Q2 starts 1 April


def test_the_live_responses_exist_for_the_flows_the_calendar_maps() -> None:
    # property prices: real index, 2010 = 100 (VALUE R, UNIT_MEASURE 628)
    spp = json.loads((LIVE / "bis_property_prices.json").read_text())
    obs = spp["data"]["structure"]["dimensions"]["observation"]
    codes = {d["id"]: [v["id"] for v in d["values"]] for d in obs}
    assert codes["VALUE"] == ["R"] and codes["UNIT_MEASURE"] == ["628"]
    assert "WS_SPP" in json.dumps(spp["data"]["dataSets"][0]["links"])


def test_every_correction_maps_a_broken_key_to_a_validated_one() -> None:
    for (flow, key), fix in KEY_CORRECTIONS.items():
        provider = "oecd" if flow.startswith("OECD") else "bis"
        geography = "US"
        pinned = pin_key(provider, flow, key, geography)
        assert pinned.corrected and pinned.validated, (flow, key)
        assert pinned.flow == fix.flow
        assert "{area}" not in pinned.key
    # keys the old defaults carried that cannot be pinned are rejected, not guessed
    with pytest.raises(SdmxKeyError, match="dimensions"):
        pin_key("bis", "BIS,WS_LBS_D_PUB,1.0", ".Q.S", "US")
    with pytest.raises(SdmxKeyError, match="dimensions"):
        pin_key("oecd", "OECD.SDD.TPS,DSD_RHPI@DF_RHPI_ALL,1.1", "USA.Q.RHPI", "US")


def test_the_oecd_housing_area_slot_is_not_the_first_one() -> None:
    pinned = pin_key(
        "oecd",
        "OECD.SDD.TPS,DSD_RHPI@DF_RHPI_ALL,1.1",
        "COU..Q.RHPI.IX.N._Z.EXISTING.SINGLE_F",
        "US",
    )
    assert pinned.key == "COU.USA.Q.RHPI.IX.N._Z.EXISTING.SINGLE_F"
    with pytest.raises(SdmxKeyError, match="area slot"):
        pin_key("oecd", "OECD.SDD.TPS,DSD_RHPI@DF_RHPI_ALL,1.1", "COU.DEU.Q.RHPI.IX.N._Z.E.S", "US")


def test_each_provider_gets_the_request_form_its_api_accepts() -> None:
    url, accept = data_request(
        "bis", "https://stats.bis.org/api/v2/", "BIS,WS_SPP,1.0", "Q.US.R.628"
    )
    assert url == "https://stats.bis.org/api/v2/data/dataflow/BIS/WS_SPP/1.0/Q.US.R.628"
    assert accept == BIS_ACCEPT == "application/vnd.sdmx.data+json;version=1.0.0"
    url, accept = data_request(
        "oecd",
        "https://sdmx.oecd.org/public/rest/v1",
        "OECD.SDD.TPS,DSD_PDB@DF_PDB,2.0",
        "USA.A.GDPHRS._T.USD_PPP_H.LR.N._Z.PPP",
    )
    assert url == (
        "https://sdmx.oecd.org/public/rest/v1/data/OECD.SDD.TPS,DSD_PDB@DF_PDB,2.0/"
        "USA.A.GDPHRS._T.USD_PPP_H.LR.N._Z.PPP"
    )
    assert accept == OECD_ACCEPT
    with pytest.raises(SdmxKeyError):
        data_request("bis", "https://stats.bis.org/api/v2", "BIS,WS_SPP", "Q.US.R.628")


def test_the_shipped_defaults_cover_the_documented_dimension_orders() -> None:
    for (provider, key), series in SHIPPED.items():
        pinned = pin_key(provider, series.flow, series.series_key, series.geography)
        assert pinned.validated, key


# ---------------------------------------------------------------- ALFRED output_type=1 (live)
ALFRED = FIXTURES / "alfred_live"


def _alfred_rows() -> list[dict[str, Any]]:
    return json.loads((ALFRED / "cpiaucsl_lin_output_type_1.json").read_text())["observations"]


def test_alfred_windows_start_at_the_real_first_release_not_at_the_request_start() -> None:
    # recorded with realtime_start = 1776-07-04: June 2024 CPI was first published on 11 July 2024
    first = min(r["realtime_start"] for r in _alfred_rows() if r["date"] == "2024-06-01")
    assert first == "2024-07-11"
    # and a revision (the February seasonal-factor update) is a second window of the same period
    windows = [r for r in _alfred_rows() if r["date"] == "2024-06-01"]
    assert len(windows) == 3 and windows[0]["value"] != windows[-1]["value"]


def test_pc1_vintages_derived_from_levels_equal_fred_pc1_for_the_current_vintage() -> None:
    from steward.macro.collect import derive_pc1_vintages

    derived = derive_pc1_vintages(_alfred_rows())
    latest = {}
    for row in derived:  # the last emitted vintage of each period is the current one
        latest[row["date"]] = row["value"]
    truth = json.loads((ALFRED / "cpiaucsl_pc1_current.json").read_text())["observations"]
    assert len(truth) >= 14
    checked = 0
    for row in truth:
        if row["value"] == ".":  # FRED has no value (October 2025: no CPI was published)
            assert row["date"] not in latest  # nothing is guessed for a missing level
            continue
        assert latest[row["date"]] == row["value"], row["date"]
        checked += 1
    assert checked >= 12
    # an observation revised by the seasonal factors has several vintages, each with its own value
    june = [r for r in derived if r["date"] == "2025-06-01"]
    assert len(june) >= 2 and june[0]["realtime_start"] < june[-1]["realtime_start"]
    assert june[-1]["realtime_end"] == "9999-12-31" and len({r["value"] for r in june}) == len(june)


def test_pc1_needs_both_levels_known_on_the_vintage_date() -> None:
    from steward.macro.collect import derive_pc1_vintages

    rows = [
        {"date": "2025-06-01", "value": "100", "realtime_start": "2025-07-01",
         "realtime_end": "9999-12-31"},
        {"date": "2026-06-01", "value": "103", "realtime_start": "2026-07-01",
         "realtime_end": "2026-12-31"},
        {"date": "2026-06-01", "value": "104", "realtime_start": "2027-01-01",
         "realtime_end": "9999-12-31"},
        {"date": "2026-07-01", "value": "104", "realtime_start": "2026-08-01",
         "realtime_end": "9999-12-31"},  # no level a year earlier: no row, nothing is guessed
    ]  # fmt: skip
    out = derive_pc1_vintages(rows)
    assert [(r["date"], r["value"], r["realtime_start"], r["realtime_end"]) for r in out] == [
        ("2026-06-01", "3.00000", "2026-07-01", "2026-12-31"),
        ("2026-06-01", "4.00000", "2027-01-01", "9999-12-31"),
    ]


@pytest.mark.db
async def test_alfred_is_requested_as_levels_from_the_earliest_realtime_date(
    backend_pool: asyncpg.Pool,
) -> None:
    from steward.config.jobs import FredSeries
    from steward.macro.collect import MacroError, store_fred_vintages

    seen: list[httpx.Request] = []
    body = (ALFRED / "cpiaucsl_lin_output_type_1.json").read_text()

    def serve(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.params["units"] != "lin":  # what the live API answers
            return httpx.Response(400, json={"error_code": 400})
        return httpx.Response(200, text=body)

    cpi = FredSeries(key="us_cpi", label="CPI", series_id="CPIAUCSL", units="pc1")
    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as http:
        stored = await store_fred_vintages(
            backend_pool, http, cpi, "secret", dt.date(2026, 10, 3), history_years=1
        )
        assert stored > 0
        params = seen[0].url.params
        assert (params["units"], params["realtime_start"], params["output_type"]) == (
            "lin",
            "1776-07-04",
            "1",
        )
        rows = await backend_pool.fetch(
            "select period, value, vintage, transformation from macro_observation"
            " where series_id='us_cpi' order by period, vintage"
        )
        assert rows and {r["transformation"] for r in rows} == {"percent change from year ago"}
        # percent change from a year ago, never index levels under a % unit
        assert all(Decimal(0) < r["value"] < Decimal(10) for r in rows)
        # a series with another transformation has no vintage history: reported, not faked
        pch = FredSeries(key="x", label="x", series_id="CPIAUCSL", units="pch")
        with pytest.raises(MacroError, match="no real-time history"):
            await store_fred_vintages(backend_pool, http, pch, "k", dt.date(2026, 10, 3), 1)


# ------------------------------------------------------------------------------ ECB keys
def test_known_broken_ecb_defaults_are_corrected_to_the_keys_the_live_api_serves() -> None:
    from steward.config.jobs import MacroSettings
    from steward.macro.sdmx_keys import ECB_KEY_CORRECTIONS, ecb_flow_key

    shipped = {s.key: s.flow_key for s in MacroSettings().ecb}
    # no shipped default repeats the dataset prefix in the key (HTTP 400 live) or needs a fix
    assert all(ecb_flow_key(k) == k for k in shipped.values())
    assert not [k for k in shipped.values() if k.split("/", 1)[1].startswith(k.split("/", 1)[0])]
    assert ecb_flow_key("BSI/BSI.M.U2.Y.V.M30.A.I.U2.0000.Z01.A") == (
        "BSI/M.U2.Y.V.M30.X.I.U2.2300.Z01.A"
    )
    assert ecb_flow_key("MIR/M.U2.B.A2I.AM.R.A.2240.EUR.N") == "MIR/M.U2.B.A2I.AM.R.A.2240.EUR.N"
    assert len(ECB_KEY_CORRECTIONS) == 4
