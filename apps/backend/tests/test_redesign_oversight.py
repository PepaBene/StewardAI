"""Data for the oversight screens of the UI redesign: the escalation lifecycle, job attempts,
digest list counts, strategy rules that can be switched off, and the cost summary's period."""

import datetime as dt
import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool, get_pool_or_error
from api.main import create_app
from steward.config.strategy import StrategyConfig
from steward.jobs.runner import JobContext, run_job
from steward.news.escalation_lifecycle import escalation_stats
from steward.portfolio.breaches import evaluate
from steward.portfolio.drift import (
    BucketDrift,
    CashDrift,
    DimensionAllocation,
    DriftReport,
    PositionDrift,
)
from steward.research.sources import Registry
from steward.review import monthly

OWNER = "7f0c3d4e-0000-4000-8000-000000000001"
MIGRATIONS = Path(__file__).resolve().parents[3] / "supabase" / "migrations"


@pytest.fixture
async def api(backend_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[get_pool_or_error] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(OWNER, None, "aal2")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# Escalation lifecycle
# ---------------------------------------------------------------------------


def _escalation_body(ticker: str = "AAPL_US_EQ") -> dict[str, Any]:
    return {
        "ticker": ticker,
        "trigger": "story",
        "cluster_id": None,
        "event_type": "guidance_change",
        "severity": 3,
        "headline": "Apple cuts guidance",
        "sentences": [],
        "kpis": [],
        "exit_conditions": [],
        "none_applies": True,
        "thesis_link": "none",
        "rationale": "No KPI is affected.",
        "sources": [],
    }


async def _add_escalation(
    pool: asyncpg.Pool, created_at: dt.datetime | None = None, ticker: str = "AAPL_US_EQ"
) -> int:
    report_id: int = await pool.fetchval(
        "insert into public.report (type, ticker, title, body, body_md, created_at)"
        " values ('escalation', $1, 'Escalation', $2::jsonb, '', coalesce($3, now()))"
        " returning id",
        ticker,
        json.dumps(_escalation_body(ticker)),
        created_at,
    )
    return report_id


@pytest.mark.db
async def test_a_new_escalation_is_open_and_other_reports_have_no_status(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    report = await _add_escalation(backend_pool)
    note = (await api.get(f"/escalations/{report}")).json()
    assert note["status"] == "open"
    assert note["acknowledged_at"] is None and note["resolved_at"] is None
    digest = await backend_pool.fetchval(
        "insert into public.report (type, title, body, body_md) values ('digest', 'd',"
        " '{}'::jsonb, '') returning escalation_status"
    )
    assert digest is None
    with pytest.raises(asyncpg.CheckViolationError):
        await backend_pool.execute(
            "insert into public.report (type, title, body, body_md, escalation_status)"
            " values ('digest', 'd', '{}'::jsonb, '', 'open')"
        )
    with pytest.raises(asyncpg.CheckViolationError):
        await backend_pool.execute(
            "update public.report set escalation_status = 'resolved' where id = $1", report
        )


@pytest.mark.db
async def test_escalation_status_moves_set_and_clear_the_timestamps(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    report = await _add_escalation(backend_pool)

    acked = await api.patch(f"/escalations/{report}", json={"status": "acknowledged"})
    assert acked.status_code == 200, acked.text
    first_ack = acked.json()["acknowledged_at"]
    assert acked.json()["status"] == "acknowledged" and first_ack is not None
    assert acked.json()["resolved_at"] is None

    # The same status again changes nothing.
    again = (await api.patch(f"/escalations/{report}", json={"status": "acknowledged"})).json()
    assert again["acknowledged_at"] == first_ack

    resolved = (await api.patch(f"/escalations/{report}", json={"status": "resolved"})).json()
    assert resolved["status"] == "resolved" and resolved["resolved_at"] is not None
    assert resolved["acknowledged_at"] == first_ack

    # Reopening clears the resolution; the first acknowledgement stays.
    reopened = (await api.patch(f"/escalations/{report}", json={"status": "open"})).json()
    assert reopened["status"] == "open" and reopened["resolved_at"] is None
    assert reopened["acknowledged_at"] == first_ack

    # Resolving one never acknowledged records the acknowledgement too.
    other = await _add_escalation(backend_pool)
    direct = (await api.patch(f"/escalations/{other}", json={"status": "resolved"})).json()
    assert direct["acknowledged_at"] is not None and direct["resolved_at"] is not None

    bad = await api.patch(f"/escalations/{report}", json={"status": "closed"})
    assert bad.status_code == 422
    missing = await api.patch(f"/escalations/{other + 100}", json={"status": "open"})
    assert missing.status_code == 404
    digest = await backend_pool.fetchval(
        "insert into public.report (type, title, body, body_md) values ('digest', 'd',"
        " '{}'::jsonb, '') returning id"
    )
    not_escalation = await api.patch(f"/escalations/{digest}", json={"status": "resolved"})
    assert not_escalation.status_code == 404


@pytest.mark.db
async def test_escalations_filter_by_status(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    opened = await _add_escalation(backend_pool)
    acked = await _add_escalation(backend_pool)
    resolved = await _add_escalation(backend_pool, ticker="MSFT_US_EQ")
    await api.patch(f"/escalations/{acked}", json={"status": "acknowledged"})
    await api.patch(f"/escalations/{resolved}", json={"status": "resolved"})

    def ids(res: httpx.Response) -> list[int]:
        assert res.status_code == 200, res.text
        return [n["id"] for n in res.json()]

    assert ids(await api.get("/escalations")) == [resolved, acked, opened]
    assert ids(await api.get("/escalations", params={"status": "open"})) == [opened]
    assert ids(await api.get("/escalations", params={"status": "acknowledged"})) == [acked]
    assert ids(await api.get("/escalations", params={"status": "resolved"})) == [resolved]
    both = await api.get("/escalations", params={"status": "resolved", "ticker": "AAPL_US_EQ"})
    assert ids(both) == []
    assert (await api.get("/escalations", params={"status": "nope"})).status_code == 422


@pytest.mark.db
async def test_escalation_stats_count_states_time_to_ack_and_days(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    now = dt.datetime(2026, 9, 27, 10, 0, tzinfo=dt.UTC)  # 12:00 Prague
    empty = await escalation_stats(backend_pool, 3, now=now)
    assert (empty.open, empty.acknowledged, empty.resolved) == (0, 0, 0)
    assert empty.median_minutes_to_ack is None
    assert [(d.date, d.opened, d.acknowledged) for d in empty.per_day] == [
        (dt.date(2026, 9, 25), 0, 0),
        (dt.date(2026, 9, 26), 0, 0),
        (dt.date(2026, 9, 27), 0, 0),
    ]

    # 23:30 UTC on the 25th is 01:30 on the 26th in Prague.
    a = await _add_escalation(backend_pool, dt.datetime(2026, 9, 25, 23, 30, tzinfo=dt.UTC))
    b = await _add_escalation(backend_pool, dt.datetime(2026, 9, 26, 8, 0, tzinfo=dt.UTC))
    c = await _add_escalation(backend_pool, dt.datetime(2026, 9, 27, 8, 0, tzinfo=dt.UTC))
    old = await _add_escalation(backend_pool, dt.datetime(2026, 8, 1, 8, 0, tzinfo=dt.UTC))
    ack = "update public.report set escalation_status = $2, acknowledged_at = $3 where id = $1"
    await backend_pool.execute(
        ack, a, "acknowledged", dt.datetime(2026, 9, 26, 0, 0, tzinfo=dt.UTC)
    )
    await backend_pool.execute(
        ack, b, "acknowledged", dt.datetime(2026, 9, 27, 9, 0, tzinfo=dt.UTC)
    )
    await backend_pool.execute(
        "update public.report set escalation_status = 'resolved', acknowledged_at = $2,"
        " resolved_at = $2 where id = $1",
        old,
        dt.datetime(2026, 8, 1, 9, 0, tzinfo=dt.UTC),
    )
    stats = await escalation_stats(backend_pool, 3, now=now)
    assert (stats.open, stats.acknowledged, stats.resolved) == (1, 2, 1)
    # Acknowledged in the window: a after 30 min, b after 25 h; the old one is outside.
    assert stats.median_minutes_to_ack == pytest.approx((30 + 25 * 60) / 2)
    assert [(d.date, d.opened, d.acknowledged) for d in stats.per_day] == [
        (dt.date(2026, 9, 25), 0, 0),
        (dt.date(2026, 9, 26), 2, 1),
        (dt.date(2026, 9, 27), 1, 1),
    ]
    assert c

    res = await api.get("/escalations/stats")
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["per_day"]) == 30 and body["days"] == 30
    assert body["timezone"] == "Europe/Prague"
    assert {"open", "acknowledged", "resolved", "median_minutes_to_ack"} <= set(body)
    assert (await api.get("/escalations/stats", params={"days": 0})).status_code == 422
    assert (await api.get("/escalations/stats", params={"days": 366})).status_code == 422


def test_cors_allows_patch() -> None:
    app = create_app()
    cors = next(m for m in app.user_middleware if "CORS" in str(m.cls))
    assert "PATCH" in cors.kwargs["allow_methods"]


# ---------------------------------------------------------------------------
# Job attempts
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_attempts_count_each_start_including_a_resume(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    async def handler(ctx: JobContext) -> None:
        async with ctx.step("work"):
            pass

    assert await run_job(backend_pool, "research", handler, trigger="manual") == "succeeded"
    once = await backend_pool.fetchval("select max(id) from job")
    # A run the worker was killed in: started once, a step left running.
    killed = await backend_pool.fetchval(
        "insert into job (job_type, trigger, status, started_at, attempts)"
        " values ('research', 'manual', 'running', now(), 1) returning id"
    )
    await backend_pool.execute(
        "insert into job_step (job_id, step_index, name, status) values ($1, 0, 'work', 'running')",
        killed,
    )
    assert await run_job(backend_pool, "research", handler, trigger="manual",
                         job_id=killed) == "succeeded"  # fmt: skip
    # Redelivered after it finished: not started again, not counted.
    assert await run_job(backend_pool, "research", handler, trigger="manual",
                         job_id=killed) == "succeeded"  # fmt: skip
    queued = await backend_pool.fetchval(
        "insert into job (job_type, trigger) values ('research', 'manual') returning id"
    )

    listed = {j["id"]: j for j in (await api.get("/jobs")).json()["jobs"]}
    assert listed[once]["attempts"] == 1
    assert listed[killed]["attempts"] == 2
    assert listed[queued]["attempts"] == 0
    detail = (await api.get(f"/jobs/{killed}")).json()
    assert detail["attempts"] == 2


def _backfill_sql() -> str:
    text = (MIGRATIONS / "20260930120100_job_attempts.sql").read_text()
    match = re.search(r"update public\.job j set attempts = .*?\nend;", text, re.S)
    assert match
    return match.group(0)


@pytest.mark.db
async def test_the_attempts_backfill_is_a_lower_bound(backend_pool: asyncpg.Pool) -> None:
    never = await backend_pool.fetchval(
        "insert into job (job_type, trigger, status) values ('research', 'manual', 'failed')"
        " returning id"
    )
    plain = await backend_pool.fetchval(
        "insert into job (job_type, trigger, status, started_at) values ('research', 'manual',"
        " 'succeeded', now()) returning id"
    )
    resumed = await backend_pool.fetchval(
        "insert into job (job_type, trigger, status, started_at) values ('research', 'manual',"
        " 'succeeded', now()) returning id"
    )
    marker = "interrupted: the worker stopped during this step"
    await backend_pool.execute(
        "insert into job_step (job_id, step_index, name, status, error) values"
        " ($1, 0, 'a', 'failed', $2), ($1, 1, 'b', 'succeeded', null)",
        resumed,
        marker,
    )
    # Closed by worker catch-up: the same marker, but the run was failed, not resumed.
    caught_up = await backend_pool.fetchval(
        "insert into job (job_type, trigger, status, started_at) values ('daily_digest',"
        " 'schedule', 'failed', now()) returning id"
    )
    await backend_pool.execute(
        "insert into job_step (job_id, step_index, name, status, error) values"
        " ($1, 0, 'a', 'succeeded', null), ($1, 1, 'b', 'failed', $2)",
        caught_up,
        marker,
    )
    await backend_pool.execute(_backfill_sql())
    rows = dict(await backend_pool.fetch("select id, attempts from job"))
    assert rows == {never: 0, plain: 1, resumed: 2, caught_up: 1}


# ---------------------------------------------------------------------------
# Report list counts
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_digest_summaries_count_stories_and_distinct_cited_sources(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    window = {
        "window_start": "2026-09-25T05:00:00+00:00",
        "window_end": "2026-09-26T05:00:00+00:00",
    }

    def story(cluster_id: int) -> dict[str, Any]:
        return {"cluster_id": cluster_id, "headline": "h", "event_type": None, "severity": 2,
                "status": "confirmed", "confidence": "corroborated",
                "news_item_ids": [1]}  # fmt: skip

    def source(item_id: int, url: str) -> dict[str, Any]:
        return {"news_item_id": item_id, "url": url, "outlet": None, "tier": 1,
                "headline": "h", "published_at": "2026-09-25T06:00:00+00:00"}  # fmt: skip

    def fact(fact_id: int, url: str) -> dict[str, Any]:
        return {"fact_id": fact_id, "ticker": "AAPL_US_EQ", "metric": "revenue", "period": "Q3",
                "value_as_written": "1", "snippet": "s", "source_url": url,
                "status": "validated"}  # fmt: skip

    full = {
        **window,
        "kind": "daily",
        "headline": "Busy",
        "nothing_material": False,
        "sections": [
            {"ticker": "AAPL_US_EQ", "name": "Apple", "stories": [story(1), story(2)]},
            {"ticker": None, "name": "Markets and macro", "stories": [story(3)]},
            {"ticker": "MSFT_US_EQ", "name": "Microsoft"},
        ],
        "sources": [source(1, "https://a.example/1"), source(2, "https://b.example/2")],
        # Two facts from one filing count once; a filing a news item shares counts once.
        "fact_sources": [
            fact(10, "https://sec.example/10q"),
            fact(11, "https://sec.example/10q"),
            fact(12, "https://a.example/1"),
        ],
    }
    quiet = {**window, "kind": "daily", "headline": "Quiet", "nothing_material": True}
    insert = (
        "insert into public.report (type, period, title, body, body_md)"
        " values ($1, 'daily', 't', $2::jsonb, '') returning id"
    )
    busy_id = await backend_pool.fetchval(insert, "digest", json.dumps(full))
    quiet_id = await backend_pool.fetchval(insert, "digest", json.dumps(quiet))
    review_id = await backend_pool.fetchval(insert, "review", json.dumps({"headline": "r"}))

    digests = {r["id"]: r for r in (await api.get("/reports")).json()}
    assert (digests[busy_id]["story_count"], digests[busy_id]["citation_count"]) == (3, 3)
    assert (digests[quiet_id]["story_count"], digests[quiet_id]["citation_count"]) == (0, 0)
    reviews = (await api.get("/reports", params={"type": "review"})).json()
    assert [(r["id"], r["story_count"], r["citation_count"]) for r in reviews] == [
        (review_id, None, None)
    ]


# ---------------------------------------------------------------------------
# Strategy rules that can be switched off
# ---------------------------------------------------------------------------


def _drift() -> DriftReport:
    return DriftReport(
        weight_basis="total",
        basis_value_base=1000,
        positions=[
            PositionDrift(ticker="AAPL_US_EQ", weight=0.95, target_weight=None, band=None,
                          lower=None, upper=None, hard_limit=None, drift=None,
                          status="no_target", held=True),
        ],
        categories=[
            DimensionAllocation(dimension="sector", buckets=[
                BucketDrift(bucket="Tech", value_base=950, weight=0.95, target_weight=None,
                            band=None, lower=None, upper=None, drift=None, status="no_target"),
            ]),
        ],
        cash=CashDrift(value_base=50, weight=0.05, target_weight=None, band=None,
                       lower=None, upper=None, drift=None, status="no_target"),
        out_of_band=0,
    )  # fmt: skip


RULES: list[dict[str, Any]] = [
    {"id": "max-position", "type": "position_weight", "operator": "<=", "value": 0.2},
    {"id": "max-tech", "type": "category_weight", "dimension": "sector", "operator": "<=",
     "value": 0.5, "enabled": False},
    {"id": "min-cash", "type": "cash_weight", "operator": ">=", "value": 0.1, "enabled": False},
    {"id": "min-count", "type": "position_count", "operator": ">=", "value": 5,
     "enabled": True},
]  # fmt: skip


def _strategy(rules: list[dict[str, Any]]) -> StrategyConfig:
    return StrategyConfig.model_validate(
        {"base_currency": "USD", "categories": ["sector"], "rules": rules}
    )


def test_rules_are_enabled_unless_switched_off() -> None:
    stored = _strategy([{"id": "r", "type": "cash_weight", "operator": ">=", "value": 0.1}])
    assert stored.rules[0].enabled is True
    assert stored.model_dump(mode="json")["rules"][0]["enabled"] is True
    strategy = _strategy(RULES)
    assert [r.id for r in strategy.enabled_rules] == ["max-position", "min-count"]
    # A disabled rule is still validated.
    with pytest.raises(ValueError, match="undeclared dimension"):
        _strategy([{**RULES[1], "dimension": "region"}])


def test_disabled_rules_raise_no_breach() -> None:
    every_rule_on = _strategy([{**r, "enabled": True} for r in RULES])
    on = {f.key for f in evaluate(_drift(), every_rule_on) if f.kind == "rule"}
    assert on == {
        "rule:max-position:AAPL_US_EQ",
        "rule:max-tech:sector:Tech",
        "rule:min-cash:cash",
        "rule:min-count:count",
    }
    some_off = {f.key for f in evaluate(_drift(), _strategy(RULES)) if f.kind == "rule"}
    assert some_off == {"rule:max-position:AAPL_US_EQ", "rule:min-count:count"}


async def test_disabled_rules_are_left_out_of_review_compliance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_overview(_pool: Any) -> Any:
        return SimpleNamespace(drift=_drift(), freshness=None, base=None)

    monkeypatch.setattr(monthly, "portfolio_overview", fake_overview)
    rows, *_ = await monthly.compliance(None, Registry(), _strategy(RULES), None)  # type: ignore[arg-type]
    rules = {r.id: r.status for r in rows if r.kind == "rule"}
    assert rules == {"max-position": "breach", "min-count": "breach"}


@pytest.mark.db
async def test_a_stored_strategy_without_the_flag_still_imports_diffs_and_rolls_back(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    # A version saved before the flag existed.
    await backend_pool.execute(
        "update config_version set active = false where domain = 'strategy' and active"
    )
    old_id = await backend_pool.fetchval(
        "insert into config_version (domain, body, schema_version, active)"
        " values ('strategy', $1::jsonb, 1, true) returning id",
        json.dumps(
            {
                "base_currency": "USD",
                "rules": [
                    {"id": "min-cash", "type": "cash_weight", "operator": ">=", "value": 0.1}
                ],
            }
        ),
    )
    loaded = (await api.get("/config/strategy")).json()
    assert loaded["body"]["rules"][0]["enabled"] is True

    exported = (await api.get("/config/export")).json()
    exported["domains"] = {"strategy": exported["domains"]["strategy"]}
    exported["domains"]["strategy"]["body"]["rules"][0]["enabled"] = False
    imported = await api.post("/config/import", json=exported)
    assert imported.status_code == 200, imported.text
    new_id = imported.json()["saved"]["strategy"]
    saved = (await api.get("/config/strategy")).json()
    assert saved["id"] == new_id and saved["body"]["rules"][0]["enabled"] is False
    validated = await api.post("/config/strategy/validate", json=saved["body"])
    assert validated.status_code == 200 and validated.json()["rules"][0]["enabled"] is False

    diff = (await api.get("/config/strategy/diff", params={"from": old_id, "to": new_id})).json()
    assert any(c["path"].endswith("enabled") for c in diff["changes"]), diff
    rolled = await api.post(f"/config/strategy/versions/{old_id}/rollback")
    assert rolled.status_code == 200, rolled.text
    assert (await api.get("/config/strategy")).json()["body"]["rules"][0]["enabled"] is True


# ---------------------------------------------------------------------------
# Cost summary period
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_the_cost_summary_carries_its_budget_month_bounds(api: httpx.AsyncClient) -> None:
    body = (await api.get("/costs/summary", params={"month": "2026-09"})).json()
    start = dt.datetime.fromisoformat(body["period_start"])
    end = dt.datetime.fromisoformat(body["period_end"])
    # Midnight Prague (CEST, UTC+2) on the 1st, to midnight on 1 October.
    assert start == dt.datetime(2026, 8, 31, 22, 0, tzinfo=dt.UTC)
    assert end == dt.datetime(2026, 9, 30, 22, 0, tzinfo=dt.UTC)
    december = (await api.get("/costs/summary", params={"month": "2026-12"})).json()
    assert dt.datetime.fromisoformat(december["period_end"]) == dt.datetime(
        2026, 12, 31, 23, 0, tzinfo=dt.UTC
    )
    for key in ("total_usd", "cap_usd", "fraction", "by_model", "by_job_type"):
        assert key in body
