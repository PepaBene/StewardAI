"""Schedule 13D/G: parser, chain changes, share-class reconciliation, collector, read route.

Fixtures are modelled on the documented structured-XML layout of Schedules 13D and 13G; they
are not recordings of live filings.
"""

import datetime as dt
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path

import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.control_stake_routes import router as control_router
from api.db import get_pool, get_pool_or_error
from api.main import create_app
from steward.jobs.runner import JobContext, run_job
from steward.ownership.control_stakes import (
    Snapshot,
    chain_priors,
    collect_control_stakes,
    compute_changes,
    load_held,
    reconcile_class,
)
from steward.ownership.read import holding_ownership
from steward.sources.edgar import EdgarClient
from steward.sources.schedule13 import (
    Schedule13Error,
    parse_schedule13_xml,
    stake_of,
)

FIXTURES = Path(__file__).parent / "fixtures" / "control_stakes"
OWNER = "7f0c3d4e-0000-4000-8000-000000000001"
ISSUER = "0000111111"
ACC_13D = "0001500001-25-000001"
ACC_13DA = "0001500001-25-000020"
ACC_13G = "0002000001-26-000005"
ACC_LEGACY = "0003000001-20-000009"


def json_keys(node: object) -> list[str]:
    if isinstance(node, dict):
        return [str(k).lower() for k in node] + [k for v in node.values() for k in json_keys(v)]
    if isinstance(node, list):
        return [k for v in node for k in json_keys(v)]
    return []


def load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_parse_initial_13d() -> None:
    f = parse_schedule13_xml(load("sc13d_initial.xml"), "SCHEDULE 13D")
    assert (f.schedule, f.is_amendment, f.amendment_no) == ("13D", False, None)
    assert f.issuer_cik == ISSUER and f.issuer_cusip == "30000A104"
    assert f.class_title == "Class A Common Stock, $0.001 par value"
    assert f.event_date == dt.date(2025, 3, 5)
    assert f.intent_text and "investment purposes" in f.intent_text
    lp, gp = f.persons
    assert lp.cik == "0001500001" and lp.member_of_group is True
    assert (lp.shared_voting, lp.sole_voting) == (Decimal(4000000), Decimal(0))
    assert lp.aggregate_shares == Decimal(4000000) and lp.percent_of_class == Decimal("8.0")
    assert gp.person_type == "OO"
    # Members report the same shares: the filing stake is the largest figure, never the sum.
    assert f.stake_shares == Decimal(4000000)
    assert f.stake_percent == Decimal("8.0")


def test_parse_group_amendment_and_member_without_cik() -> None:
    f = parse_schedule13_xml(load("sc13da_group.xml"), "SCHEDULE 13D/A")
    assert f.is_amendment and f.amendment_no == 1
    assert f.event_date == dt.date(2025, 9, 10)
    assert len(f.persons) == 3
    roe = f.persons[2]
    assert roe.cik is None and roe.key == "name:JOHN ROE"
    assert f.stake_shares == Decimal(5600000)  # largest member, not 5.5M + 5.5M + 5.6M


def test_parse_13g_variant_tags_has_no_intent() -> None:
    f = parse_schedule13_xml(load("sc13g_passive.xml"), "SCHEDULE 13G")
    assert f.schedule == "13G" and f.intent_text is None
    assert f.issuer_cusip == "30000A104" and f.event_date == dt.date(2025, 12, 31)
    person = f.persons[0]
    assert person.aggregate_shares == Decimal(3050000)
    assert person.percent_of_class == Decimal("6.1")
    assert person.sole_dispositive == Decimal(3050000)


def test_parser_rejects_entities_and_non_schedule_documents() -> None:
    with pytest.raises(Schedule13Error):
        parse_schedule13_xml(b'<!DOCTYPE x [<!ENTITY a "b">]><edgarSubmission/>')
    with pytest.raises(Schedule13Error):
        parse_schedule13_xml(b"<ownershipDocument><issuer/></ownershipDocument>")


def test_percent_outside_range_is_unknown_not_clamped() -> None:
    xml = load("sc13d_initial.xml").replace(b"<percentOfClass>8.0", b"<percentOfClass>180.0")
    f = parse_schedule13_xml(xml)
    assert all(p.percent_of_class is None for p in f.persons)
    assert stake_of(f.persons)[1] is None


# ---------------------------------------------------------------------------
# Chain changes
# ---------------------------------------------------------------------------


def snapshots() -> tuple[Snapshot, Snapshot]:
    first = parse_schedule13_xml(load("sc13d_initial.xml"))
    second = parse_schedule13_xml(load("sc13da_group.xml"))
    return (
        Snapshot(ACC_13D, dt.date(2025, 3, 10), first.schedule, first.persons, first.intent_text),
        Snapshot(
            ACC_13DA, dt.date(2025, 9, 15), second.schedule, second.persons, second.intent_text
        ),
    )


def test_group_amendment_changes_stake_membership_and_intent() -> None:
    before, after = snapshots()
    kinds = {(c.change_type, c.person_key) for c in compute_changes(before, after)}
    assert ("holder_added", "name:JOHN ROE") in kinds
    assert ("holder_shares_changed", "cik:0001500001") in kinds
    assert ("holder_percent_changed", "cik:0001500002") in kinds
    assert ("holder_power_changed", "cik:0001500001") in kinds
    assert ("stake_shares_changed", "") in kinds and ("stake_percent_changed", "") in kinds
    assert ("intent_changed", "") in kinds
    shares = next(
        c for c in compute_changes(before, after) if c.change_type == "stake_shares_changed"
    )
    assert (shares.old_value, shares.new_value) == ("4000000", "5600000")
    # Nothing changed: no changes.
    assert compute_changes(before, before) == []


def test_member_leaving_and_schedule_switch_and_below_five() -> None:
    first = parse_schedule13_xml(load("sc13g_passive.xml"))
    d = parse_schedule13_xml(load("sc13d_initial.xml"))
    prev = Snapshot("a", dt.date(2025, 1, 1), "13G", first.persons)
    cur = Snapshot("b", dt.date(2025, 2, 1), "13D", d.persons, d.intent_text)
    kinds = {c.change_type for c in compute_changes(prev, cur)}
    assert {"schedule_type_changed", "holder_added", "holder_removed", "intent_changed"} <= kinds
    big = parse_schedule13_xml(load("sc13d_initial.xml").replace(b"8.0", b"4.9"))
    kinds = {
        c.change_type
        for c in compute_changes(
            Snapshot("a", dt.date(2025, 1, 1), "13D", d.persons, d.intent_text),
            Snapshot("b", dt.date(2025, 2, 1), "13D", big.persons, big.intent_text),
        )
    }
    assert "fell_below_5_percent" in kinds


def test_chains_keep_unrelated_holders_apart() -> None:
    before, after = snapshots()
    other = parse_schedule13_xml(load("sc13g_passive.xml"))
    unrelated = Snapshot(ACC_13G, dt.date(2025, 6, 1), "13G", other.persons)
    priors = chain_priors([after, unrelated, before])
    assert priors == {ACC_13D: None, ACC_13G: None, ACC_13DA: ACC_13D}


# ---------------------------------------------------------------------------
# Share-class reconciliation
# ---------------------------------------------------------------------------


def shares_row(**over: object) -> dict[str, object]:
    return {"shares": Decimal(50_000_000), "as_of": dt.date(2025, 12, 31), "classes": [],
            "ads_ratio_reported": None, **over}  # fmt: skip


def test_reconcile_consistent_inconsistent_and_unreconciled() -> None:
    ok = reconcile_class(
        cusip_matched=True,
        class_title="Class A Common Stock",
        stake_shares=Decimal(4_000_000),
        stake_percent=Decimal("8.0"),
        shares_row=shares_row(),
    )
    assert ok.percent_check == "consistent" and ok.computed_percent == Decimal("8.00")
    off = reconcile_class(
        cusip_matched=True,
        class_title="Class A",
        stake_shares=Decimal(4_000_000),
        stake_percent=Decimal("12.0"),
        shares_row=shares_row(),
    )
    assert off.percent_check == "inconsistent"
    other = reconcile_class(
        cusip_matched=False,
        class_title="Class B",
        stake_shares=Decimal(4_000_000),
        stake_percent=Decimal("8.0"),
        shares_row=shares_row(),
    )
    assert other.class_status == "class_unreconciled" and other.computed_percent is None


def test_reconcile_uses_the_named_share_class() -> None:
    classes = [
        {"member": "A", "label": "Class A", "shares": 20_000_000, "counted": True},
        {"member": "B", "label": "Class B", "shares": 80_000_000, "counted": True},
    ]
    a = reconcile_class(
        cusip_matched=True,
        class_title="Class A Common Stock",
        stake_shares=Decimal(4_000_000),
        stake_percent=Decimal(20),
        shares_row=shares_row(classes=classes),
    )
    assert a.class_label == "Class A" and a.class_shares_outstanding == 20_000_000
    assert a.percent_check == "consistent"
    unnamed = reconcile_class(
        cusip_matched=True,
        class_title="Common Stock",
        stake_shares=Decimal(4_000_000),
        stake_percent=Decimal(20),
        shares_row=shares_row(classes=classes),
    )
    assert unnamed.percent_check == "not_checkable"
    ads = reconcile_class(
        cusip_matched=True,
        class_title="ADS",
        stake_shares=Decimal(1),
        stake_percent=Decimal(1),
        shares_row=shares_row(ads_ratio_reported=Decimal(5)),
    )
    assert ads.percent_check == "not_checkable"
    missing = reconcile_class(
        cusip_matched=True,
        class_title="x",
        stake_shares=None,
        stake_percent=None,
        shares_row=None,
    )
    assert missing.percent_check == "not_checkable"


# ---------------------------------------------------------------------------
# Collector, storage and read route
# ---------------------------------------------------------------------------

FILES = {
    ACC_13D: ("sc13d_initial.xml", "SCHEDULE 13D", "2025-03-10"),
    ACC_13DA: ("sc13da_group.xml", "SCHEDULE 13D/A", "2025-09-15"),
    ACC_13G: ("sc13g_passive.xml", "SCHEDULE 13G", "2026-02-10"),
}


def submissions() -> dict[str, object]:
    rows = [(a, form, filed) for a, (_, form, filed) in FILES.items()]
    rows.append((ACC_LEGACY, "SC 13G", "2026-01-05"))
    return {
        "filings": {
            "recent": {
                "form": [r[1] for r in rows],
                "accessionNumber": [r[0] for r in rows],
                "filingDate": [r[2] for r in rows],
                "reportDate": ["" for _ in rows],
                "primaryDocument": [
                    "primary_doc.xml" if r[0] in FILES else "legacy.txt" for r in rows
                ],
                "items": ["" for _ in rows],
            }
        }
    }


def mock_edgar(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "submissions/CIK" in url:
        return httpx.Response(200, json=submissions())
    for accession, (name, _, _) in FILES.items():
        if accession.replace("-", "") in url:
            if url.endswith("index.json"):
                return httpx.Response(
                    200, json={"directory": {"item": [{"name": "primary_doc.xml"}]}}
                )
            if url.endswith("primary_doc.xml"):
                return httpx.Response(200, content=load(name))
    return httpx.Response(404)


async def seed(pool: asyncpg.Pool) -> None:
    await pool.execute(
        "insert into instrument (t212_ticker, isin, name, currency, cik) values"
        " ('EXM_US_EQ', 'US30000A1043', 'Example Corp', 'USD', $1)",
        ISSUER,
    )
    job = await pool.fetchval(
        "insert into job (job_type, trigger, status) values ('portfolio_sync', 'manual',"
        " 'succeeded') returning id"
    )
    await pool.execute(
        "insert into account_snapshot (snapshot_date, job_id, captured_at, t212_account_id,"
        " account_currency, total_value, cash_available, cash_in_pies, cash_reserved,"
        " invested_value, invested_cost, unrealized_ppl, realized_ppl, base_currency,"
        " fx_source, fx_rate, cash_base, invested_base, total_base, weight_basis) values"
        " ('2026-09-25', $1, now(), 1, 'USD', 1000, 0, 0, 0, 1000, 1000, 0, 0, 'USD', 'ecb',"
        " 1, 0, 1000, 1000, 'total')",
        job,
    )
    await pool.execute(
        "insert into position_snapshot (snapshot_date, ticker, job_id, captured_at, quantity,"
        " avg_price, current_price, instrument_currency, value, account_currency, value_account,"
        " cost_account, ppl, base_currency, fx_source, fx_rate, value_base) values"
        " ('2026-09-25', 'EXM_US_EQ', $1, now(), 1, 1, 1, 'USD', 1, 'USD', 1, 1, 0, 'USD',"
        " 'ecb', 1, 1)",
        job,
    )
    await pool.execute(
        "insert into shares_outstanding (ticker, cik, shares, as_of, method, raw) values"
        " ('EXM_US_EQ', $1, 50000000, '2025-12-31', 'dei_companyfacts', '{}')",
        ISSUER,
    )


async def run_collector(pool: asyncpg.Pool) -> str:
    async with httpx.AsyncClient(transport=httpx.MockTransport(mock_edgar)) as http:
        edgar = EdgarClient(http, "StewardAI test test@example.com")

        async def handler(ctx: JobContext) -> None:
            held = await load_held(ctx.pool)
            await collect_control_stakes(
                ctx, edgar, held, ["0001500001"], since=dt.date(2024, 1, 1)
            )

        return await run_job(pool, "control_stakes", handler, trigger="manual")


@pytest.fixture
async def api(backend_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    app.include_router(control_router)

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
async def test_collector_stores_chain_provenance_and_reads_back(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    await seed(backend_pool)
    assert await run_collector(backend_pool) == "succeeded"

    filings = await backend_pool.fetch(
        "select accession, prior_accession, stake_shares, stake_percent, schedule"
        " from control_stake_filing order by filed"
    )
    assert [f["accession"] for f in filings] == [ACC_13D, ACC_13DA, ACC_13G]
    assert filings[1]["prior_accession"] == ACC_13D
    assert filings[2]["prior_accession"] is None  # an unrelated passive holder
    assert filings[1]["stake_shares"] == Decimal(5600000)

    # Provenance: every filing has a versioned source document and a record link.
    evidence = await backend_pool.fetchval(
        "select count(*) from source_record_evidence where domain_table = 'control_stake_filing'"
    )
    assert evidence == 3
    amends = await backend_pool.fetchval(
        "select v.amends_version_id is not null from source_document d"
        " join source_document_version v on v.document_id = d.id where d.external_id = $1",
        ACC_13DA,
    )
    assert amends is True

    link = await backend_pool.fetchrow(
        "select * from control_stake_issuer_link where accession = $1", ACC_13D
    )
    assert link["match_basis"] == "cusip" and link["class_status"] == "cusip_match"
    assert link["percent_check"] == "consistent"
    assert link["class_shares_outstanding"] == Decimal(50_000_000)
    bad = await backend_pool.fetchrow(
        "select percent_check, computed_percent from control_stake_issuer_link"
        " where accession = $1",
        ACC_13DA,
    )
    assert bad["computed_percent"] == Decimal("11.20")  # 5.6M / 50M, kept beside the filer's 11.2

    # Re-running is idempotent and does not refetch stored filings.
    assert await run_collector(backend_pool) == "succeeded"
    assert await backend_pool.fetchval("select count(*) from control_stake_filing") == 3
    assert await backend_pool.fetchval("select count(*) from control_stake_holder") == 6

    body = (await api.get("/holdings/EXM_US_EQ/control-stakes")).json()
    assert body["channel"] == "beneficial_ownership_13dg"
    assert "form_13f_positions" in body["not_additive_with"]
    current = {s["accession"]: s for s in body["stakes"]}
    assert set(current) == {ACC_13DA, ACC_13G}  # the initial 13D is superseded by its /A
    assert [s["accession"] for s in body["superseded"]] == [ACC_13D]
    amended = current[ACC_13DA]
    kinds = {c["change_type"] for c in amended["changes"]}
    assert {"holder_added", "stake_shares_changed", "intent_changed"} <= kinds
    assert len(amended["holders"]) == 3
    assert amended["reconciliation"]["percent_check"] == "consistent"
    # No total across filings or channels is ever produced.
    assert not [k for k in json_keys(body) if "total" in k or "combined" in k]


@pytest.mark.db
async def test_13dg_is_never_summed_with_13f(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    await seed(backend_pool)
    await backend_pool.execute("insert into form13f_dataset (url) values ('u')")
    await backend_pool.execute(
        "insert into form13f_filing (accession, cik, manager_name, period, filed,"
        " submission_type, dataset_url) values ('0000000001-26-000001', '0000000001',"
        " 'Big Fund', '2026-06-30', '2026-08-10', '13F-HR', 'u')"
    )
    await backend_pool.execute(
        "insert into institutional_holding (accession, infotable_sk, cusip, ticker, value_usd,"
        " shares, shares_type) values ('0000000001-26-000001', 1, '30000A104', 'EXM_US_EQ', 1,"
        " 1000000, 'SH')"
    )
    before = await holding_ownership(backend_pool, "EXM_US_EQ")
    assert await run_collector(backend_pool) == "succeeded"
    after = await holding_ownership(backend_pool, "EXM_US_EQ")
    # 13F aggregation is untouched by stored 13D/G stakes ...
    assert after.model_dump() == before.model_dump()
    assert before.periods and before.periods[0].shares == Decimal(1_000_000)
    # ... and the 13D/G channel reports only filer figures, never 13F-inclusive ones.
    body = (await api.get("/holdings/EXM_US_EQ/control-stakes")).json()
    figures = {Decimal(str(s["stake_shares"])) for s in body["stakes"]}
    assert figures == {Decimal(5_600_000), Decimal(3_050_000)}  # filer figures only
    assert "1000000" not in str(body)  # nothing from the 13F position leaks in


@pytest.mark.db
async def test_filings_for_unheld_issuers_are_not_stored(backend_pool: asyncpg.Pool) -> None:
    await backend_pool.execute(
        "insert into instrument (t212_ticker, isin, name, currency, cik) values"
        " ('OTHER_US_EQ', 'US9999999995', 'Other', 'USD', '0000999999')"
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
        " ('2026-09-25', $1, now(), 1, 'USD', 1000, 0, 0, 0, 1000, 1000, 0, 0, 'USD', 'ecb',"
        " 1, 0, 1000, 1000, 'total')",
        job,
    )
    await backend_pool.execute(
        "insert into position_snapshot (snapshot_date, ticker, job_id, captured_at, quantity,"
        " avg_price, current_price, instrument_currency, value, account_currency, value_account,"
        " cost_account, ppl, base_currency, fx_source, fx_rate, value_base) values"
        " ('2026-09-25', 'OTHER_US_EQ', $1, now(), 1, 1, 1, 'USD', 1, 'USD', 1, 1, 0, 'USD',"
        " 'ecb', 1, 1)",
        job,
    )
    assert await run_collector(backend_pool) == "succeeded"
    assert await backend_pool.fetchval("select count(*) from control_stake_filing") == 0
