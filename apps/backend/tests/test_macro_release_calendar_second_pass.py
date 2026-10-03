"""Second pass of the release calendar: the ECB Governing Council meetings, the DG ECFIN survey
calendar, the ČNB Bank Board meetings, the corrected Eurostat and BIS mappings, and the
exact-series-before-dataset read path. Parsers run on recorded live responses (2026-10-03)."""

import datetime as dt
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest

from steward.macro.release_calendar import (
    CALENDAR_BUDGETS,
    CALENDAR_PROVIDERS,
    ENDPOINT,
    next_releases,
    store_calendar,
)
from steward.sources.base import AccessState
from steward.sources.release_calendar import (
    BIS_FLOW_CATEGORY,
    DEFAULT_BIS_FLOWS,
    EURO_AREA_GEOS,
    CalendarDrift,
    ecfin_pdf_text,
    fetch_bis,
    fetch_cnb,
    fetch_ecb,
    fetch_ecfin,
    fetch_eurostat,
    parse_cnb_bank_board,
    parse_ecb_decisions,
    parse_ecfin_calendar,
    series_with_calendar,
)

FIXTURES = Path(__file__).parent / "fixtures" / "release_calendar"
TODAY = dt.date(2026, 10, 3)
NOW = dt.datetime(2026, 10, 3, 16, 0, tzinfo=dt.UTC)


def _text(name: str) -> str:
    return (FIXTURES / name).read_text()


def _pdf() -> bytes:
    return (FIXTURES / "ecfin_publication_dates_2026.pdf").read_bytes()


async def _allow() -> None:
    return None


# ----------------------------------------------------------------------------- ECB meetings
def test_ecb_decisions_are_day_two_of_the_monetary_policy_meetings() -> None:
    days = parse_ecb_decisions(_text("ecb_mgcgc.html"))
    assert days[:3] == [dt.date(2026, 10, 29), dt.date(2026, 12, 17), dt.date(2027, 2, 4)]
    # Day 1 (28 Oct), the non-monetary meetings and the General Council are not decisions.
    assert dt.date(2026, 10, 28) not in days and dt.date(2026, 11, 25) not in days
    assert dt.date(2026, 11, 26) not in days


def test_a_day_one_entry_missing_its_label_is_still_a_day_one() -> None:
    # The live page has "monetary policy meeting in Frankfurt" without "(Day 1)" on 2028-10-11.
    page = _text("ecb_mgcgc.html").replace(
        "monetary policy meeting in Frankfurt (Day 1)", "monetary policy meeting in Frankfurt"
    )
    assert parse_ecb_decisions(page)[0] == dt.date(2026, 10, 29)


@pytest.mark.parametrize(
    "page",
    [
        "<html></html>",
        '<div class="definition-list"><dl></dl></div>',
        # a Day 2 without its Day 1 on the previous day
        '<div class="definition-list"><dl><dt>29/10/2026</dt><dd>Governing Council of the ECB: '
        "monetary policy meeting in Frankfurt (Day 2), followed by press conference"
        "</dd></dl></div>",
        # no monetary policy decision at all
        '<div class="definition-list"><dl><dt>26/11/2026</dt><dd>General Council meeting of the '
        "ECB in Frankfurt</dd></dl></div>",
    ],
)
def test_ecb_meeting_layout_changes_are_drift(page: str) -> None:
    with pytest.raises(CalendarDrift):
        parse_ecb_decisions(page)


# -------------------------------------------------------------------------------- DG ECFIN
def test_ecfin_pdf_gives_date_and_time_of_the_sentiment_release() -> None:
    rows = parse_ecfin_calendar(ecfin_pdf_text(_pdf()), 2026)
    assert len(rows) == 12
    assert rows[9] == (dt.date(2026, 10, 29), dt.time(11, 0))
    # December's results come out in January of the next year.
    assert rows[11] == (dt.date(2027, 1, 7), dt.time(11, 0))
    # Past months are in the file too (the fetcher keeps only upcoming dates).
    assert rows[8][0] == dt.date(2026, 9, 29)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t.replace("Publication dates 2026", "Dates 2026"),
        lambda t: t.replace("Business and consumer survey results", "Surveys"),
        lambda t: t.replace("October 1) 29 October 2026", "November 1) 29 October 2026"),
        lambda t: "\n".join(line for line in t.splitlines() if not line.startswith("May ")),
        lambda t: t.replace("29 October 2026 11h00", "31 February 2026 11h00"),
    ],
)
def test_ecfin_layout_changes_are_drift(mutate: Any) -> None:
    with pytest.raises(CalendarDrift):
        parse_ecfin_calendar(mutate(ecfin_pdf_text(_pdf())), 2026)


def test_a_pdf_that_is_not_a_pdf_is_drift() -> None:
    with pytest.raises(CalendarDrift):
        ecfin_pdf_text(b"<html>maintenance</html>")


def _ecfin_transport(next_year_status: int = 404) -> httpx.MockTransport:
    def serve(request: httpx.Request) -> httpx.Response:
        if "2026" in request.url.path:
            return httpx.Response(200, content=_pdf())
        return httpx.Response(next_year_status, text="x")

    return httpx.MockTransport(serve)


@pytest.mark.parametrize("next_year_status", [404, 301])
async def test_ecfin_dates_the_eurostat_sentiment_series_with_time(next_year_status: int) -> None:
    # An unpublished next-year PDF is a 404, or (observed 2026-10-03) a redirect to the home page.
    async with httpx.AsyncClient(transport=_ecfin_transport(next_year_status)) as http:
        fetch = await fetch_ecfin(http, TODAY, _allow)
    assert fetch.provider == "ecfin" and fetch.rows_provider == "eurostat"
    assert fetch.access.state == AccessState.AVAILABLE and fetch.calls == 2
    first = min(fetch.entries, key=lambda e: e.release_date)
    assert (first.provider, first.series_id) == ("eurostat", "sentiment")
    assert (first.release_date, first.release_time) == (dt.date(2026, 10, 29), dt.time(11, 0))
    assert all(e.release_date >= TODAY for e in fetch.entries)
    assert dt.date(2027, 1, 7) in {e.release_date for e in fetch.entries}


async def test_ecfin_missing_current_year_pdf_is_a_typed_failure_not_a_calendar() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(404, text="x"))
    ) as http:
        fetch = await fetch_ecfin(http, TODAY, _allow)
    assert fetch.entries == [] and fetch.covered == set()
    assert fetch.access.state == AccessState.TEMPORARILY_FAILED


async def test_ecfin_next_year_pdf_replaces_the_januar_date_of_this_year() -> None:
    def serve(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_pdf())  # the same layout for both years

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as http:
        # The 2027 request returns the 2026 file: the year heading does not match -> drift.
        fetch = await fetch_ecfin(http, TODAY, _allow)
    assert fetch.drift and "heading" in fetch.drift and fetch.entries == []


@pytest.mark.parametrize(
    ("status", "state"), [(402, AccessState.PAID_ONLY), (403, AccessState.ACCESS_UNVERIFIED)]
)
async def test_ecfin_and_cnb_denials_are_typed(status: int, state: AccessState) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(status, text="no"))
    ) as http:
        assert (await fetch_ecfin(http, TODAY, _allow)).access.state == state
        assert (await fetch_cnb(http, TODAY, _allow)).access.state == state
        assert (await fetch_ecb(http, TODAY, _allow)).access.state == state


# ------------------------------------------------------------------------------------ ČNB
def test_cnb_bank_board_dates_and_the_published_decision_time() -> None:
    days, when = parse_cnb_bank_board(_text("cnb_bank_board.html"))
    assert days[0] == dt.date(2026, 2, 5) and dt.date(2026, 11, 5) in days
    assert len(days) == 8 and when == dt.time(14, 30)  # "2:30 p.m." on the page


def test_cnb_time_is_none_when_the_page_does_not_state_it() -> None:
    page = _text("cnb_bank_board.html").replace("at 2:30 p.m.", "after lunch")
    assert parse_cnb_bank_board(page)[1] is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: "<html></html>",
        lambda p: p.replace("Bank Board meetings on monetary policy in", "Meetings in"),
        lambda p: p.replace("19 March", "1 January"),  # not increasing
        lambda p: p.replace("7 May", "7 Mai"),
        lambda p: p.replace("18 June", "31 June"),
    ],
)
def test_cnb_layout_changes_are_drift(mutate: Any) -> None:
    with pytest.raises(CalendarDrift):
        parse_cnb_bank_board(mutate(_text("cnb_bank_board.html")))


async def test_cnb_maps_the_repo_rate_to_upcoming_meetings() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, text=_text("cnb_bank_board.html"))
        )
    ) as http:
        fetch = await fetch_cnb(http, TODAY, _allow)
    assert [e.release_date for e in fetch.entries] == [dt.date(2026, 11, 5), dt.date(2026, 12, 17)]
    assert {(e.provider, e.series_id) for e in fetch.entries} == {("cnb", "cnb_repo_2w")}
    assert all(e.release_time == dt.time(14, 30) for e in fetch.entries)


# -------------------------------------------------------------------- Eurostat mapping fixes
async def _eurostat() -> Any:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, text=_text("eurostat_euro_indicators.ics"))
        )
    ) as http:
        return await fetch_eurostat(http, TODAY, _allow)


def _dates(fetch: Any, series: str) -> list[dt.date]:
    return sorted({e.release_date for e in fetch.entries if e.series_id == series})


async def test_gdp_includes_the_preliminary_flash_that_already_updates_the_dataset() -> None:
    fetch = await _eurostat()
    assert _dates(fetch, "gdp") == [
        dt.date(2026, 10, 30),  # preliminary flash t+30 (euro indicator release, namq_10_gdp)
        dt.date(2026, 11, 13),  # flash t+45
        dt.date(2026, 12, 7),  # full release t+65
    ]


async def test_euro_area_hicp_also_gets_the_flash_but_czechia_does_not() -> None:
    fetch = await _eurostat()
    final = [dt.date(2026, 10, 16), dt.date(2026, 11, 18), dt.date(2026, 12, 17)]
    assert _dates(fetch, "hicp") == final  # the dataset's full release: Czechia and others
    for geo in ("EA21", "DE"):
        assert _dates(fetch, f"hicp:{geo}") == sorted(
            [*final, dt.date(2026, 11, 4), dt.date(2026, 12, 1)]
        )
    assert "hicp:CZ" not in {e.series_id for e in fetch.entries}
    assert {"EA21", "DE", "FR", "EA"} <= set(EURO_AREA_GEOS) and "CZ" not in EURO_AREA_GEOS


def test_every_mapped_eurostat_title_is_unambiguous() -> None:
    # No two of our series share a calendar title except the deliberate geo variants of hicp.
    from steward.sources.release_calendar import EUROSTAT_EVENTS, EUROSTAT_GEO_EVENTS

    owners: dict[str, set[str]] = {}
    for series, titles in EUROSTAT_EVENTS.items():
        for title in titles:
            owners.setdefault(title, set()).add(series)
    assert all(len(v) == 1 for v in owners.values())
    assert set(EUROSTAT_GEO_EVENTS) == {f"hicp:{g}" for g in EURO_AREA_GEOS}


# ------------------------------------------------------------------------------ BIS mapping
async def _bis(flows: dict[str, str] | None = None) -> Any:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, text=_text("bis_releasecalendar.json"))
        )
    ) as http:
        return await fetch_bis(http, TODAY, _allow, flows)


async def test_international_debt_securities_series_maps_to_ids_not_dss() -> None:
    fetch = await _bis()
    # WS_DEBT_SEC2_PUB is "International debt securities (BIS-compiled)": calendar category IDS.
    assert BIS_FLOW_CATEGORY["WS_DEBT_SEC2_PUB"] == "IDS"
    assert {e.calendar_key for e in fetch.entries if e.series_id == "debt_securities"} == {"IDS"}
    # property_prices is collected from WS_SPP (selected residential property prices): the BIS
    # category RPP, and only its "Selected residential" release types (not the detailed WS_DPP).
    rows = [e for e in fetch.entries if e.series_id == "property_prices"]
    assert {e.calendar_key for e in rows} == {"RPP"}
    assert all(e.title == "BIS RPP release" for e in rows)
    assert _dates(fetch, "property_prices")[0] == dt.date(2026, 10, 29)


async def test_bis_mapping_follows_the_configured_dataflow_and_skips_unknown_ones() -> None:
    fetch = await _bis({"total_credit": "WS_TC", "mystery": "WS_UNKNOWN", "x": "WS_DSR"})
    assert {e.series_id for e in fetch.entries} == {"total_credit", "x"}
    assert {c for _, c in fetch.covered} == {"TOTAL_CREDIT", "DSR"}


def test_default_bis_flows_match_the_shipped_configuration() -> None:
    from steward.config.jobs import PublicDataSettings

    shipped = {
        s.key: s.flow.split(",")[1] for s in PublicDataSettings().sdmx if s.provider == "bis"
    }
    assert shipped == DEFAULT_BIS_FLOWS


def test_series_with_calendar_lists_the_new_mappings() -> None:
    # the yield curve and CZEONIA are dated by a published rule (third pass)
    assert series_with_calendar("ecb", ["ecb_deposit_rate", "euro_area_10y_yield"]) == [
        "ecb_deposit_rate",
        "euro_area_10y_yield",
    ]
    assert series_with_calendar("cnb", ["czeonia", "cnb_repo_2w", "x"]) == [
        "czeonia",
        "cnb_repo_2w",
    ]
    assert series_with_calendar("eurostat", ["sentiment", "hicp:EA21", "x"]) == [
        "sentiment",
        "hicp:EA21",
    ]


# ------------------------------------------------------------------------- budgets and gate
def test_new_calendars_have_conservative_literal_daily_caps() -> None:
    assert set(CALENDAR_PROVIDERS) == set(CALENDAR_BUDGETS)
    assert CALENDAR_BUDGETS["ecfin"].daily == 6 and CALENDAR_BUDGETS["cnb"].daily == 6
    assert CALENDAR_BUDGETS["ecb"].daily == 15  # 4 pages per run


@pytest.mark.db
async def test_exact_series_rows_win_over_the_dataset_rows(backend_pool: asyncpg.Pool) -> None:
    from steward.sources.release_calendar import CalendarEntry, CalendarFetch

    def fetch(series: str, key: str, *days: dt.date) -> CalendarFetch:
        out = CalendarFetch("eurostat", AccessResult(AccessState.AVAILABLE))
        out.covered.add((series, key))
        out.entries = [
            CalendarEntry("eurostat", series, key, d, "https://x.test/", title=key) for d in days
        ]
        return out

    from steward.sources.base import AccessResult

    await store_calendar(
        backend_pool, fetch("hicp", "Inflation (HICP)", dt.date(2026, 10, 16)), NOW
    )
    await store_calendar(
        backend_pool,
        fetch("hicp:EA21", "Flash", dt.date(2026, 10, 9), dt.date(2026, 11, 4)),
        NOW,
    )
    got = await next_releases(
        backend_pool,
        [("eurostat", "hicp:EA21"), ("eurostat", "hicp:CZ"), ("eurostat", "hicp:DE")],
        NOW,
    )
    assert got[("eurostat", "hicp:EA21")].release_date == dt.date(2026, 10, 9)
    assert got[("eurostat", "hicp:CZ")].release_date == dt.date(2026, 10, 16)  # dataset row
    assert got[("eurostat", "hicp:DE")].release_date == dt.date(2026, 10, 16)  # no exact rows


@pytest.mark.db
async def test_ecfin_rows_are_stored_under_the_eurostat_source_and_supersede_there(
    backend_pool: asyncpg.Pool,
) -> None:
    async with httpx.AsyncClient(transport=_ecfin_transport()) as http:
        fetch = await fetch_ecfin(http, TODAY, _allow)
    result = await store_calendar(backend_pool, fetch, NOW)
    assert result.inserted == len(fetch.entries) and result.inserted >= 3
    rows = await backend_pool.fetch(
        "select distinct provider, series_id, release_time is not null t"
        " from macro_release_calendar"
    )
    assert [(r["provider"], r["series_id"], r["t"]) for r in rows] == [
        ("eurostat", "sentiment", True)
    ]
    # ECFIN moves the October release: the old date is superseded, never deleted.
    fetch.entries = [e for e in fetch.entries if e.release_date != dt.date(2026, 10, 29)]
    again = await store_calendar(backend_pool, fetch, NOW + dt.timedelta(days=1))
    assert again.superseded == 1 and again.inserted == 0


@pytest.mark.db
async def test_provider_check_admits_cnb_and_rejects_unknown_providers(
    backend_pool: asyncpg.Pool,
) -> None:
    insert = (
        "insert into macro_release_calendar(provider,series_id,calendar_key,release_date,"
        "source_url,known_at) values ($1,'s','k','2026-11-05','https://x.test',now())"
    )
    await backend_pool.execute(insert, "cnb")
    with pytest.raises(asyncpg.CheckViolationError):
        await backend_pool.execute(insert, "ecfin")  # ecfin is an endpoint, never a row provider
    assert ENDPOINT == "release_calendar"
