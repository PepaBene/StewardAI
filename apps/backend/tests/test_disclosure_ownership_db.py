"""P3 against a real database: the known_at default, 13F amendment replacement at three cutoffs,
options kept out of the share lane, and the lane remainder against the stated denominator."""

import datetime as dt
from decimal import Decimal as D

import asyncpg
import pytest

from steward.disclosure import ownership_read

pytestmark = pytest.mark.db

UTC = dt.UTC
PERIOD = dt.date(2025, 12, 31)
BEFORE = dt.datetime(2026, 1, 1, tzinfo=UTC)
BETWEEN = dt.datetime(2026, 3, 1, tzinfo=UTC)
AFTER = dt.datetime(2026, 6, 1, tzinfo=UTC)
CIK = "0000012345"


async def filing(
    pool: asyncpg.Pool,
    accession: str,
    filed: dt.date,
    sub: str = "13F-HR",
    amend: str | None = None,
    name: str = "Alpha Capital",
) -> None:
    await pool.execute(
        """insert into public.form13f_filing
             (accession, cik, manager_name, period, filed, submission_type, amendment_type)
           values ($1,$2,$3,$4,$5,$6,$7)""",
        accession,
        CIK,
        name,
        PERIOD,
        filed,
        sub,
        amend,
    )


async def hold(
    pool: asyncpg.Pool, accession: str, shares: str, idx: int, put_call: str | None = None
) -> None:
    await pool.execute(
        """insert into public.institutional_holding
             (accession, infotable_sk, cusip, ticker, shares, value_usd, put_call)
           values ($1,$2,'037833100','AAPL',$3,$3,$4)""",
        accession,
        idx,
        D(shares),
        put_call,
    )


async def test_known_at_defaults_to_the_end_of_the_filing_day(backend_pool: asyncpg.Pool) -> None:
    await filing(backend_pool, "0000012345-26-000001", dt.date(2026, 2, 10))
    known = await backend_pool.fetchval(
        "select known_at from public.form13f_filing where accession='0000012345-26-000001'"
    )
    assert known == dt.datetime(2026, 2, 10, 23, 59, 59, tzinfo=UTC)
    flag = await backend_pool.fetchval(
        "select confidential_treatment from public.form13f_filing limit 1"
    )
    assert flag == "unknown"  # never assumed to be "none"


async def test_restatement_and_options_across_three_cutoffs(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    await filing(pool, "0000012345-26-000001", dt.date(2026, 2, 10))
    await hold(pool, "0000012345-26-000001", "1000", 1)
    await hold(pool, "0000012345-26-000001", "700", 2, put_call="Call")
    await filing(pool, "0000012345-26-000002", dt.date(2026, 4, 20), "13F-HR/A", "RESTATEMENT")
    await hold(pool, "0000012345-26-000002", "800", 1)
    await pool.execute(
        "insert into public.shares_outstanding(ticker, shares, as_of, method, filed, raw)"
        " values('AAPL', 10000, '2025-12-31', 'manual', '2026-01-30', '{}')"
    )

    def lane(out: dict, key: str = "institutional_13f") -> dict:
        return next(x for x in out["lanes"] if x["key"] == key)

    assert lane(await ownership_read.ownership(pool, "AAPL", BEFORE))["status"] == "no_data"
    mid = lane(await ownership_read.ownership(pool, "AAPL", BETWEEN))
    assert D(mid["total_shares"]) == D(1000)  # the call option is not shares owned
    assert D(mid["percent_of_denominator"]) == D(10)
    assert D(mid["remainder_shares"]) == D(9000)
    late = lane(await ownership_read.ownership(pool, "AAPL", AFTER))
    assert D(late["total_shares"]) == D(800) and late["entries"][0]["amended"] is True
    assert late["entries"][0]["lag_days"] == 110  # report date 31 Dec to filing 20 Apr


async def test_no_denominator_before_it_was_filed_and_no_combined_total(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    await filing(pool, "0000012345-26-000001", dt.date(2026, 2, 10))
    await hold(pool, "0000012345-26-000001", "1000", 1)
    await pool.execute(
        "insert into public.shares_outstanding(ticker, shares, as_of, method, filed, raw)"
        " values('AAPL', 10000, '2025-12-31', 'manual', '2026-03-30', '{}')"
    )
    out = await ownership_read.ownership(pool, "AAPL", dt.datetime(2026, 3, 1, tzinfo=UTC))
    assert out["denominator"] is None
    assert any("No denominator" in w for w in out["warnings"])
    assert "combined_total" not in out
    assert next(x for x in out["lanes"] if x["key"] == "national_voting")["status"] == "not_covered"


async def test_a_manager_that_exited_is_not_shown_with_its_old_position(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    q3, q4 = dt.date(2025, 9, 30), dt.date(2025, 12, 31)
    for acc, period, filed in (
        ("0000012345-25-000001", q3, dt.date(2025, 11, 10)),
        ("0000012345-26-000001", q4, dt.date(2026, 2, 10)),
    ):
        await pool.execute(
            """insert into public.form13f_filing(accession, cik, manager_name, period, filed,
                 submission_type) values ($1,$2,'Alpha Capital',$3,$4,'13F-HR')""",
            acc,
            CIK,
            period,
            filed,
        )
    await hold(pool, "0000012345-25-000001", "500", 1)  # the Q4 report lists no AAPL row

    def entries(out: dict) -> list[dict]:
        return next(x for x in out["lanes"] if x["key"] == "institutional_13f")["entries"]

    nov = await ownership_read.ownership(pool, "AAPL", dt.datetime(2025, 12, 1, tzinfo=UTC))
    assert [e["holder"] for e in entries(nov)] == ["Alpha Capital"]
    after_exit = await ownership_read.ownership(pool, "AAPL", dt.datetime(2026, 3, 1, tzinfo=UTC))
    assert entries(after_exit) == []
    trend = after_exit["trend"]
    assert trend["prev_period"] == q3 and trend["cur_period"] == q4
    assert list(trend["prev"]) == ["Alpha Capital"] and trend["cur"] == {}  # the exit
