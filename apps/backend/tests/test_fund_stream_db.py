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
from steward.config.holding_profiles import HoldingProfile
from steward.config.jobs import FsdsSettings, ValuationSettings
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
from tests.fund_stream_helpers import Ctx, Fact, instance_xml

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


# ----------------------------------------------------------------- ESEF store (B29 / B30 / D4.9)


def _esef_filing(fid: str, added: dt.datetime | None) -> EsefFiling:
    return EsefFiling(
        id=fid,
        period_end=dt.date(2025, 12, 31),
        added=added,
        country="DK",
        json_url=f"https://filings.xbrl.org/{fid}.json",
        report_url=f"https://filings.xbrl.org/{fid}.xhtml",
        raw={},
    )


def _esef_doc() -> dict[str, Any]:
    from tests.test_fund_stream_pure import _esef_doc

    return _esef_doc()


async def test_esef_facts_get_fiscal_periods_evidence_and_a_known_at_that_never_resets(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed_instrument(backend_pool, "NVO_EQ", None)
    parsed = parse_xbrl_json(_esef_doc(), None, include_dimensions=True, include_text_blocks=True)
    first = _esef_filing("esef-1", None)  # the portal gave no date added
    await valuation_collect._store_esef_statement_facts(
        backend_pool, "NVO_EQ", first, parsed, content_hash="a" * 64
    )
    rows = await backend_pool.fetch(
        "select * from public.financial_statement_fact where ticker='NVO_EQ' and source='esef'"
    )
    assert {r["fiscal_period"] for r in rows} == {"FY2025"}
    assert all(r["evidence_id"] is not None for r in rows)
    segment = next(r for r in rows if r["statement"] == "segment")
    assert json.loads(segment["dimensions"]) == {
        "members": [["ifrs-full:SegmentsAxis", "co:CloudMember"]]
    }
    gap = next(r for r in rows if r["concept"].endswith("LeasesExplanatoryTextBlock"))
    assert gap["value_text"] == "unavailable"
    assert json.loads(gap["validation"])["text_block"] == "unavailable"
    known = {r["known_at"] for r in rows}
    assert len(known) == 1

    # Stored again later: known_at is carried over, not reset to now().
    await valuation_collect._store_esef_statement_facts(
        backend_pool, "NVO_EQ", first, parsed, content_hash="a" * 64
    )
    again = await backend_pool.fetch(
        "select known_at from public.financial_statement_fact where ticker='NVO_EQ'"
    )
    assert {r["known_at"] for r in again} == known

    # A corrected package for the same period links to the filing it replaces.
    corrected = _esef_filing("esef-2", dt.datetime(2026, 3, 20, tzinfo=dt.UTC))
    await valuation_collect._store_esef_statement_facts(
        backend_pool, "NVO_EQ", corrected, parsed, content_hash="b" * 64, amends=first
    )
    link = await backend_pool.fetchrow(
        "select a.accession, a.amends_accession, a.revision, a.amends_fact_id, o.id oid"
        " from public.financial_statement_fact a join public.financial_statement_fact o"
        " on o.id = a.amends_fact_id where a.accession='esef-2' and a.concept='ifrs-full:Assets'"
    )
    assert link["amends_accession"] == "esef-1" and link["revision"] == 1


# ------------------------------------------------------------ peers, A9 over the routes, B29


async def evidence(pool: asyncpg.Pool) -> int:
    from steward.sources.documents import link_public_document

    await link_public_document(
        pool,
        regulator="test",
        external_id="doc-1",
        stable_url="https://example.test/doc",
        document_type="note",
        content=b"peer evidence",
        known_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        quote="AAA and BBB compete.",
    )
    return int(await pool.fetchval("select id from public.source_evidence order by id limit 1"))


async def insert_fact(
    pool: asyncpg.Pool,
    ticker: str,
    concept: str,
    value: Decimal | int,
    *,
    dims: dict[str, Any] | None = None,
    start: dt.date | None = dt.date(2025, 1, 1),
    end: dt.date = dt.date(2025, 12, 31),
    basis: str = "US_GAAP",
    unit: str = "USD",
    known_at: dt.datetime = dt.datetime(2026, 2, 1, tzinfo=dt.UTC),
    validation: dict[str, Any] | None = None,
) -> None:
    statement = "segment" if dims else "income"
    await pool.execute(
        "insert into public.financial_statement_fact (ticker, concept, statement, period_start,"
        " period_end, basis, value, unit, dimensions, source, source_url, known_at, validation)"
        " values ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,'test','https://x',$10,$11::jsonb)",
        ticker,
        concept,
        statement,
        start,
        end,
        basis,
        Decimal(value),
        unit,
        json.dumps(dims or {"members": []}),
        known_at,
        json.dumps(validation or {}),
    )


def no_json_strings(value: Any, path: str = "$") -> list[str]:
    """Paths of strings that are really JSON objects or arrays (jsonb that stayed text)."""
    bad: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            bad += no_json_strings(item, f"{path}.{key}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            bad += no_json_strings(item, f"{path}[{i}]")
    elif isinstance(value, str) and value[:1] in "{[":
        try:
            if isinstance(json.loads(value), (dict, list)):
                bad.append(path)
        except ValueError:
            pass
    return bad


async def test_peer_set_status_history_unconfirm_and_as_of(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    evidence_id = await evidence(backend_pool)
    created = await api.post(
        "/holdings/AAA/peer-set",
        json={
            "peer_ticker": "BBB",
            "basis": "owner review",
            "source": "owner",
            "status": "confirmed",
            "evidence_id": evidence_id,
            "detail": {"why": "same market"},
        },
    )
    assert created.status_code == 200, created.text
    peer_id = created.json()["id"]
    first = await backend_pool.fetchrow("select known_at from public.peer_set where id=$1", peer_id)

    # Re-posting (even as a suggestion) changes neither status nor known_at (insert-once).
    again = await api.post(
        "/holdings/AAA/peer-set",
        json={"peer_ticker": "BBB", "basis": "owner review", "source": "owner"},
    )
    assert again.json()["id"] == peer_id
    row = await backend_pool.fetchrow(
        "select known_at, status from public.peer_set where id=$1", peer_id
    )
    assert row["known_at"] == first["known_at"] and row["status"] == "confirmed"

    before_change = await backend_pool.fetchval("select now()")
    # B29: a confirmed peer could never be un-confirmed.
    changed = await api.put(
        f"/holdings/AAA/peer-set/{peer_id}/status",
        json={"status": "rejected", "reason": "different end market"},
    )
    assert changed.status_code == 200 and changed.json()["peer"]["status"] == "rejected"
    now = (await api.get("/holdings/AAA/peer-set")).json()["peers"][0]
    assert now["status"] == "rejected"
    assert isinstance(now["detail"], dict) and isinstance(now["status_history"], list)
    assert [h["status"] for h in now["status_history"]] == ["confirmed", "rejected"]
    past = (
        await api.get("/holdings/AAA/peer-set", params={"as_of": before_change.isoformat()})
    ).json()["peers"][0]
    assert past["status"] == "confirmed"  # the status that was in force then
    # Confirming needs evidence; the unknown id is a 404.
    assert (
        await api.put("/holdings/AAA/peer-set/99999/status", json={"status": "suggested"})
    ).status_code == 404
    # An unknown peer id belonging to another holding is not reachable through this holding.
    assert (
        await api.put(f"/holdings/ZZZ/peer-set/{peer_id}/status", json={"status": "suggested"})
    ).status_code == 404


async def test_vendor_peer_resync_keeps_known_at_and_owner_decisions(
    backend_pool: asyncpg.Pool,
) -> None:
    sql = (
        "insert into public.peer_set (ticker,peer_ticker,basis,status,source,detail,status_history)"
        " values ('AAA','BBB','Finnhub peer candidate','suggested','finnhub','{}'::jsonb,"
        " jsonb_build_array(jsonb_build_object('status','suggested','at',now())))"
        " on conflict(ticker,peer_ticker,basis,source) do update set"
        " detail=peer_set.detail || excluded.detail"
    )
    await backend_pool.execute(sql)
    row = await backend_pool.fetchrow("select id, known_at from public.peer_set")
    from steward.free_sources import set_peer_status

    await set_peer_status(backend_pool, row["id"], "rejected", "not a peer")
    await backend_pool.execute(sql)  # the vendor suggests it again
    after = await backend_pool.fetchrow("select known_at, status from public.peer_set")
    assert after["known_at"] == row["known_at"] and after["status"] == "rejected"


async def test_peer_metrics_route_uses_consolidated_facts_and_explains_every_exclusion(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    evidence_id = await evidence(backend_pool)
    # A9: the segment fact (dimensions stored as jsonb, delivered as text) must not be read as
    # the consolidated revenue.
    await insert_fact(backend_pool, "AAA", "us-gaap:Revenues", 190_000)
    await insert_fact(
        backend_pool,
        "AAA",
        "us-gaap:Revenues",
        7_000,
        dims={"members": [["srt:ProductOrServiceAxis", "co:Cloud"]]},
    )
    await insert_fact(backend_pool, "AAA", "us-gaap:GrossProfit", 95_000)
    await insert_fact(backend_pool, "BBB", "us-gaap:Revenues", 100)
    await insert_fact(backend_pool, "BBB", "us-gaap:GrossProfit", 30)
    await insert_fact(
        backend_pool,
        "BBB",
        "us-gaap:OperatingIncomeLoss",
        10,
        validation={"reconciled": False, "failed_checks": ["x"]},
    )
    await insert_fact(backend_pool, "CCC", "ifrs-full:Revenue", 100, basis="IFRS", unit="EUR")
    for peer in ("BBB", "CCC", "DDD"):
        response = await api.post(
            "/holdings/AAA/peer-set",
            json={
                "peer_ticker": peer,
                "basis": "owner review",
                "source": "owner",
                "status": "confirmed",
                "evidence_id": evidence_id,
            },
        )
        assert response.status_code == 200
    body = (await api.get("/holdings/AAA/peer-metrics")).json()
    gross = body["covered_peer_metrics"]["gross_margin"]
    assert gross["holding_value"] == pytest.approx(0.5)  # 95,000 / 190,000, not 7,000
    assert gross["included"] == ["BBB"] and gross["mean"] == pytest.approx(0.3)
    assert "differs" in gross["excluded_reasons"]["CCC"]
    assert gross["excluded_reasons"]["DDD"] == "no filed statement facts"
    assert {e["peer"] for e in body["excluded_peers"]} == {"CCC", "DDD"}
    # BBB's operating income failed reconciliation: it is not used, and the reason is "missing".
    operating = body["covered_peer_metrics"]["operating_margin"]
    assert "BBB" in operating["excluded"]
    assert body["formula_definitions"]["gross_margin"].startswith("gross profit / revenue")
    assert no_json_strings(body) == []


async def test_financial_history_returns_objects_flags_reconciliation_and_filters(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    await insert_fact(
        backend_pool,
        "AAA",
        "us-gaap:Revenues",
        100,
        dims={"members": [["srt:ProductOrServiceAxis", "co:Cloud"]]},
        validation={"reconciled": True, "passed_checks": ["a"]},
    )
    await insert_fact(backend_pool, "AAA", "us-gaap:GrossProfit", 40)
    rows = (await api.get("/holdings/AAA/financial-history")).json()
    assert no_json_strings(rows) == []
    revenue = next(r for r in rows if r["concept"] == "us-gaap:Revenues")
    assert revenue["dimensions"]["members"][0][1] == "co:Cloud"
    assert revenue["validation"]["reconciled"] is True
    assert revenue["reconciliation"] == "reconciled"
    gross = next(r for r in rows if r["concept"] == "us-gaap:GrossProfit")
    assert gross["reconciliation"] == "unchecked"
    only = (
        await api.get("/holdings/AAA/financial-history", params={"reconciled_only": True})
    ).json()
    assert [r["concept"] for r in only] == ["us-gaap:Revenues"]


async def test_forward_observations_are_not_duplicated_and_consensus_is_per_source(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    def body(provider: str, currency: str, value: str, vintage: str) -> dict[str, Any]:
        return {
            "kind": "published_analyst_consensus",
            "provider": provider,
            "metric": "revenue",
            "fiscal_period": "FY2026",
            "currency": currency,
            "value": value,
            "analyst_count": 10,
            "vintage": vintage,
            "source_url": "https://example.test/consensus",
            "raw": {"table": [1, 2]},
        }

    first = await api.post("/holdings/AAA/forward-data", json=body("a", "USD", "100", "v1"))
    repeat = await api.post("/holdings/AAA/forward-data", json=body("a", "USD", "100.0", "v1"))
    assert first.json()["id"] == repeat.json()["id"]
    assert await backend_pool.fetchval("select count(*) from public.forward_observation") == 1
    # A changed figure, another provider and another currency are separate observations.
    await api.post("/holdings/AAA/forward-data", json=body("a", "USD", "110", "v2"))
    await api.post("/holdings/AAA/forward-data", json=body("b", "USD", "500", "v1"))
    await api.post("/holdings/AAA/forward-data", json=body("a", "EUR", "90", "v1"))
    rows = (await api.get("/holdings/AAA/forward-data")).json()
    assert len(rows) == 4 and no_json_strings(rows) == [] and rows[0]["raw"] == {"table": [1, 2]}
    series = (await api.get("/holdings/AAA/forward-data/consensus")).json()["consensus_series"]
    by_key = {(s["provider"], s["currency"]): s for s in series}
    assert len(series) == 3
    assert Decimal(str(by_key[("a", "USD")]["revision_since_previous"])) == Decimal(10)
    assert by_key[("b", "USD")]["revision_since_previous"] is None
    # The stored key equals the migration's backfill expression (so a backfilled row and a new
    # row for the same figures collide instead of duplicating).
    same = await backend_pool.fetchval(
        "select bool_and(observation_key = md5(concat_ws('|', kind, coalesce(provider,''), metric,"
        " fiscal_period, coalesce(basis,''), coalesce(currency,''), coalesce(vintage,''),"
        " coalesce(period_end::text,''), coalesce(trim_scale(value)::text,''),"
        " coalesce(trim_scale(low)::text,''), coalesce(trim_scale(high)::text,''),"
        " coalesce(analyst_count::text,''))))"
        " from public.forward_observation"
    )
    assert same is True


# ------------------------------------------------------------------------- D4.10: IR releases

IR_URL = "https://ir.example.test/guidance.html"


def _page(revenue_low: str, revenue_high: str) -> bytes:
    return (
        "<html><body><h1>ACME reports results</h1>"
        "<p>For the fourth quarter of fiscal 2026, the Company expects revenue of between "
        f"${revenue_low} billion and ${revenue_high} billion.</p>"
        "<p>The Board authorized a $5 billion share repurchase program.</p>"
        "</body></html>"
    ).encode()


async def _ir_run(pool: asyncpg.Pool, pages: dict[str, bytes], **params: Any) -> str:
    def handler(request: httpx.Request) -> httpx.Response:
        body = pages.get(str(request.url))
        if body is None:
            return httpx.Response(404)
        return httpx.Response(
            200,
            content=body,
            headers={
                "content-type": "text/html",
                "last-modified": "Tue, 29 Sep 2026 12:00:00 GMT",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:

        async def job(ctx: JobContext) -> None:
            await ir_module.run_ir_releases(ctx, http)

        return await run_job(pool, "ir_releases", job, trigger="manual", params=params)


async def test_ir_release_job_fetches_versions_parses_and_cites(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    registered = await api.post(
        "/holdings/AAA/issuer-ir-sources",
        json={"url": IR_URL, "release_type": "guidance", "label": "Guidance page"},
    )
    assert registered.status_code == 200
    bad = await api.post(
        "/holdings/AAA/issuer-ir-sources",
        json={"url": "https://127.0.0.1/x", "release_type": "guidance"},
    )
    assert bad.status_code == 422

    assert await _ir_run(backend_pool, {IR_URL: _page("10.5", "11.0")}) == "succeeded"
    releases = (await api.get("/holdings/AAA/issuer-ir-releases")).json()["releases"]
    assert len(releases) == 1
    first = releases[0]
    assert isinstance(first["metadata"], dict) and first["metadata"]["forward_rows"] == 1
    assert first["metadata"]["capital_return"][0]["type"] == "repurchase"
    assert first["previous_release_id"] is None and first["document_version_id"]
    forward = (await api.get("/holdings/AAA/forward-data")).json()
    assert len(forward) == 1 and forward[0]["kind"] == "company_guidance"
    assert forward[0]["fiscal_period"] == "FY2026Q4"
    assert Decimal(str(forward[0]["low"])) == Decimal(10_500_000_000)
    quote = await backend_pool.fetchval(
        "select quote from public.source_evidence where id=$1", forward[0]["evidence_id"]
    )
    assert "expects revenue of between" in quote

    # Same bytes again: no new version, known_at untouched, only the check time moves.
    first_known = first["known_at"]
    assert await _ir_run(backend_pool, {IR_URL: _page("10.5", "11.0")}) == "succeeded"
    releases = (await api.get("/holdings/AAA/issuer-ir-releases")).json()["releases"]
    assert len(releases) == 1 and releases[0]["known_at"] == first_known
    source = (await api.get("/holdings/AAA/issuer-ir-sources")).json()["sources"][0]
    assert source["last_status"] == "unchanged" and source["last_checked_at"] is not None

    # The page changes: a new version points at the previous one; the old one is superseded.
    assert await _ir_run(backend_pool, {IR_URL: _page("11.0", "11.5")}) == "succeeded"
    releases = (await api.get("/holdings/AAA/issuer-ir-releases")).json()["releases"]
    assert len(releases) == 2
    newest = next(r for r in releases if r["previous_release_id"] is not None)
    assert newest["previous_release_id"] == first["id"]
    superseded = await backend_pool.fetchval(
        "select superseded_by_id from public.source_document_version where id=$1",
        first["document_version_id"],
    )
    assert superseded == newest["document_version_id"]
    assert await backend_pool.fetchval("select count(*) from public.forward_observation") == 2
    # Both vintages stay, point in time.
    old = await api.get("/holdings/AAA/forward-data", params={"as_of": first_known})
    assert len(old.json()) == 1


async def test_a_failing_ir_page_does_not_stop_the_others(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    other = "https://ir.example.test/other.html"
    for url in ("https://ir.example.test/missing.html", other):
        await api.post(
            "/holdings/AAA/issuer-ir-sources", json={"url": url, "release_type": "guidance"}
        )
    assert await _ir_run(backend_pool, {other: _page("1.0", "2.0")}) == "succeeded"
    statuses = {
        r["url"]: r["last_status"]
        for r in await backend_pool.fetch("select url, last_status from public.issuer_ir_source")
    }
    assert statuses["https://ir.example.test/missing.html"] == "fetch_failed"
    assert statuses[other] == "changed"


async def test_actual_vs_consensus_uses_the_vintage_known_before_the_report(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    reported = dt.datetime(2026, 7, 30, 20, 0, tzinfo=dt.UTC)
    await backend_pool.execute(
        "insert into public.financial_statement_fact (ticker, concept, statement, line_item,"
        " period_start, period_end, fiscal_period, basis, value, unit, currency, dimensions, source,"  # noqa: E501
        " source_url, known_at, published) values ('AAA','us-gaap:Revenues','income','revenue',"
        " '2026-04-01','2026-06-30','FY2026Q2','US_GAAP',1050,'USD','USD','{\"members\": []}'::jsonb,"  # noqa: E501
        " 'test','https://x',$1,true)",
        reported,
    )
    for value, known in (
        ("1000", reported - dt.timedelta(days=20)),
        ("1100", reported + dt.timedelta(days=2)),
    ):
        await backend_pool.execute(
            "insert into public.forward_observation (ticker, kind, provider, metric, fiscal_period,"
            " currency, value, analyst_count, vintage, known_at, source_url, observation_key)"
            " values ('AAA','published_analyst_consensus','p','revenue','FY2026Q2','USD',$1,9,$2,$3,"  # noqa: E501
            " 'https://x', md5($2))",
            Decimal(value),
            value,
            known,
        )
    body = (
        await api.get(
            "/holdings/AAA/actual-vs-consensus",
            params={"metric": "revenue", "fiscal_period": "FY2026Q2"},
        )
    ).json()
    assert body["state"] == "available"
    assert Decimal(str(body["consensus"]["value"])) == 1000  # the later 1100 vintage is hindsight
    assert Decimal(str(body["surprise"])) == 50
    assert body["surprise_percent"] == pytest.approx(0.05)
    missing = (
        await api.get(
            "/holdings/AAA/actual-vs-consensus",
            params={"metric": "revenue", "fiscal_period": "FY2030"},
        )
    ).json()
    assert missing["state"] == "no_actual"


# --------------------------------------------------------- D4.8: SEC financial statement data sets

SUB = (
    "adsh\tcik\tname\tform\tperiod\tfy\tfp\tfiled\taccepted\tprevrpt\tfye\n"
    "0000000001-26-000001\t1\tACME\t10-K\t20251231\t2025\tFY\t20260201\t2026-02-01 16:05:11.0\t1\t1231\n"  # noqa: E501
    "0000000001-26-000009\t1\tACME\t10-K/A\t20251231\t2025\tFY\t20260420\t2026-04-20 09:00:00.0\t0\t1231\n"  # noqa: E501
    "0000000002-26-000001\t2\tOTHER\t10-K\t20251231\t2025\tFY\t20260201\t2026-02-01 16:05:11.0\t0\t1231\n"  # noqa: E501
    "0000000001-26-000005\t1\tACME\t8-K\t20260301\t2026\tQ1\t20260301\t2026-03-01 10:00:00.0\t0\t1231\n"  # noqa: E501
)
TAG = (
    "tag\tversion\tcustom\tabstract\tdatatype\tiord\tcrdr\ttlabel\tdoc\n"
    "Revenues\tus-gaap/2025\t0\t0\tmonetary\tD\tC\tRevenues\t\n"
    "Assets\tus-gaap/2025\t0\t0\tmonetary\tI\tD\tAssets\t\n"
    "Liabilities\tus-gaap/2025\t0\t0\tmonetary\tI\tC\tLiabilities\t\n"
    "StockholdersEquity\tus-gaap/2025\t0\t0\tmonetary\tI\tC\tEquity\t\n"
    "LiabilitiesAndStockholdersEquity\tus-gaap/2025\t0\t0\tmonetary\tI\tC\tL&E\t\n"
    "OperatingIncomeLoss\tus-gaap/2025\t0\t0\tmonetary\tD\tC\tOperating income\t\n"
    "MyExtension\t0000000001-26-000001\t1\t0\tmonetary\tD\tC\tMine\t\n"
)
NUM_HEADER = "adsh\ttag\tversion\tddate\tqtrs\tuom\tdimh\tiprx\tvalue\tfootnote\tfootlen\tdimn\tcoreg\tdurp\tdatp\tdcml\n"  # noqa: E501


def _num(
    adsh: str,
    tag: str,
    ddate: str,
    qtrs: int,
    value: str,
    *,
    dimh: str = "0x00000000",
    dimn: int = 0,
    coreg: str = "",
    version: str = "us-gaap/2025",
) -> str:
    return f"{adsh}\t{tag}\t{version}\t{ddate}\t{qtrs}\tUSD\t{dimh}\t0\t{value}\t\t0\t{dimn}\t{coreg}\t0\t0\t0\n"  # noqa: E501


def fsds_zip() -> bytes:
    a, amend, other = "0000000001-26-000001", "0000000001-26-000009", "0000000002-26-000001"
    num = NUM_HEADER
    num += _num(a, "Revenues", "20251231", 4, "1000")
    num += _num(a, "Assets", "20251231", 0, "3000")
    num += _num(a, "Liabilities", "20251231", 0, "2000")
    num += _num(a, "StockholdersEquity", "20251231", 0, "1000")
    num += _num(a, "LiabilitiesAndStockholdersEquity", "20251231", 0, "3000")
    num += _num(a, "OperatingIncomeLoss", "20251231", 4, "120", dimh="0xABCDEF01", dimn=1)
    num += _num(a, "Revenues", "20251231", 4, "5", coreg="SUBSIDIARY")
    num += _num(a, "MyExtension", "20251231", 4, "7", version=a)
    num += _num(amend, "Revenues", "20251231", 4, "1100")
    num += _num(other, "Revenues", "20251231", 4, "9")
    txt = (
        "adsh\ttag\tversion\tddate\tqtrs\tiprx\tlang\tdcml\tdurp\tdatp\tdimh\tdimn\tcoreg\tescaped"
        "\tsrclen\ttxtlen\tfootnote\tfootlen\tcontext\tvalue\n"
        f"{a}\tDebtDisclosureTextBlock\tus-gaap/2025\t20251231\t4\t0\ten-US\t0\t0\t0\t0x00000000\t0\t\t1"
        "\t30\t30\t\t0\tctx\tOur notes mature in 2030.\n"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("sub.tsv", SUB)
        archive.writestr("tag.tsv", TAG)
        archive.writestr("num.tsv", num)
        archive.writestr("txt.tsv", txt)
    return buffer.getvalue()


def test_fsds_reader_applies_the_documented_layout() -> None:
    filings = fsds.read_filings(io.BytesIO(fsds_zip()), ciks={CIK})
    assert {f.adsh for f in filings} == {"0000000001-26-000001", "0000000001-26-000009"}
    original = next(f for f in filings if f.adsh.endswith("000001"))
    assert original.accepted_at == dt.datetime(2026, 2, 1, 21, 5, 11, tzinfo=dt.UTC)  # ET -> UTC
    assert original.superseded is True
    facts = {(f.concept, f.statement): f for f in original.facts}
    revenue = facts[("us-gaap:Revenues", "income")]
    assert revenue.value == 1000 and revenue.period_start == dt.date(2025, 1, 1)
    assert revenue.fiscal_period == "FY2025" and revenue.currency == "USD"
    assert revenue.line_item == "revenue" and revenue.cik == CIK
    assert facts[("us-gaap:Assets", "balance_sheet")].period_start is None
    assert ("us-gaap:OperatingIncomeLoss", "segment") in facts  # dimensional
    assert not any(f.value == 5 for f in original.facts)  # co-registrant skipped
    assert not any("MyExtension" in f.concept for f in original.facts)  # extension skipped
    note = facts[("us-gaap:DebtDisclosureTextBlock", "note")]
    assert note.text_block == "Our notes mature in 2030."
    assert fsds.amends_map(filings) == {"0000000001-26-000009": "0000000001-26-000001"}
    assert fsds.period_start(dt.date(2025, 12, 31), 4) == dt.date(2025, 1, 1)
    assert fsds.period_start(dt.date(2025, 6, 30), 1) == dt.date(2025, 4, 1)


async def test_fsds_backfill_job_stores_reconciled_facts_with_lineage_and_is_idempotent(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed_instrument(backend_pool)

    async def run() -> str:
        async def job(ctx: JobContext) -> None:
            await fsds.run_fsds_backfill(ctx, None, archive=io.BytesIO(fsds_zip()))

        return await run_job(backend_pool, "sec_fsds_backfill", job, trigger="manual")

    assert await run() == "succeeded"
    rows = await backend_pool.fetch(
        "select * from public.financial_statement_fact where source='sec_fsds'"
    )
    assert {r["accession"] for r in rows} == {"0000000001-26-000001", "0000000001-26-000009"}
    assets = next(r for r in rows if r["concept"] == "us-gaap:Assets")
    assert assets["published"] is True and assets["evidence_id"] is not None
    assert assets["known_at"] == dt.datetime(2026, 2, 1, 21, 5, 11, tzinfo=dt.UTC)
    amended = next(
        r for r in rows if r["accession"].endswith("000009") and r["concept"] == "us-gaap:Revenues"
    )
    original = next(
        r
        for r in rows
        if r["accession"].endswith("000001")
        and r["concept"] == "us-gaap:Revenues"
        and r["statement"] == "income"
    )
    assert amended["amends_fact_id"] == original["id"] and amended["revision"] == 1
    count = len(rows)
    assert await run() == "succeeded"
    assert (
        await backend_pool.fetchval(
            "select count(*) from public.financial_statement_fact where source='sec_fsds'"
        )
        == count
    )
    # Filings the live XBRL job already stored are not duplicated by the backfill.
    await backend_pool.execute(
        "update public.financial_statement_fact set source='sec_xbrl' where source='sec_fsds'"
        " and accession='0000000001-26-000001'"
    )
    await backend_pool.execute(
        "delete from public.financial_statement_fact where source='sec_fsds'"
    )
    assert await run() == "succeeded"
    assert not await backend_pool.fetchval(
        "select count(*) from public.financial_statement_fact where source='sec_fsds'"
        " and accession='0000000001-26-000001'"
    )


async def test_scheduled_fsds_backfill_reads_planned_sets_and_remembers_them(
    backend_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_instrument(backend_pool)
    monkeypatch.setattr(
        fsds, "get_settings", lambda: type("S", (), {"sec_user_agent": "Test test@example.com"})
    )
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        requested.append(name)
        if name == "2026_09_notes.zip":
            return httpx.Response(200, content=fsds_zip())
        return httpx.Response(404)

    async def run(settings: Any) -> str:
        async def job(ctx: JobContext) -> None:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
                await fsds.run_fsds_backfill(
                    ctx, http, settings=settings, today=dt.date(2026, 10, 16)
                )

        return await run_job(backend_pool, "sec_fsds_backfill", job, trigger="schedule")

    settings = FsdsSettings(history_months=12, datasets_per_run=1)
    assert await run(settings) == "succeeded"
    assert requested == ["2026_09_notes.zip"]
    assert await backend_pool.fetchval(
        "select count(*) from public.financial_statement_fact where source='sec_fsds'"
    )
    loaded, missing = await fsds.load_state(backend_pool)
    assert loaded == {"2026_09": {CIK}} and missing == {}

    # The next run passes the set it read; the older months answer 404 here, so each is noted
    # and the run stops after four misses (the next run tries their quarters instead).
    requested.clear()
    assert await run(FsdsSettings(history_months=12, datasets_per_run=1)) == "succeeded"
    assert "2026_09_notes.zip" not in requested
    assert requested[:2] == ["2026_08_notes.zip", "2026_07_notes.zip"]
    assert len(requested) == fsds.MAX_MISSES_PER_RUN
    _, missing = await fsds.load_state(backend_pool)
    assert missing["2026_08"] == dt.date(2026, 10, 16)
    summary = await backend_pool.fetchval(
        "select s.detail from public.job_step s join public.job j on j.id = s.job_id"
        " where j.job_type = 'sec_fsds_backfill' and s.name = 'summary'"
        " order by s.id desc limit 1"
    )
    detail = json.loads(summary) if isinstance(summary, str) else summary
    assert detail["read"] == [] and len(detail["not_published"]) == fsds.MAX_MISSES_PER_RUN

    # Switched off: nothing is fetched.
    requested.clear()
    assert await run(FsdsSettings(enabled=False)) == "succeeded"
    assert requested == []


# -------------------------------------------------------------- F17: indexes, jsonb audit


async def test_job_id_foreign_keys_are_indexed(backend_pool: asyncpg.Pool) -> None:
    indexed = {
        r["tablename"]
        for r in await backend_pool.fetch(
            "select tablename from pg_indexes where schemaname='public'"
            " and indexdef like '%(job_id)%'"
        )
    }
    assert {"xbrl_filing", "shares_outstanding"} <= indexed


async def test_no_free_source_route_returns_jsonb_as_a_string(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    """A9 (C-list SUSP): audit every GET route of the phase 3-5 router over seeded rows. A jsonb
    column is delivered as text (no codec is registered), so a route that passes the record on
    sends a JSON string where clients expect an object."""
    pool = backend_pool
    await seed_instrument(pool, "AAA")
    ev = await evidence(pool)
    await pool.execute(
        "insert into public.company_research_observation (ticker, provider, dataset, metric,"
        " period_end, value, source_url, known_at, raw) values ('AAA','alpha_vantage',"
        " 'exposure_context','wti','2026-06-30',70,'https://x',now(),'{\"series\": [1, 2]}'::jsonb)"
    )
    await pool.execute(
        "insert into public.corporate_action (symbol, action_type, ex_date, ratio, source,"
        " source_url, status, evidence_id, raw) values ('AAA','split','2026-01-02',2,'owner',"
        " 'https://x','confirmed',$1,'{\"per\": \"share\"}'::jsonb)",
        ev,
    )
    await pool.execute(
        "insert into public.company_graph_edge (subject_key, object_key, object_name, edge_type,"
        " direction, status, evidence_tier, source, source_url, detail) values ('AAA','BBB','B',"
        " 'partner','symmetric','suggested','candidate','owner','https://x','{\"k\": [1]}'::jsonb)"
    )
    await pool.execute(
        "insert into public.company_event (ticker, event_type, event_state, known_at, source,"
        " source_url, terms, detail, status) values ('AAA','deal','announced',now(),'owner',"
        " 'https://x','{\"value\": 5}'::jsonb,'{\"note\": [1]}'::jsonb,'suggested')"
    )
    await pool.execute(
        "insert into public.patent_family (family_key, legal_status, source, source_url, known_at,"
        " raw) values ('F1','granted','epo','https://x',now(),'{\"a\": 1}'::jsonb)"
    )
    family = await pool.fetchval("select id from public.patent_family")
    await pool.execute(
        "insert into public.patent_family_owner (family_id, ticker, applicant, ownership_confidence,"  # noqa: E501
        " ownership_basis) values ($1,'AAA','AAA Corp',1,'direct')",
        family,
    )
    await pool.execute(
        "insert into public.exchange_session (mic, session_date, state, source, confirmed)"
        " values ('XNYS','2026-07-03','closed','owner',true)"
    )
    await insert_fact(pool, "AAA", "us-gaap:Revenues", 100)
    await api.post(
        "/holdings/AAA/forward-data",
        json={
            "kind": "company_guidance",
            "metric": "revenue",
            "fiscal_period": "FY2026",
            "value": "1",
            "source_url": "https://example.test/g",
            "raw": {"a": [1]},
        },
    )
    await api.post(
        "/holdings/AAA/issuer-ir-releases",
        json={
            "release_type": "guidance",
            "source_url": "https://example.test/ir",
            "content_hash": "a" * 64,
            "metadata": {"k": [1]},
        },
    )
    await api.post(
        "/holdings/AAA/peer-set",
        json={"peer_ticker": "BBB", "basis": "x", "source": "owner", "detail": {"a": [1]}},
    )
    paths = [
        "/holdings/AAA/company-research-observations",
        "/holdings/AAA/exposure-context",
        "/exchange-sessions/XNYS?start=2026-07-01&end=2026-07-31",
        "/holdings/AAA/peer-set",
        "/holdings/AAA/peer-metrics",
        "/holdings/AAA/corporate-actions",
        "/holdings/AAA/financial-history",
        "/holdings/AAA/forward-data",
        "/holdings/AAA/forward-data/consensus",
        "/holdings/AAA/issuer-ir-releases",
        "/holdings/AAA/issuer-ir-sources",
        "/holdings/AAA/business-network",
        "/holdings/AAA/customer-supplier-concentration",
        "/holdings/AAA/patent-trends",
    ]
    leaks: dict[str, list[str]] = {}
    for path in paths:
        response = await api.get(path)
        assert response.status_code == 200, (path, response.text[:200])
        found = no_json_strings(response.json())
        if found:
            leaks[path] = found
    assert leaks == {}
