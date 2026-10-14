"""Second-look audits for prices, fund holdings and digests: pure rules, then the notices."""

import datetime as dt
import json

import asyncpg

from steward.engine import data_audit as audit
from tests.conftest import MigratedDatabase

NOW = dt.datetime.now(dt.UTC)
DAY = NOW.date() - dt.timedelta(days=1)


def test_two_sources_that_agree_are_fine_and_ones_that_differ_are_flagged() -> None:
    assert audit.price_disagreement("AAA", DAY, {"stooq": 100.0, "eodhd": 100.5}) is None
    assert audit.price_disagreement("AAA", DAY, {"stooq": 100.0}) is None
    found = audit.price_disagreement("AAA", DAY, {"stooq": 100.0, "eodhd": 110.0})
    assert found is not None
    assert "differ" in found.detail


def test_only_a_very_large_one_day_move_is_flagged() -> None:
    assert audit.price_jump("AAA", DAY, 100.0, 110.0) is None
    assert audit.price_jump("AAA", DAY, 100.0, 45.0) is not None
    assert audit.price_jump("AAA", DAY, 100.0, 150.0) is not None


def test_holdings_must_add_up_to_the_fund() -> None:
    assert audit.weights_problem("F", 0.99, 0.08) is None
    assert audit.weights_problem("F", 0.5, 0.08) is not None
    assert audit.weights_problem("F", 1.3, 0.08) is not None
    assert audit.weights_problem("F", 1.0, 0.7) is not None


def test_a_digest_is_flagged_only_when_enough_sentences_are_unbacked() -> None:
    assert audit.digest_problem(1, "Daily", 9, 1, 0) is None
    assert audit.digest_problem(1, "Daily", 2, 1, 0) is None  # too few to judge
    assert audit.digest_problem(1, "Daily", 5, 3, 0) is not None


async def open_notices(pool: asyncpg.Pool) -> int:
    return int(
        await pool.fetchval(
            "select count(*) from public.ops_task where kind = $1 and status <> 'resolved'",
            audit.KIND,
        )
    )


async def test_the_run_opens_one_notice_per_finding_and_closes_it_when_fixed(
    migrated_db: MigratedDatabase, backend_pool: asyncpg.Pool
) -> None:
    admin = await asyncpg.connect(migrated_db.admin_url)
    try:
        for source, close in (("stooq", 100.0), ("eodhd", 120.0)):
            await admin.execute(
                "insert into public.price_bar (symbol, date, close, source) values"
                " ('AAA', $1, $2, $3)",
                DAY,
                close,
                source,
            )
        claims = {"mode": "mark", "supported": 2, "unsupported": 6, "unchecked": 0}
        await admin.execute(
            "insert into public.report (type, period, title, body, body_md) values ('digest',"
            " 'daily', 'Daily digest', $1::jsonb, 'x')",
            json.dumps({"claims": claims}),
        )
        counts = await audit.run(backend_pool, NOW)
        assert counts["prices"] == 1
        assert counts["digests"] == 1
        assert await open_notices(backend_pool) == 2

        await admin.execute("delete from public.price_bar where source = 'eodhd'")
        await admin.execute("delete from public.report")
    finally:
        await admin.close()
    await audit.run(backend_pool, NOW)
    assert await open_notices(backend_pool) == 0
