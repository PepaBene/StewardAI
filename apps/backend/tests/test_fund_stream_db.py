"""Fundamentals stream against a real database: the statement store written by the SEC and ESEF
paths, reconciliation, amendments and lineage, the valuation job's Alpha Vantage fallback and its
failure containment, peers (status history, as-of), forward data, IR releases and the SEC
Financial Statement and Notes backfill."""

import datetime as dt
import io
import json
import zipfile
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool, get_pool_or_error
from api.main import create_app
from steward.config import HoldingProfilesConfig
from steward.config.holding_profiles import HoldingProfile
from steward.config.jobs import ValuationSettings
from steward.config.store import save_config
from steward.fundamentals import collect as fundamentals_collect
from steward.fundamentals import fsds
from steward.fundamentals import ir as ir_module
from steward.fundamentals.collect import run_sec_xbrl
from steward.jobs.runner import JobContext, run_job
from steward.sources.edgar import EdgarClient
from steward.sources.esef import EsefFiling, parse_xbrl_json
from steward.valuation import collect as valuation_collect
from steward.valuation.collect import (
    TickerContext,
    ValuationDeps,
    _stored,
    refresh_alpha_vantage,
)
from tests.fund_stream_helpers import Ctx, Fact, instance_xml, xlsx_bytes

pytestmark = pytest.mark.db

OWNER = "7f0c3d4e-0000-4000-8000-0000000000aa"
CIK = "0000000001"
TODAY = dt.date(2026, 9, 28)


# ------------------------------------------------------------------------------ fixtures


@pytest.fixture
async def api(backend_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[get_pool_or_error] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(OWNER, None, "aal2")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def seed_instrument(
    pool: asyncpg.Pool, ticker: str = "AAA_US_EQ", cik: str | None = CIK
) -> None:
    await pool.execute(
        "insert into instrument (t212_ticker, isin, name, currency, cik, security_type)"
        " values ($1, $2, $3, 'USD', $4, 'Common Stock') on conflict do nothing",
        ticker,
        "US" + ticker[:3].ljust(9, "0") + "0",
        ticker,
        cik,
    )


def statements(
    *,
    revenue: str = "1000",
    form: str = "10-K",
    amendment: bool = False,
    full: bool = True,
    equity: str = "1000",
) -> bytes:
    end, start = dt.date(2025, 12, 31), dt.date(2025, 1, 1)
    contexts = [
        Ctx("d", start, end),
        Ctx("i", None, end),
        Ctx("seg", start, end, [("us-gaap:StatementBusinessSegmentsAxis", "co:CloudMember")]),
    ]
    facts = [
        Fact("dei:DocumentType", "d", form, unit=None),
        Fact("dei:DocumentPeriodEndDate", "d", "2025-12-31", unit=None),
        Fact("dei:DocumentFiscalYearFocus", "d", "2025", unit=None),
        Fact("dei:DocumentFiscalPeriodFocus", "d", "FY", unit=None),
        Fact("dei:EntityCentralIndexKey", "d", CIK, unit=None),
        Fact("dei:AmendmentFlag", "d", "true" if amendment else "false", unit=None),
    ]
    if full:
        facts += [
            Fact("us-gaap:Revenues", "d", revenue, decimals="0"),
            Fact("us-gaap:GrossProfit", "d", "600", decimals="0"),
            Fact("us-gaap:CostOfRevenue", "d", str(Decimal(revenue) - 600), decimals="0"),
            Fact("us-gaap:OperatingIncomeLoss", "d", "200", decimals="0"),
            Fact("us-gaap:NetIncomeLoss", "d", "150", decimals="0"),
            Fact("us-gaap:EarningsPerShareDiluted", "d", "1.5", unit="USD/shares", decimals="2"),
            Fact("us-gaap:ResearchAndDevelopmentExpense", "d", "80", decimals="0"),
            Fact("us-gaap:Assets", "i", "3000", decimals="0"),
            Fact("us-gaap:Liabilities", "i", str(3000 - int(equity)), decimals="0"),
            Fact("us-gaap:StockholdersEquity", "i", equity, decimals="0"),
            Fact("us-gaap:LiabilitiesAndStockholdersEquity", "i", "3000", decimals="0"),
            Fact("us-gaap:LongTermDebtNoncurrent", "i", "800", decimals="0"),
            Fact("us-gaap:LongTermDebtCurrent", "i", "100", decimals="0"),
            Fact("us-gaap:OperatingIncomeLoss", "seg", "120", decimals="0"),
            Fact(
                "us-gaap:DebtDisclosureTextBlock",
                "d",
                "<p>Our notes mature in 2030.</p>",
                unit=None,
            ),
        ]
    return instance_xml(contexts, facts)


class FakeEdgar:
    """EDGAR serving submissions, an optional companyfacts and the filings' instances."""

    def __init__(self, filings: list[dict[str, Any]], companyfacts: dict[str, Any] | None = None):
        self.filings = filings
        self.companyfacts = companyfacts
        self.downloads: list[str] = []

    def submissions(self) -> dict[str, Any]:
        f = self.filings
        return {
            "cik": CIK,
            "filings": {
                "recent": {
                    "form": [x["form"] for x in f],
                    "accessionNumber": [x["accession"] for x in f],
                    "filingDate": [x["filed"] for x in f],
                    "reportDate": [x.get("report", "2025-12-31") for x in f],
                    "primaryDocument": [x["accession"][-6:] + ".htm" for x in f],
                    "items": ["" for _ in f],
                    "acceptanceDateTime": [x.get("accepted", "") for x in f],
                }
            },
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith(f"submissions/CIK{CIK}.json"):
            return httpx.Response(200, json=self.submissions())
        if url.endswith(f"companyfacts/CIK{CIK}.json"):
            if self.companyfacts is None:
                return httpx.Response(404)
            return httpx.Response(200, json=self.companyfacts)
        for x in self.filings:
            folder = x["accession"].replace("-", "")
            if f"/{folder}/" not in url:
                continue
            base = x["accession"][-6:]
            if url.endswith("index.json"):
                names = [{"name": f"{base}.htm"}, {"name": f"{base}_htm.xml"}]
                return httpx.Response(200, json={"directory": {"item": names}})
            if url.endswith(f"{base}_htm.xml"):
                self.downloads.append(x["accession"])
                return httpx.Response(200, content=x["instance"])
        return httpx.Response(404)


async def run_sec(pool: asyncpg.Pool, edgar: FakeEdgar, **params: Any) -> str:
    params.setdefault("ticker", "AAA_US_EQ")
    async with httpx.AsyncClient(transport=httpx.MockTransport(edgar)) as http:
        client = EdgarClient(http, "StewardAI test test@example.com")

        async def handler(ctx: JobContext) -> None:
            await run_sec_xbrl(ctx, client)

        return await run_job(pool, "sec_xbrl", handler, trigger="manual", params=params)


ORIGINAL = {
    "form": "10-K",
    "accession": "0000000001-26-000001",
    "filed": "2026-02-01",
    "accepted": "2026-02-01T21:05:11.000Z",
}
PART_III = {
    "form": "10-K/A",
    "accession": "0000000001-26-000009",
    "filed": "2026-04-20",
    "accepted": "2026-04-20T13:00:00.000Z",
}


# ------------------------------------------------------------------- A12 / A13 / B28 / B29


async def test_sec_job_stores_the_statement_set_and_ignores_a_part_iii_amendment(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed_instrument(backend_pool)
    edgar = FakeEdgar(
        [
            {**PART_III, "instance": statements(form="10-K/A", amendment=True, full=False)},
            {**ORIGINAL, "instance": statements()},
        ]
    )
    assert await run_sec(backend_pool, edgar) == "succeeded"
    # The amendment's instance was read to see whether it restates (it does not) ...
    assert set(edgar.downloads) == {PART_III["accession"], ORIGINAL["accession"]}
    # ... and the ORIGINAL 10-K was parsed (B28: it used to be skipped for the 10-K/A).
    parsed = {r["accession"] for r in await backend_pool.fetch("select accession from xbrl_filing")}
    assert parsed == {PART_III["accession"], ORIGINAL["accession"]}

    rows = await backend_pool.fetch(
        "select * from public.financial_statement_fact where accession=$1", ORIGINAL["accession"]
    )
    by_concept = {r["concept"]: r for r in rows if not json.loads(r["dimensions"])["members"]}
    revenue = by_concept["us-gaap:Revenues"]
    assert revenue["statement"] == "income" and revenue["line_item"] == "revenue"
    assert revenue["fiscal_period"] == "FY2025" and revenue["currency"] == "USD"
    assert revenue["cik"] == CIK
    assert revenue["known_at"] == dt.datetime(2026, 2, 1, 21, 5, 11, tzinfo=dt.UTC)
    assert revenue["accepted_at"] == revenue["known_at"]
    assert revenue["evidence_id"] is not None
    quote = await backend_pool.fetchval(
        "select quote from source_evidence where id=$1", revenue["evidence_id"]
    )
    assert ORIGINAL["accession"] in quote
    # The CIK is text, leading zeros kept; it is not a number.
    cik = by_concept["dei:EntityCentralIndexKey"]
    assert cik["value"] is None and cik["value_text"] == CIK
    # Assets closed against liabilities + equity and revenue against cost + gross profit:
    # reconciled and published. Operating income is covered by no check here (no companyfacts):
    # it is kept for history and stays unpublished.
    assert by_concept["us-gaap:Assets"]["published"] is True
    assert revenue["published"] is True
    assert json.loads(revenue["validation"])["reconciled"] is True
    operating = by_concept["us-gaap:OperatingIncomeLoss"]
    assert operating["published"] is False
    assert json.loads(operating["validation"])["reconciled"] is None
    # The statement set beyond revenue and shares.
    assert {"net_income", "eps_diluted", "research_and_development", "total_assets"} <= {
        r["line_item"] for r in rows
    }
    segment = next(r for r in rows if r["statement"] == "segment")
    assert segment["concept"] == "us-gaap:OperatingIncomeLoss"
    note = next(r for r in rows if r["concept"] == "us-gaap:DebtDisclosureTextBlock")
    assert note["statement"] == "note" and "mature in 2030" in note["text_block"]
    # The amendment's cover page is stored under its own accession, not as the annual report.
    cover = await backend_pool.fetch(
        "select concept from public.financial_statement_fact where accession=$1",
        PART_III["accession"],
    )
    assert {r["concept"] for r in cover} <= {
        "dei:DocumentType",
        "dei:DocumentPeriodEndDate",
        "dei:DocumentFiscalYearFocus",
        "dei:DocumentFiscalPeriodFocus",
        "dei:EntityCentralIndexKey",
        "dei:AmendmentFlag",
    }

    # A re-parse (force) does not move the moment the filing became known.
    before = {r["id"]: r["known_at"] for r in rows}
    assert await run_sec(backend_pool, edgar, force=True) == "succeeded"
    again = await backend_pool.fetch(
        "select known_at from public.financial_statement_fact where accession=$1",
        ORIGINAL["accession"],
    )
    assert {r["known_at"] for r in again} == {dt.datetime(2026, 2, 1, 21, 5, 11, tzinfo=dt.UTC)}
    assert len(again) == len(before)


async def test_a_restating_amendment_becomes_the_report_and_links_to_the_original(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed_instrument(backend_pool)
    restated = {
        **PART_III,
        "instance": statements(form="10-K/A", amendment=True, revenue="1100"),
    }
    edgar = FakeEdgar([restated, {**ORIGINAL, "instance": statements()}])
    assert await run_sec(backend_pool, edgar) == "succeeded"
    original = await backend_pool.fetchrow(
        "select id, value from public.financial_statement_fact where accession=$1"
        " and concept='us-gaap:Revenues' and statement='income'",
        ORIGINAL["accession"],
    )
    amended = await backend_pool.fetchrow(
        "select * from public.financial_statement_fact where accession=$1"
        " and concept='us-gaap:Revenues' and statement='income'",
        PART_III["accession"],
    )
    assert original["value"] == 1000 and amended["value"] == 1100
    assert amended["is_amendment"] is True
    assert amended["amends_accession"] == ORIGINAL["accession"]
    assert amended["amends_fact_id"] == original["id"] and amended["revision"] == 1
    # Point in time: before the amendment was accepted the original figure stands.
    history = await backend_pool.fetch(
        "select accession from public.financial_statement_fact where concept='us-gaap:Revenues'"
        " and statement='income' and known_at <= '2026-03-01' ",
    )
    assert [r["accession"] for r in history] == [ORIGINAL["accession"]]


async def test_companyfacts_reconciles_revenue_and_a_mismatch_blocks_publication(
    backend_pool: asyncpg.Pool,
) -> None:
    def facts(value: int) -> dict[str, Any]:
        return {
            "cik": 1,
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {
                            "USD": [
                                {
                                    "start": "2025-01-01",
                                    "end": "2025-12-31",
                                    "val": value,
                                    "accn": ORIGINAL["accession"],
                                    "form": "10-K",
                                    "filed": "2026-02-01",
                                }
                            ]
                        }
                    }
                }
            },
        }

    await seed_instrument(backend_pool)
    edgar = FakeEdgar([{**ORIGINAL, "instance": statements()}], facts(1000))
    assert await run_sec(backend_pool, edgar) == "succeeded"
    row = await backend_pool.fetchrow(
        "select published, validation from public.financial_statement_fact"
        " where concept='us-gaap:Revenues' and statement='income'"
    )
    assert row["published"] is True
    assert "matches_companyfacts" in json.loads(row["validation"])["passed_checks"]

    edgar = FakeEdgar([{**ORIGINAL, "instance": statements()}], facts(999_999))
    assert await run_sec(backend_pool, edgar, force=True) == "succeeded"
    row = await backend_pool.fetchrow(
        "select published, validation from public.financial_statement_fact"
        " where concept='us-gaap:Revenues' and statement='income'"
    )
    validation = json.loads(row["validation"])
    assert row["published"] is False and validation["reconciled"] is False
    assert "matches_companyfacts" in validation["failed_checks"]


async def test_a_database_error_for_one_holding_does_not_stop_the_sec_job(
    backend_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_instrument(backend_pool, "AAA_US_EQ")
    await seed_instrument(backend_pool, "BBB_US_EQ")
    real = fundamentals_collect.collect_ticker
    seen: list[str] = []

    async def flaky(ctx: Any, edgar: Any, ticker: str, *args: Any, **kwargs: Any) -> None:
        seen.append(ticker)
        if ticker == "AAA_US_EQ":
            raise asyncpg.UniqueViolationError("duplicate key")
        await real(ctx, edgar, ticker, *args, **kwargs)

    monkeypatch.setattr(fundamentals_collect, "collect_ticker", flaky)
    edgar = FakeEdgar([{**ORIGINAL, "instance": statements()}])
    async with httpx.AsyncClient(transport=httpx.MockTransport(edgar)) as http:
        client = EdgarClient(http, "StewardAI test test@example.com")

        async def handler(ctx: JobContext) -> None:
            await run_sec_xbrl(ctx, client)

        # Both holdings are looked up through the holdings list (position snapshot) or, for a
        # single holding, `ticker`; run them one after the other as the scheduler would.
        assert (
            await run_job(
                backend_pool, "sec_xbrl", handler, trigger="manual", params={"ticker": "AAA_US_EQ"}
            )
            == "succeeded"
        )
        assert (
            await run_job(
                backend_pool, "sec_xbrl", handler, trigger="manual", params={"ticker": "BBB_US_EQ"}
            )
            == "succeeded"
        )
    assert seen == ["AAA_US_EQ", "BBB_US_EQ"]
    assert await backend_pool.fetchval(
        "select count(*) from public.financial_statement_fact where ticker='BBB_US_EQ'"
    )


# ------------------------------------------------------------------- A3 / B31: valuation


def _overview(symbol: str = "AAA") -> dict[str, Any]:
    return {"Symbol": symbol, "LatestQuarter": "2026-06-30", "PERatio": "21.5", "EPS": "2.1"}


async def _alpha(
    pool: asyncpg.Pool, body: Any, *, status: int = 200, force: bool = True
) -> dict[str, Any]:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        deps = ValuationDeps(http=http, edgar=None, alpha_vantage_key="k", today=TODAY)
        tc = TickerContext(
            ticker="AAA_US_EQ",
            profile=HoldingProfile(ticker="AAA_US_EQ"),
            preferred=["USD"],
            lei=None,
            stored=await _stored(pool, "AAA_US_EQ"),
        )
        detail: dict[str, Any] = {}
        await refresh_alpha_vantage(tc, deps, ValuationSettings(), force, detail, pool)
        return detail


async def test_alpha_vantage_overview_is_stored_and_errors_never_overwrite_good_data(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed_instrument(backend_pool)
    # A3: the source CHECK admitted only sec / esef / finnhub, so this insert raised.
    await _alpha(backend_pool, _overview())
    row = await backend_pool.fetchrow(
        "select figures, last_error from public.fundamentals where source='alphavantage'"
    )
    assert json.loads(row["figures"])["Symbol"] == "AAA" and row["last_error"] is None

    # B31: an empty body, a provider error message and a 5xx leave the good overview alone.
    for body, status in (({}, 200), ({"Error Message": "Invalid API call"}, 200), ({}, 503)):
        detail = await _alpha(backend_pool, body, status=status)
        assert "alphavantage_error" in detail
        row = await backend_pool.fetchrow(
            "select figures, last_error, fetched_at from public.fundamentals"
            " where source='alphavantage'"
        )
        assert json.loads(row["figures"])["Symbol"] == "AAA"
        assert row["last_error"]


async def test_a_transient_failure_without_stored_data_is_not_cached_but_an_unknown_symbol_is(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed_instrument(backend_pool)
    await _alpha(backend_pool, {}, status=503)
    assert not await backend_pool.fetchval(
        "select count(*) from public.fundamentals where source='alphavantage'"
    )
    await _alpha(backend_pool, {"Error Message": "Invalid API call"})
    row = await backend_pool.fetchrow(
        "select figures, last_error from public.fundamentals where source='alphavantage'"
    )
    assert json.loads(row["figures"]) == {} and "Invalid API call" in row["last_error"]


async def test_a_postgres_error_in_one_source_does_not_abort_the_valuation_job(
    backend_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_valuation import _refresh, _seed

    await _seed(backend_pool)

    async def broken(*args: Any, **kwargs: Any) -> None:
        raise asyncpg.CheckViolationError("fundamentals_source_check")

    monkeypatch.setattr(valuation_collect, "refresh_sec", broken)
    assert await _refresh(backend_pool, []) == "succeeded"
    steps = await backend_pool.fetch(
        "select name, detail from public.job_step where name like 'fundamentals:%'"
    )
    names = {r["name"] for r in steps}
    assert {"fundamentals:TSM_US_EQ", "fundamentals:CEZ_EQ"} <= names  # the later ticker ran
    tsm = next(r for r in steps if r["name"] == "fundamentals:TSM_US_EQ")
    assert "sec_error" in json.loads(tsm["detail"])
