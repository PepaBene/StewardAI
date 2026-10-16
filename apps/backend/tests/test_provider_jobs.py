"""FMP adapter parsers/transport, the scheduled free-quota jobs' planning and their wiring.

No database and no network. FMP payloads below are written from FMP's public `/stable`
documentation: field names such as `epsAvg`, `numAnalystsEps`, `sector` and `industry` could NOT
be verified against a live response (no API key was available). The OPS sample is likewise
documentation-shaped and unverified against ops.epo.org.
"""

import contextlib
import datetime as dt
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import get_args

import httpx
import pytest

from steward.config.budget import BudgetJobType
from steward.config.jobs import JobsConfig
from steward.graph import ops
from steward.jobs.registry import JOB_HANDLERS, JobType
from steward.sources import fmp
from steward.sources.free_market import FreeSourceError
from steward.sources.provider_jobs import stalest_first

PROFILE = [
    {
        "symbol": "AAPL",
        "companyName": "Apple Inc.",
        "currency": "USD",
        "exchange": "NASDAQ",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "country": "US",
        "marketCap": 3_000_000_000_000,
        "description": "Apple designs  phones.",
        "ceo": "Tim Cook",
        "fullTimeEmployees": "164000",
        "ipoDate": "1980-12-12",
        "isActivelyTrading": True,
    }
]
ESTIMATES = [
    {
        "symbol": "AAPL",
        "date": "2027-09-30",
        "revenueLow": 410000000000,
        "revenueHigh": 450000000000,
        "revenueAvg": 430000000000,
        "epsAvg": 7.9,
        "epsHigh": 8.4,
        "epsLow": 7.5,
        "numAnalystsRevenue": 30,
        "numAnalystsEps": 35,
    },
    {  # EPS present but no analyst count: cannot be a consensus; revenue absent entirely
        "symbol": "AAPL",
        "date": "2028-09-30",
        "epsAvg": 8.8,
    },
]


def test_profile_keeps_the_fields_sent_under_the_page_names_and_rejects_other_symbols() -> None:
    profile = fmp.parse_profile(PROFILE, "aapl")
    assert profile is not None
    assert profile.fields == {
        "companyName": "Apple Inc.",
        "currency": "USD",
        "exchange": "NASDAQ",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "country": "US",
        "description": "Apple designs phones.",
        "ceo": "Tim Cook",
        "employees": "164000",
        "ipo_date": "1980-12-12",
    }
    assert fmp.parse_profile([], "AAPL") is None
    assert fmp.parse_profile(PROFILE, "MSFT") is None
    assert fmp.parse_profile([{"symbol": "AAPL", "sector": "  "}], "AAPL") is None
    with pytest.raises(FreeSourceError):
        fmp.parse_profile({"Error Message": "x"}, "AAPL")


def test_estimate_selection_keeps_the_next_fiscal_year_though_fmp_answers_newest_first() -> None:
    import datetime as dt

    payload = [
        {"symbol": "AAPL", "date": f"{y}-09-27", "epsAvg": 7 + y - 2026, "numAnalystsEps": 20}
        for y in (2031, 2030, 2029, 2028, 2027, 2026, 2025, 2024, 2023, 2022)
    ]
    rows = fmp.select_estimates(fmp.parse_estimates(payload, "AAPL"), dt.date(2026, 10, 7))
    assert sorted({r.fiscal_year for r in rows}) == [2026, 2027, 2028, 2029]
    assert 2025 not in {r.fiscal_year for r in rows}  # ended more than the lookback ago


def test_estimates_missing_figures_are_null_not_zero() -> None:
    rows = fmp.parse_estimates(ESTIMATES, "AAPL")
    eps = {r.fiscal_year: r for r in rows if r.metric == "earnings_per_share"}
    revenue = [r for r in rows if r.metric == "revenue"]
    assert eps[2027].value == Decimal("7.9")
    assert (eps[2027].low, eps[2027].high, eps[2027].analyst_count) == (
        Decimal("7.5"),
        Decimal("8.4"),
        35,
    )
    assert eps[2028].analyst_count is None  # the writer drops it: no count, no consensus
    assert eps[2028].low is None and eps[2028].high is None
    assert [r.fiscal_year for r in revenue] == [2027]  # 2028 sent no revenue: no row, no 0
    assert fmp.parse_estimates([{"symbol": "AAPL", "date": "bad", "epsAvg": 1}], "AAPL") == []
    with pytest.raises(FreeSourceError):
        fmp.parse_estimates({"unexpected": 1}, "AAPL")


async def _client(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler)


async def test_fetch_sends_key_as_query_and_never_leaks_it_in_errors() -> None:
    seen: list[httpx.Request] = []

    def ok(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=PROFILE)

    async with await _client(httpx.MockTransport(ok)) as http:
        assert await fmp._get(http, fmp.PROFILE_URL, {"symbol": "AAPL"}, "SECRET") == PROFILE
    assert seen[0].url.params["apikey"] == "SECRET"

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    async with await _client(httpx.MockTransport(boom)) as http:
        with pytest.raises(FreeSourceError) as caught:
            await fmp._get(http, fmp.PROFILE_URL, {"symbol": "AAPL"}, "SECRET")
    assert "SECRET" not in str(caught.value) and caught.value.provider_charged is False


@pytest.mark.parametrize("status", [402, 429])
async def test_fetch_http_failures_carry_their_status(status: int) -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(status, text="Premium Query"))
    async with await _client(transport) as http:
        with pytest.raises(FreeSourceError) as caught:
            await fmp._get(http, fmp.ESTIMATES_URL, {"symbol": "AAPL"}, "k")
    assert caught.value.status_code == status


async def test_fetch_error_envelope_on_http_200_is_an_error() -> None:
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, json={"Error Message": "Invalid API KEY."})
    )
    async with await _client(transport) as http:
        with pytest.raises(FreeSourceError, match="Invalid API KEY"):
            await fmp._get(http, fmp.PROFILE_URL, {"symbol": "AAPL"}, "k")


def test_compensation_keeps_the_newest_years_and_never_invents_a_component() -> None:
    payload = [
        {
            "symbol": "AAPL",
            "nameAndPosition": "  Jane Doe   Chief Financial Officer ",
            "year": year,
            "salary": 1000000,
            "stockAward": 5000000,
            "total": 6500000,
            "filingDate": f"{year + 1}-01-08",
            "link": "https://www.sec.gov/Archives/x",
        }
        for year in (2023, 2024, 2025)
    ] + [
        {"symbol": "AAPL", "nameAndPosition": "", "year": 2025, "total": 1},  # no name: dropped
        {"symbol": "AAPL", "nameAndPosition": "No Total", "year": 2025},  # no total: dropped
        {"symbol": "MSFT", "nameAndPosition": "Other", "year": 2025, "total": 1},
    ]
    rows = fmp.latest_compensation(fmp.parse_compensation(payload, "AAPL"))
    assert sorted({r.fiscal_year for r in rows}) == [2024, 2025]
    one = rows[0]
    assert one.name_and_position == "Jane Doe Chief Financial Officer"
    assert set(one.components) == {"salary", "stock_award"}  # bonus was not sent: absent, not 0
    assert one.total == Decimal(6500000) and one.source_url.startswith("https://www.sec.gov")
    with pytest.raises(FreeSourceError):
        fmp.parse_compensation({"unexpected": 1}, "AAPL")


def test_fmp_endpoint_budget_never_exceeds_a_third_of_the_free_account_quota() -> None:
    providers = JobsConfig().providers
    assert fmp.endpoint_budget(providers["fmp"]).daily == Decimal(83)
    # three endpoints together stay inside the 250/day free key
    assert 3 * fmp.ENDPOINT_DAILY_CAP <= providers["fmp"].daily_quota  # type: ignore[operator]
    low = providers["fmp"].model_copy(update={"daily_quota": 40})
    assert fmp.endpoint_budget(low).daily == Decimal(40)


def test_stalest_first_orders_never_then_oldest_and_skips_fresh() -> None:
    now = dt.datetime(2026, 10, 5, tzinfo=dt.UTC)
    last = {
        "fresh": now - dt.timedelta(hours=1),
        "old": now - dt.timedelta(days=30),
        "older": now - dt.timedelta(days=90),
    }
    picked = stalest_first(
        ["fresh", "old", "never", "older"], last.get, now=now, refresh_hours=168, limit=3
    )
    assert picked == ["never", "older", "old"]
    assert stalest_first(["fresh"], last.get, now=now, refresh_hours=168, limit=3) == []
    assert stalest_first(["never"], last.get, now=now, refresh_hours=168, limit=0) == []


def test_new_jobs_are_registered_budgeted_and_scheduled() -> None:
    names = {"fmp_company", "alpha_vantage_statements", "epo_patents"}
    assert names <= set(get_args(JobType)) <= set(JOB_HANDLERS)
    assert names <= set(get_args(BudgetJobType))
    schedules = JobsConfig().schedules()
    # They run whenever their provider key is set; a missing key skips the run.
    for name in names:
        assert name in JOB_HANDLERS and schedules[name].enabled is True


OPS_MULTI_OFFICE = """<?xml version="1.0" encoding="UTF-8"?>
<ops:world-patent-data xmlns="http://www.epo.org/exchange" xmlns:ops="http://ops.epo.org">
 <ops:biblio-search total-result-count="2"><ops:search-result><exchange-documents>
  <exchange-document family-id="900001" country="EP" doc-number="3000001" kind="A1">
   <bibliographic-data>
    <publication-reference><document-id document-id-type="docdb">
      <country>EP</country><doc-number>3000001</doc-number><kind>A1</kind><date>20180110</date>
    </document-id></publication-reference>
    <priority-claims><priority-claim sequence="1"><document-id document-id-type="docdb">
      <country>US</country><doc-number>201662000001</doc-number><kind>P</kind>
      <date>20160701</date></document-id></priority-claim></priority-claims>
    <parties><applicants><applicant data-format="original" sequence="1">
      <applicant-name><name>Acme Robotics Inc.</name></applicant-name></applicant>
    </applicants></parties>
    <invention-title lang="en">Gripper</invention-title>
   </bibliographic-data>
  </exchange-document>
  <exchange-document family-id="900001" country="US" doc-number="10000002" kind="B2">
   <bibliographic-data>
    <publication-reference><document-id document-id-type="docdb">
      <country>US</country><doc-number>10000002</doc-number><kind>B2</kind><date>20190305</date>
    </document-id></publication-reference>
    <parties><applicants><applicant data-format="original" sequence="1">
      <applicant-name><name>Acme Robotics Inc.</name></applicant-name></applicant>
    </applicants></parties>
   </bibliographic-data>
  </exchange-document>
 </exchange-documents></ops:search-result></ops:biblio-search>
</ops:world-patent-data>"""


def test_ops_one_invention_in_two_offices_is_one_family_with_grant_date() -> None:
    page = ops.parse_ops_page(OPS_MULTI_OFFICE, ("Acme Robotics Inc.",))
    assert page.total == 2 and page.documents == 2
    (family,) = page.families
    assert family.family_id == "900001"
    assert family.jurisdictions == ["EP", "US"]
    assert family.priority_date == dt.date(2016, 7, 1)
    assert family.grant_date == dt.date(2019, 3, 5)
    assert ops.parse_ops_page(OPS_MULTI_OFFICE, ("Someone Else",)).families == []


class _Step:
    def __init__(self) -> None:
        self.detail: dict[str, object] = {}


class _Ctx:
    pool = None

    def __init__(self) -> None:
        self.steps: list[_Step] = []

    @contextlib.asynccontextmanager
    async def step(self, name: str) -> AsyncIterator[_Step]:
        self.steps.append(_Step())
        yield self.steps[-1]


async def test_yahoo_dividends_waits_out_the_minute_window_and_stops_at_the_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from steward.sources import provider_jobs, yahoo
    from steward.sources.governor import QuotaExceeded

    tickers = [f"T{i}" for i in range(9)]
    calls: list[str] = []
    waits: list[float] = []

    async def collect(pool: object, http: object, *, listing_id: int, **_: object) -> dict:
        calls.append(tickers[listing_id])
        # The minute admits six; after one wait the day is used up at the eighth call.
        if len(calls) == 7 or len(calls) >= 9:
            raise QuotaExceeded("free quota exhausted")
        return {"proposals": 1, "new": 0}

    async def symbol(pool: object, t: str) -> tuple[int, str, str]:
        return tickers.index(t), t, t

    async def no_state(*_: object, **__: object) -> dict:
        return {}

    async def wait(seconds: float) -> None:
        waits.append(seconds)

    async def holdings(pool: object) -> list[str]:
        return tickers

    monkeypatch.setattr(provider_jobs, "holding_tickers", holdings)
    monkeypatch.setattr(provider_jobs, "_state_times", no_state)
    monkeypatch.setattr(provider_jobs, "_save_state", no_state)
    monkeypatch.setattr(provider_jobs, "_sleep", wait)
    monkeypatch.setattr(yahoo, "yahoo_symbol_for", symbol)
    monkeypatch.setattr(yahoo, "collect_yahoo_dividends", collect)

    ctx = _Ctx()
    await provider_jobs.yahoo_dividends_handler(ctx)  # type: ignore[arg-type]
    detail = ctx.steps[0].detail
    assert len(waits) == 2 and all(0 < w <= 61 for w in waits)
    assert list(detail["refreshed"]) == tickers[:7]  # type: ignore[call-overload]
    assert "QuotaExceeded" in str(detail["stopped"])
