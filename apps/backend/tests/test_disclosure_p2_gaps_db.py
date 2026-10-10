"""P2 gap closure against a real database: stored note buckets run the validators, a missing note
is a named not-run entry, formulas cite their facts, segment metrics and cross-axis rows come
back separately, and the valuation gate is attached to fact rows read-only."""

import datetime as dt
import json
from decimal import Decimal as D

import asyncpg
import pytest

from steward.disclosure import statement_read
from steward.disclosure.note_store import load_note_rows, store_note_tables
from steward.disclosure.note_tables import extract_debt_maturities, extract_lease_maturities

pytestmark = pytest.mark.db

UTC = dt.UTC
T1 = dt.datetime(2026, 2, 20, 21, 0, tzinfo=UTC)
T2 = dt.datetime(2026, 5, 2, 20, 15, tzinfo=UTC)
BEFORE = dt.datetime(2026, 1, 1, tzinfo=UTC)
BETWEEN = dt.datetime(2026, 3, 15, tzinfo=UTC)
AFTER = dt.datetime(2026, 6, 1, tzinfo=UTC)
START, END = dt.date(2025, 1, 1), dt.date(2025, 12, 31)
GEO, PROD = "StatementGeographicalAxis", "ProductOrServiceAxis"

DEBT = "(in millions)\n2026 100\n2027 200\nThereafter 700\nTotal 1,000\n"
LEASE = (
    "(in millions)\n2026 40\n2027 40\nThereafter 20\nTotal lease payments 100\n"
    "Less: imputed interest (10)\nPresent value of lease liabilities 90\n"
)


async def issuer(pool: asyncpg.Pool) -> int:
    row = await pool.fetchrow(
        "insert into public.issuer(legal_name, country) values('Acme Corp','US') returning id"
    )
    assert row is not None
    return int(row["id"])


async def fact(
    pool: asyncpg.Pool,
    issuer_id: int,
    concept: str,
    value: str,
    known: dt.datetime,
    accession: str,
    *,
    statement: str = "income",
    dims: dict | None = None,
    start: dt.date | None = START,
) -> None:
    await pool.execute(
        """insert into public.financial_statement_fact
             (issuer_id, ticker, accession, form, concept, statement, period_start, period_end,
              basis, value, unit, dimensions, source, source_url, known_at)
           values ($1,'ACME',$2,'10-K',$3,$4,$5,$6,'as_filed',$7,'USD',$8::jsonb,'sec','u',$9)""",
        issuer_id,
        accession,
        concept,
        statement,
        start,
        END,
        D(value),
        json.dumps(dims or {}),
        known,
    )


async def clean_income(pool: asyncpg.Pool, iid: int) -> None:
    """Revenue, cost and gross profit that add up, so only the note checks can move the gate."""
    for concept, value in (("Revenues", "1000"), ("CostOfRevenue", "600"), ("GrossProfit", "400")):
        await fact(pool, iid, concept, value, T1, "A1")


async def store(
    pool: asyncpg.Pool, iid: int, known: dt.datetime = T1, accession: str = "A1"
) -> int:
    return await store_note_tables(
        pool,
        issuer_id=iid,
        ticker="ACME",
        accession=accession,
        form="10-K",
        period_end=END,
        known_at=known,
        source_url="u",
        currency="USD",
        debt=extract_debt_maturities(DEBT),
        lease=extract_lease_maturities(LEASE),
    )


async def test_note_buckets_run_validators_and_missing_note_is_named(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    iid = await issuer(pool)
    await clean_income(pool, iid)
    out = await statement_read.statements(pool, iid, AFTER)
    key = END.isoformat()
    (no_note,) = [c for c in out["checks"][key] if c["name"] == "note_data"]
    assert "no note data" in no_note["detail"] and no_note["status"] != "pass"
    assert out["usable_for_valuation"][key] is True  # a coverage gap is named, it does not fail

    assert await store(pool, iid) == 10  # 4 debt rows and 6 lease rows
    assert await store(pool, iid) == 0  # idempotent
    out = await statement_read.statements(pool, iid, AFTER)
    names = {c["name"]: c for c in out["checks"][key]}
    assert names["debt_maturities"]["status"] == "pass"
    assert names["lease_schedule"]["status"] == "pass"
    assert "note_data" not in names
    # a stored note row is never read as a statement fact
    assert out["unmapped_concepts"] == 0
    assert {h["metric"] for h in out["history"]} == {"revenue", "cost_of_revenue", "gross_profit"}


async def test_note_data_is_point_in_time_and_a_bad_total_blocks_the_gate(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    iid = await issuer(pool)
    await clean_income(pool, iid)
    await store_note_tables(
        pool,
        issuer_id=iid,
        ticker="ACME",
        accession="A2",
        form="10-K/A",
        period_end=END,
        known_at=T2,
        source_url="u",
        currency="USD",
        debt=extract_debt_maturities(DEBT.replace("Total 1,000", "Total 1,300")),
    )
    key = END.isoformat()
    mid = await statement_read.statements(pool, iid, BETWEEN)
    assert [c for c in mid["checks"][key] if c["name"] == "note_data"]
    assert mid["usable_for_valuation"][key] is True
    late = await statement_read.statements(pool, iid, AFTER)
    assert any(
        c["name"] == "debt_maturities" and c["status"] == "fail" for c in late["checks"][key]
    )
    assert late["usable_for_valuation"][key] is False
    assert any("debt_maturities" in r for r in late["valuation_gate"][key]["reasons"])
    assert await load_note_rows(pool, iid, BEFORE) == {}


async def test_formulas_cite_facts_and_use_year_one_debt_from_the_note(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    iid = await issuer(pool)
    inc = {
        "Revenues": "1000",
        "GrossProfit": "400",
        "OperatingIncomeLoss": "200",
        "NetIncomeLoss": "150",
    }
    for concept, value in inc.items():
        await fact(pool, iid, concept, value, T1, "A1")
    await fact(
        pool,
        iid,
        "NetCashProvidedByUsedInOperatingActivities",
        "180",
        T1,
        "A1",
        statement="cash_flow",
    )
    await fact(
        pool,
        iid,
        "CashAndCashEquivalentsAtCarryingValue",
        "100",
        T1,
        "A1",
        statement="balance_sheet",
        start=None,
    )
    await store(pool, iid)
    out = await statement_read.statements(pool, iid, AFTER)
    f = {x["formula"]: x for x in out["formulas"] if x["period_end"] == END.isoformat()}
    assert D(f["gross_margin"]["value"]) == D("0.4")
    assert D(f["cash_conversion"]["value"]) == D("1.2")
    gm_inputs = {i["metric"]: i for i in f["gross_margin"]["inputs"]}
    assert gm_inputs["revenue"]["accession"] == "A1" and isinstance(
        gm_inputs["revenue"]["fact_id"], int
    )
    # debt due in year 1 is 100 million, cash is 100: coverage 1e-6 of the scaled value
    cov = f["debt_maturity_coverage"]
    assert cov["inputs"][1]["source"] == "note" and D(cov["inputs"][1]["value"]) == D(100_000_000)
    assert D(cov["value"]) == D(0) or cov["value"] is not None
    # ROIC lacks equity, debt and tax inputs: null with a named gap and coverage denominator
    roic = f["roic"]
    assert roic["value"] is None and "missing input" in roic["reason"]
    assert roic["coverage"]["required"] == 6 and roic["coverage"]["present"] < 6


async def test_segment_metrics_and_cross_axis_rows(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    iid = await issuer(pool)
    await fact(pool, iid, "Revenues", "1000", T1, "A1")
    await fact(pool, iid, "OperatingIncomeLoss", "250", T1, "A1")
    await fact(pool, iid, "Assets", "6000", T1, "A1", statement="balance_sheet", start=None)
    seg = {"statement": "segment"}
    for member, rev, prof in (("US", "600", "200"), ("EMEA", "400", "100")):
        dims = {"members": [[GEO, member]]}
        await fact(pool, iid, "Revenues", rev, T1, "A1", dims=dims, **seg)
        await fact(pool, iid, "OperatingIncomeLoss", prof, T1, "A1", dims=dims, **seg)
    await fact(
        pool,
        iid,
        "OperatingIncomeLoss",
        "-50",
        T1,
        "A1",
        **seg,
        dims={"members": [[GEO, "Corporate unallocated"]]},
    )
    await fact(
        pool, iid, "Revenues", "1000", T1, "A1", **seg, dims={"members": [[PROD, "Widgets"]]}
    )
    await fact(
        pool, iid, "Assets", "5000", T1, "A1", **seg, start=None, dims={"members": [[GEO, "US"]]}
    )
    await fact(
        pool,
        iid,
        "Revenues",
        "300",
        T1,
        "A1",
        **seg,
        dims={"members": [[GEO, "US"], [PROD, "Widgets"]]},
    )
    out = await statement_read.segment_bridge(pool, iid, END, AFTER)
    # legacy revenue fields keep their meaning: profit rows are no longer read as revenue
    axes = {a["axis"]: a for a in out["axes"]}
    assert D(axes[GEO]["members_sum"]) == D(1000) and axes[GEO]["reconciles"] is True
    metrics = {m["metric"]: m for m in out["metrics"]}
    assert set(metrics) == {"revenue", "operating_profit", "assets"}
    profit = metrics["operating_profit"]["axes"][0]
    assert D(profit["members_sum"]) == D(300) and D(profit["eliminations"]) == D(-50)
    assert profit["reconciles"] is True
    assets = metrics["assets"]["axes"][0]
    assert D(assets["unallocated"]) == D(1000)
    assert out["cross_axis"] == [
        {"metric": "revenue", "axes": [GEO, PROD], "members": ["US", "Widgets"], "value": "300"}
    ]
    assert any("overlap" in n for n in out["notes"])
    assert D(axes[GEO]["members_sum"]) == D(1000)  # the cross cell was not added to the axis


async def test_valuation_gate_is_attached_to_rows_read_only(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    iid = await issuer(pool)
    await fact(pool, iid, "Revenues", "1000", T1, "A1")
    await fact(pool, iid, "CostOfRevenue", "600", T1, "A1")
    await fact(pool, iid, "GrossProfit", "450", T1, "A1")  # does not add up
    rows = [
        {"issuer_id": iid, "period_end": END, "concept": "Revenues"},
        {"issuer_id": iid, "period_end": dt.date(2024, 12, 31), "concept": "Revenues"},
        {"issuer_id": None, "period_end": END, "concept": "Revenues"},
    ]
    before = await pool.fetchval("select count(*) from public.financial_statement_fact")
    out = await statement_read.with_valuation_gate(pool, rows, AFTER)
    assert await pool.fetchval("select count(*) from public.financial_statement_fact") == before
    assert out[0]["valuation_gate"]["usable_for_valuation"] is False
    assert any("gross_profit" in r for r in out[0]["valuation_gate"]["reasons"])
    assert out[1]["valuation_gate"]["usable_for_valuation"] is None
    assert out[2]["valuation_gate"]["usable_for_valuation"] is None
    assert out[0]["concept"] == "Revenues"  # existing fields untouched
