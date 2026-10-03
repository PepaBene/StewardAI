"""Browser-pass item 7: real SEC 13D/G filings (scrubbed, October 2026) and the XSL path trap.

The submissions API names a 13D/G's primary document `xslSCHEDULE_13G_X02/primary_doc.xml`, a
rendered HTML page; the raw submission is the same file name without the `xsl...` folder. The
collector used to download the rendered page, whose DOCTYPE the XML guard refused, so every
real filing ended as `parse_failed`.
"""

import datetime as dt
from decimal import Decimal
from pathlib import Path

import asyncpg
import httpx
import pytest

from steward.jobs.runner import JobContext, run_job
from steward.ownership.control_stakes import (
    _is_submission_xml,
    collect_control_stakes,
    load_held,
    submission_xml_names,
)
from steward.sources.edgar import EdgarClient
from steward.sources.schedule13 import Schedule13Error, parse_schedule13_xml

FIXTURES = Path(__file__).parent / "fixtures" / "control_stakes"
RENDERED = (
    b'<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" "http://www.w3.org/TR/xhtml1/DTD/'
    b'xhtml1-strict.dtd"><html xmlns:p="http://www.sec.gov/edgar/schedule13g"><head></head>'
    b"<body>Schedule 13G</body></html>"
)


def test_live_13g_amendment_reads_the_cusip_from_issuer_cusips() -> None:
    filing = parse_schedule13_xml((FIXTURES / "live_13ga_vanguard.xml").read_bytes())
    assert (filing.schedule, filing.is_amendment, filing.amendment_no) == ("13G", True, 8)
    assert filing.issuer_cik == "0001560385" and filing.issuer_cusip == "531229755"
    assert filing.event_date == dt.date(2026, 3, 13)
    assert filing.stake_shares == Decimal(0) and filing.stake_percent == Decimal(0)


def test_live_13g_with_two_reporting_persons() -> None:
    filing = parse_schedule13_xml((FIXTURES / "live_13g_ubs.xml").read_bytes())
    assert filing.issuer_cusip == "224916106" and len(filing.persons) == 2
    assert filing.stake_percent == Decimal("5.1") and filing.stake_shares == Decimal(2785976)
    assert filing.intent_text is None  # a 13G is passive


def test_live_13d_amendment_group_is_never_summed() -> None:
    filing = parse_schedule13_xml((FIXTURES / "live_13da_group_gamco.xml").read_bytes())
    assert filing.schedule == "13D" and filing.is_amendment and len(filing.persons) == 8
    assert filing.issuer_cusip == "074014200" and filing.issuer_cik == "0001099160"
    assert filing.stake_shares == Decimal(62869)  # the largest single person, not the sum
    assert filing.intent_text


def test_the_rendered_page_gets_a_clear_reason_not_a_dtd_message() -> None:
    with pytest.raises(Schedule13Error, match="rendered HTML page"):
        parse_schedule13_xml(RENDERED)
    with pytest.raises(Schedule13Error, match=r"root <feed>"):
        parse_schedule13_xml(b"<feed xmlns='http://www.w3.org/2005/Atom'/>")


def test_xsl_rendered_primary_document_maps_to_the_raw_file() -> None:
    assert submission_xml_names(
        "xslSCHEDULE_13G_X02/primary_doc.xml",
        ["0000102909-26-002725.txt", "primary_doc.xml", "xslSCHEDULE_13G_X02/primary_doc.xml"],
    ) == ["primary_doc.xml"]
    assert submission_xml_names("legacy.txt", ["a.xml"]) == ["a.xml"]
    assert not _is_submission_xml(RENDERED)
    assert _is_submission_xml((FIXTURES / "live_13g_ubs.xml").read_bytes())


ACC = "0000807249-26-000088"
CIK = "0001099160"


def _mock(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "submissions/CIK" in url:
        recent = {
            "form": ["SCHEDULE 13D/A"],
            "accessionNumber": [ACC],
            "filingDate": ["2026-10-02"],
            "reportDate": ["2026-09-30"],
            "primaryDocument": ["xslSCHEDULE_13D_X01/primary_doc.xml"],
            "items": [""],
        }
        return httpx.Response(200, json={"filings": {"recent": recent}})
    if url.endswith("index.json"):
        names = [{"name": "primary_doc.xml"}, {"name": f"{ACC}.txt"}]
        return httpx.Response(200, json={"directory": {"item": names}})
    if url.endswith("xslSCHEDULE_13D_X01/primary_doc.xml"):
        return httpx.Response(200, content=RENDERED)  # what the SEC serves at the xsl path
    if url.endswith("/primary_doc.xml"):
        return httpx.Response(200, content=(FIXTURES / "live_13da_group_gamco.xml").read_bytes())
    return httpx.Response(404)


@pytest.mark.db
async def test_a_real_shaped_filing_is_stored_through_the_xsl_primary_document(
    backend_pool: asyncpg.Pool,
) -> None:
    await backend_pool.execute(
        "insert into instrument (t212_ticker, isin, name, currency, cik) values"
        " ('BBGI_US_EQ', 'US0740142009', 'Beasley Broadcast', 'USD', $1)",
        CIK,
    )
    job = await backend_pool.fetchval(
        "insert into job (job_type, trigger, status) values ('portfolio_sync', 'manual',"
        " 'succeeded') returning id"
    )
    await backend_pool.execute(
        "insert into account_snapshot (snapshot_date, job_id, captured_at, t212_account_id,"
        " account_currency, total_value, cash_available, cash_in_pies, cash_reserved,"
        " invested_value, invested_cost, unrealized_ppl, realized_ppl, base_currency,"
        " fx_source, fx_rate, cash_base, invested_base, total_base, weight_basis) values"
        " ('2026-10-01', $1, now(), 1, 'USD', 1000, 0, 0, 0, 1000, 1000, 0, 0, 'USD', 'ecb',"
        " 1, 0, 1000, 1000, 'total')",
        job,
    )
    await backend_pool.execute(
        "insert into position_snapshot (snapshot_date, ticker, job_id, captured_at, quantity,"
        " avg_price, current_price, instrument_currency, value, account_currency, value_account,"
        " cost_account, ppl, base_currency, fx_source, fx_rate, value_base) values"
        " ('2026-10-01', 'BBGI_US_EQ', $1, now(), 1, 1, 1, 'USD', 1, 'USD', 1, 1, 0, 'USD',"
        " 'ecb', 1, 1)",
        job,
    )
    counts: dict[str, int] = {}
    async with httpx.AsyncClient(transport=httpx.MockTransport(_mock)) as http:
        edgar = EdgarClient(http, "StewardAI test test@example.com")

        async def handler(ctx: JobContext) -> None:
            held = await load_held(ctx.pool)
            counts.update(
                await collect_control_stakes(ctx, edgar, held, [], since=dt.date(2026, 1, 1))
            )

        assert await run_job(backend_pool, "control_stakes", handler, trigger="manual") == (
            "succeeded"
        )
    assert counts["parse_failed"] == 0 and counts["stored"] == 1
    stored = await backend_pool.fetchrow(
        "select stake_shares, issuer_cusip from control_stake_filing where accession=$1", ACC
    )
    assert stored is not None and stored["issuer_cusip"] == "074014200"
    assert await backend_pool.fetchval("select count(*) from control_stake_holder") == 8
