"""P6.2: deal filings become deals, states and terms with their passages, once. Skipped without
STEWARD_TEST_DATABASE_URL."""

import datetime as dt
from decimal import Decimal as D

import asyncpg
import pytest

from steward.disclosure import deal_job
from steward.disclosure.registry import seed_sources
from steward.disclosure.sec_discovery import DiscoveredFiling
from tests.test_disclosure_deals import PRESS
from tests.test_disclosure_estimates_db import setup

pytestmark = pytest.mark.db

CLOSED = (
    "Item 2.01 Completion of Acquisition. On October 12, 2026, Acme Corp announced that it has "
    "completed the transaction to acquire Borealis Systems, Inc for $44.50 per share in cash."
)


def html(text: str) -> bytes:
    return f"<html><body><p>{text}</p></body></html>".encode()


def filing(accession: str, form: str, items: str, day: int) -> DiscoveredFiling:
    filed = dt.date(2026, 10, day)
    known = dt.datetime(2026, 10, day, 21, tzinfo=dt.UTC)
    return DiscoveredFiling(
        cik="0000011111",
        accession=accession,
        form=form,
        filed_at=filed,
        accepted_at=known,
        report_date=filed,
        primary_document="d.htm",
        items=items,
        known_at=known,
        date_notes={},
    )


async def prepare(pool: asyncpg.Pool) -> int:
    await seed_sources(pool)
    issuer, _ = await setup(pool)
    return issuer


async def store(pool: asyncpg.Pool, issuer: int, f: DiscoveredFiling, text: str):
    return await deal_job.store_deal_filing(
        pool, f, html(text), issuer_id=issuer, ticker="ACME", now=f.known_at
    )


async def test_an_announcement_becomes_a_deal_with_state_terms_and_passages(
    backend_pool: asyncpg.Pool,
) -> None:
    issuer = await prepare(backend_pool)
    out = await store(backend_pool, issuer, filing("0000011111-26-000001", "8-K", "1.01", 5), PRESS)
    assert out.created and out.state == "definitive" and out.term_version_id is not None
    deal = await backend_pool.fetchrow("select * from public.deal_event where id=$1", out.deal_id)
    assert deal["deal_type"] == "acquisition" and deal["ticker"] == "ACME"
    roles = await backend_pool.fetch(
        "select role, name, issuer_id from public.deal_participant where deal_id=$1 order by id",
        out.deal_id,
    )
    assert [(r["role"], r["issuer_id"]) for r in roles] == [("acquirer", None), ("target", None)]
    terms = await backend_pool.fetchrow(
        "select v.price_per_share, v.price_per_share_state, e.quote"
        " from public.deal_term_version v"
        " join public.source_evidence e on e.id = v.evidence_id where v.id=$1",
        out.term_version_id,
    )
    assert terms["price_per_share"] == D("44.50") and terms["price_per_share_state"] == "stated"
    assert terms["quote"]
    stated = await backend_pool.fetchval(
        "select termination_fee_state from public.deal_term_version where id=$1",
        out.term_version_id,
    )
    assert stated == "stated"
    synergy_missing = await backend_pool.fetchval(
        "select cash_pct_state from public.deal_term_version where id=$1", out.term_version_id
    )
    assert synergy_missing == "not_stated"  # never a zero


async def test_the_same_bytes_are_read_once(backend_pool: asyncpg.Pool) -> None:
    issuer = await prepare(backend_pool)
    f = filing("0000011111-26-000001", "8-K", "1.01", 5)
    await store(backend_pool, issuer, f, PRESS)
    again = await store(backend_pool, issuer, f, PRESS)
    assert again.skipped == "unchanged"
    assert await backend_pool.fetchval("select count(*) from public.deal_event") == 1
    assert await backend_pool.fetchval("select count(*) from public.deal_state_transition") == 1


async def test_a_closing_filing_joins_the_deal_and_adds_a_state_not_a_term_version(
    backend_pool: asyncpg.Pool,
) -> None:
    issuer = await prepare(backend_pool)
    first = await store(
        backend_pool, issuer, filing("0000011111-26-000001", "8-K", "1.01", 5), PRESS
    )
    second = await store(
        backend_pool, issuer, filing("0000011111-26-000002", "8-K", "2.01", 12), CLOSED
    )
    assert second.deal_id == first.deal_id and not second.created and second.state == "closed"
    states = await backend_pool.fetch(
        "select state from public.deal_state_transition where deal_id=$1 order by id",
        first.deal_id,
    )
    assert [s["state"] for s in states] == ["definitive", "closed"]
    # the closing filing repeats the same price, so it is not a new version of the terms
    assert second.term_version_id is None
    assert await backend_pool.fetchval("select count(*) from public.deal_term_version") == 1


async def test_a_changed_price_adds_a_version_that_supersedes_the_first(
    backend_pool: asyncpg.Pool,
) -> None:
    issuer = await prepare(backend_pool)
    first = await store(
        backend_pool, issuer, filing("0000011111-26-000001", "8-K", "1.01", 5), PRESS
    )
    raised = CLOSED.replace("$44.50", "$46.00")
    second = await store(
        backend_pool, issuer, filing("0000011111-26-000002", "8-K", "8.01", 12), raised
    )
    row = await backend_pool.fetchrow(
        "select version_no, supersedes_id, price_per_share from public.deal_term_version"
        " where id=$1",
        second.term_version_id,
    )
    assert row["version_no"] == 2 and row["price_per_share"] == D("46.00")
    assert row["supersedes_id"] == await backend_pool.fetchval(
        "select id from public.deal_term_version where id=$1", first.term_version_id
    )


async def test_text_without_two_parties_or_a_type_stores_nothing(
    backend_pool: asyncpg.Pool,
) -> None:
    issuer = await prepare(backend_pool)
    quiet = await store(
        backend_pool,
        issuer,
        filing("0000011111-26-000003", "8-K", "8.01", 6),
        "Item 8.01 Other Events. The company held its annual meeting.",
    )
    assert quiet.skipped == "no_deal_type" and quiet.deal_id is None
    assert await backend_pool.fetchval("select count(*) from public.deal_event") == 0


def test_only_deal_forms_and_items_are_read() -> None:
    assert deal_job.wanted(filing("a", "8-K", "1.01,9.01", 5))
    assert not deal_job.wanted(filing("a", "8-K", "2.02,9.01", 5))  # earnings only
    assert deal_job.wanted(filing("a", "S-4", "", 5))
    assert not deal_job.wanted(filing("a", "10-K", "", 5))
