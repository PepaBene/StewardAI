"""D6.2 fund/ownership provider probes and D6.3 SEC N-PORT ingestion (offline fixtures).

Fixtures under tests/fixtures/funds are hand-built to Finnhub's/FMP's documented response
shapes and the SEC NPORT-P schema; nothing here was verified against a live endpoint.
"""

import datetime as dt
import json
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
from pydantic import SecretStr

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool, get_pool_or_error
from api.main import create_app
from steward.jobs.runner import JobContext, run_job
from steward.ownership.nport_collect import FundMap, parse_mf_tickers, reconcile, run_nport
from steward.ownership.nport_read import holding_fund_positions
from steward.settings import Settings
from steward.sources.base import AccessState
from steward.sources.edgar import EdgarClient, Filing
from steward.sources.fund_probes import (
    classify_fund_probe,
    fund_probe_list,
    normalize_probe,
    run_fund_probes,
)
from steward.sources.nport import NportError, parse_nport, public_after, valid_isin

FIX = Path(__file__).parent / "fixtures" / "funds"
OWNER = "7f0c3d4e-0000-4000-8000-000000000001"
ACCESSION = "0001234567-26-000111"
OTHER_SERIES_ACCESSION = "0001234567-26-000112"


def fixture_json(name: str) -> Any:
    return json.loads((FIX / name).read_text())


# ------------------------------------------------------------------------------ N-PORT parser


def test_parser_reads_fund_identity_period_and_fund_level_fields() -> None:
    report = parse_nport((FIX / "nport_p_sample.xml").read_bytes())
    assert report.submission_type == "NPORT-P"
    assert (report.registrant_cik, report.series_id) == ("0001234567", "S000009999")
    assert report.series_name == "Example US Large Cap ETF"
    assert report.class_ids == ("C000012345", "C000012346")
    assert report.report_period_end == dt.date(2026, 6, 30)
    assert report.fiscal_year_end == dt.date(2026, 10, 31)
    assert report.net_assets == Decimal("5000000000.00")
    assert report.risk_metrics["interest_rate_dv01"]["USD"]["period1Yr"] == "2300.25"
    assert report.risk_metrics["credit_spread_investment_grade"]["period5Yr"] == "30.5"
    assert [r["classId"] for r in report.class_returns] == ["C000012345", "C000012346"]
    assert report.flows["mon1Flow"]["redemption"] == "40000000.00"
    assert len(report.holdings) == 6


def test_parser_holdings_keep_identifiers_values_and_flags_as_filed() -> None:
    by_name = {
        h.issuer_name: h for h in parse_nport((FIX / "nport_p_sample.xml").read_bytes()).holdings
    }
    apple = by_name["Apple Inc"]
    assert (apple.cusip, apple.isin, apple.issuer_lei) == (
        "037833100",
        "US0378331005",
        "HWUPKR0MPOU8FGXBT394",
    )
    assert (apple.balance, apple.units, apple.value_usd, apple.pct_value) == (
        Decimal("1500000.00"),
        "NS",
        Decimal("345000000.00"),
        Decimal("6.9"),
    )
    assert (apple.asset_cat, apple.issuer_cat, apple.payoff_profile) == ("EC", "CORP", "Long")
    assert not apple.is_derivative
    # Placeholders mean "unknown": N/A CUSIP and LEI are None, never "" or zero.
    unmapped = by_name["Unmapped Holdings plc"]
    assert unmapped.cusip is None and unmapped.issuer_lei is None
    assert unmapped.isin == "GB00B0000001" or unmapped.isin is None  # checksum decides
    treasury = by_name["US Treasury Note 4.25% 2028"]
    assert treasury.isin is None and treasury.other_id == "BBG000000000"
    assert treasury.units == "PA" and treasury.asset_cat == "DBT"
    future = by_name["S&P 500 E-mini Future Dec26"]
    assert future.is_derivative and future.derivative_cat == "FUT"
    assert future.cusip is None  # 000000000 placeholder
    assert future.value_usd == Decimal("-125000.00") and future.payoff_profile == "Short"


def test_parser_rejects_entities_and_foreign_documents() -> None:
    with pytest.raises(NportError):
        parse_nport(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><edgarSubmission/>')
    with pytest.raises(NportError):
        parse_nport(b"<html/>")
    with pytest.raises(NportError):
        parse_nport(b"<edgarSubmission><formData><genInfo/></formData></edgarSubmission>")


def test_isin_checksum_and_public_lag() -> None:
    assert valid_isin("US0378331005") and not valid_isin("US0378331006")
    # Quarter end 2026-06-30 + 60 days; a mid-quarter month maps to its quarter end.
    assert public_after(dt.date(2026, 6, 30)) == dt.date(2026, 8, 29)
    assert public_after(dt.date(2026, 4, 30)) == dt.date(2026, 8, 29)
    assert public_after(dt.date(2026, 12, 31)) == dt.date(2027, 3, 1)


def _filing(report: dt.date | None, filed: dt.date = dt.date(2026, 8, 26)) -> Filing:
    return Filing("0001234567", "NPORT-P", ACCESSION, filed, report, "primary_doc.xml", "")


def test_reconcile_requires_series_class_and_report_date_agreement() -> None:
    report = parse_nport((FIX / "nport_p_sample.xml").read_bytes())
    fund = FundMap("EXLC_US_EQ", "0001234567", "S000009999", "C000012345")
    assert reconcile(report, _filing(dt.date(2026, 6, 30)), fund) == ("confirmed", None)
    assert reconcile(report, _filing(None), fund)[0] == "confirmed"
    assert reconcile(report, _filing(dt.date(2026, 3, 31)), fund)[0] == "date_mismatch"
    assert reconcile(report, _filing(dt.date(2026, 6, 30), dt.date(2026, 6, 1)), fund)[0] == (
        "date_mismatch"
    )
    assert reconcile(report, _filing(None), FundMap("X", "0001234567", "S000000001", None))[0] == (
        "unmatched"
    )
    assert reconcile(report, _filing(None), FundMap("X", "0001234567", "S000009999", "C000099999"))[
        0
    ] == ("unmatched")


def test_mf_ticker_file_maps_symbol_to_series_and_class() -> None:
    mapping = parse_mf_tickers((FIX / "company_tickers_mf.json").read_bytes())
    assert mapping["EXLC"] == ("0001234567", "S000009999", "C000012345")


# ---------------------------------------------------------------------------- probe typing


def test_denials_are_typed_never_empty_success() -> None:
    assert classify_fund_probe(403, fixture_json("finnhub_denied.json")).state == (
        AccessState.PAID_ONLY
    )
    assert classify_fund_probe(402, {}).state == AccessState.PAID_ONLY
    assert classify_fund_probe(200, fixture_json("fmp_premium.json")).state == AccessState.PAID_ONLY
    assert classify_fund_probe(401, {"error": "Invalid API key."}).state == (
        AccessState.ACCESS_UNVERIFIED
    )
    assert classify_fund_probe(429, {"error": "API limit reached."}).state == (
        AccessState.QUOTA_EXHAUSTED
    )
    assert classify_fund_probe(
        200, {"error": "API limit reached. Please try again later."}
    ).state == (AccessState.QUOTA_EXHAUSTED)
    assert classify_fund_probe(200, {"symbol": "SPY", "holdings": []}).state == (
        AccessState.NOT_COVERED
    )
    assert classify_fund_probe(200, []).state == AccessState.NOT_COVERED
    assert classify_fund_probe(200, fixture_json("finnhub_etf_holdings.json"), "SPY").state == (
        AccessState.AVAILABLE
    )
    assert classify_fund_probe(200, fixture_json("finnhub_etf_holdings.json"), "QQQ").state == (
        AccessState.DISPUTED
    )


def test_probe_list_is_opt_in_without_keys_and_keeps_keys_out_of_urls() -> None:
    bare = fund_probe_list(Settings(_env_file=None, finnhub_api_key=None, fmp_api_key=None))
    assert {(p.provider, p.endpoint) for p in bare} == {
        ("finnhub", "etf_profile"),
        ("finnhub", "etf_holdings"),
        ("finnhub", "index_constituents"),
        ("finnhub", "institutional_ownership"),
        ("fmp", "etf_info"),
        ("fmp", "etf_holdings"),
        ("fmp", "institutional_ownership"),
    }
    assert all(p.credential is None for p in bare)
    assert all("key" not in p.url.casefold() and "token" not in p.url for p in bare)


def test_normalizers_produce_typed_records_and_drop_bad_values() -> None:
    records, _ = normalize_probe(
        "finnhub", "etf_holdings", "SPY", fixture_json("finnhub_etf_holdings.json")
    )
    apple, msft, bad = records
    assert (apple.isin, apple.cusip, apple.weight_pct, apple.as_of) == (
        "US0378331005",
        "037833100",
        Decimal("7.01"),
        dt.date(2026, 9, 26),
    )
    assert apple.shares == Decimal("183500000") and apple.record_kind == "etf_holding"
    # A bad ISIN checksum / placeholder CUSIP / 700 % weight / null value stay NULL.
    assert (bad.isin, bad.cusip, bad.weight_pct, bad.value) == (None, None, None, None)
    assert msft.item_key == "US5949181045"

    profile, _ = normalize_probe(
        "finnhub", "etf_profile", "SPY", fixture_json("finnhub_etf_profile.json")
    )
    assert profile[0].attributes["expenseRatio"] == 0.0945 and profile[0].currency == "USD"

    index, warnings = normalize_probe(
        "finnhub", "index_constituents", "^GSPC", fixture_json("finnhub_index_constituents.json")
    )
    assert [r.item_symbol for r in index] == ["AAPL", "MSFT", "NVDA"]
    assert all(r.as_of is None and r.weight_pct is None for r in index)
    assert any("not a constituent history" in w for w in warnings)

    holders, _ = normalize_probe(
        "finnhub", "institutional_ownership", "AAPL", fixture_json("finnhub_institutional.json")
    )
    assert len(holders) == 1  # the row with neither CIK nor name is dropped
    assert holders[0].item_key == "0000102909:2026-06-30"
    assert holders[0].shares == Decimal("1300000000")

    fmp, warnings = normalize_probe(
        "fmp", "etf_holdings", "SPY", fixture_json("fmp_etf_holdings.json")
    )
    assert len(fmp) == 1 and any("duplicate" in w for w in warnings)
    info, _ = normalize_probe("fmp", "etf_info", "SPY", fixture_json("fmp_etf_info.json"))
    assert info[0].isin == "US78462F1030" and info[0].attributes["holdingsCount"] == 503

    # An unexpected schema is quarantined (no records), not published as zero rows.
    assert normalize_probe("finnhub", "etf_holdings", "SPY", {"unexpected": 1})[0] == []


# ------------------------------------------------------------------------ probe runner (DB)


class Provider:
    """Mock Finnhub/FMP transport that counts requests per endpoint."""

    def __init__(self, responses: dict[str, httpx.Response]) -> None:
        self.responses = responses
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        for suffix, response in self.responses.items():
            if request.url.path.endswith(suffix):
                return response
        return httpx.Response(404)


async def _run_probes(pool: asyncpg.Pool, provider: Provider, settings: Settings) -> str:
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:

        async def handler(ctx: JobContext) -> None:
            await run_fund_probes(ctx, http, settings)

        return await run_job(pool, "fund_probes", handler, trigger="manual")


@pytest.mark.db
async def test_free_success_is_stored_and_premium_denials_disable_the_endpoint(
    backend_pool: asyncpg.Pool,
) -> None:
    settings = Settings(
        _env_file=None, finnhub_api_key=SecretStr("fh"), fmp_api_key=SecretStr("fm")
    )
    provider = Provider(
        {
            "/etf/profile": httpx.Response(403, json=fixture_json("finnhub_denied.json")),
            "/finnhub/etf/holdings": httpx.Response(200, json={}),
            "/etf/holdings": httpx.Response(200, json=fixture_json("finnhub_etf_holdings.json")),
            "/index/constituents": httpx.Response(
                429, json={"error": "API limit reached."}, headers={"Retry-After": "120"}
            ),
            "/institutional/ownership": httpx.Response(
                200, json=fixture_json("finnhub_institutional.json")
            ),
            "/etf/info": httpx.Response(200, json=fixture_json("fmp_etf_info.json")),
            "/extract-analytics/holder": httpx.Response(200, json=fixture_json("fmp_premium.json")),
        }
    )
    assert await _run_probes(backend_pool, provider, settings) == "succeeded"
    # FMP /etf/holdings shares the Finnhub suffix in the mock; it still got a typed answer.
    states = {
        (r["provider"], r["endpoint"]): r
        for r in await backend_pool.fetch(
            "select provider,endpoint,state,hard_blocked,retry_at from public.provider_access"
            " where endpoint in ('etf_profile','etf_holdings','index_constituents',"
            "'institutional_ownership','etf_info')"
        )
    }
    assert states[("finnhub", "etf_profile")]["state"] == "paid_only"
    assert states[("finnhub", "etf_profile")]["hard_blocked"]
    assert states[("finnhub", "index_constituents")]["state"] == "quota_exhausted"
    assert states[("finnhub", "index_constituents")]["retry_at"] is not None
    assert states[("finnhub", "etf_holdings")]["state"] == "available"
    assert states[("fmp", "etf_info")]["state"] == "available"
    fmp_inst = await backend_pool.fetchrow(
        "select state,hard_blocked from public.provider_access"
        " where provider='fmp' and endpoint='institutional_ownership'"
    )
    assert fmp_inst["state"] == "paid_only" and fmp_inst["hard_blocked"]

    kinds = {
        r["record_kind"]: r["n"]
        for r in await backend_pool.fetch(
            "select record_kind, count(*) n from public.fund_probe_record group by 1"
        )
    }
    assert kinds["etf_holding"] >= 2 and kinds["institutional_holder"] == 1
    assert kinds["etf_profile"] == 1
    # The Finnhub key travels in a header, never the URL.
    finnhub_calls = [c for c in provider.calls if "finnhub" in c.url.host]
    assert finnhub_calls and all(c.headers["X-Finnhub-Token"] == "fh" for c in finnhub_calls)
    assert all("fh" not in str(c.url) for c in finnhub_calls)

    # A second daily run does not retry the denied endpoints (nor the Retry-After window).
    first_round = len(provider.calls)
    assert await _run_probes(backend_pool, provider, settings) == "succeeded"
    retried = [
        c.url.path
        for c in provider.calls[first_round:]
        if c.url.path.endswith(("/profile", "/constituents"))
    ]
    assert retried == []
    assert not any(
        c.url.host == "financialmodelingprep.com" and c.url.path.endswith("holder")
        for c in provider.calls[first_round:]
    )


@pytest.mark.db
async def test_missing_keys_skip_cleanly_without_a_request(backend_pool: asyncpg.Pool) -> None:
    provider = Provider({})
    assert (
        await _run_probes(
            backend_pool, provider, Settings(_env_file=None, finnhub_api_key=None, fmp_api_key=None)
        )
        == "succeeded"
    )
    assert provider.calls == []
    state = await backend_pool.fetchval(
        "select state from public.provider_access"
        " where provider='finnhub' and endpoint='etf_holdings'"
    )
    assert state == "access_unverified"
    assert await backend_pool.fetchval("select count(*) from public.fund_probe_record") == 0


# ------------------------------------------------------------------------ N-PORT collector


def _submissions(report: str = "2026-06-30", series_filings: bool = True) -> dict[str, Any]:
    accessions = [OTHER_SERIES_ACCESSION, ACCESSION] if series_filings else []
    return {
        "filings": {
            "recent": {
                "form": ["NPORT-P"] * len(accessions),
                "accessionNumber": accessions,
                "filingDate": ["2026-08-27", "2026-08-26"][: len(accessions)],
                "reportDate": [report] * len(accessions),
                "primaryDocument": ["primary_doc.xml"] * len(accessions),
                "acceptanceDateTime": ["2026-08-27T10:00:00.000Z", "2026-08-26T10:00:00.000Z"][
                    : len(accessions)
                ],
            }
        }
    }


class Edgar:
    def __init__(self, report_date: str = "2026-06-30") -> None:
        self.report_date = report_date
        self.documents: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("company_tickers_mf.json"):
            return httpx.Response(200, content=(FIX / "company_tickers_mf.json").read_bytes())
        if url.endswith("submissions/CIK0001234567.json"):
            return httpx.Response(200, json=_submissions(self.report_date))
        if url.endswith("primary_doc.xml"):
            self.documents.append(url)
            xml = (FIX / "nport_p_sample.xml").read_bytes()
            if "000123456726000112" in url:
                xml = xml.replace(b"S000009999", b"S000008888")
            return httpx.Response(200, content=xml)
        return httpx.Response(404)


async def _seed(pool: asyncpg.Pool) -> None:
    job = await pool.fetchval(
        "insert into public.job (job_type, trigger, status) values"
        " ('portfolio_sync', 'schedule', 'running') returning id"
    )
    await pool.execute(
        "insert into instrument (t212_ticker, short_name, isin, name, currency, security_type)"
        " values ('EXLC_US_EQ', 'EXLC', 'US30000000A8', 'Example US Large Cap ETF', 'USD', 'ETF'),"
        " ('VWCEd_EQ', 'VWCE', 'IE00BK5BQT80', 'Vanguard FTSE All-World', 'EUR', 'ETP'),"
        " ('AAPL_US_EQ', 'AAPL', 'US0378331005', 'Apple', 'USD', 'Common Stock'),"
        " ('GOOGL_US_EQ', 'GOOGL', 'US02079K3059', 'Alphabet', 'USD', 'Common Stock')"
    )
    await pool.execute(
        "insert into account_snapshot (snapshot_date, job_id, captured_at, t212_account_id,"
        " account_currency, total_value, cash_available, cash_in_pies, cash_reserved,"
        " invested_value, invested_cost, unrealized_ppl, realized_ppl, base_currency, fx_source,"
        " fx_rate, cash_base, invested_base, total_base, weight_basis)"
        " values ('2026-09-30', $1, now(), 1, 'EUR', 100, 100, 0, 0, 0, 0, 0, 0, 'CZK', 'cnb',"
        " 25, 2500, 0, 2500, 'total')",
        job,
    )
    for ticker in ("EXLC_US_EQ", "VWCEd_EQ", "AAPL_US_EQ", "GOOGL_US_EQ"):
        await pool.execute(
            "insert into position_snapshot (snapshot_date, ticker, job_id, captured_at, quantity,"
            " avg_price, current_price, instrument_currency, value, account_currency,"
            " value_account, cost_account, ppl, base_currency, fx_source, fx_rate, value_base)"
            " values ('2026-09-30', $1, $2, now(), 1, 1, 1, 'USD', 1, 'EUR', 1, 1, 0, 'EUR',"
            " 'ecb', 1, 1)",
            ticker,
            job,
        )
    # Canonical identity: Apple's ISIN is a known security of a known issuer.
    issuer = await pool.fetchval(
        "insert into issuer(legal_name, lei)"
        " values('Apple Inc', 'HWUPKR0MPOU8FGXBT394') returning id"
    )
    await pool.execute(
        "insert into security(issuer_id, isin, kind, name)"
        " values($1, 'US0378331005', 'equity', 'Apple')",
        issuer,
    )
    # A Microsoft issuer found by LEI only, and a conflicting Alphabet identity.
    await pool.execute(
        "insert into issuer(legal_name, lei) values('Microsoft', 'INR2EJN1ERAN0W5ZP974')"
    )
    other = await pool.fetchval(
        "insert into issuer(legal_name) values('Not Alphabet') returning id"
    )
    await pool.execute(
        "insert into security(issuer_id, isin, kind, name)"
        " values($1, 'US02079K3059', 'equity', 'GOOGL')",
        other,
    )
    gissuer = await pool.fetchval(
        "insert into issuer(legal_name, lei)"
        " values('Alphabet', '5493006MHB84DD0ZWV18') returning id"
    )
    assert gissuer != other


async def _collect(pool: asyncpg.Pool, edgar: Edgar, today: dt.date) -> str:
    async with httpx.AsyncClient(transport=httpx.MockTransport(edgar)) as http:
        client = EdgarClient(http, "StewardAI test test@example.com")

        async def handler(ctx: JobContext) -> None:
            await run_nport(ctx, client, today)

        return await run_job(pool, "nport_holdings", handler, trigger="manual")


@pytest.fixture
async def api(backend_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    from api.nport_routes import router

    app.include_router(router)

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[get_pool_or_error] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(OWNER, None, "aal2")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.db
async def test_collector_stores_reconciled_filing_with_provenance_and_mapping(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    await _seed(backend_pool)
    edgar = Edgar()
    assert await _collect(backend_pool, edgar, dt.date(2026, 9, 30)) == "succeeded"

    # Only the US gap fund is mapped; the UCITS ETP is not an N-PORT filer.
    maps = await backend_pool.fetch("select ticker, series_id, status from nport_fund_map")
    assert [(m["ticker"], m["series_id"], m["status"]) for m in maps] == [
        ("EXLC_US_EQ", "S000009999", "suggested")
    ]
    filings = await backend_pool.fetch("select * from nport_filing")
    # The registrant's other series is read and skipped, never stored under this fund.
    assert [f["accession"] for f in filings] == [ACCESSION]
    f = filings[0]
    assert f["reconcile_status"] == "confirmed" and f["matched_ticker"] == "EXLC_US_EQ"
    assert f["fund_isin"] == "US30000000A8"
    assert f["report_period_end"] == dt.date(2026, 6, 30) and f["filed"] == dt.date(2026, 8, 26)
    assert f["holdings_count"] == 6 and f["document_version_id"] is not None

    evidence = await backend_pool.fetchval(
        "select count(*) from source_record_evidence where domain_table='nport_filing'"
        " and record_key=$1 and document_version_id=$2",
        ACCESSION,
        f["document_version_id"],
    )
    assert evidence == 1
    assert (
        await backend_pool.fetchval(
            "select count(*) from source_record_evidence where domain_table='nport_holding'"
        )
        == 6
    )
    document = await backend_pool.fetchrow(
        "select d.regulator, d.document_type, v.filed_at, v.accepted_at from source_document d"
        " join source_document_version v on v.document_id=d.id where d.external_id=$1",
        ACCESSION,
    )
    assert (document["regulator"], document["document_type"]) == ("sec", "NPORT-P")
    assert document["accepted_at"] is not None

    held = {
        r["issuer_name"]: r
        for r in await backend_pool.fetch("select * from nport_holding order by row_index")
    }
    # ISIN -> security -> canonical issuer; the portfolio instrument is tied by ISIN too.
    assert held["Apple Inc"]["map_basis"] == "isin" and held["Apple Inc"]["issuer_id"] is not None
    assert held["Apple Inc"]["held_ticker"] == "AAPL_US_EQ"
    # LEI-only issuer match.
    assert held["Microsoft Corp"]["map_basis"] == "lei"
    assert held["Microsoft Corp"]["held_ticker"] is None
    # Conflicting identifiers (ISIN says one issuer, LEI another): left unmapped, not guessed.
    alphabet = held["Alphabet Inc"]
    assert alphabet["issuer_id"] is None and alphabet["map_basis"] is None
    assert alphabet["held_ticker"] == "GOOGL_US_EQ"
    # Names never map; unresolved rows keep NULL.
    assert held["Unmapped Holdings plc"]["issuer_id"] is None

    # Fresh report: a second run neither downloads nor duplicates.
    downloads = len(edgar.documents)
    assert await _collect(backend_pool, edgar, dt.date(2026, 9, 30)) == "succeeded"
    assert len(edgar.documents) == downloads
    assert await backend_pool.fetchval("select count(*) from nport_holding") == 6

    response = await api.get("/holdings/AAPL_US_EQ/fund-positions", params={"as_of": "2026-09-30"})
    assert response.status_code == 200
    body = response.json()
    assert body["basis"] == "fund_position" and "Not beneficial ownership" in body["notice"]
    assert body["coverage"]["state"] == "available"
    assert body["coverage"]["funds_loaded"] == 1 and body["coverage"]["funds_holding"] == 1
    (position,) = body["positions"]
    assert position["fund_name"] == "Example US Large Cap ETF"
    assert (position["report_period_end"], position["filed"]) == ("2026-06-30", "2026-08-26")
    assert (position["lag_days"], position["age_days"]) == (57, 92)
    assert position["public_after"] == "2026-08-29" and not position["stale"]
    assert Decimal(position["balance"]) == Decimal("1500000.00")
    assert Decimal(position["pct_of_fund"]) == Decimal("6.9")
    # No total across funds is offered anywhere in the response.
    assert not any("total" in key for key in position) and "total" not in body["coverage"]


@pytest.mark.db
async def test_date_mismatch_is_kept_out_of_the_read_model(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    await _seed(backend_pool)
    # EDGAR lists the filing as a 2026-03-31 report; the document says 2026-06-30.
    assert await _collect(backend_pool, Edgar("2026-03-31"), dt.date(2026, 9, 30)) == "succeeded"
    row = await backend_pool.fetchrow("select reconcile_status, reconcile_detail from nport_filing")
    assert row["reconcile_status"] == "date_mismatch" and "2026-03-31" in row["reconcile_detail"]
    assert await backend_pool.fetchval("select count(*) from nport_holding") == 0
    body = (await api.get("/holdings/AAPL_US_EQ/fund-positions")).json()
    assert body["positions"] == [] and body["coverage"]["state"] == "no_funds_loaded"


@pytest.mark.db
async def test_read_model_flags_stale_reports_and_no_coverage(
    backend_pool: asyncpg.Pool,
) -> None:
    await _seed(backend_pool)
    empty = await holding_fund_positions(backend_pool, "AAPL_US_EQ", as_of=dt.date(2026, 9, 30))
    assert empty.coverage.state == "no_funds_loaded" and empty.positions == []
    assert await _collect(backend_pool, Edgar(), dt.date(2026, 9, 30)) == "succeeded"
    stale = await holding_fund_positions(backend_pool, "AAPL_US_EQ", as_of=dt.date(2027, 3, 1))
    assert stale.coverage.state == "stale" and stale.positions[0].stale
    # A holding no tracked fund reports: loaded funds exist but no position is claimed.
    none = await holding_fund_positions(backend_pool, "VWCEd_EQ", as_of=dt.date(2026, 9, 30))
    assert none.coverage.state == "no_positions" and none.coverage.funds_loaded == 1
    # As of a date before the filing was public nothing is visible (point-in-time).
    early = await holding_fund_positions(backend_pool, "AAPL_US_EQ", as_of=dt.date(2026, 8, 1))
    assert early.coverage.state == "no_funds_loaded"
