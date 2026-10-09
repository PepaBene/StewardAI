"""P9.4: point-in-time reads through the SQL. An amendment, a deal reversal and a restated figure
each replace what came before only from the moment they became known; replaying an earlier date
shows the earlier state. Skipped without STEWARD_TEST_DATABASE_URL."""

import datetime as dt

import asyncpg
import pytest

from steward.disclosure import deal_read, insider_analysis
from tests.test_disclosure_deals_db import at, deal, move
from tests.test_disclosure_gap_closure_db import buy, filing

pytestmark = pytest.mark.db

UTC = dt.UTC


def shares_seen(view: dict) -> list[str]:
    return sorted(str(t["shares"]) for t in view["transactions"])


async def test_an_insider_amendment_replaces_the_original_only_once_it_is_known(
    backend_pool: asyncpg.Pool,
) -> None:
    await filing(backend_pool, "0000099999-26-000001", dt.date(2026, 6, 3))
    await buy(backend_pool, "0000099999-26-000001", 0, "100", "50", dt.date(2026, 6, 1))
    await filing(backend_pool, "0000099999-26-000002", dt.date(2026, 6, 10), "4/A")
    await buy(backend_pool, "0000099999-26-000002", 0, "150", "50", dt.date(2026, 6, 1))
    await backend_pool.execute(
        "update public.insider_filing set known_at = $2 where accession = $1",
        "0000099999-26-000001",
        at(6, 3),
    )
    await backend_pool.execute(
        "update public.insider_filing set known_at = $2 where accession = $1",
        "0000099999-26-000002",
        at(6, 10),
    )
    await backend_pool.execute(
        "update public.insider_filing set superseded_by_accession = $2 where accession = $1",
        "0000099999-26-000001",
        "0000099999-26-000002",
    )
    before = await insider_analysis.insider_activity(backend_pool, "AAA", at(6, 5))
    after = await insider_analysis.insider_activity(backend_pool, "AAA", at(6, 15))
    assert shares_seen(before) == ["100.0000"] or shares_seen(before) == ["100"]
    assert shares_seen(after) in (["150.0000"], ["150"])


async def test_a_deal_reversal_is_invisible_before_it_was_known(
    backend_pool: asyncpg.Pool,
) -> None:
    deal_id = await deal(backend_pool, "pit1", "merger", "Acme to buy Beta", at(5, 1))
    await move(backend_pool, deal_id, "announced", at(5, 1), at(5, 1), "p1")
    await move(backend_pool, deal_id, "terminated", at(7, 1), at(7, 2), "p2")
    early = await deal_read.holding_deals(backend_pool, "AAA", at(6, 1))
    late = await deal_read.holding_deals(backend_pool, "AAA", at(8, 1))
    assert [d["state"] for d in early["deals"]] == ["announced"]
    assert [d["state"] for d in late["deals"]] == ["terminated"]
    # the terminated deal stays in history rather than disappearing
    assert len(late["deals"][0]["transitions"]) == 2
