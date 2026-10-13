"""Alert tuning: an ignored instant alert drops one level, once, and stays undoable."""

import datetime as dt
import json

import asyncpg
import pytest

from steward.config.event_rules import EventRulesConfig
from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.ops import tasks
from steward.ops import tuning as tn

NOW = dt.datetime.now(dt.UTC)
SEVERITIES = {"m_and_a": 3, "dividend_cut": 3, "earnings_release": 2}


def eng(kind: str, sent: int, acked: int) -> tn.Engagement:
    return tn.Engagement(kind, sent, acked)


def test_the_most_ignored_instant_alert_is_picked() -> None:
    rows = [eng("m_and_a", 12, 1), eng("dividend_cut", 20, 0), eng("earnings_release", 30, 0)]
    picked = tn.pick(rows, SEVERITIES, set())
    assert picked is not None and picked.event_type == "dividend_cut"  # 2 is already the floor


def test_too_few_alerts_acknowledged_ones_and_recent_changes_are_left_alone() -> None:
    assert tn.pick([eng("m_and_a", 9, 0)], SEVERITIES, set()) is None
    assert tn.pick([eng("m_and_a", 20, 4)], SEVERITIES, set()) is None
    assert tn.pick([eng("m_and_a", 20, 0)], SEVERITIES, {"m_and_a"}) is None


def test_the_inbox_level_rises_only_for_ignored_items_and_only_to_three() -> None:
    assert tn.pick_inbox(12, 0, 2, False)
    assert not tn.pick_inbox(9, 0, 2, False)  # too few decided
    assert not tn.pick_inbox(12, 3, 2, False)  # used more than 10%
    assert not tn.pick_inbox(12, 0, 3, False)  # already at the top
    assert not tn.pick_inbox(12, 0, 2, True)  # changed recently


@pytest.mark.db
async def test_ignored_inbox_items_raise_the_minimum_severity_once(
    backend_pool: asyncpg.Pool,
) -> None:
    for i in range(11):
        await backend_pool.execute(
            "insert into public.inbox_item (trigger_key, trigger_kind, trigger_ref, severity,"
            " headline, body, status) values ($1, 'alert', $2, 2, 'h', '{}'::jsonb, 'dismissed')",
            f"alert:{i}",
            i,
        )
    out = await tn.tune_inbox(backend_pool, NOW)
    assert out["raised"] == 3
    stored = await load_active(backend_pool, "jobs", JobsConfig)
    assert stored is not None and stored.body.inbox_settings.min_severity == 3
    assert (await tn.tune_inbox(backend_pool, NOW))["raised"] is None


async def escalation(pool: asyncpg.Pool, event_type: str, acknowledged: bool) -> None:
    await pool.execute(
        "insert into public.report (type, ticker, title, body, body_md, escalation_status,"
        " acknowledged_at) values ('escalation', 'A', 't', $1::jsonb, '', $2, $3)",
        json.dumps({"event_type": event_type}),
        "acknowledged" if acknowledged else "open",
        NOW if acknowledged else None,
    )


@pytest.mark.db
async def test_an_ignored_alert_is_lowered_once_as_a_config_version(
    backend_pool: asyncpg.Pool,
) -> None:
    for _ in range(11):
        await escalation(backend_pool, "m_and_a", False)
    out = await tn.tune_alerts(backend_pool, NOW)
    assert out["lowered"] == "m_and_a"
    stored = await load_active(backend_pool, "event_rules", EventRulesConfig)
    assert stored is not None and stored.body.severities["m_and_a"] == 2
    notice = (await tasks.needs_owner(backend_pool))[0]
    assert notice.kind == "alert_tuning" and "0 of 11" in notice.detail

    assert (await tn.tune_alerts(backend_pool, NOW))["lowered"] is None  # now a highlight
