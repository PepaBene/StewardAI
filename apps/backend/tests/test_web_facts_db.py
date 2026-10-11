"""Gap research against a real database (needs STEWARD_TEST_DATABASE_URL, else skipped)."""

import datetime as dt

import asyncpg

from steward.research.gaps import SLOTS, find_gaps, is_filled
from steward.research.web_facts import Candidate, Checked, store_fact, supersede

TODAY = dt.date(2026, 10, 6)


async def seed_instrument(pool: asyncpg.Pool, report_date: dt.date | None) -> None:
    await pool.execute(
        "insert into public.instrument (t212_ticker, name, isin, currency, next_report_date)"
        " values ('AAA_US_EQ', 'Aaa Inc', 'US0000000001', 'USD', $1)"
        " on conflict (t212_ticker) do update set next_report_date = $1",
        report_date,
    )


async def new_job(pool: asyncpg.Pool) -> int:
    job_id = await pool.fetchval(
        "insert into public.job (job_type, trigger, status)"
        " values ('gap_research', 'manual', 'running') returning id"
    )
    assert isinstance(job_id, int)
    return job_id


def candidate(url: str = "https://investors.aaa.example/calendar") -> Candidate:
    return Candidate(value="2027-02-18", url=url, quote="Results on 18 February 2027", reason="r")


async def test_owner_decision_is_never_overwritten(backend_pool: asyncpg.Pool) -> None:
    await seed_instrument(backend_pool, None)
    job_id = await new_job(backend_pool)
    slot = SLOTS["next_report_date"]
    checked = Checked("2027-02-18", None, dt.date(2027, 2, 18))
    first = await store_fact(
        backend_pool,
        "AAA_US_EQ",
        slot,
        candidate(),
        checked,
        "T1",
        "investors.aaa.example",
        "approved",
        None,
        "ai",
        "gpt-6.1-sol",
        job_id,
    )
    assert first is not None
    await backend_pool.execute(
        "update public.web_fact set status = 'rejected', decided_by = 'owner:me' where id = $1",
        first,
    )
    # the same fact found again by a later run: nothing is inserted or changed
    again = await store_fact(
        backend_pool,
        "AAA_US_EQ",
        slot,
        candidate(),
        checked,
        "T1",
        "investors.aaa.example",
        "approved",
        None,
        "ai",
        "gpt-6.1-sol",
        job_id,
    )
    assert again is None
    row = await backend_pool.fetchrow(
        "select status, decided_by from public.web_fact where id = $1", first
    )
    assert row is not None and (row["status"], row["decided_by"]) == ("rejected", "owner:me")


async def test_gaps_and_supersede(backend_pool: asyncpg.Pool) -> None:
    await seed_instrument(backend_pool, None)
    gaps, unavailable = await find_gaps(
        backend_pool, ["AAA_US_EQ"], ["next_report_date", "analyst_estimates"], TODAY
    )
    assert gaps == [("AAA_US_EQ", "next_report_date")]
    assert unavailable and unavailable[0][1] == "analyst_estimates"

    job_id = await new_job(backend_pool)
    await store_fact(
        backend_pool,
        "AAA_US_EQ",
        SLOTS["next_report_date"],
        candidate(),
        Checked("2027-02-18", None, dt.date(2027, 2, 18)),
        "T1",
        "investors.aaa.example",
        "approved",
        None,
        "ai",
        "gpt-6.1-sol",
        job_id,
    )
    # an open fact: the slot is no longer a gap to search
    assert await find_gaps(backend_pool, ["AAA_US_EQ"], ["next_report_date"], TODAY) == ([], [])
    # a structured source now fills it: the web fact is superseded
    await seed_instrument(backend_pool, dt.date(2027, 2, 19))
    assert await is_filled(backend_pool, "AAA_US_EQ", "next_report_date", TODAY)
    assert await supersede(backend_pool, TODAY) == 1
    status = await backend_pool.fetchval("select status from public.web_fact")
    assert status == "superseded"
