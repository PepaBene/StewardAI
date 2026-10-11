"""Database checks for the gap closure: provenance backfill, alert latency and volume, and the
marks exported as benchmark candidates."""

import datetime as dt

import asyncpg
import pytest

from steward.config.event_rules import EventRulesConfig, with_default_services
from steward.config.news_sources import NewsSource, NewsSourcesConfig
from steward.news.marks_export import marks_candidates
from steward.news.ops import ops_report, percentile
from steward.news.provenance import backfill_provenance

NOW = dt.datetime.now(dt.UTC)


async def source(pool: asyncpg.Pool, source_id: str = "wire-a") -> None:
    await pool.execute(
        "insert into public.source (id, name, tier, type) values ($1, $1, 2, 'rss')"
        " on conflict do nothing",
        source_id,
    )


async def item(
    pool: asyncpg.Pool,
    url: str,
    *,
    cluster_id: int | None = None,
    links: str = "[]",
    published: dt.datetime | None = None,
    kind: str | None = None,
    tier: int = 2,
    headline: str = "Headline",
) -> int:
    await source(pool)
    row = await pool.fetchrow(
        "insert into public.news_item (url, source_id, outlet, tier, published_at, headline,"
        " cluster_id, links, evidence_kind) values ($1, 'wire-a', 'example.com', $2, $3, $4, $5,"
        " $6::jsonb, $7) returning id",
        url,
        tier,
        published or NOW,
        headline,
        cluster_id,
        links,
        kind,
    )
    assert row is not None
    return int(row["id"])


async def cluster(pool: asyncpg.Pool, key: str) -> int:
    row = await pool.fetchrow(
        "insert into public.story_cluster (ticker, headline, headline_key, best_tier, severity,"
        " status, first_seen, last_seen) values ('AAA_US_EQ', $1, $1, 2, 2, 'confirmed', $2, $2)"
        " returning id",
        key,
        NOW,
    )
    assert row is not None
    return int(row["id"])


def test_percentile_is_nearest_rank() -> None:
    assert percentile([], 50) is None
    assert percentile([5.0, 1.0, 3.0], 50) == 3.0
    assert percentile(list(map(float, range(1, 101))), 95) == 95.0


def test_saved_rules_gain_the_default_rules_of_services_they_lack() -> None:
    saved = EventRulesConfig.model_validate(
        {
            "announcement_types": [
                {"service": "rns", "pattern": "results", "event_type": "earnings_release"},
                {"service": "any", "pattern": ".", "event_type": "classify"},
            ]
        }
    )
    merged = with_default_services(saved)
    services = [r.service for r in merged.announcement_types]
    assert {"tdnet", "hkex", "dart"} <= set(services)
    assert services[0] == "rns" and services[-1] == "any"
    assert [r for r in merged.announcement_types if r.service == "rns"] == [
        saved.announcement_types[0]
    ]
    assert with_default_services(merged) == merged


@pytest.mark.db
async def test_backfill_classifies_only_items_without_a_kind(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    old = await item(pool, "https://example.com/old", headline="Correction: figures revised")
    done = await item(pool, "https://example.com/done", kind="regulator")
    unknown = await item(pool, "https://example.com/t1", tier=1)
    sources = {
        "wire-a": NewsSource(
            id="wire-a", name="Wire A", tier=2, type="rss", url="https://example.com/feed"
        )
    }

    updated = await backfill_provenance(pool, sources, NewsSourcesConfig(), set())

    assert updated == 2  # the item with a kind already is not touched
    row = await pool.fetchrow("select evidence_kind, is_correction from news_item where id=$1", old)
    assert row is not None and row["evidence_kind"] == "outlet" and row["is_correction"]
    kept = await pool.fetchval("select evidence_kind from news_item where id = $1", done)
    assert kept == "regulator"
    assert await pool.fetchval("select evidence_kind from news_item where id=$1", unknown)
    assert await backfill_provenance(pool, sources, NewsSourcesConfig(), set()) == 0


@pytest.mark.db
async def test_backfill_leaves_an_unknown_primary_source_alone(backend_pool: asyncpg.Pool) -> None:
    unknown = await item(backend_pool, "https://example.com/gone", tier=1)
    assert await backfill_provenance(backend_pool, {}, NewsSourcesConfig(), set()) == 0
    kind = await backend_pool.fetchval("select evidence_kind from news_item where id=$1", unknown)
    assert kind is None


@pytest.mark.db
async def test_ops_reports_alert_latency_and_indirect_volume(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    direct = await cluster(pool, "direct story")
    await item(
        pool,
        "https://example.com/d",
        cluster_id=direct,
        links='[{"ticker": "AAA_US_EQ", "via": "name"}]',
        published=NOW - dt.timedelta(minutes=30),
    )
    indirect = await cluster(pool, "indirect story")
    await item(
        pool,
        "https://example.com/i",
        cluster_id=indirect,
        links='[{"ticker": "AAA_US_EQ", "via": "supplier"}]',
        published=NOW - dt.timedelta(minutes=90),
    )
    for cluster_id in (direct, indirect):
        await pool.execute(
            "insert into public.alert (kind, severity, ticker, cluster_id, channel, message,"
            " status, sent_at) values ('news', 2, 'AAA_US_EQ', $1, 'email', 'm', 'sent', $2)",
            cluster_id,
            NOW,
        )

    report = await ops_report(pool, days=10)

    assert report.volume.total == 2
    assert report.volume.indirect == 1 and report.volume.direct == 1
    assert report.volume.per_day == pytest.approx(0.2)
    assert report.latency.alerts == 2
    assert report.latency.p50_minutes == pytest.approx(30, abs=1)
    assert report.latency.p95_minutes == pytest.approx(90, abs=1)


@pytest.mark.db
async def test_marks_become_unlabelled_benchmark_candidates(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    story = await cluster(pool, "wrongly linked story")
    await item(pool, "https://example.com/m", cluster_id=story, headline="Wrongly linked story")
    other = await cluster(pool, "fine story")
    await item(pool, "https://example.com/f", cluster_id=other)
    for target_id, kind in ((story, "wrong_entity"), (other, "useful")):
        await pool.execute(
            "insert into public.news_feedback (target, target_id, kind, note)"
            " values ('story', $1, $2, 'n')",
            str(target_id),
            kind,
        )

    cases = await marks_candidates(pool)

    assert [c.id for c in cases] == [f"story-{story}"]
    assert cases[0].mark == "wrong_entity" and cases[0].expected is None
    assert cases[0].candidates == ["AAA_US_EQ"]
