"""The engine's own changes to a rule, a source, a schedule or a model go through the pipeline:
a proposal with fixed gates, numbers from a replay, a config version with no author, an undo."""

import datetime as dt
from typing import Any, ClassVar

import asyncpg
import pytest

from steward.config.budget import BudgetConfig, ModelPrice
from steward.config.event_rules import EventRulesConfig
from steward.config.news_sources import NewsSource, NewsSourcesConfig
from steward.config.store import load_active, save_config
from steward.engine import change, config_change, tuners
from steward.engine.change import Gates, Metrics
from tests.conftest import MigratedDatabase

NOW = dt.datetime(2026, 10, 14, 12, 0, tzinfo=dt.UTC)


def test_the_cadence_reads_back_the_cron_the_engine_writes() -> None:
    assert tuners.cadence_of("0 6-22/2 * * *") == 2
    assert tuners.cadence_of(tuners.cron_for(3)) == 3
    assert tuners.cadence_of("0 6-22 * * *") == 1
    assert tuners.cadence_of("*/15 * * * *") is None  # not a form the engine tunes
    assert tuners.cadence_of("0 6-22/7 * * *") is None


def test_a_story_waits_until_the_next_run_of_the_cadence() -> None:
    # 07:30 Prague (05:30 UTC, summer time): the next run at a 2 h cadence is 08:00.
    published = dt.datetime(2026, 7, 1, 5, 30, tzinfo=dt.UTC)
    assert tuners.wait_hours(published, 2) == pytest.approx(0.5)
    assert tuners.wait_hours(published, 4) == pytest.approx(2.5)  # 06, 10, 14, ...
    # After the last run of the day it waits for the first of the next.
    night = dt.datetime(2026, 7, 1, 21, 30, tzinfo=dt.UTC)  # 23:30 Prague
    assert tuners.wait_hours(night, 2) == pytest.approx(6.5)


def test_late_items_counts_those_waiting_over_the_limit() -> None:
    nights = [dt.datetime(2026, 7, 1, 21, 30, tzinfo=dt.UTC)] * 3
    days = [dt.datetime(2026, 7, 1, 8, 0, tzinfo=dt.UTC)] * 7  # 10:00 Prague: on a 2 h slot
    assert tuners.late_items(nights + days, 2) == 3
    assert tuners.late_items(days, 4) == 0


def test_the_cheapest_step_down_that_saves_real_money_is_chosen() -> None:
    table = BudgetConfig(
        prices=[
            ModelPrice(model="gpt-big", input_per_mtok=10, output_per_mtok=40),
            ModelPrice(model="gpt-mid", input_per_mtok=5, output_per_mtok=20),
            ModelPrice(model="gpt-small", input_per_mtok=1, output_per_mtok=4),
            ModelPrice(model="gpt-tiny", input_per_mtok=0.2, output_per_mtok=0.8),
        ]
    )
    usable = {"gpt-mid", "gpt-small", "gpt-tiny"}
    assert tuners.cheaper_than(table, "gpt-big", usable) == "gpt-mid"  # smallest step that saves
    assert tuners.cheaper_than(table, "gpt-big", {"gpt-small", "gpt-tiny"}) == "gpt-small"
    assert tuners.cheaper_than(table, "gpt-mid", {"gpt-big", "gpt-mid"}) is None  # not cheaper
    assert tuners.cheaper_than(table, "gpt-tiny", usable) is None
    assert tuners.cheaper_than(table, "gpt-big", {"gpt-big"}) is None
    assert tuners.cheaper_than(table, "unpriced", usable) is None


def test_only_a_source_none_of_whose_items_were_used_is_a_candidate() -> None:
    assert tuners.unused(tuners.SourceUse("a", 60, 0, 10))
    assert not tuners.unused(tuners.SourceUse("a", 60, 1, 10))
    assert not tuners.unused(tuners.SourceUse("a", 10, 0, 10))  # too few to say


def test_the_engine_may_only_edit_the_fields_it_is_allowed() -> None:
    ok = config_change.Edit("jobs", "digest.model")
    config_change.check_allowed("model", ok)
    with pytest.raises(config_change.NotAllowed):
        config_change.check_allowed("schedule", ok)  # a model field is not a schedule
    with pytest.raises(config_change.NotAllowed):
        config_change.check_allowed("model", config_change.Edit("jobs", "digest.use_batch"))
    with pytest.raises(config_change.NotAllowed):
        config_change.check_allowed("rule", config_change.Edit("budget", "severities.x"))
    with pytest.raises(config_change.NotAllowed):
        config_change.parse_target("autopilot:listing:prompt")
    assert config_change.parse_target(ok.target) == ok


def test_a_path_reads_and_writes_through_a_list_item_by_key() -> None:
    data: dict[str, Any] = {"sources": [{"id": "a", "enabled": True}, {"id": "b", "enabled": True}]}
    config_change.write(data, "sources[id=b].enabled", False)
    assert data["sources"][1]["enabled"] is False and data["sources"][0]["enabled"] is True
    assert config_change.read(data, "sources[id=b].enabled") is False
    with pytest.raises(config_change.Stale):
        config_change.read(data, "sources[id=zzz].enabled")


async def seed_rules(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        saved = await save_config(conn, "event_rules", EventRulesConfig(), created_by=None)
    return saved.id


@pytest.mark.db
async def test_a_promoted_rule_change_is_a_config_version_and_its_undo_restores_the_value(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed_rules(backend_pool)
    edit = config_change.Edit("event_rules", "severities.m_and_a")
    before = await config_change.current(backend_pool, edit)
    gates = Gates(min_n=10, min_reviewed=10, require_golden=False)
    c = await config_change.propose(
        backend_pool, "rule", edit, before - 1, "lower it", "alerts read", gates
    )
    await change.record_results(
        backend_pool, c.id, Metrics(20, 20, 18, 0, 0, 0.0), Metrics(20, 20, 6, 0, 0, 0.0)
    )
    settled = await config_change.promote(backend_pool, c, golden_passed=None)
    assert settled.status == "promoted"
    assert await config_change.current(backend_pool, edit) == before - 1
    assert (
        await backend_pool.fetchval(
            "select created_by from public.config_version where domain = 'event_rules' and active"
        )
        is None
    )
    assert await config_change.undo(backend_pool, settled, ["owner"])
    assert await config_change.current(backend_pool, edit) == before
    assert await backend_pool.fetchval("select status from public.engine_change") == "rolled_back"


@pytest.mark.db
async def test_a_field_whose_changes_were_rolled_back_twice_is_left_to_the_owner(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed_rules(backend_pool)
    edit = config_change.Edit("event_rules", "severities.m_and_a")
    gates = Gates(min_n=1, min_reviewed=1, require_golden=False)
    assert not await config_change.blocked(backend_pool, edit.target, NOW)
    for _ in range(2):
        before = await config_change.current(backend_pool, edit)
        c = await config_change.propose(backend_pool, "rule", edit, before - 1, "h", "m", gates)
        await change.record_results(
            backend_pool, c.id, Metrics(5, 5, 4, 0, 0, 0.0), Metrics(5, 5, 1, 0, 0, 0.0)
        )
        settled = await config_change.promote(backend_pool, c, golden_passed=None)
        assert await config_change.undo(backend_pool, settled, ["worse"])
        if _ == 0:
            assert not await config_change.blocked(
                backend_pool, edit.target, NOW + dt.timedelta(days=1)
            )
    assert await config_change.blocked(backend_pool, edit.target, dt.datetime.now(dt.UTC))


@pytest.mark.db
async def test_an_undo_leaves_a_value_the_owner_changed_since(backend_pool: asyncpg.Pool) -> None:
    await seed_rules(backend_pool)
    edit = config_change.Edit("event_rules", "severities.m_and_a")
    before = await config_change.current(backend_pool, edit)
    gates = Gates(min_n=1, min_reviewed=1, require_golden=False)
    c = await config_change.propose(backend_pool, "rule", edit, before - 1, "h", "m", gates)
    await change.record_results(
        backend_pool, c.id, Metrics(5, 5, 4, 0, 0, 0.0), Metrics(5, 5, 1, 0, 0, 0.0)
    )
    settled = await config_change.promote(backend_pool, c, golden_passed=None)
    stored = await load_active(backend_pool, "event_rules", EventRulesConfig)
    assert stored is not None
    owner_edit = stored.body.model_copy(
        update={"severities": {**stored.body.severities, "m_and_a": before - 2}}
    )
    async with backend_pool.acquire() as conn:
        await save_config(conn, "event_rules", owner_edit, created_by=None, note="owner")
    assert await config_change.revert(backend_pool, settled) is False
    assert await config_change.current(backend_pool, edit) == before - 2


@pytest.mark.db
async def test_a_change_that_fails_its_gates_is_not_written(backend_pool: asyncpg.Pool) -> None:
    await seed_rules(backend_pool)
    edit = config_change.Edit("event_rules", "severities.m_and_a")
    before = await config_change.current(backend_pool, edit)
    gates = Gates(min_n=10, min_reviewed=10, require_golden=False)
    c = await config_change.propose(backend_pool, "rule", edit, before - 1, "h", "m", gates)
    await change.record_results(
        backend_pool, c.id, Metrics(20, 20, 2, 0, 0, 0.0), Metrics(20, 20, 15, 0, 0, 0.0)
    )
    assert (await config_change.promote(backend_pool, c, golden_passed=None)).status == "rejected"
    assert await config_change.current(backend_pool, edit) == before


class _NoCases:
    """An evaluator with nothing to test, so that field is skipped."""

    max_cases = 1

    async def cases(self, pool: Any) -> list[Any]:
        return []


class _Llm:
    def configured(self, model: str) -> bool:
        return True


class _Client:
    llm = _Llm()


@pytest.mark.db
async def test_a_cheaper_chat_model_is_used_only_after_it_passes_the_chat_test_set(
    backend_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from steward.config.jobs import JobsConfig
    from steward.engine import evaluators
    from steward.engine.replay import Outcome

    class FakeChat:
        max_cases = 3
        bad_for: ClassVar[set[str]] = set()

        async def cases(self, pool: Any) -> list[Any]:
            return [1, 2, 3]

        async def run(
            self,
            pool: Any,
            client: Any,
            case: Any,
            extra: str,
            usage: Any,
            model: str | None = None,
        ) -> Outcome:
            usage.input_tokens += 1000
            usage.output_tokens += 500
            return Outcome(checked=10, bad=5 if model in FakeChat.bad_for else 0)

    for name in ("NewsEvaluator", "DigestEvaluator", "ThesisEvaluator"):
        monkeypatch.setattr(evaluators, name, _NoCases)
    monkeypatch.setattr(evaluators, "ChatEvaluator", FakeChat)
    current = JobsConfig().chat.model

    # The cheaper model is much worse on the chat set: nothing changes.
    FakeChat.bad_for = {"gpt-6-luna", "gemini-3.8-flash"}
    out = await tuners.tune_models(backend_pool, _Client(), NOW, {"calls": 500})
    assert out["model"] == "chat.model" and out["status"] == "rejected"
    stored = await load_active(backend_pool, "jobs", JobsConfig)
    assert stored is None or stored.body.chat.model == current

    # Once the earlier proposal is old enough to retry and the model is as good, it is used.
    await backend_pool.execute(
        "update public.engine_change set created_at = created_at - interval '40 days'"
    )
    FakeChat.bad_for = set()
    out = await tuners.tune_models(
        backend_pool, _Client(), NOW + dt.timedelta(days=1), {"calls": 500}
    )
    assert out["status"] == "promoted"
    stored = await load_active(backend_pool, "jobs", JobsConfig)
    assert stored is not None and stored.body.chat.model == out["candidate"] != current


async def seed_source(admin_url: str, source_id: str, items: int, opened: int) -> None:
    admin = await asyncpg.connect(admin_url)
    try:
        await admin.execute(
            "insert into public.source (id, name, tier, type, url) values ($1, $1, 2, 'rss', 'http://x')",
            source_id,
        )
        for i in range(items):
            cluster = await admin.fetchval(
                "insert into public.story_cluster (headline, headline_key, best_tier)"
                " values ('h', $1, 2) returning id",
                f"{source_id}{i}",
            )
            await admin.execute(
                "insert into public.news_item (url, source_id, tier, published_at, headline,"
                " cluster_id, first_seen_at) values ($1, $2, 2, $3, 'h', $4, $3)",
                f"http://x/{source_id}/{i}",
                source_id,
                NOW - dt.timedelta(days=5),
                cluster,
            )
            if i < opened:
                await admin.execute(
                    "insert into public.engagement_event (kind, ref, created_at)"
                    " values ('news_story', $1, $2)",
                    str(cluster),
                    NOW - dt.timedelta(days=1),
                )
    finally:
        await admin.close()


async def store_sources(pool: asyncpg.Pool, ids: list[str]) -> None:
    body = NewsSourcesConfig(
        sources=[NewsSource(id=i, name=i, tier=2, type="rss", url=f"http://x/{i}") for i in ids]  # type: ignore[arg-type]
    )
    async with pool.acquire() as conn:
        await save_config(conn, "news_sources", body, created_by=None)


@pytest.mark.db
async def test_a_feed_nobody_used_is_switched_off_and_one_that_was_used_is_kept(
    backend_pool: asyncpg.Pool, migrated_db: MigratedDatabase
) -> None:
    await seed_source(migrated_db.admin_url, "dead-feed", 45, 0)
    await seed_source(migrated_db.admin_url, "read-feed", 45, 2)
    await store_sources(backend_pool, ["dead-feed", "read-feed"])
    out = await tuners.tune_sources(backend_pool, NOW)
    assert out == {"switched_off": "dead-feed"}
    stored = await load_active(backend_pool, "news_sources", NewsSourcesConfig)
    assert stored is not None
    assert {s.id: s.enabled for s in stored.body.sources} == {
        "dead-feed": False,
        "read-feed": True,
    }
    # Nothing more to switch off, and the cooldown keeps the same feed from being touched again.
    assert (await tuners.tune_sources(backend_pool, NOW))["switched_off"] is None


def test_a_feed_used_a_little_is_demoted_not_switched_off() -> None:
    use = tuners.SourceUse
    assert tuners.barely_used(use("a", 100, 3, 0))
    assert not tuners.barely_used(use("a", 100, 0, 0))  # switched off instead
    assert not tuners.barely_used(use("a", 100, 20, 0))
    assert not tuners.barely_used(use("a", 10, 1, 0))  # too few items to judge


@pytest.mark.db
async def test_a_feed_used_rarely_moves_to_tier_three_and_one_used_often_stays(
    backend_pool: asyncpg.Pool, migrated_db: MigratedDatabase
) -> None:
    await seed_source(migrated_db.admin_url, "meh-feed", 45, 1)
    await seed_source(migrated_db.admin_url, "busy-feed", 45, 20)
    await store_sources(backend_pool, ["meh-feed", "busy-feed"])
    out = await tuners.tune_source_tiers(backend_pool, NOW)
    assert out == {"demoted": "meh-feed"}
    stored = await load_active(backend_pool, "news_sources", NewsSourcesConfig)
    assert stored is not None
    assert {s.id: s.tier for s in stored.body.sources} == {"meh-feed": 3, "busy-feed": 2}
    assert (await tuners.tune_source_tiers(backend_pool, NOW))["demoted"] is None


def test_a_daily_cron_is_read_and_written_back_and_other_shapes_are_left_alone() -> None:
    assert tuners.parse_daily("30 7 * * *") == (450, "* * *")
    assert tuners.parse_daily("0 6 * * 1-5") == (360, "* * 1-5")
    assert tuners.parse_daily("*/15 * * * *") is None
    assert tuners.parse_daily("0 6,18 * * *") is None
    assert tuners.daily_cron(405, "* * 1-5") == "45 6 * * 1-5"


def test_the_daily_job_moves_only_when_it_is_well_after_the_wanted_time() -> None:
    # Owner opens at 07:00; a 10 minute job should start by 06:05, but never before 06:30.
    assert tuners.better_minute([420] * 9, 10, 480) == 390
    assert tuners.better_minute([420] * 9, 10, 400) is None  # less than 15 minutes to gain
    assert tuners.better_minute([420] * 9, 10, 390) is None  # already at the floor
    assert tuners.better_minute([600] * 9, 10, 700) == 545  # 10:00 - 45 - 10 = 09:05


def test_days_the_job_finished_after_the_first_visit_are_counted() -> None:
    d1, d2, d3 = (dt.date(2026, 10, n) for n in (1, 2, 3))
    opens = {d1: 420, d2: 420, d3: 420}
    assert tuners.late_days(opens, {d1: 430, d2: 400, d3: 421}) == 2
    assert tuners.late_days(opens, {d1: 370, d2: 340, d3: 361}) == 0
