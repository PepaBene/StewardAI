"""A promoted web domain whose facts the owner keeps rejecting is demoted; owner picks are kept."""

import datetime as dt

import asyncpg
import pytest

from steward.engine import domain_learning as dl

NOW = dt.datetime.now(dt.UTC)


def test_only_repeated_rejections_outweighing_approvals_demote() -> None:
    assert not dl.should_demote(0, 2)
    assert dl.should_demote(0, 3)
    assert dl.should_demote(2, 3)
    assert not dl.should_demote(3, 3)
    assert not dl.should_demote(9, 3)


async def add_domain(pool: asyncpg.Pool, domain: str, decided_by: str, tier: str = "T2") -> None:
    await pool.execute(
        "insert into public.web_domain_tier (domain, tier, decided_by) values ($1, $2, $3)",
        domain,
        tier,
        decided_by,
    )


async def add_facts(pool: asyncpg.Pool, host: str, status: str, n: int) -> None:
    for i in range(n):
        await pool.execute(
            "insert into public.web_fact (ticker, slot, value_text, source_url, host, tier, quote,"
            " status, decided_by) values ('AAA', $1, 'v', $2, $3, 'T2', 'q', $4, 'owner:u')",
            f"slot{status}{i}{host}",
            f"https://{host}/{status}{i}",
            host,
            status,
        )


@pytest.mark.db
async def test_an_ai_placed_domain_you_keep_rejecting_is_demoted_and_yours_are_not(
    backend_pool: asyncpg.Pool,
) -> None:
    await add_domain(backend_pool, "bad-news.com", "ai:model")
    await add_domain(backend_pool, "good-news.com", "ai:model")
    await add_domain(backend_pool, "mine-news.com", "owner:u")
    await add_facts(backend_pool, "www.bad-news.com", "rejected", 3)
    await add_facts(backend_pool, "good-news.com", "rejected", 3)
    await add_facts(backend_pool, "good-news.com", "approved", 5)
    await add_facts(backend_pool, "mine-news.com", "rejected", 4)
    out = await dl.learn(backend_pool, NOW)
    assert out == {"demoted": ["bad-news.com"]}
    tiers = {
        r["domain"]: r["tier"]
        for r in await backend_pool.fetch("select domain, tier from public.web_domain_tier")
    }
    assert tiers == {
        "bad-news.com": "rejected",
        "good-news.com": "T2",
        "mine-news.com": "T2",
    }
    assert (await dl.learn(backend_pool, NOW)) == {"demoted": []}
