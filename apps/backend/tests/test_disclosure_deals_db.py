"""P6 against a real database: a deal replayed at three cutoffs shows no future knowledge, the
ledgers
are append-only, a typed unknown cannot hold a value, evidence is mandatory, and filters count what
they leave out."""

import datetime as dt
from decimal import Decimal as D

import asyncpg
import pytest

from steward.disclosure import deal_read

pytestmark = pytest.mark.db

UTC = dt.UTC


def at(month: int, day: int) -> dt.datetime:
    return dt.datetime(2026, month, day, 12, tzinfo=UTC)


async def evidence(pool: asyncpg.Pool, ext: str, known: dt.datetime) -> tuple[int, int]:
    doc = await pool.fetchval(
        """insert into public.source_document (regulator, external_id, stable_url, document_type)
           values ('sec', $1, 'https://sec.example/' || $1, '8-K') returning id""",
        ext,
    )
    ver = await pool.fetchval(
        """insert into public.source_document_version (document_id, content_hash, known_at,
        filed_at)
           values ($1, 'hash-' || $2, $3, $3) returning id""",
        doc,
        ext,
        known,
    )
    ev = await pool.fetchval(
        """insert into public.source_evidence (document_version_id, quote, span_start, span_end)
           values ($1, 'quoted passage ' || $2, 0, 10) returning id""",
        ver,
        ext,
    )
    return int(ver), int(ev)


async def deal(pool: asyncpg.Pool, key: str, kind: str, title: str, known: dt.datetime) -> int:
    return int(
        await pool.fetchval(
            """insert into public.deal_event (transaction_key, deal_type, title, ticker,
            announced_at, known_at)
               values ($1, $2, $3, 'AAA', $4, $4) returning id""",
            key,
            kind,
            title,
            known,
        )
    )


async def move(
    pool: asyncpg.Pool, deal_id: int, state: str, eff: dt.datetime, known: dt.datetime, ext: str
) -> None:
    ver, ev = await evidence(pool, ext, known)
    await pool.execute(
        """insert into public.deal_state_transition
             (deal_id, state, effective_at, known_at, document_version_id, evidence_id)
           values ($1, $2, $3, $4, $5, $6)""",
        deal_id,
        state,
        eff,
        known,
        ver,
        ev,
    )


async def terms(
    pool: asyncpg.Pool,
    deal_id: int,
    no: int,
    known: dt.datetime,
    ext: str,
    price: str | None,
    *,
    unaffected: str | None = None,
    unaffected_on: dt.date | None = None,
) -> int:
    ver, ev = await evidence(pool, ext, known)
    return int(
        await pool.fetchval(
            """insert into public.deal_term_version
                 (deal_id, version_no, known_at, document_version_id, evidence_id, currency,
                  price_per_share, price_per_share_state, unaffected_price, unaffected_price_state,
                  unaffected_date)
               values ($1, $2, $3, $4, $5, 'USD', $6, $7, $8, $9, $10) returning id""",
            deal_id,
            no,
            known,
            ver,
            ev,
            None if price is None else D(price),
            "stated" if price else "not_stated",
            None if unaffected is None else D(unaffected),
            "stated" if unaffected else "not_stated",
            unaffected_on,
        )
    )


async def seeded(pool: asyncpg.Pool) -> tuple[int, int, int]:
    d = await deal(
        pool, "txn:acme|borealis@2026-10-05", "acquisition", "Acme to buy Borealis", at(10, 5)
    )
    await move(pool, d, "announced", at(10, 5), at(10, 5), "a1")
    await move(pool, d, "definitive", at(10, 20), at(10, 20), "a2")
    await move(pool, d, "terminated", at(11, 1), at(11, 3), "a3")  # effective Nov 1, known Nov 3
    v1 = await terms(
        pool, d, 1, at(10, 5), "t1", "42.00", unaffected="33.00", unaffected_on=dt.date(2026, 10, 2)
    )
    v2 = await terms(
        pool,
        d,
        2,
        at(10, 20),
        "t2",
        "44.50",
        unaffected="33.00",
        unaffected_on=dt.date(2026, 10, 2),
    )
    return d, v1, v2


async def test_a_deal_replays_at_three_cutoffs_without_future_knowledge(
    backend_pool: asyncpg.Pool,
) -> None:
    _, v1, v2 = await seeded(backend_pool)
    before = await deal_read.holding_deals(backend_pool, "AAA", at(10, 1))
    assert before["deals"] == [] and before["total_known"] == 0 and before["notes"]

    early = (await deal_read.holding_deals(backend_pool, "AAA", at(10, 10)))["deals"][0]
    assert early["state"] == "announced" and early["latest_version_id"] == v1
    assert [t["state"] for t in early["transitions"]] == ["announced"]
    premium = next(i for i in early["impacts"] if i["kind"] == "premium")
    assert premium["term_version_ids"] == [v1] and D(premium["value"]).quantize(D("0.0001")) == D(
        "0.2727"
    )

    mid = (await deal_read.holding_deals(backend_pool, "AAA", at(11, 2)))["deals"][0]
    assert mid["state"] == "definitive"  # the termination is not yet known
    assert mid["latest_version_id"] == v2
    (change,) = [c for c in mid["term_versions"][1]["changes"] if c["field"] == "price_per_share"]
    assert (change["old"], change["new"]) == ("42.00", "44.50")

    late = (await deal_read.holding_deals(backend_pool, "AAA", at(11, 4)))["deals"][0]
    assert late["state"] == "terminated" and len(late["term_versions"]) == 2  # history is kept
    assert all(t["source_url"] and t["quote"] for t in late["transitions"])


async def test_unaffected_price_on_the_announcement_day_gives_no_premium(
    backend_pool: asyncpg.Pool,
) -> None:
    d = await deal(backend_pool, "txn:late", "acquisition", "Late unaffected", at(10, 5))
    await move(backend_pool, d, "announced", at(10, 5), at(10, 5), "l1")
    await terms(
        backend_pool,
        d,
        1,
        at(10, 5),
        "l2",
        "44.50",
        unaffected="33.00",
        unaffected_on=dt.date(2026, 10, 5),
    )
    got = (await deal_read.holding_deals(backend_pool, "AAA", at(10, 6)))["deals"][0]
    premium = next(i for i in got["impacts"] if i["kind"] == "premium")
    assert premium["value"] is None and "on or after the announcement" in premium["reason"]


async def test_a_term_that_is_not_stated_cannot_hold_a_value(backend_pool: asyncpg.Pool) -> None:
    d = await deal(backend_pool, "txn:typed", "acquisition", "Typed unknown", at(10, 5))
    ver, ev = await evidence(backend_pool, "u1", at(10, 5))
    with pytest.raises(asyncpg.CheckViolationError):
        await backend_pool.execute(
            """insert into public.deal_term_version
                 (deal_id, version_no, known_at, document_version_id, evidence_id, cash_amount)
               values ($1, 1, $2, $3, $4, 100)""",
            d,
            at(10, 5),
            ver,
            ev,
        )


async def test_state_changes_need_evidence_and_the_ledgers_are_append_only(
    backend_pool: asyncpg.Pool,
) -> None:
    d, _, _ = await seeded(backend_pool)
    ver, _ = await evidence(backend_pool, "e1", at(10, 5))
    with pytest.raises(asyncpg.NotNullViolationError):
        await backend_pool.execute(
            """insert into public.deal_state_transition
                 (deal_id, state, effective_at, known_at, document_version_id)
               values ($1, 'approved', $2, $2, $3)""",
            d,
            at(10, 6),
            ver,
        )
    with pytest.raises(asyncpg.PostgresError):
        await backend_pool.execute(
            "update public.deal_state_transition set state = 'closed' where deal_id = $1", d
        )
    with pytest.raises(asyncpg.PostgresError):
        await backend_pool.execute("delete from public.deal_term_version where deal_id = $1", d)


async def test_a_rumor_needs_its_source_in_the_ledger(backend_pool: asyncpg.Pool) -> None:
    d = await deal(backend_pool, "txn:rumor", "acquisition", "Rumored", at(9, 1))
    ver, ev = await evidence(backend_pool, "r1", at(9, 1))
    with pytest.raises(asyncpg.CheckViolationError):
        await backend_pool.execute(
            """insert into public.deal_state_transition
                 (deal_id, state, effective_at, known_at, document_version_id, evidence_id)
               values ($1, 'rumored', $2, $2, $3, $4)""",
            d,
            at(9, 1),
            ver,
            ev,
        )


async def test_filters_count_deals_whose_size_is_unknown_and_a_same_party_deal_stays_apart(
    backend_pool: asyncpg.Pool,
) -> None:
    await seeded(backend_pool)
    other = await deal(
        backend_pool,
        "txn:acme|cinder@2026-10-06",
        "divestiture",
        "Simultaneous asset sale",
        at(10, 6),
    )
    await move(backend_pool, other, "announced", at(10, 6), at(10, 6), "o1")
    both = await deal_read.holding_deals(backend_pool, "AAA", at(10, 25))
    assert {d["transaction_key"] for d in both["deals"]} == {
        "txn:acme|borealis@2026-10-05",
        "txn:acme|cinder@2026-10-06",
    }
    by_type = await deal_read.holding_deals(
        backend_pool, "AAA", at(10, 25), deal_type="divestiture"
    )
    assert [d["id"] for d in by_type["deals"]] == [other]
    sized = await deal_read.holding_deals(backend_pool, "AAA", at(10, 25), min_size=D("1"))
    assert sized["deals"] == [] and sized["excluded_unknown_size"] == 2
    cur = await deal_read.holding_deals(backend_pool, "AAA", at(10, 25), currency="EUR")
    assert cur["deals"] == []
