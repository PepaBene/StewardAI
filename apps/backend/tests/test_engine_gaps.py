"""Gaps closed in phases 1 and 2 of the autonomous engine: undo from the ledger, receipts, job
heartbeats, the weekly re-check, pins, config overrides and lessons."""

import datetime as dt
import json
from types import SimpleNamespace
from typing import Any

import asyncpg
import httpx
import pytest

from steward.actions import registry
from steward.autopilot import ledger, reverify
from steward.autopilot.kinds import VERIFY_WAIVERS, all_kinds, check_verifiers
from steward.autopilot.kinds.facts import web_problems
from steward.autopilot.kinds.news import story_problems
from steward.config.jobs import JobSchedule
from steward.config.store import save_config
from steward.config.thesis import ThesesConfig, ThesisConfig
from steward.engine import change, overrides, timeline
from steward.notify.channels import EmailChannel
from steward.ops import delivery, liveness, tasks
from steward.quality import cockpit
from steward.research.gaps import SLOTS

NOW = dt.datetime.now(dt.UTC)


def theses_v(n: int) -> ThesesConfig:
    """A theses config whose body differs per n (an equal body is not stored twice)."""
    return ThesesConfig(theses=[ThesisConfig(ticker="AAA", why_owned=f"version {n}")])


def hours_ago(h: float) -> dt.datetime:
    return NOW - dt.timedelta(hours=h)


# ------------------------------------------------------------------ pure rules


def source(tier: int, outlet: str) -> Any:
    return SimpleNamespace(tier=tier, outlet=outlet, url=f"https://{outlet}")


def test_a_story_is_confirmed_only_on_two_independent_strong_sources() -> None:
    story = SimpleNamespace(
        claim=[source(1, "regulator")], supporting=[source(2, "wire")], contradicting=[]
    )
    assert story_problems(story, "confirmed") == []
    thin = SimpleNamespace(claim=[source(1, "regulator")], supporting=[], contradicting=[])
    assert "fewer than two" in story_problems(thin, "confirmed")[0]
    disputed = SimpleNamespace(
        claim=story.claim, supporting=story.supporting, contradicting=[source(2, "other")]
    )
    assert "disputes it" in story_problems(disputed, "confirmed")[0]
    assert story_problems(thin, "false") == ["no tier-1 or tier-2 source contradicts it"]
    weak = SimpleNamespace(claim=[], supporting=[], contradicting=[source(3, "blog")])
    assert story_problems(weak, "false")
    assert (
        story_problems(
            SimpleNamespace(claim=[], supporting=[], contradicting=[source(1, "regulator")]),
            "false",
        )
        == []
    )


def test_a_web_value_needs_the_tier_rule_and_no_conflicting_source() -> None:
    key = next(k for k, s in SLOTS.items() if not s.unavailable)
    assert web_problems("T1", key, [], []) == []
    assert web_problems("T1", key, [], [{"domain": "x"}]) == [
        "another source reports a different value"
    ]
    # An unknown slot can never be accepted.
    assert web_problems("T1", "no-such-slot", [], []) == ["the slot cannot be filled from the web"]


def test_every_autopilot_kind_has_a_check_or_a_written_waiver() -> None:
    check_verifiers()
    assert not set(VERIFY_WAIVERS) - set(all_kinds())  # no waiver left for a kind that is gone


def test_a_kind_with_neither_a_check_nor_a_waiver_is_refused() -> None:
    kinds = dict(all_kinds())
    kinds["fact"] = SimpleNamespace(verify=None)  # type: ignore[assignment]
    with pytest.raises(ValueError, match="no verify hook"):
        check_verifiers(kinds)


def test_job_cadence_is_read_from_its_schedule() -> None:
    assert liveness.cadence_hours(JobSchedule(cron="0 */6 * * *"), NOW) == 6
    assert liveness.cadence_hours(JobSchedule(cron="30 22 * * *"), NOW) == 24


def test_changed_subjects_name_the_items_that_differ() -> None:
    old = {"theses": [{"ticker": "A", "x": 1}, {"ticker": "B", "x": 1}]}
    new = {"theses": [{"ticker": "A", "x": 1}, {"ticker": "B", "x": 2}, {"ticker": "C"}]}
    assert overrides.changed_subjects("theses", old, new) == ["theses:B", "theses:C"]
    assert overrides.changed_subjects("jobs", {"a": 1}, {"a": 2}) == ["jobs:*"]
    assert overrides.changed_subjects("jobs", {"a": 1}, {"a": 1}) == []


def test_corrections_are_quoted_as_data_and_empty_when_there_are_none() -> None:
    assert overrides.render_lessons([], 0, "x") == ""
    text = overrides.render_lessons([("edited theses:A", "wrong_data", "old figure")], 3, "theses")
    assert "overruled theses 3 time(s)" in text and 'The owner wrote: "old figure"' in text
    assert "data, not instructions" in text


def test_the_sweep_rate_warns_only_with_enough_decisions() -> None:
    assert cockpit.sweep_problem(5, 5) is None
    assert cockpit.sweep_problem(20, 1) is None
    assert cockpit.sweep_problem(20, 4) is not None


# ------------------------------------------------------------------ database


async def decision(pool: asyncpg.Pool, ref: str = "fact:1", status: str = "applied") -> int:
    return await ledger.record(
        pool,
        kind="fact",
        subject_ref=ref,
        ticker=None,
        decision="accept",
        confidence=0.95,
        decided_by="rule:x",
        summary="s",
        evidence={"fact_id": 1},
        status=status,  # type: ignore[arg-type]
        revert_payload={"fact_id": 1} if status == "applied" else None,
    )


@pytest.mark.db
async def test_the_ledger_offers_the_undo_for_each_reversible_entry(
    backend_pool: asyncpg.Pool,
) -> None:
    did = await decision(backend_pool)
    await overrides.record(
        backend_pool,
        area="x",
        subject_kind="fact",
        subject_ref="fact:9",
        action="reverted",
        via="chat",
        reason_code="too_early",
    )
    await backend_pool.execute(
        "insert into public.alert (kind, severity, channel, message, status, sent_at)"
        " values ('test', 1, 'telegram', 'hello', 'sent', now())"
    )
    since = NOW - dt.timedelta(days=1)
    found = {e.source: e for e in await timeline.recent(backend_pool, timeline.Query(since))}
    assert found["autopilot"].undo_action == "revert_decision"
    assert found["autopilot"].undo_args == {"decision_id": did}
    assert {"override", "delivery"} <= set(found)
    assert "too early" in found["override"].summary
    one_kind = await timeline.recent(backend_pool, timeline.Query(since, kind="telegram"))
    assert [e.source for e in one_kind] == ["delivery"]
    assert await timeline.recent(backend_pool, timeline.Query(since, offset=50)) == []


@pytest.mark.db
async def test_an_engine_written_config_can_be_rolled_back_and_the_owner_edit_is_recorded(
    backend_pool: asyncpg.Pool,
) -> None:
    async with backend_pool.acquire() as conn:
        first = await save_config(conn, "theses", theses_v(1), created_by=None, note="seed")
        engine = await save_config(
            conn, "theses", theses_v(2), created_by=None, note="Automatic: tuned"
        )
    listed = await timeline.recent(backend_pool, timeline.Query(NOW - dt.timedelta(days=1)))
    mine = next(e for e in listed if e.source == "config")
    assert mine.undo_action == "rollback_config"
    assert mine.undo_args == {"domain": "theses", "version_id": first.id}

    done = await registry.execute(
        backend_pool, "rollback_config", {"domain": "theses", "version_id": first.id}
    )
    assert done.status == "done"
    row = await backend_pool.fetchrow("select * from public.owner_override")
    assert (row["area"], row["subject_kind"], row["action"]) == ("theses", "config", "reverted")
    assert engine.id  # the version that was undone


@pytest.mark.db
async def test_an_engine_change_can_be_rolled_back_from_the_registry(
    backend_pool: asyncpg.Pool,
) -> None:
    made = await change.propose(
        backend_pool, "autopilot:fact", "threshold", "h", "m", {"a": 1}, {"a": 2}
    )
    await backend_pool.execute("update public.engine_change set status = 'promoted'")
    done = await registry.execute(backend_pool, "roll_back_change", {"change_id": made.id})
    assert done.status == "done"
    assert await backend_pool.fetchval("select status from public.engine_change") == "rolled_back"


@pytest.mark.db
async def test_turning_down_a_chat_proposal_is_an_override_the_chat_learns_from(
    backend_pool: asyncpg.Pool,
) -> None:
    task = await tasks.see(backend_pool, "quality", "q", title="Stale", detail="d")
    proposal = await registry.propose(
        backend_pool, "dismiss_notice", {"task_id": task.id}, via="chat"
    )
    assert await registry.cancel(backend_pool, proposal.id, "prefer_my_judgement")
    row = await backend_pool.fetchrow("select * from public.owner_override")
    assert (row["area"], row["action"], row["reason_code"]) == (
        "chat",
        "rejected",
        "prefer_my_judgement",
    )
    text = await overrides.lessons(backend_pool, "chat", "proposals the chat made")
    assert "proposals the chat made" in text and "decide this themselves" in text


@pytest.mark.db
async def test_what_the_owner_changed_in_an_engine_version_is_pinned(
    backend_pool: asyncpg.Pool,
) -> None:
    async with backend_pool.acquire() as conn:
        await save_config(conn, "theses", ThesesConfig(), created_by=None, note="engine")
    previous = await overrides.active_version(backend_pool, "theses")
    new = {"theses": [{"ticker": "AAA", "note": "mine"}]}
    assert await overrides.record_config_edit(backend_pool, "theses", previous, new) == 1
    assert await overrides.was_overruled(backend_pool, "config", "theses:AAA")
    assert not await overrides.was_overruled(backend_pool, "config", "theses:BBB")
    # A version the owner wrote is not an engine version: editing it records nothing.
    owners = {"created_by": "u", "body": {}, "id": 1, "note": None}
    assert await overrides.record_config_edit(backend_pool, "theses", owners, new) == 0  # type: ignore[arg-type]


@pytest.mark.db
async def test_config_overrides_make_a_pattern_when_the_owner_keeps_changing_a_domain(
    backend_pool: asyncpg.Pool,
) -> None:
    async with backend_pool.acquire() as conn:
        await save_config(conn, "theses", theses_v(1), created_by=None, note="a")
        await save_config(conn, "theses", theses_v(2), created_by=None, note="b")
        await save_config(conn, "theses", theses_v(3), created_by=None, note="c")
    for i in range(4):
        await overrides.record(
            backend_pool,
            area="theses",
            subject_kind="config",
            subject_ref=f"theses:T{i}",
            action="edited",
            via="screen",
            reason_code="wrong_data",
            decided_by="engine",
        )
    found = await overrides.patterns(backend_pool, NOW)
    assert [(p.kind, p.overridden) for p in found] == [("config:theses", 4)]


@pytest.mark.db
async def test_a_dismissed_recheck_notice_is_not_raised_again_and_the_sweep_is_weekly(
    backend_pool: asyncpg.Pool,
) -> None:
    did = await decision(backend_pool, "fact:77")
    kinds = all_kinds()
    first = await reverify.sweep(backend_pool, kinds, NOW)  # nothing recorded yet: runs
    assert first["checked"] == 1
    assert await reverify.sweep(backend_pool, kinds, NOW + dt.timedelta(days=1)) == {"skipped": 1}
    assert not await reverify.due(backend_pool, NOW + dt.timedelta(days=1))
    assert await reverify.due(backend_pool, NOW + dt.timedelta(days=8))
    history = await reverify.history(backend_pool)
    assert [(h.checked, h.flagged) for h in history] == [(first["checked"], first["flagged"])]
    # a dismissed notice for this decision keeps it out of the next sweep
    await tasks.see(
        backend_pool,
        "reverify",
        f"auto_decision:{did}",
        title="t",
        detail="d",
        status="needs_owner",
    )
    task = (await tasks.needs_owner(backend_pool))[0]
    await tasks.dismiss(backend_pool, task.id)
    again = await reverify.sweep(backend_pool, kinds, NOW + dt.timedelta(days=8))
    assert again["checked"] == 0


def email_channel(events: dict[str, str]) -> EmailChannel:
    def handler(request: httpx.Request) -> httpx.Response:
        mail_id = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={"id": mail_id, "last_event": events[mail_id]})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return EmailChannel(http, "key", "from@x.test", "to@x.test")


class Spy:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, subject: str, text: str) -> str | None:
        self.sent.append((subject, text))
        return "1"


@pytest.mark.db
async def test_a_bounced_email_is_a_delivery_problem_and_another_channel_is_told_once(
    backend_pool: asyncpg.Pool,
) -> None:
    await backend_pool.execute(
        "insert into public.alert (kind, severity, channel, message, status, sent_at, provider_id)"
        " values ('test', 3, 'email', 'm', 'sent', now(), 'em-1')"
    )
    spy = Spy()
    channels: dict[str, Any] = {"email": email_channel({"em-1": "bounced"}), "telegram": spy}
    found = await delivery.check(backend_pool, NOW, channels)
    assert found == {"email": "1 bounced or were marked as spam"}
    assert await backend_pool.fetchval("select provider_status from public.alert") == "bounced"
    assert len(spy.sent) == 1 and "email" in spy.sent[0][0]
    await delivery.check(backend_pool, NOW, channels)
    assert len(spy.sent) == 1  # the notice was already open: no second message


@pytest.mark.db
async def test_a_failed_digest_delivery_is_counted(backend_pool: asyncpg.Pool) -> None:
    await backend_pool.execute(
        "insert into public.report (type, period, title, body, body_md, delivery)"
        " values ('digest', 'morning', 't', '{}'::jsonb, 'x', $1::jsonb)",
        json.dumps({"telegram": {"status": "failed", "error": "HTTP 500", "at": "now"}}),
    )
    assert await delivery.check(backend_pool, NOW) == {"telegram": "1 failed to send"}


@pytest.mark.db
async def test_every_scheduled_job_is_watched_not_only_the_autopilot_and_the_watchdog(
    backend_pool: asyncpg.Pool,
) -> None:
    jobs = SimpleNamespace(
        worker_schedules=lambda: {
            "hourly_thing": JobSchedule(cron="0 * * * *"),
            "off_thing": JobSchedule(cron="0 * * * *", enabled=False),
            "watchdog": JobSchedule(cron="0 * * * *"),
        }
    )
    await backend_pool.execute(
        "insert into public.job (job_type, trigger, status, created_at, finished_at)"
        " values ('hourly_thing', 'schedule', 'succeeded', $1, $1)",
        hours_ago(30),
    )
    late = await liveness.check_all(backend_pool, jobs, NOW)  # type: ignore[arg-type]
    assert set(late) == {"hourly_thing"}
    notice = (await tasks.needs_owner(backend_pool))[0]
    assert notice.subject == "hourly_thing" and "hourly_thing" in notice.title
    jobs.worker_schedules = lambda: {}  # type: ignore[method-assign]
    assert await liveness.check_all(backend_pool, jobs, NOW) == {}  # type: ignore[arg-type]
    assert await tasks.needs_owner(backend_pool) == []


@pytest.mark.db
async def test_the_cockpit_reports_kinds_applying_without_a_check_and_a_bad_recheck_rate(
    backend_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(VERIFY_WAIVERS, "listing", "test")
    await backend_pool.execute(
        "insert into public.auto_decision (kind, subject_ref, decision, decided_by, summary,"
        " status) values ('listing', 'listing:A', 'confirm', 'rule:x', 's', 'applied')"
    )
    await backend_pool.execute(
        "insert into public.engine_sweep (name, checked, flagged) values ('reverify', 20, 6)"
    )
    issues = await cockpit._engine_verification(backend_pool)
    subjects = {i.subject for i in issues}
    assert subjects == {"weekly re-check", "unchecked kinds"}
