"""P2 against a real database: as-filed history with a restatement at three cutoffs, derived
figures with their inputs, arithmetic checks that block, and segment axes bridged separately."""

import datetime as dt
import json
from decimal import Decimal as D

import asyncpg
import pytest

from steward.disclosure import statement_read

pytestmark = pytest.mark.db

UTC = dt.UTC
T1 = dt.datetime(2026, 2, 20, 21, 0, tzinfo=UTC)
T2 = dt.datetime(2026, 5, 2, 20, 15, tzinfo=UTC)
BEFORE = dt.datetime(2026, 1, 1, tzinfo=UTC)
BETWEEN = dt.datetime(2026, 3, 15, tzinfo=UTC)
AFTER = dt.datetime(2026, 6, 1, tzinfo=UTC)
START, END = dt.date(2025, 1, 1), dt.date(2025, 12, 31)


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


async def test_restatement_is_invisible_until_known(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    iid = await issuer(pool)
    await fact(pool, iid, "Revenues", "1000", T1, "A1")
    await fact(pool, iid, "Revenues", "950", T2, "A2")

    def revenue(out: dict) -> list[dict]:
        return [h for h in out["history"] if h["metric"] == "revenue"]

    assert revenue(await statement_read.statements(pool, iid, BEFORE)) == []
    mid = revenue(await statement_read.statements(pool, iid, BETWEEN))
    assert [(D(h["value"]), h["restated"]) for h in mid] == [(D(1000), False)]
    late = revenue(await statement_read.statements(pool, iid, AFTER))
    assert len(late) == 1
    assert D(late[0]["value"]) == D(950) and late[0]["restated"] is True
    assert D(late[0]["original_value"]) == D(1000) and late[0]["accession"] == "A2"


async def test_free_cash_flow_names_its_inputs_and_blocks_on_a_bad_check(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    iid = await issuer(pool)
    await fact(
        pool,
        iid,
        "NetCashProvidedByUsedInOperatingActivities",
        "500",
        T1,
        "A1",
        statement="cash_flow",
    )
    await fact(
        pool,
        iid,
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "120",
        T1,
        "A1",
        statement="cash_flow",
    )
    # gross profit that does not equal revenue minus cost: the period is not usable for valuation
    await fact(pool, iid, "Revenues", "1000", T1, "A1")
    await fact(pool, iid, "CostOfRevenue", "600", T1, "A1")
    await fact(pool, iid, "GrossProfit", "450", T1, "A1")
    out = await statement_read.statements(pool, iid, AFTER)
    (fcf,) = out["derived"]
    assert D(fcf["value"]) == D(380) and fcf["formula"] == "operating_cash_flow - capex"
    assert fcf["inputs"] == ["A1", "A1"] and fcf["formula_version"] == "formulas-1"
    key = END.isoformat()
    assert out["usable_for_valuation"][key] is False
    assert any(c["status"] == "fail" for c in out["checks"][key])


async def test_negative_capex_is_sent_to_review_not_derived(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    iid = await issuer(pool)
    await fact(
        pool,
        iid,
        "NetCashProvidedByUsedInOperatingActivities",
        "500",
        T1,
        "A1",
        statement="cash_flow",
    )
    await fact(
        pool,
        iid,
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "-100",
        T1,
        "A1",
        statement="cash_flow",
    )
    out = await statement_read.statements(pool, iid, AFTER)
    assert out["derived"] == []
    assert any(c["status"] == "needs_review" for c in out["checks"][END.isoformat()])


async def test_segment_axes_bridge_separately_and_crosstabs_are_skipped(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    iid = await issuer(pool)
    await fact(pool, iid, "Revenues", "950", T1, "A1")
    for member, value in (("US", "600"), ("EMEA", "350")):
        await fact(
            pool,
            iid,
            "Revenues",
            value,
            T1,
            "A1",
            statement="segment",
            dims={"members": [["StatementGeographicalAxis", member]]},
        )
    for member, value in (("Widgets", "800"), ("Services", "100")):
        await fact(
            pool,
            iid,
            "Revenues",
            value,
            T1,
            "A1",
            statement="segment",
            dims={"members": [["ProductOrServiceAxis", member]]},
        )
    # a cross-tab (product within region) must not be placed on one axis
    await fact(
        pool,
        iid,
        "Revenues",
        "300",
        T1,
        "A1",
        statement="segment",
        dims={
            "members": [["StatementGeographicalAxis", "US"], ["ProductOrServiceAxis", "Widgets"]]
        },
    )
    out = await statement_read.segment_bridge(pool, iid, END, AFTER)
    axes = {a["axis"]: a for a in out["axes"]}
    assert axes["StatementGeographicalAxis"]["reconciles"] is True
    assert axes["ProductOrServiceAxis"]["reconciles"] is False
    assert D(axes["ProductOrServiceAxis"]["unallocated"]) == D(50)
    assert any("not added together" in n for n in out["notes"])
    unknown = await statement_read.segment_bridge(pool, iid, END, BEFORE)
    assert unknown["total"] is None and unknown["axes"] == []
