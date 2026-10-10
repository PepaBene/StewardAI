"""A deal party is matched to an issuer only on the owner's confirmation."""

import datetime as dt

import asyncpg
import pytest

from steward.disclosure import deal_parties
from tests.test_disclosure_deals_db import deal

pytestmark = pytest.mark.db

NOW = dt.datetime(2026, 10, 5, 12, tzinfo=dt.UTC)


async def party(pool: asyncpg.Pool, deal_id: int, role: str, name: str) -> int:
    return int(
        await pool.fetchval(
            """insert into public.deal_participant (deal_id, role, name, known_at)
               values ($1,$2,$3,$4) returning id""",
            deal_id,
            role,
            name,
            NOW,
        )
    )


async def test_a_name_match_is_only_a_suggestion_until_confirmed(
    backend_pool: asyncpg.Pool,
) -> None:
    acme = await backend_pool.fetchval(
        "insert into public.issuer (legal_name) values ('Acme Corporation') returning id"
    )
    await backend_pool.execute("insert into public.issuer (legal_name) values ('Acme Foods Inc.')")
    d = await deal(backend_pool, "txn:acme", "acquisition", "Acme buys Borealis", NOW)
    a = await party(backend_pool, d, "acquirer", "ACME Corp.")
    b = await party(backend_pool, d, "target", "Borealis Holdings")

    found = {s.participant_id: s for s in await deal_parties.pending(backend_pool)}
    assert found[a].status == "candidate" and found[a].issuer_ids == (acme,)
    assert found[b].status == "none" and found[b].issuer_ids == ()
    assert (
        await backend_pool.fetchval(
            "select issuer_id from public.deal_participant where id = $1", a
        )
        is None
    )  # nothing was written by looking

    assert await deal_parties.confirm(backend_pool, a, acme, "owner:1")
    assert not await deal_parties.confirm(backend_pool, a, acme, "owner:1")  # already matched
    assert a not in {s.participant_id for s in await deal_parties.pending(backend_pool)}


async def test_a_rejected_issuer_is_not_offered_again(backend_pool: asyncpg.Pool) -> None:
    acme = await backend_pool.fetchval(
        "insert into public.issuer (legal_name) values ('Acme Corporation') returning id"
    )
    d = await deal(backend_pool, "txn:acme2", "acquisition", "Acme buys X", NOW)
    a = await party(backend_pool, d, "acquirer", "Acme Corp")
    await deal_parties.reject(backend_pool, a, acme, "owner:1")
    again = {s.participant_id: s for s in await deal_parties.pending(backend_pool)}
    assert again[a].status == "none"
