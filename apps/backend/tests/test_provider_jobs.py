"""FMP adapter parsers/transport, the scheduled free-quota jobs' planning and their wiring.

No database and no network. FMP payloads below are written from FMP's public `/stable`
documentation: field names such as `epsAvg`, `numAnalystsEps`, `sector` and `industry` could NOT
be verified against a live response (no API key was available). The OPS sample is likewise
documentation-shaped and unverified against ops.epo.org.
"""

import datetime as dt
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
        "description": "ignored",
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


def test_profile_keeps_only_text_fields_sent_and_rejects_other_symbols() -> None:
    profile = fmp.parse_profile(PROFILE, "aapl")
    assert profile is not None
    assert profile.fields == {
        "companyName": "Apple Inc.",
        "currency": "USD",
        "exchange": "NASDAQ",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "country": "US",
    }
    assert fmp.parse_profile([], "AAPL") is None
    assert fmp.parse_profile(PROFILE, "MSFT") is None
    assert fmp.parse_profile([{"symbol": "AAPL", "sector": "  "}], "AAPL") is None
    with pytest.raises(FreeSourceError):
        fmp.parse_profile({"Error Message": "x"}, "AAPL")


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


def test_fmp_endpoint_budget_never_exceeds_half_the_free_account_quota() -> None:
    providers = JobsConfig().providers
    assert fmp.endpoint_budget(providers["fmp"]).daily == Decimal(125)
    # two endpoints together stay inside the 250/day free key
    assert 2 * fmp.ENDPOINT_DAILY_CAP <= providers["fmp"].daily_quota  # type: ignore[operator]
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


def test_new_jobs_are_registered_budgeted_and_off_by_default() -> None:
    names = {"fmp_company", "alpha_vantage_statements", "epo_patents"}
    assert names <= set(get_args(JobType)) <= set(JOB_HANDLERS)
    assert names <= set(get_args(BudgetJobType))
    config = JobsConfig()
    schedules = config.schedules()
    for name in names:
        assert name in JOB_HANDLERS and schedules[name].enabled is False
    # the providers they spend are opt-in as well
    for provider in ("fmp", "alpha_vantage", "epo_ops"):
        assert config.providers[provider].enabled is False


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
