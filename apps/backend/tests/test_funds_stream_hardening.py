"""Regression tests for the funds / ownership audit items (B33-B40, C-lows, D6.1, UK PDMR).

Each test fails on the code before the matching fix. Fixtures follow the documented layouts;
none is a recording of a live response.
"""

import asyncio
import datetime as dt
import json
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar

import asyncpg
import httpx
import pytest

from steward.config.jobs import CSPX_ISIN
from steward.funds.aggregate import aggregate_exposure
from steward.funds.drift import record_observation
from steward.jobs.runner import JobContext, run_job
from steward.lookthrough.coverage import derive_ledger, refresh_ledger
from steward.lookthrough.factsheet import (
    ManualFactsheet,
    extract_issuer_facts,
    latest_expense_ratios,
    normalise_expense_ratio,
    store_factsheet,
    store_manual_factsheet,
)
from steward.lookthrough.read import UNKNOWN_FUND as READ_UNKNOWN_FUND
from steward.lookthrough.read import compute_look_through
from steward.lookthrough.retention import (
    RetentionPolicy,
    pin_snapshot,
    prune_snapshots,
)
from steward.ownership.control_stakes import collect_control_stakes, load_held
from steward.ownership.insider import parse_ownership_xml
from steward.ownership.insider_collect import run_insider_filings, tickers_by_cik
from steward.ownership.insider_read import holding_insider_activity
from steward.ownership.nport_collect import run_nport
from steward.ownership.pdmr_collect import run_pdmr
from steward.ownership.pdmr_read import access_from_counts, holding_pdmr, pdmr_access_from_db
from steward.sources.base import AccessState
from steward.sources.edgar import EdgarClient, EdgarError, Filing
from steward.sources.etf_issuers import (
    FundSnapshot,
    HoldingRow,
    IssuerSchemaError,
    parse_ishares_holdings,
    parse_ishares_spreadsheet,
)
from steward.sources.fund_characteristics import FundFigures
from steward.sources.fund_probes import classify_fund_probe
from steward.sources.nport import (
    NportError,
    fiscal_quarter_end_months,
    parse_nport,
    public_after,
)
from steward.sources.pdmr_uk import classify_nature
from steward.sources.schedule13 import Schedule13Error, parse_schedule13_xml
from steward.sources.xml_guard import has_dtd_or_entity, safe_fromstring
from tests.test_control_stakes import ISSUER, load
from tests.test_control_stakes import seed as seed_control
from tests.test_fund_lookthrough_phase6 import (
    SETTINGS,
    portfolio,
    position,
    rowin,
    snap,
)
from tests.test_fund_probes_nport import ACCESSION, Edgar
from tests.test_fund_probes_nport import _seed as seed_nport

FIXTURES = Path(__file__).parent / "fixtures"
EXA, EXB = "EXA_US_EQ", "EXB_US_EQ"


def ownership(name: str = "form4_original.xml") -> bytes:
    return (FIXTURES / "insider" / name).read_bytes()


def utf16(text: str, *, bom: bool = True) -> bytes:
    raw = text.encode("utf-16-le")
    return (b"\xff\xfe" + raw) if bom else raw


EVIL = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE x [<!ENTITY a "b">]><edgarSubmission/>'


# ---------------------------------------------------------------------------------------
# B37 shared DTD guard
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        utf16(EVIL),
        utf16(EVIL, bom=False),
        EVIL.replace("UTF-16", "UTF-32").encode("utf-32"),
        EVIL.replace("UTF-16", "cp037").encode("cp037"),
        EVIL.replace("UTF-16", "UTF-16BE").encode("utf-16-be"),
        b"\xef\xbb\xbf" + EVIL.replace("UTF-16", "UTF-8").encode(),
        b"<!doctype x><a/>",
    ],
)
def test_guard_sees_the_dtd_under_every_encoding(data: bytes) -> None:
    assert has_dtd_or_entity(data)
    with pytest.raises(ValueError):
        safe_fromstring(data)


def test_guard_accepts_plain_documents_and_refuses_unknown_encodings() -> None:
    assert not has_dtd_or_entity(b'<?xml version="1.0" encoding="UTF-8"?><a>text</a>')
    assert not has_dtd_or_entity(utf16('<?xml version="1.0" encoding="UTF-16"?><a>x</a>'))
    with pytest.raises(ValueError, match="unsupported encoding"):
        safe_fromstring(b'<?xml version="1.0" encoding="x-made-up"?><a/>')


def test_every_parser_refuses_a_utf16_dtd() -> None:
    doc = utf16(EVIL)
    with pytest.raises(NportError):
        parse_nport(doc)
    with pytest.raises(Schedule13Error):
        parse_schedule13_xml(doc)
    filing = Filing("0000111111", "4", "0001222222-26-000101", dt.date(2026, 8, 12), None, "x", "")
    with pytest.raises(ValueError, match="DTD"):
        parse_ownership_xml(doc, filing)
    # The old check looked at the first 4096 characters only for the DOCTYPE.
    padded = "<?xml version='1.0'?><!-- " + "x" * 5000 + " --><!DOCTYPE x><Workbook/>"
    with pytest.raises(IssuerSchemaError):
        parse_ishares_spreadsheet(padded, "1")


# ---------------------------------------------------------------------------------------
# B33 / C-lows: Rule 10b5-1 flag and classification (see also test_insider_enrichment)
# ---------------------------------------------------------------------------------------


def test_pdmr_natures_use_word_boundaries() -> None:
    # "invested" contains "vest", "cisa" contains "isa": neither is a grant or an ISA.
    assert classify_nature("Shares invested in a fund", "XLON")[0] == "other"
    assert classify_nature("Purchase of ordinary shares", "XLON")[0] == "open_market_buy"
    assert classify_nature("Purchase by CISA nominee", "XLON")[0] == "open_market_buy"
    assert classify_nature("Vesting of LTIP award", "XLON")[0] == "grant_award"
    assert classify_nature("Transfer into ISA", "XLON")[0] == "other"
    assert classify_nature("Wholesale arrangement", "XLON")[0] == "other"


# ---------------------------------------------------------------------------------------
# B34 / B35: insider collection
# ---------------------------------------------------------------------------------------


class FakeEdgar:
    """EDGAR client stand-in: filings per CIK, documents per accession."""

    def __init__(self, filings: list[Filing], documents: dict[str, bytes | Exception]) -> None:
        self._filings = filings
        self._documents = documents
        self.document_calls: list[str] = []

    async def filings(self, cik: str, since: dt.date | None = None) -> list[Filing]:
        return list(self._filings)

    async def filing_files(self, filing: Filing) -> list[str]:
        doc = self._documents.get(filing.accession)
        if isinstance(doc, Exception):
            raise doc
        return [filing.primary_document]

    async def document(self, url: str, max_bytes: int = 0) -> bytes:
        self.document_calls.append(url)
        for accession, doc in self._documents.items():
            if accession.replace("-", "") in url:
                if isinstance(doc, Exception):
                    raise doc
                return doc
        raise EdgarError(f"HTTP 404 for {url}")


def form4(accession: str, filed: str) -> Filing:
    return Filing(
        cik="0000111111",
        form="4",
        accession=accession,
        filing_date=dt.date.fromisoformat(filed),
        report_date=None,
        primary_document="form4.xml",
        items="",
    )


async def seed_dual_class(pool: asyncpg.Pool) -> None:
    await seed_control(pool)  # EXM_US_EQ, CIK 0000111111, held
    await pool.execute(
        "insert into instrument (t212_ticker, isin, name, currency, cik) values"
        " ($1, 'US30000A1043', 'Example Corp A', 'USD', $3),"
        " ($2, 'US30000B1043', 'Example Corp B', 'USD', $3)",
        EXA,
        EXB,
        ISSUER,
    )
    job = await pool.fetchval("select job_id from position_snapshot limit 1")
    for ticker in (EXA, EXB):
        await pool.execute(
            "insert into position_snapshot (snapshot_date, ticker, job_id, captured_at,"
            " quantity, avg_price, current_price, instrument_currency, value, account_currency,"
            " value_account, cost_account, ppl, base_currency, fx_source, fx_rate, value_base)"
            " values ('2026-09-25', $1, $2, now(), 1, 1, 1, 'USD', 1, 'USD', 1, 1, 0, 'USD',"
            " 'ecb', 1, 1)",
            ticker,
            job,
        )
    await pool.execute("delete from position_snapshot where ticker = 'EXM_US_EQ'")


async def run_insiders(pool: asyncpg.Pool, edgar: FakeEdgar) -> int:
    async def handler(ctx: JobContext) -> None:
        await run_insider_filings(ctx, edgar)  # type: ignore[arg-type]

    await run_job(pool, "insider_trades", handler, trigger="manual")
    return int(await pool.fetchval("select max(id) from job"))


async def step_detail(pool: asyncpg.Pool, job_id: int, name: str) -> dict[str, Any]:
    raw = await pool.fetchval(
        "select detail from job_step where job_id = $1 and name = $2", job_id, name
    )
    return dict(json.loads(raw) if isinstance(raw, str) else raw)


def test_held_tickers_group_by_shared_cik() -> None:
    grouped = tickers_by_cik(
        [
            {"ticker": "GOOG_US_EQ", "cik": "0001652044"},
            {"ticker": "GOOGL_US_EQ", "cik": "0001652044"},
            {"ticker": "AAPL_US_EQ", "cik": "0000320193"},
        ]
    )
    assert grouped == {
        "0001652044": ["GOOG_US_EQ", "GOOGL_US_EQ"],
        "0000320193": ["AAPL_US_EQ"],
    }


@pytest.mark.db
async def test_dual_class_tickers_both_get_the_insider_filings(backend_pool: asyncpg.Pool) -> None:
    await seed_dual_class(backend_pool)
    accession = "0001222222-26-000101"
    edgar = FakeEdgar([form4(accession, "2026-08-12")], {accession: ownership()})
    await run_insiders(backend_pool, edgar)

    linked = {
        r["ticker"] for r in await backend_pool.fetch("select ticker from insider_filing_ticker")
    }
    assert linked == {EXA, EXB}
    window = {"since": dt.date(2026, 1, 1), "until": dt.date(2026, 12, 31)}
    first = await holding_insider_activity(backend_pool, EXA, **window)
    second = await holding_insider_activity(backend_pool, EXB, **window)
    assert first.transactions and len(first.transactions) == len(second.transactions)
    assert second.net_discretionary.bought_shares == first.net_discretionary.bought_shares

    # A second run neither re-downloads nor loses the links.
    downloads = len(edgar.document_calls)
    await run_insiders(backend_pool, edgar)
    assert len(edgar.document_calls) == downloads

    # A ticker added later is linked to the filing already stored (the exists branch).
    await backend_pool.execute("delete from insider_filing_ticker where ticker = $1", EXB)
    await run_insiders(backend_pool, edgar)
    assert (
        await backend_pool.fetchval(
            "select count(*) from insider_filing_ticker where ticker = $1", EXB
        )
        == 1
    )


@pytest.mark.db
async def test_one_bad_filing_is_recorded_and_does_not_abort_the_run(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed_dual_class(backend_pool)
    good, malformed, unreachable, wrong_cik = (
        "0001222222-26-000101",
        "0001222222-26-000102",
        "0001222222-26-000103",
        "0001222222-26-000104",
    )
    edgar = FakeEdgar(
        [
            form4(malformed, "2026-08-10"),
            form4(unreachable, "2026-08-11"),
            form4(wrong_cik, "2026-08-11"),
            form4(good, "2026-08-12"),
        ],
        {
            malformed: b"<ownershipDocument><issuer></ownershipDocument>",
            unreachable: EdgarError("HTTP 503 for the filing index"),
            # Non-numeric issuer / owner CIKs must not reach the CHECK constraints.
            wrong_cik: ownership()
            .replace(b"<issuerCik>0000111111", b"<issuerCik>NOT-A-CIK")
            .replace(b"<rptOwnerCik>0000222222", b"<rptOwnerCik>12AB"),
            good: ownership(),
        },
    )
    job_id = await run_insiders(backend_pool, edgar)
    status = await backend_pool.fetchval("select status from job where id = $1", job_id)
    assert status == "succeeded"
    stored = {
        r["accession"] for r in await backend_pool.fetch("select accession from insider_filing")
    }
    assert stored == {good, wrong_cik}
    detail = await step_detail(backend_pool, job_id, "issuer-filings")
    assert detail["filings_imported"] == 2 and detail["filings_failed"] == 2
    failed = {f["accession"] for f in detail["failures"]}
    assert failed == {malformed, unreachable}
    row = await backend_pool.fetchrow(
        "select issuer_cik, reporting_owner_cik from insider_filing where accession = $1",
        wrong_cik,
    )
    assert row["issuer_cik"] == "0000111111" and row["reporting_owner_cik"] is None


# ---------------------------------------------------------------------------------------
# B35 / C-low: control stakes
# ---------------------------------------------------------------------------------------


def test_negative_shares_and_non_numeric_ciks_are_unknown_not_stored() -> None:
    xml = (
        load("sc13d_initial.xml")
        .replace(b"<aggregateAmountOwned>4000000", b"<aggregateAmountOwned>-4000000", 1)
        .replace(b"<sharedVotingPower>4000000", b"<sharedVotingPower>-5", 1)
        .replace(b"<cik>0001500001</cik>", b"<cik>ABCDEFGHIJ</cik>")
        .replace(b"<reportingPersonCIK>0001500002", b"<reportingPersonCIK>\xc2\xb2\xc2\xb3")
    )
    parsed = parse_schedule13_xml(xml)
    assert parsed.persons[0].aggregate_shares is None and parsed.persons[0].shared_voting is None
    assert parsed.filer_cik is None and parsed.persons[1].cik is None
    assert parsed.persons[1].aggregate_shares == Decimal(4_000_000)  # the other person is kept
    assert parsed.stake_shares == Decimal(4_000_000)


class StakeEdgar:
    """Submissions for one held issuer's four Schedule 13 filings; counts XML downloads."""

    FILINGS: ClassVar[dict[str, tuple[str, str]]] = {
        "0001500001-25-000001": ("good", "2025-03-10"),
        "0001500001-25-000002": ("malformed", "2025-03-11"),
        "0001500001-25-000003": ("unheld", "2025-03-12"),
        "0001500001-25-000004": ("flaky", "2025-03-13"),
        "0001500001-25-000005": ("legacy", "2025-03-14"),
    }

    def __init__(self) -> None:
        self.downloads: dict[str, int] = {}
        self.flaky_fails = True

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "submissions/CIK" in url:
            accessions = list(self.FILINGS)
            return httpx.Response(
                200,
                json={
                    "filings": {
                        "recent": {
                            "form": ["SCHEDULE 13D"] * len(accessions),
                            "accessionNumber": accessions,
                            "filingDate": [v[1] for v in self.FILINGS.values()],
                            "reportDate": [""] * len(accessions),
                            "primaryDocument": [
                                "legacy.txt" if v[0] == "legacy" else "primary_doc.xml"
                                for v in self.FILINGS.values()
                            ],
                            "items": [""] * len(accessions),
                        }
                    }
                },
            )
        for accession, (kind, _) in self.FILINGS.items():
            if accession.replace("-", "") not in url:
                continue
            if url.endswith("index.json"):
                names = [] if kind == "legacy" else [{"name": "primary_doc.xml"}]
                return httpx.Response(200, json={"directory": {"item": names}})
            self.downloads[accession] = self.downloads.get(accession, 0) + 1
            if kind == "malformed":
                return httpx.Response(200, content=b"<edgarSubmission><schedule13D>")
            if kind == "flaky":
                if self.flaky_fails:
                    return httpx.Response(503)
                return httpx.Response(200, content=load("sc13d_initial.xml"))
            if kind == "unheld":
                return httpx.Response(
                    200,
                    content=load("sc13d_initial.xml")
                    .replace(b"<issuerCIK>0000111111", b"<issuerCIK>0000999999")
                    .replace(b"<issuerCUSIP>30000A104", b"<issuerCUSIP>99999A104"),
                )
            return httpx.Response(200, content=load("sc13d_initial.xml"))
        return httpx.Response(404)


async def run_stakes(pool: asyncpg.Pool, edgar: StakeEdgar) -> int:
    async with httpx.AsyncClient(transport=httpx.MockTransport(edgar)) as http:
        client = EdgarClient(http, "StewardAI test test@example.com")

        async def handler(ctx: JobContext) -> None:
            held = await load_held(ctx.pool)
            await collect_control_stakes(ctx, client, held, [], since=dt.date(2024, 1, 1))

        await run_job(pool, "control_stakes", handler, trigger="manual")
    return int(await pool.fetchval("select max(id) from job"))


@pytest.mark.db
async def test_control_stakes_record_failures_and_skip_known_accessions(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed_control(backend_pool)
    edgar = StakeEdgar()
    job_id = await run_stakes(backend_pool, edgar)
    assert (
        await backend_pool.fetchval("select status from job where id = $1", job_id) == "succeeded"
    )
    stored = {
        r["accession"]
        for r in await backend_pool.fetch("select accession from control_stake_filing")
    }
    # The flaky filing and the malformed one did not stop the run or the filings after them.
    assert stored == {"0001500001-25-000001"}
    skipped = {
        r["accession"]: r["reason"]
        for r in await backend_pool.fetch(
            "select accession, reason from control_stake_skipped_filing"
        )
    }
    assert skipped == {
        "0001500001-25-000002": "parse_failed",
        "0001500001-25-000003": "not_held",
        "0001500001-25-000005": "unstructured",
    }
    detail = await step_detail(backend_pool, job_id, "store-filings")
    assert detail["failed"] == 1 and detail["failures"][0]["accession"] == "0001500001-25-000004"

    # Second run: only the flaky one (never recorded as skipped) is downloaded again.
    before = dict(edgar.downloads)
    edgar.flaky_fails = False
    await run_stakes(backend_pool, edgar)
    delta = {a: edgar.downloads.get(a, 0) - before.get(a, 0) for a in StakeEdgar.FILINGS}
    assert delta == {
        "0001500001-25-000001": 0,  # stored
        "0001500001-25-000002": 0,  # parse failure, retried only after a week
        "0001500001-25-000003": 0,  # issuer not held
        "0001500001-25-000004": 1,  # transient failure: retried
        "0001500001-25-000005": 0,  # unstructured
    }
    assert (
        await backend_pool.fetchval(
            "select count(*) from control_stake_filing where accession = $1",
            "0001500001-25-000004",
        )
        == 1
    )

    # A previously unheld issuer that becomes held is downloaded again and stored.
    await backend_pool.execute(
        "insert into instrument (t212_ticker, isin, name, currency, cik) values"
        " ('NEW_US_EQ', 'US9999999995', 'New', 'USD', '0000999999')"
    )
    job = await backend_pool.fetchval("select job_id from position_snapshot limit 1")
    await backend_pool.execute(
        "insert into position_snapshot (snapshot_date, ticker, job_id, captured_at, quantity,"
        " avg_price, current_price, instrument_currency, value, account_currency, value_account,"
        " cost_account, ppl, base_currency, fx_source, fx_rate, value_base) values"
        " ('2026-09-25', 'NEW_US_EQ', $1, now(), 1, 1, 1, 'USD', 1, 'USD', 1, 1, 0, 'USD',"
        " 'ecb', 1, 1)",
        job,
    )
    await run_stakes(backend_pool, edgar)
    assert (
        await backend_pool.fetchval(
            "select count(*) from control_stake_filing where accession = $1",
            "0001500001-25-000003",
        )
        == 1
    )
    assert (
        await backend_pool.fetchval(
            "select count(*) from control_stake_skipped_filing where accession = $1",
            "0001500001-25-000003",
        )
        == 0
    )


# ---------------------------------------------------------------------------------------
# B36 fund probes
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"error": "Limit reached. Please upgrade your plan for more calls."},
        {"message": "You have exceeded your daily limit; upgrade your plan"},
        {"Information": "API call frequency is 5 per minute. Upgrade to premium for more."},
    ],
)
def test_quota_phrases_win_over_premium_words(body: dict[str, str]) -> None:
    assert classify_fund_probe(200, body).state is AccessState.QUOTA_EXHAUSTED
    assert classify_fund_probe(403, body).state is AccessState.QUOTA_EXHAUSTED
    assert classify_fund_probe(402, body).state is AccessState.QUOTA_EXHAUSTED


def test_plan_boundaries_are_still_paid_only_and_transients_are_not() -> None:
    assert (
        classify_fund_probe(200, {"error": "This endpoint is premium only"}).state
        is AccessState.PAID_ONLY
    )
    assert classify_fund_probe(403, {"error": "forbidden"}).state is AccessState.PAID_ONLY
    # A bare "plan" no longer means paid-tier.
    assert (
        classify_fund_probe(200, {"message": "Symbol not on your radar plan"}).state
        is not AccessState.PAID_ONLY
    )
    for status in (500, 502, 503, 504):
        assert (
            classify_fund_probe(status, {"error": "boom"}).state is AccessState.TEMPORARILY_FAILED
        )
    assert classify_fund_probe(429, {}).state is AccessState.QUOTA_EXHAUSTED


# ---------------------------------------------------------------------------------------
# B38 / C-lows: look-through unknown weight, aggregate
# ---------------------------------------------------------------------------------------


def test_base_look_through_keeps_unaccounted_weight_unknown() -> None:
    rows = [rowin("APPLE", "US0378331005", 0.50), rowin("USD CASH", None, 0.30, "cash")]
    report = compute_look_through(
        portfolio(position("CSPX", CSPX_ISIN, 800.0, "ETF"), cash=200.0),
        SETTINGS,
        {CSPX_ISIN: snap(CSPX_ISIN, rows)},
        {},
        benchmark_symbols=[],
        today=dt.date(2026, 9, 28),
    )
    w = 0.8
    for dimension in report.exposures:
        by_key = {b.key: b for b in dimension.buckets}
        assert by_key["__fund_cash__"].weight == pytest.approx(w * 0.30)  # published cash only
        unknown = by_key[READ_UNKNOWN_FUND]
        assert unknown.kind == "unknown_fund" and unknown.weight == pytest.approx(w * 0.20)
    assert any("no published row accounts for" in n for n in report.notes)


def test_base_look_through_flags_over_reported_funds() -> None:
    rows = [rowin("APPLE", "US0378331005", 1.05)]
    report = compute_look_through(
        portfolio(position("CSPX", CSPX_ISIN, 800.0, "ETF"), cash=200.0),
        SETTINGS,
        {CSPX_ISIN: snap(CSPX_ISIN, rows)},
        {},
        benchmark_symbols=[],
        today=dt.date(2026, 9, 28),
    )
    assert all(READ_UNKNOWN_FUND not in {b.key for b in d.buckets} for d in report.exposures)
    assert any("over 100%" in n for n in report.notes)


def test_unknown_by_fund_adds_on_duplicate_isin_and_keeps_unidentified_rows() -> None:
    rows = [
        rowin("APPLE", "US0378331005", 0.40),
        rowin("", None, 0.10),  # neither ISIN nor name
        rowin("", None, 0.05),
    ]
    rows[1] = rows[1].__class__(**{**rows[1].__dict__, "name": None})
    rows[2] = rows[2].__class__(**{**rows[2].__dict__, "name": None})
    report = aggregate_exposure(
        portfolio(
            position("CSPX_A", CSPX_ISIN, 400.0, "ETF"),
            position("CSPX_B", CSPX_ISIN, 400.0, "ETF"),
            cash=200.0,
        ),
        SETTINGS,
        {CSPX_ISIN: snap(CSPX_ISIN, rows)},
        as_of=dt.date(2026, 9, 28),
    )
    assert report.unknown is not None
    # Two tickers on one ISIN: the unknown 45 % of each 40 % position adds up (not overwritten).
    assert report.unknown.by_fund[CSPX_ISIN] == pytest.approx(2 * 0.4 * 0.45)
    assert report.unknown.by_fund[CSPX_ISIN] == pytest.approx(report.unknown.residual_weight)
    unidentified = next(i for i in report.issuers if i.key == "unidentified")
    assert unidentified.weight == pytest.approx(2 * 0.4 * 0.15)
    assert any("neither an ISIN nor a name" in n for n in report.notes)


# ---------------------------------------------------------------------------------------
# C-low coverage ledger
# ---------------------------------------------------------------------------------------


def test_historical_constituents_need_real_history() -> None:
    def ledger(count: int, first: dt.date) -> bool | None:
        entries = derive_ledger(
            SETTINGS,
            [],
            None,
            {CSPX_ISIN: dt.date(2026, 9, 25)},
            {CSPX_ISIN: count},
            {CSPX_ISIN: first},
        )
        return next(e for e in entries if e.subject_key == CSPX_ISIN).historical_constituents

    assert ledger(2, dt.date(2026, 9, 18)) is None
    assert ledger(30, dt.date(2026, 9, 1)) is None  # many snapshots, too short a span
    assert ledger(12, dt.date(2026, 1, 1)) is True


@pytest.mark.db
async def test_refresh_removes_stale_derived_rows_but_not_manual_ones(
    backend_pool: asyncpg.Pool,
) -> None:
    await backend_pool.execute(
        "insert into fund_index_coverage (subject_kind, subject_key, label, tier, origin, reason)"
        " values ('fund', 'IE0000STALE01', 'Gone', 'unavailable', 'derived', 'x'),"
        " ('fund', 'IE0000MANUAL01', 'Mine', 'unavailable', 'manual', 'x')"
    )
    await refresh_ledger(backend_pool, SETTINGS, [], None)
    keys = {
        r["subject_key"]
        for r in await backend_pool.fetch("select subject_key from fund_index_coverage")
    }
    assert "IE0000STALE01" not in keys and "IE0000MANUAL01" in keys


# ---------------------------------------------------------------------------------------
# B39 / D6.1: factsheet
# ---------------------------------------------------------------------------------------


def ishares_snapshot(body_extra: dict[str, Any] | None = None) -> FundSnapshot:
    body: dict[str, Any] = {
        "shareClass": "Acc",
        "fundName": "iShares Core S&P 500",
        "componentsByNameMap": {
            "fundamentals": {
                "containersByNameMap": {
                    "all": {
                        "dataPointsByNameMap": {
                            "Total Expense Ratio": {"value": "0.07%"},
                            "fundSize": {"value": 111111.5},
                            "Distribution_Type": {"value": "Accumulating"},
                            "benchmark-index": {"value": "S&P 500"},
                        }
                    }
                }
            }
        },
    }
    body.update(body_extra or {})
    return FundSnapshot(
        provider="ishares",
        portfolio_id="253743",
        source="ishares_json",
        as_of=dt.date(2026, 9, 25),
        fund_name="iShares Core S&P 500",
        fund_currency="USD",
        country_basis="country_of_risk",
        currency_basis="issuer",
        rows=[
            HoldingRow(
                name="APPLE",
                isin="US0378331005",
                ticker="AAPL",
                sedol=None,
                asset_class="equity",
                security_type="Equity",
                weight=Decimal("0.95"),
                sector_raw=None,
                sector=None,
                country_raw=None,
                country=None,
                currency="USD",
                currency_derived=False,
                exchange=None,
                market_value=None,
            )
        ],
        raw={"holdings": body},
    )


def test_factsheet_extraction_is_tolerant_and_marks_names_unverified() -> None:
    facts = extract_issuer_facts(ishares_snapshot())
    assert facts.expense_ratio == Decimal("0.0007")
    assert facts.total_assets == Decimal("111111.5")
    assert facts.distribution_policy == "accumulating" and facts.benchmark_name == "S&P 500"
    assert facts.provenance["expense_ratio"]["verified"] is False
    assert facts.provenance["expense_ratio"]["name"] == "Total Expense Ratio"


def test_vanguard_factsheet_names_nested_anywhere_under_the_fund() -> None:
    snapshot = ishares_snapshot()
    snapshot = FundSnapshot(
        **{
            **snapshot.__dict__,
            "provider": "vanguard",
            "source": "vanguard_graphql",
            "raw": {
                "fund": {
                    "data": {
                        "funds": [
                            {
                                "profile": {"shareClassName": "USD Acc", "ongoingCharge": 0.22},
                                "fees": {"items": [{"name": "netAssets", "value": "9,000,000"}]},
                            }
                        ]
                    }
                }
            },
        }
    )
    facts = extract_issuer_facts(snapshot)
    assert facts.expense_ratio == Decimal("0.0022") and facts.total_assets == Decimal(9_000_000)
    assert facts.share_class == "USD Acc"
    assert facts.provenance["total_assets"]["verified"] is False


@pytest.mark.parametrize(
    ("raw", "hint", "expected"),
    [
        ("0.07%", True, Decimal("0.0007")),
        ("0.07", True, Decimal("0.0007")),
        (0.0007, True, Decimal("0.0007")),  # already a fraction under the percent hint
        ("0.0007", False, Decimal("0.0007")),
        ("0.5", False, Decimal("0.005")),  # above 0.2 as a fraction: percent
        ("70", True, None),  # 70 % a year is a misread, not stored
        ("-0.1", True, None),
        ("n/a", True, None),
    ],
)
def test_expense_ratio_scale_is_detected(raw: Any, hint: bool, expected: Decimal | None) -> None:
    value, note = normalise_expense_ratio(raw, percent_hint=hint)
    assert value == expected
    if expected is None:
        assert note


@pytest.mark.db
async def test_factsheet_versions_are_insert_only_and_survive_snapshot_pruning(
    backend_pool: asyncpg.Pool,
) -> None:
    from steward.config.jobs import LookThroughFund

    fund = LookThroughFund(isin=CSPX_ISIN, label="CSPX", provider="ishares", portfolio_id="253743")
    snapshot_id = await backend_pool.fetchval(
        "insert into etf_snapshot (fund_isin, provider, portfolio_id, source, as_of, fund_name,"
        " raw) values ($1, 'ishares', '253743', 'ishares_json', '2026-09-25', 'x', '{}')"
        " returning id",
        CSPX_ISIN,
    )
    first = await store_factsheet(backend_pool, fund, ishares_snapshot(), snapshot_id)
    again = await store_factsheet(backend_pool, fund, ishares_snapshot(), snapshot_id)
    assert again == first  # identical figures add nothing

    changed = ishares_snapshot()
    changed.raw["holdings"]["componentsByNameMap"]["fundamentals"]["containersByNameMap"]["all"][
        "dataPointsByNameMap"
    ]["Total Expense Ratio"]["value"] = "0.05%"
    second = await store_factsheet(backend_pool, fund, changed, snapshot_id)
    assert second != first
    rows = await backend_pool.fetch(
        "select id, expense_ratio from fund_factsheet where fund_isin = $1 order by id", CSPX_ISIN
    )
    assert [(r["id"], r["expense_ratio"]) for r in rows] == [
        (first, Decimal("0.000700")),
        (second, Decimal("0.000500")),
    ]
    # The old version is untouched, so a read before the change still sees it.
    await backend_pool.execute(
        "update fund_factsheet set fetched_at = '2026-09-26T08:00Z' where id = $1", first
    )
    await backend_pool.execute(
        "update fund_factsheet set fetched_at = '2026-09-28T08:00Z' where id = $1", second
    )
    assert (await latest_expense_ratios(backend_pool, [CSPX_ISIN], dt.date(2026, 9, 27)))[
        CSPX_ISIN
    ] == Decimal("0.0007")
    assert (await latest_expense_ratios(backend_pool, [CSPX_ISIN], dt.date(2026, 9, 28)))[
        CSPX_ISIN
    ] == Decimal("0.0005")

    # Pruning the snapshot no longer deletes the factsheet history.
    await backend_pool.execute("delete from etf_snapshot where id = $1", snapshot_id)
    assert await backend_pool.fetchval("select count(*) from fund_factsheet") == 2
    assert (
        await backend_pool.fetchval("select count(*) from fund_factsheet where snapshot_id is null")
        == 2
    )

    # Manual versions are insert-only too.
    one = await store_manual_factsheet(
        backend_pool,
        ManualFactsheet(CSPX_ISIN, dt.date(2026, 9, 30), expense_ratio=Decimal("0.001")),
    )
    same = await store_manual_factsheet(
        backend_pool,
        ManualFactsheet(CSPX_ISIN, dt.date(2026, 9, 30), expense_ratio=Decimal("0.001")),
    )
    corrected = await store_manual_factsheet(
        backend_pool,
        ManualFactsheet(CSPX_ISIN, dt.date(2026, 9, 30), expense_ratio=Decimal("0.002")),
    )
    assert one == same and corrected != one


# ---------------------------------------------------------------------------------------
# C-lows: retention race, audit growth, unparseable weights
# ---------------------------------------------------------------------------------------


async def make_snapshots(pool: asyncpg.Pool, count: int) -> list[int]:
    ids = []
    for i in range(count):
        ids.append(
            await pool.fetchval(
                "insert into etf_snapshot (fund_isin, provider, portfolio_id, source, as_of,"
                " fund_name, raw) values ($1, 'ishares', '1', 'ishares_json', $2, 'x', '{}')"
                " returning id",
                CSPX_ISIN,
                dt.date(2026, 9, 1) + dt.timedelta(days=i),
            )
        )
    return ids


@pytest.mark.db
async def test_pin_and_prune_race_does_not_raise_or_lose_the_pin(
    backend_pool: asyncpg.Pool,
) -> None:
    ids = await make_snapshots(backend_pool, 6)
    policy = RetentionPolicy(keep_latest=2, keep_month_ends=0)
    # Pin the oldest while a prune is running at the same time.
    results = await asyncio.gather(
        prune_snapshots(backend_pool, CSPX_ISIN, policy),
        pin_snapshot(backend_pool, ids[0], "report", "r1"),
        return_exceptions=True,
    )
    assert not any(isinstance(r, Exception) for r in results), results
    pinned = results[1]
    remaining = {r["id"] for r in await backend_pool.fetch("select id from etf_snapshot")}
    if pinned:
        assert ids[0] in remaining  # a pin that was accepted is never pruned under
        assert await backend_pool.fetchval("select count(*) from fund_snapshot_pin") == 1
    assert set(ids[-2:]) <= remaining

    # A pin that lands before the prune keeps the snapshot (and counts as pinned).
    ids2 = await make_snapshots(backend_pool, 3)
    assert await pin_snapshot(backend_pool, ids2[0], "manual", "m")
    outcome = await prune_snapshots(backend_pool, CSPX_ISIN, RetentionPolicy(1, 0))
    assert outcome["pinned"] >= 1
    assert ids2[0] in {r["id"] for r in await backend_pool.fetch("select id from etf_snapshot")}
    # Pinning a snapshot that is gone is False, not an exception.
    assert await pin_snapshot(backend_pool, 99999999, "manual", "gone") is False


def figures(pe: str = "20.1") -> FundFigures:
    return FundFigures(
        provider="ishares",
        as_of=dt.date(2026, 9, 25),
        pe=Decimal(pe),
        pb=Decimal("4.2"),
        index_pe=None,
        index_pb=None,
        dividend_yield=None,
        dividend_yield_kind=None,
        methodology="m",
        source_url="u",
        raw={},
        rules={},
        shape={"a": 1},
        fingerprint="fp1",
    )


@pytest.mark.db
async def test_characteristic_audit_stores_changes_and_a_weekly_heartbeat(
    backend_pool: asyncpg.Pool,
) -> None:
    assert (
        await record_observation(backend_pool, "CSPX", figures())
    ).verdict == "first_observation"
    for _ in range(5):  # daily runs with nothing new
        result = await record_observation(backend_pool, "CSPX", figures())
        assert result.verdict == "unchanged" and result.recorded is False
    assert await backend_pool.fetchval("select count(*) from fund_characteristic_audit") == 1
    # A restated figure is a change and is stored.
    changed = await record_observation(backend_pool, "CSPX", figures("25.0"))
    assert changed.verdict == "value_change" and changed.recorded
    assert await backend_pool.fetchval("select count(*) from fund_characteristic_audit") == 2
    # After a week of silence the next unchanged check is stored as the heartbeat.
    await backend_pool.execute(
        "update fund_characteristic_audit set observed_at = now() - interval '8 days'"
    )
    beat = await record_observation(backend_pool, "CSPX", figures("25.0"))
    assert beat.verdict == "unchanged" and beat.recorded
    assert await backend_pool.fetchval("select count(*) from fund_characteristic_audit") == 3


def test_unparseable_weights_are_warned_about_in_every_parser() -> None:
    body = json.loads((FIXTURES / "look_through" / "ishares_holdings.json").read_text())
    points = body["componentsByNameMap"]["holdings"]["containersByNameMap"]["all"][
        "dataPointsByNameMap"
    ]
    points["holdingPercent"]["value"][0] = "n/a"
    snapshot = parse_ishares_holdings(body, "253743")
    assert any("skipped: weight missing or unparseable" in w for w in snapshot.warnings)
    assert all(r.weight is not None for r in snapshot.rows)


# ---------------------------------------------------------------------------------------
# B40 N-PORT
# ---------------------------------------------------------------------------------------


def test_public_after_is_report_date_plus_sixty_days() -> None:
    assert public_after(dt.date(2026, 4, 30)) == dt.date(2026, 6, 29)
    assert public_after(dt.date(2026, 6, 30)) == dt.date(2026, 8, 29)
    assert fiscal_quarter_end_months(dt.date(2026, 10, 31)) == {1, 4, 7, 10}
    assert fiscal_quarter_end_months(None) == {3, 6, 9, 12}


class AmendingEdgar(Edgar):
    """The registrant later files an NPORT-P/A for the same period."""

    AMENDMENT = "0001234567-26-000150"

    def __init__(self) -> None:
        super().__init__()
        self.amended = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if self.amended and url.endswith("submissions/CIK0001234567.json"):
            body = json.loads(super().__call__(request).content)
            recent = body["filings"]["recent"]
            for key, value in (
                ("form", "NPORT-P/A"),
                ("accessionNumber", self.AMENDMENT),
                ("filingDate", "2026-09-10"),
                ("reportDate", "2026-06-30"),
                ("primaryDocument", "primary_doc.xml"),
                ("acceptanceDateTime", "2026-09-10T10:00:00.000Z"),
            ):
                recent[key].insert(0, value)
            return httpx.Response(200, json=body)
        return super().__call__(request)


@pytest.mark.db
async def test_amendment_of_the_latest_period_is_picked_up_inside_the_freshness_window(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed_nport(backend_pool)
    edgar = AmendingEdgar()

    async def collect(today: dt.date) -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(edgar)) as http:
            client = EdgarClient(http, "StewardAI test test@example.com")

            async def handler(ctx: JobContext) -> None:
                await run_nport(ctx, client, today)

            assert (
                await run_job(backend_pool, "nport_holdings", handler, trigger="manual")
                == "succeeded"
            )

    await collect(dt.date(2026, 9, 5))
    assert await backend_pool.fetchval("select count(*) from nport_filing") == 1
    # The report (06-30) is 77 days old: inside FRESH_DAYS. The /A must still be found.
    edgar.amended = True
    await collect(dt.date(2026, 9, 15))
    forms = {
        r["accession"]: r["form"]
        for r in await backend_pool.fetch("select accession, form from nport_filing")
    }
    assert forms == {ACCESSION: "NPORT-P", AmendingEdgar.AMENDMENT: "NPORT-P/A"}
    # Nothing is downloaded again on the next run (other series are remembered too).
    downloads = len(edgar.documents)
    await collect(dt.date(2026, 9, 16))
    assert len(edgar.documents) == downloads


# ---------------------------------------------------------------------------------------
# UK PDMR: persistence, honest status, read route
# ---------------------------------------------------------------------------------------


def test_pdmr_status_is_derived_from_attempts() -> None:
    assert access_from_counts({}).state is AccessState.ACCESS_UNVERIFIED
    failing = access_from_counts({"no_template": 3, "access_unverified": 1})
    assert failing.state is AccessState.ACCESS_UNVERIFIED and "none parsed" in (
        failing.detail or ""
    )
    ok = access_from_counts({"parsed": 2, "no_template": 1})
    assert ok.state is AccessState.AVAILABLE and "2 of 3" in (ok.detail or "")


async def seed_rns(pool: asyncpg.Pool, count: int) -> list[str]:
    await pool.execute(
        "insert into source (id, name, tier, type) values ('rns-investegate', 'RNS', 1, 'rns')"
        " on conflict do nothing"
    )
    urls = []
    for i in range(count):
        url = f"https://www.investegate.co.uk/announcement/rns/example/{i}"
        urls.append(url)
        await pool.execute(
            "insert into news_item (url, source_id, tier, published_at, headline, tickers,"
            " affected_ticker, event_type) values ($1, 'rns-investegate', 1, $2,"
            " 'Director/PDMR Shareholding', array['EXM_US_EQ'], 'EXM_US_EQ',"
            " 'insider_transaction')",
            url,
            dt.datetime.now(dt.UTC) - dt.timedelta(days=i + 1),
        )
    await pool.execute(
        "insert into news_item (url, source_id, tier, published_at, headline, tickers) values"
        " ('https://www.investegate.co.uk/announcement/rns/example/other', 'rns-investegate', 1,"
        " now(), 'Holding(s) in Company', array['EXM_US_EQ'])"
    )
    return urls


@pytest.mark.db
async def test_pdmr_notifications_are_persisted_with_honest_status(
    backend_pool: asyncpg.Pool,
) -> None:
    urls = await seed_rns(backend_pool, 3)
    page = (FIXTURES / "pdmr_uk" / "rns_pdmr.html").read_text()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == urls[0]:
            return httpx.Response(200, text=page, headers={"content-type": "text/html"})
        if url == urls[1]:
            return httpx.Response(
                200,
                text="<html><body><div id='app'></div></body></html>",
                headers={"content-type": "text/html"},
            )
        return httpx.Response(503)

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:

            async def job(ctx: JobContext) -> None:
                await run_pdmr(ctx, http)

            assert await run_job(backend_pool, "pdmr_uk", job, trigger="manual") == "succeeded"

    await run()
    statuses = {
        r["source_url"]: r["status"]
        for r in await backend_pool.fetch("select * from pdmr_notification")
    }
    # Only PDMR announcements were attempted (the "Holding(s) in Company" item was not).
    assert statuses == {urls[0]: "parsed", urls[1]: "no_template", urls[2]: "temporarily_failed"}
    assert await backend_pool.fetchval("select count(*) from pdmr_transaction") >= 1

    access = await pdmr_access_from_db(backend_pool)
    assert access.state is AccessState.AVAILABLE and "1 of 3" in (access.detail or "")
    out = await holding_pdmr(backend_pool, "EXM_US_EQ")
    assert out.status.by_status == {"parsed": 1, "no_template": 1, "temporarily_failed": 1}
    parsed = next(n for n in out.notifications if n.status == "parsed")
    assert parsed.transactions and parsed.person_name
    failed = next(n for n in out.notifications if n.status == "temporarily_failed")
    assert failed.transactions == [] and failed.detail

    # Parsed ones are final; failures wait before they are retried (no hammering).
    calls_before = await backend_pool.fetchval("select sum(attempts) from pdmr_notification")
    await run()
    assert (
        await backend_pool.fetchval("select sum(attempts) from pdmr_notification") == calls_before
    )
    await backend_pool.execute(
        "update pdmr_notification set last_attempt_at = now() - interval '2 days'"
        " where status = 'temporarily_failed'"
    )
    await run()
    assert (
        await backend_pool.fetchval(
            "select attempts from pdmr_notification where source_url = $1", urls[2]
        )
        == 2
    )


@pytest.mark.db
async def test_insider_activity_reports_uk_coverage_from_stored_attempts(
    backend_pool: asyncpg.Pool,
) -> None:
    activity = await holding_insider_activity(backend_pool, "EXM_US_EQ")
    uk = next(c for c in activity.jurisdiction_coverage if c.jurisdiction == "GB")
    assert uk.access == "access_unverified"
    await backend_pool.execute(
        "insert into pdmr_notification (source_url, status, ticker)"
        " values ('u', 'parsed', 'EXM_US_EQ')"
    )
    activity = await holding_insider_activity(backend_pool, "EXM_US_EQ")
    uk = next(c for c in activity.jurisdiction_coverage if c.jurisdiction == "GB")
    assert uk.access == "available"


# ---------------------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------------------


@pytest.fixture
async def api(backend_pool: asyncpg.Pool):  # type: ignore[no-untyped-def]
    from api.auth import AuthenticatedUser, require_owner
    from api.db import get_pool, get_pool_or_error
    from api.fund_routes import router as fund_router
    from api.main import create_app
    from api.pdmr_routes import router as pdmr_router

    app = create_app()
    app.include_router(pdmr_router)
    app.include_router(fund_router)

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[get_pool_or_error] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(
        "7f0c3d4e-0000-4000-8000-000000000001", None, "aal2"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.db
async def test_pdmr_route_lists_attempts_and_factsheet_route_marks_unverified_names(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    from steward.config.jobs import LookThroughFund

    await backend_pool.execute(
        "insert into pdmr_notification (source_url, status, ticker, detail)"
        " values ('u1', 'no_template', 'EXM_US_EQ', 'page had no template')"
    )
    body = (await api.get("/holdings/EXM_US_EQ/pdmr-notifications")).json()
    assert body["status"]["state"] == "access_unverified" and body["status"]["parsed"] == 0
    assert body["notifications"][0]["status"] == "no_template" and body["limitations"]

    fund = LookThroughFund(isin=CSPX_ISIN, label="CSPX", provider="ishares", portfolio_id="253743")
    await store_factsheet(backend_pool, fund, ishares_snapshot(), None)
    rows = (await api.get(f"/funds/{CSPX_ISIN}/factsheet")).json()
    assert rows[0]["field_names_verified"] is False
    assert rows[0]["field_provenance"]["expense_ratio"]["verified"] is False


@pytest.mark.db
async def test_transient_probe_failures_and_quota_texts_never_hard_block(
    backend_pool: asyncpg.Pool,
) -> None:
    from pydantic import SecretStr

    from steward.settings import Settings
    from tests.test_fund_probes_nport import Provider, _run_probes

    settings = Settings(_env_file=None, finnhub_api_key=SecretStr("fh"), fmp_api_key=None)
    provider = Provider(
        {
            "/etf/profile": httpx.Response(503, text="upstream down"),
            "/etf/holdings": httpx.Response(
                403, json={"error": "Limit reached. Please upgrade your plan."}
            ),
        }
    )
    assert await _run_probes(backend_pool, provider, settings) == "succeeded"

    async def gate(endpoint: str) -> asyncpg.Record:
        row = await backend_pool.fetchrow(
            "select state, last_attempt_state, hard_blocked from provider_access"
            " where provider = 'finnhub' and endpoint = $1",
            endpoint,
        )
        assert row is not None
        return row

    profile = await gate("etf_profile")
    assert profile["last_attempt_state"] == "temporarily_failed" and not profile["hard_blocked"]
    holdings = await gate("etf_holdings")
    assert holdings["last_attempt_state"] == "quota_exhausted" and not holdings["hard_blocked"]
    # Neither endpoint is latched: the next run calls them again.
    calls = len(provider.calls)
    await _run_probes(backend_pool, provider, settings)
    assert any(c.url.path.endswith("/profile") for c in provider.calls[calls:])
