"""Proactive triggers, phase 4: every start leaves a reason, and is held back by a cooldown, daily
caps, a cost cap, the monthly budget, or the owner dismissing its notices. Learning triggers only
ever make the engine do less on its own."""

import datetime as dt

import asyncpg
import pytest

from steward.autopilot import ledger
from steward.engine import learning, overrides, triggers

NOW = dt.datetime.now(dt.UTC)


# ------------------------------------------------------------------ pure rules


def load(**kw: object) -> triggers.Load:
    base: dict[str, object] = {
        "kind_today": 0,
        "total_today": 0,
        "usd_today": 0.0,
        "budget_used": None,
        "dismissed": 0,
        "closed": 0,
    }
    return triggers.Load(**{**base, **kw})  # type: ignore[arg-type]


def test_a_quiet_day_holds_nothing_back() -> None:
    assert triggers.hold_back(load()) is None


@pytest.mark.parametrize(
    ("kw", "word"),
    [
        ({"kind_today": triggers.DAILY_PER_KIND}, "of this kind"),
        ({"total_today": triggers.DAILY_TOTAL}, "runs already"),
        ({"usd_today": triggers.DAILY_USD}, "cap"),
        ({"budget_used": 0.95}, "budget"),
        ({"dismissed": 3, "closed": 3}, "dismissed"),
    ],
)
def test_each_limit_holds_a_start_back_and_says_why(kw: dict[str, object], word: str) -> None:
    reason = triggers.hold_back(load(**kw))
    assert reason is not None
    assert word in reason


def test_dismissals_only_count_when_every_recent_notice_was_dismissed() -> None:
    assert triggers.is_suppressed(closed=3, dismissed=3)
    assert not triggers.is_suppressed(closed=4, dismissed=3)  # one was acted on
    assert not triggers.is_suppressed(closed=2, dismissed=2)  # too few to mean anything


# ------------------------------------------------------------------ database


class Counter:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self) -> int | None:
        self.calls += 1
        return None


async def fire(
    pool: asyncpg.Pool,
    counter: Counter,
    subject: str = "AAA",
    kind: str = "staleness",
    **kw: object,
) -> triggers.Outcome:
    return await triggers.fire(
        pool,
        kind,  # type: ignore[arg-type]
        subject,
        "because",
        "do it",
        counter,
        now=NOW,
        **kw,  # type: ignore[arg-type]
    )


async def rows(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    return list(await pool.fetch("select * from public.engine_trigger order by id"))


async def dismissed_notice(pool: asyncpg.Pool, kind: str, n: int) -> None:
    for i in range(n):
        await pool.execute(
            "insert into public.ops_task (kind, subject, title, detail, status, last_action,"
            " resolved_at) values ($1, $2, 't', 'd', 'resolved', 'dismissed', $3)",
            kind,
            f"s{i}",
            NOW - dt.timedelta(days=1),
        )


async def test_a_start_records_the_reason_it_began(backend_pool: asyncpg.Pool) -> None:
    c = Counter()
    result = await fire(backend_pool, c)
    assert result.fired
    assert c.calls == 1
    [row] = await rows(backend_pool)
    assert (row["status"], row["reason"], row["action"]) == ("fired", "because", "do it")


async def test_the_same_subject_waits_out_the_cooldown(backend_pool: asyncpg.Pool) -> None:
    c = Counter()
    await fire(backend_pool, c)
    again = await fire(backend_pool, c)
    assert (again.fired, again.skipped) == (False, "cooldown")
    assert c.calls == 1


async def test_a_kind_stops_at_its_daily_cap_and_the_hold_is_recorded_once(
    backend_pool: asyncpg.Pool,
) -> None:
    c = Counter()
    for i in range(triggers.DAILY_PER_KIND):
        assert (await fire(backend_pool, c, f"T{i}")).fired
    held = await fire(backend_pool, c, "LATE")
    await fire(backend_pool, c, "LATE")
    assert not held.fired
    skipped = [r for r in await rows(backend_pool) if r["status"] == "skipped"]
    assert len(skipped) == 1
    assert "already started today" in skipped[0]["skip_reason"]


async def test_the_total_cap_counts_runs_across_kinds(backend_pool: asyncpg.Pool) -> None:
    async def job() -> int | None:
        return int(await backend_pool.fetchval("select 1"))

    for i in range(triggers.DAILY_TOTAL):
        kind = ("staleness", "event", "coverage")[i % 3]
        await triggers.fire(backend_pool, kind, f"T{i}", "r", "a", job, now=NOW)  # type: ignore[arg-type]
    # job ids are not real here, so the table must not require them to exist
    held = await fire(backend_pool, Counter(), "ONE-MORE", kind="staleness")
    assert held.skipped is not None


async def test_an_essential_start_ignores_cooldown_and_caps(backend_pool: asyncpg.Pool) -> None:
    c = Counter()
    for _ in range(triggers.DAILY_PER_KIND + 2):
        assert (await fire(backend_pool, c, "AAA", kind="event", essential=True)).fired
    assert c.calls == triggers.DAILY_PER_KIND + 2


async def test_dismissing_every_recent_notice_pauses_the_kind_and_says_so_once(
    backend_pool: asyncpg.Pool,
) -> None:
    await dismissed_notice(backend_pool, "thesis_draft", triggers.SUPPRESS_AFTER)
    c = Counter()
    held = await fire(backend_pool, c, "AAA", kind="coverage")
    assert not held.fired
    assert c.calls == 0
    assert await triggers.suppressed(backend_pool, "coverage")
    await fire(backend_pool, c, "BBB", kind="coverage")
    paused = await backend_pool.fetch(
        "select title from public.ops_task where kind = $1", triggers.STOPPED_NOTICE
    )
    assert len(paused) == 1


async def test_a_notice_is_logged_once_per_subject_in_its_cooldown(
    backend_pool: asyncpg.Pool,
) -> None:
    assert await triggers.log(backend_pool, "anticipation", "AAA:2026-12-01", "r", "a", now=NOW)
    assert not await triggers.log(backend_pool, "anticipation", "AAA:2026-12-01", "r", "a", now=NOW)
    assert len(await triggers.recent(backend_pool)) == 1


# ------------------------------------------------------------------ learning


def pattern(kind: str) -> overrides.Pattern:
    return overrides.Pattern(kind, "model:x", 6, 10, "wrong_call")


async def test_a_kind_acting_on_its_own_that_is_overruled_goes_back_to_watching(
    backend_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake(pool: asyncpg.Pool, now: dt.datetime) -> list[overrides.Pattern]:
        return [pattern("fact")]

    monkeypatch.setattr(overrides, "patterns", fake)
    await ledger.set_mode(backend_pool, "fact", "auto", "test")
    out = await learning.run(
        backend_pool,
        {"fact": "auto"},
        promote_min_reviewed=10,
        promote_precision=0.9,
        owner_decides=set(),
        now=NOW,
    )
    assert out["to_shadow"] == ["fact"]
    mode = await backend_pool.fetchval(
        "select mode from public.autopilot_kind_state where kind = 'fact'"
    )
    assert mode == "shadow"
    assert await backend_pool.fetchval(
        "select count(*) from public.ops_task where kind = $1", learning.NOTICE
    )


async def test_a_watching_kind_that_only_the_owner_may_promote_gets_one_notice(
    backend_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def none(pool: asyncpg.Pool, now: dt.datetime) -> list[overrides.Pattern]:
        return []

    monkeypatch.setattr(overrides, "patterns", none)
    for i in range(12):
        await backend_pool.execute(
            "insert into public.auto_decision (kind, subject_ref, decision, confidence,"
            " decided_by, summary, evidence, status, review, decided_at) values ('listing', $1,"
            " 'accept', 0.9, 'model:x', 's', '{}'::jsonb, 'shadow', 'ok', $2)",
            f"l{i}",
            NOW - dt.timedelta(days=2),
        )
    args = dict(promote_min_reviewed=10, promote_precision=0.9, owner_decides={"listing"}, now=NOW)
    first = await learning.run(backend_pool, {"listing": "shadow"}, **args)  # type: ignore[arg-type]
    second = await learning.run(backend_pool, {"listing": "shadow"}, **args)  # type: ignore[arg-type]
    assert first["ready_for_owner"] == ["listing"]
    assert second["ready_for_owner"] == []
