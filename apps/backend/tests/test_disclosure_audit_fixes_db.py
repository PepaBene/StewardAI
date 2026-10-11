"""Audit fixes against a real database: the new identities, the label author, backfill attempts,
the 8-K item index, source cursors, append-only party decisions and the deep purge."""

import datetime as dt
from decimal import Decimal

import asyncpg
import pytest

from steward.disclosure import adjudication, estimate_read
from steward.disclosure.ingest import (
    DocumentRecord,
    attempts_done_sql,
    ingest_document_version,
    record_attempt,
    record_observation,
)
from steward.disclosure.ingest_job import index_filing_items
from steward.disclosure.registry import load_cursor, save_cursor, seed_sources
from steward.disclosure.sec_discovery import DiscoveredFiling

pytestmark = pytest.mark.db

UTC = dt.UTC
KNOWN = dt.datetime(2026, 5, 1, 21, tzinfo=UTC)


async def _issuer(pool: asyncpg.Pool) -> int:
    return int(
        await pool.fetchval(
            "insert into public.issuer(legal_name, cik, country) values('Acme','0000001234','US')"
            " returning id"
        )
    )


async def _version(
    pool: asyncpg.Pool, issuer_id: int, accession: str = "0000001234-26-000001"
) -> int:
    out = await ingest_document_version(
        pool,
        DocumentRecord(
            regulator="sec",
            external_id=accession,
            stable_url=f"https://www.sec.gov/{accession}.htm",
            form="10-Q",
            known_at=KNOWN,
            issuer_id=issuer_id,
        ),
        accession.encode(),
    )
    return out.version_id


async def test_three_and_nine_month_values_are_two_observations(backend_pool: asyncpg.Pool) -> None:
    acme = await _issuer(backend_pool)
    version = await _version(backend_pool, acme)
    evidence = await backend_pool.fetchval(
        "insert into public.source_evidence (document_version_id, quote) values ($1,'x')"
        " returning id",
        version,
    )
    common = {
        "metric": "revenue",
        "issuer_id": acme,
        "version_id": version,
        "evidence_id": evidence,
        "known_at": KNOWN,
        "period_end": dt.date(2026, 3, 31),
        "unit": "amount",
        "parser_version": "t",
        "currency": "USD",
    }
    q = await record_observation(
        backend_pool, value=Decimal(10), period_start=dt.date(2026, 1, 1), **common
    )
    ytd = await record_observation(
        backend_pool, value=Decimal(30), period_start=dt.date(2025, 7, 1), **common
    )
    again = await record_observation(
        backend_pool, value=Decimal(10), period_start=dt.date(2026, 1, 1), **common
    )
    assert q is not None and ytd is not None and q != ytd and again is None


async def test_model_labels_are_recorded_as_such_and_never_measured(
    backend_pool: asyncpg.Pool,
) -> None:
    for i in range(55):
        await adjudication.record(
            backend_pool,
            dataset="insider_class",
            sample_key="s",
            item_ref=f"i{i}",
            machine_value="open_market_buy",
            human_value="open_market_buy",
            by="ai-model (not a human)",
            kind="model",
        )
    gate = await adjudication.measure(backend_pool, "insider_class", "s")
    assert gate.labelled == 0 and gate.met is False
    with pytest.raises(ValueError):
        await adjudication.record(
            backend_pool,
            dataset="insider_class",
            sample_key="s",
            item_ref="x",
            machine_value="open_market_buy",
            human_value="open_market_buy",
            by="owner",
            kind="robot",
        )


async def test_a_backfill_step_gives_up_after_an_empty_read_or_three_failures(
    backend_pool: asyncpg.Pool,
) -> None:
    acme = await _issuer(backend_pool)
    empty, failing = (
        await _version(backend_pool, acme, "a-1"),
        await _version(backend_pool, acme, "a-2"),
    )

    async def done(version: int) -> bool:
        return bool(
            await backend_pool.fetchval(
                f"select {attempts_done_sql('$1::bigint', 'sec_backfill', '$2')}", version, "p1"
            )
        )

    await record_attempt(backend_pool, empty, "sec_backfill", "p1", "empty")
    assert await done(empty)
    for n in range(3):
        assert not await done(failing), n
        await record_attempt(backend_pool, failing, "sec_backfill", "p1", "failed", "Boom")
    assert await done(failing)
    # a new parser version tries again
    assert not bool(
        await backend_pool.fetchval(
            f"select {attempts_done_sql('$1::bigint', 'sec_backfill', '$2')}", failing, "p2"
        )
    )


async def test_an_item_2_02_8k_dates_the_release(backend_pool: asyncpg.Pool) -> None:
    acme = await _issuer(backend_pool)
    release = dt.datetime(2026, 4, 28, 20, tzinfo=UTC)
    filings = [
        DiscoveredFiling(
            "0000001234",
            "e-1",
            "8-K",
            release.date(),
            release,
            None,
            "a.htm",
            "2.02,9.01",
            release,
            {},
        ),
        DiscoveredFiling(
            "0000001234", "q-1", "10-Q", dt.date(2026, 5, 1), None, None, "q.htm", "", KNOWN, {}
        ),
    ]
    assert await index_filing_items(backend_pool, acme, filings) == 1
    assert await index_filing_items(backend_pool, acme, filings) == 0
    known = await estimate_read.release_filings(backend_pool, acme, KNOWN)
    assert estimate_read.release_for(dt.date(2026, 3, 31), known) == (release, "earnings_release")
    before = await estimate_read.release_filings(backend_pool, acme, release - dt.timedelta(days=1))
    assert before == []  # not known yet at that moment


async def test_cursor_entries_are_replaced_one_at_a_time(backend_pool: asyncpg.Pool) -> None:
    await seed_sources(backend_pool)
    await save_cursor(backend_pool, "sec.submissions", "a", {"etag": '"1"'})
    await save_cursor(backend_pool, "sec.submissions", "b", {"etag": '"2"'})
    await save_cursor(backend_pool, "sec.submissions", "a", {"etag": '"3"'})
    assert await load_cursor(backend_pool, "sec.submissions", "a") == {"etag": '"3"'}
    assert await load_cursor(backend_pool, "sec.submissions", "b") == {"etag": '"2"'}
    assert await load_cursor(backend_pool, "sec.submissions", "missing") == {}


async def test_party_decisions_are_append_only(backend_pool: asyncpg.Pool) -> None:
    acme = await _issuer(backend_pool)
    deal = await backend_pool.fetchval(
        "insert into public.deal_event (transaction_key, ticker, title, deal_type, known_at)"
        " values ('k1','ACME','A buys B','acquisition',$1) returning id",
        KNOWN,
    )
    part = await backend_pool.fetchval(
        "insert into public.deal_participant (deal_id, role, name, known_at)"
        " values ($1,'target','B Corp',$2) returning id",
        deal,
        KNOWN,
    )
    for decision in ("confirmed", "rejected", "confirmed"):
        await backend_pool.execute(
            "insert into public.deal_participant_decision"
            " (participant_id, issuer_id, decision, decided_by) values ($1,$2,$3,'owner')",
            part,
            acme,
            decision,
        )
    assert (
        await backend_pool.fetchval(
            "select count(*) from public.deal_participant_decision where participant_id=$1", part
        )
        == 3
    )
    with pytest.raises(asyncpg.PostgresError):
        await backend_pool.execute("delete from public.deal_participant_decision")
