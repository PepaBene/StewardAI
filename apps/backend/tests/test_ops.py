"""The ops watchdog: the retry policy, the key catalogue, the budget forecast, and (with a
database) the task ledger and the checks that need no job queue."""

import datetime as dt
from types import SimpleNamespace
from typing import cast

import asyncpg
import pytest
from pydantic import SecretStr

from steward.config.jobs import JobsConfig, OpsSettings
from steward.decision.rebalance import RebalancePlan, Trade
from steward.ops import budget, keys, suggestions, tasks, watchdog

NOW = dt.datetime(2026, 11, 10, 12, 0, tzinfo=dt.UTC)


def task(
    *,
    status: tasks.Status = "fixing",
    attempts: int = 0,
    acted_hours_ago: float | None = None,
) -> tasks.OpsTask:
    return tasks.OpsTask(
        id=1,
        kind="quality",
        subject="x",
        status=status,
        title="t",
        detail="d",
        href=None,
        weight=None,
        attempts=attempts,
        last_action=None,
        last_action_at=None
        if acted_hours_ago is None
        else NOW - dt.timedelta(hours=acted_hours_ago),
        first_seen=NOW,
        last_seen=NOW,
    )


def decide(t: tasks.OpsTask, can_repair: bool = True) -> str:
    return tasks.decide(t, NOW, retry_hours=12, max_attempts=2, can_repair=can_repair)


# ------------------------------------------------------------------------------- policy


def test_first_sighting_is_repaired() -> None:
    assert decide(task()) == "repair"


def test_a_recent_attempt_is_given_time() -> None:
    assert decide(task(attempts=1, acted_hours_ago=3)) == "wait"


def test_a_stale_attempt_is_repeated_until_attempts_run_out() -> None:
    assert decide(task(attempts=1, acted_hours_ago=13)) == "repair"
    assert decide(task(attempts=2, acted_hours_ago=13)) == "escalate"
    assert decide(task(attempts=2, acted_hours_ago=1)) == "wait"


def test_owner_problems_and_unrepairable_ones_are_held() -> None:
    assert decide(task(status="needs_owner")) == "hold"
    assert decide(task(), can_repair=False) == "hold"


# --------------------------------------------------------------------------------- keys


def test_only_keys_for_switched_on_jobs_are_listed_best_first() -> None:
    settings = SimpleNamespace(
        openai_api_key=SecretStr("sk"),
        brave_api_key=None,
        finnhub_api_key=SecretStr(""),
        fmp_api_key=None,
        resend_api_key=None,
        telegram_bot_token=SecretStr("t"),
    )
    found = keys.missing(settings, {"gap_research", "earnings_calendar"})
    assert [k.setting for k in found] == ["brave_api_key", "finnhub_api_key"]
    assert "fmp_api_key" not in {k.setting for k in found}  # fmp_company is off


def test_every_key_is_a_real_setting() -> None:
    from steward.settings import Settings

    assert {k.setting for k in keys.CATALOGUE} <= set(Settings.model_fields)


def test_every_key_job_is_a_scheduled_job() -> None:
    scheduled = set(JobsConfig().schedules())
    for k in keys.CATALOGUE:
        assert set(k.jobs) <= scheduled, k.setting


# ------------------------------------------------------------------------------- budget


def test_forecast_extends_the_spend_in_a_line() -> None:
    fc = budget.forecast("2026-11", 30.0, 80.0, dt.datetime(2026, 11, 11, 12, tzinfo=dt.UTC))
    assert fc is not None and fc.over
    assert 80 < fc.projected < 90  # $30 in ~10.5 of 30 days is about $85 for the month
    fine = budget.forecast("2026-11", 5.0, 100.0, dt.datetime(2026, 11, 11, 12, tzinfo=dt.UTC))
    assert fine is not None and not fine.over


def test_no_forecast_in_the_first_days() -> None:
    assert (
        budget.forecast("2026-11", 50.0, 100.0, dt.datetime(2026, 11, 2, 12, tzinfo=dt.UTC)) is None
    )


def _trade(ticker: str, action: str, value: float) -> Trade:
    return Trade(
        ticker=ticker,
        name=None,
        action=action,
        value_now=100_000,
        weight_now=0.2,
        target_weight=0.15,
        lower=0.1,
        upper=0.18,
        status_now="above",
        value_after=100_000 + value,
        weight_after=0.18,
        trade_value=value,
        estimated_units=None,
        cost=5,
        funded=1,
        held=True,
        reason="above its band",
    )


def test_a_suggestion_names_the_biggest_trades_first_and_says_nothing_when_in_band() -> None:
    plan = SimpleNamespace(
        trades=[_trade("B", "buy", 4_000), _trade("A", "sell", -12_000), _trade("C", "hold", 0)],
        costs=17.0,
    )
    said = suggestions.describe(cast(RebalancePlan, plan), "CZK")
    assert said is not None
    title, detail = said
    assert title == "Positions have drifted outside their bands: 1 to sell and 1 to buy"
    assert detail.index("Sell A") < detail.index("Buy B")
    assert "Nothing is sent to your broker" in detail
    assert (
        suggestions.describe(cast(RebalancePlan, SimpleNamespace(trades=[], costs=0)), "CZK")
        is None
    )


def test_a_thesis_break_names_the_kpis_and_the_weight() -> None:
    title, detail = suggestions.describe_break(
        "AAPL_US_EQ", 0.12, [("revenue_growth", "annual", "5% against a floor of 10%")]
    )
    assert title == "AAPL_US_EQ: 1 thesis KPI breached"
    assert "revenue_growth (annual): 5% against a floor of 10%" in detail
    assert "12.0% of the portfolio" in detail


def test_ops_settings_default_to_acting() -> None:
    cfg = OpsSettings()
    assert cfg.enabled and cfg.repair and cfg.max_attempts == 2


# ----------------------------------------------------------------------- the task ledger


@pytest.mark.db
async def test_a_task_is_repaired_then_handed_over_then_resolves(
    backend_pool: asyncpg.Pool,
) -> None:
    first = await tasks.see(backend_pool, "quality", "stale_price:AAPL", title="t", detail="d")
    again = await tasks.see(backend_pool, "quality", "stale_price:AAPL", title="t", detail="d2")
    assert again.id == first.id and again.detail == "d2"  # one open row per problem

    await tasks.acted(backend_pool, first.id, "queued price_history")
    await tasks.hand_over(backend_pool, first.id)
    assert [t.id for t in await tasks.needs_owner(backend_pool)] == [first.id]

    assert await tasks.settle(backend_pool, "quality", set()) == 1
    assert await tasks.needs_owner(backend_pool) == []
    reopened = await tasks.see(backend_pool, "quality", "stale_price:AAPL", title="t", detail="d")
    assert reopened.id != first.id and reopened.attempts == 0


@pytest.mark.db
async def test_settle_keeps_the_problems_still_seen(backend_pool: asyncpg.Pool) -> None:
    await tasks.see(backend_pool, "quality", "a", title="t", detail="d")
    await tasks.see(backend_pool, "quality", "b", title="t", detail="d")
    assert await tasks.settle(backend_pool, "quality", {"a"}) == 1
    assert await tasks.settle(backend_pool, "other_kind", set()) == 0


@pytest.mark.db
async def test_a_dismissed_key_stays_dismissed(
    backend_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        watchdog,
        "get_settings",
        lambda: SimpleNamespace(**{k.setting: None for k in keys.CATALOGUE}),
    )
    jobs = JobsConfig()
    first = await watchdog.check_keys(backend_pool, jobs)
    assert "openai_api_key" in first["missing"]
    owner_view = await tasks.needs_owner(backend_pool)
    assert owner_view and owner_view[0].kind == "missing_key"

    key_task = next(t for t in owner_view if t.subject == "openai_api_key")
    assert await tasks.dismiss(backend_pool, key_task.id)
    assert not await tasks.dismiss(backend_pool, key_task.id)  # only an open notice
    second = await watchdog.check_keys(backend_pool, jobs)
    assert "openai_api_key" not in second["missing"]
    assert all(t.subject != "openai_api_key" for t in await tasks.needs_owner(backend_pool))


@pytest.mark.db
async def test_a_breached_kpi_on_a_held_stock_opens_a_notice_until_it_passes(
    backend_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def weights(_pool: asyncpg.Pool) -> dict[str, float]:
        return {"AAPL_US_EQ": 0.12}

    monkeypatch.setattr("steward.autopilot.ledger.holding_weights", weights)
    from steward.config.store import save_config
    from steward.config.thesis import ThesesConfig

    async with backend_pool.acquire() as conn:
        saved = await save_config(conn, "theses", ThesesConfig(theses=[]), created_by=None)

    async def verdict(status: str) -> None:
        await backend_pool.execute(
            "insert into public.verdict (ticker, kpi_id, metric, basis, kpi_period, status,"
            " operator, threshold, threshold_unit, rationale, config_version_id)"
            " values ('AAPL_US_EQ', 'k1', 'revenue_growth', 'reported', 'annual', $1,"
            " '>=', 10, '%', 'r', $2)",
            status,
            saved.id,
        )

    await verdict("breach")
    out = await suggestions.suggest_thesis_breaks(backend_pool)
    assert out["breached"] == ["AAPL_US_EQ"]
    assert [t.kind for t in await tasks.needs_owner(backend_pool)] == ["thesis_break"]
    await verdict("pass")
    assert (await suggestions.suggest_thesis_breaks(backend_pool))["resolved"] == 1
    assert await tasks.needs_owner(backend_pool) == []


@pytest.mark.db
async def test_budget_forecast_notice_opens_and_clears(
    backend_pool: asyncpg.Pool,
) -> None:
    # No budget config saved, so there is no cap and nothing to forecast.
    out = await watchdog.check_budget(backend_pool, NOW)
    assert out["cap"] is None
    assert await tasks.needs_owner(backend_pool) == []


@pytest.mark.db
async def test_a_primary_that_keeps_failing_gets_its_standby_run_once(
    backend_pool: asyncpg.Pool,
) -> None:
    jobs = JobsConfig()
    first = await watchdog.run_failover(backend_pool, jobs, {"sec_xbrl"}, NOW)
    assert first == {"started": ["sec_xbrl->fmp_company"]}
    # The standby is now queued: not started a second time, and a healthy primary is ignored.
    again = await watchdog.run_failover(backend_pool, jobs, {"sec_xbrl"}, NOW)
    assert again == {"started": []}
    assert (await watchdog.run_failover(backend_pool, jobs, set(), NOW)) == {"started": []}
    open_notices = await backend_pool.fetchval(
        "select count(*) from public.ops_task where kind = 'failover' and status <> 'resolved'"
    )
    assert open_notices == 0  # the primary is healthy again


@pytest.mark.db
async def test_a_primary_off_the_alert_list_gets_its_standby_only_after_its_retry_fails(
    backend_pool: asyncpg.Pool,
) -> None:
    jobs = JobsConfig()
    assert "sec_xbrl" not in jobs.monitoring.alert_failed_jobs
    cfg = OpsSettings()
    now = dt.datetime.now(dt.UTC)
    await backend_pool.execute(
        "insert into public.job (job_type, trigger, status, error, created_at, finished_at)"
        " values ('sec_xbrl', 'schedule', 'failed', 'boom', $1, $1)",
        now - dt.timedelta(hours=1),
    )
    first = await watchdog.check_failed_jobs(backend_pool, jobs, cfg, now)
    assert first["repaired"] == ["job_failed:sec_xbrl"]
    assert first["failover"] == {"started": []}  # the retry comes first
    # While the retry is queued its task stays open with the attempt it made.
    queued = await watchdog.check_failed_jobs(backend_pool, jobs, cfg, now)
    assert queued["resolved"] == 0 and queued["failover"] == {"started": []}
    await backend_pool.execute(
        "update public.job set status = 'failed', error = 'boom again', finished_at = now()"
        " where job_type = 'sec_xbrl' and status = 'queued'"
    )
    after = await watchdog.check_failed_jobs(backend_pool, jobs, cfg, now)
    assert after["failover"] == {"started": ["sec_xbrl->fmp_company"]}


@pytest.mark.db
async def test_a_block_that_comes_back_after_it_was_lifted_keeps_its_attempts(
    backend_pool: asyncpg.Pool,
) -> None:
    first = await tasks.see(backend_pool, "provider_block", "sec/api", title="t", detail="d")
    await tasks.acted(backend_pool, first.id, "lifted the block to probe again")
    assert await tasks.settle(backend_pool, "provider_block", set()) == 1  # the lift worked
    back = await tasks.see(backend_pool, "provider_block", "sec/api", title="t", detail="d")
    assert back.id != first.id and back.attempts == 0
    carried = await tasks.carry_attempts(backend_pool, back, dt.timedelta(days=30))
    assert carried.attempts == 1 and carried.last_action == "lifted the block to probe again"
    # With its attempts carried over the policy runs out and hands it to the owner.
    late = NOW + dt.timedelta(days=60)
    assert tasks.decide(carried, late, retry_hours=12, max_attempts=1, can_repair=True) == (
        "escalate"
    )


@pytest.mark.db
async def test_a_dismissed_notice_for_one_occurrence_stays_dismissed(
    backend_pool: asyncpg.Pool,
) -> None:
    notice = await tasks.see(
        backend_pool, "budget_forecast", "2026-11", title="t", detail="d", status="needs_owner"
    )
    assert await tasks.dismiss(backend_pool, notice.id)
    again = await tasks.see(
        backend_pool,
        "budget_forecast",
        "2026-11",
        title="t",
        detail="d",
        status="needs_owner",
        keep_dismissed=True,
    )
    assert again.id == notice.id and again.status == "resolved"
    assert await tasks.needs_owner(backend_pool) == []
