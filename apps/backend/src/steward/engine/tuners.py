"""The engine's own tuning of rules, sources, schedules and models, each tested before it applies
(engine plan, L5). All four are one edit of one config field (`engine/config_change.py`), so a
promoted change is an ordinary config version with no author, and the owner's rollback of that
version is the undo.

- rule: an alert's severity in `event_rules`; replay the instant alerts of the last 60 days and
  count how many were acknowledged.
- source: a news source's `enabled` flag; replay its items of the last 60 days and count how many
  the owner opened or marked useful.
- schedule: the news ingest cadence in `jobs`; replay when the last 14 days' items would have been
  picked up at each cadence.
- model: the model of news classification or the digest; run the labelled set / stored windows on
  both models (the only one that calls a model).

The gates are written when the change is proposed (`Gates`), and pure code applies them. Rollback
for schedule and model is automatic (failed runs after the change); rule and source changes stay
until the owner undoes them, which the notice explains.
"""

import datetime as dt
import logging
import re
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg

from steward.config.budget import BudgetConfig
from steward.config.news_sources import NewsSourcesConfig
from steward.config.store import load_active
from steward.engine import change, config_change
from steward.engine.change import Gates, Metrics
from steward.ops import tasks

logger = logging.getLogger("steward.engine")

KIND = "engine_change"
COOLDOWN_DAYS = 30
ROLLBACK_AFTER_DAYS = 14
ROLLBACK_UNTIL_DAYS = 28
PRAGUE = ZoneInfo("Europe/Prague")


async def _recent_change(pool: asyncpg.Pool, target: str, now: dt.datetime) -> bool:
    """True while the field is cooling down after a change, or is blocked after two roll-backs."""
    if await config_change.blocked(pool, target, now):
        return True
    return bool(
        await pool.fetchval(
            "select exists (select 1 from public.engine_change where target = $1"
            " and created_at >= $2)",
            target,
            now - dt.timedelta(days=COOLDOWN_DAYS),
        )
    )


async def _tell(pool: asyncpg.Pool, c: change.Change, title: str, detail: str, href: str) -> None:
    await tasks.see(
        pool,
        KIND,
        f"{c.id}:{c.status}",
        title=title,
        detail=detail,
        href=href,
        weight=0.3,
        status="needs_owner",
    )


# ------------------------------------------------------------------------------------ sources

SOURCE_WINDOW_DAYS = 60
SOURCE_MIN_ITEMS = 40
SOURCE_GATES = Gates(
    min_n=SOURCE_MIN_ITEMS,
    min_reviewed=SOURCE_MIN_ITEMS,
    max_wrong_rate_increase=0.0,
    max_cost_increase=1.0,
    max_escalation_drop=1.0,
    require_golden=False,
)


@dataclass(frozen=True)
class SourceUse:
    source_id: str
    items: int
    used: int  # items of stories the owner opened or marked useful
    unique: int  # items of stories no other source carried


def unused(row: SourceUse) -> bool:
    return row.items >= SOURCE_MIN_ITEMS and row.used == 0


async def source_use(
    pool: asyncpg.Pool, ids: list[str], since: dt.datetime
) -> dict[str, SourceUse]:
    rows = await pool.fetch(
        "select i.source_id, count(*) as items,"
        " count(*) filter (where exists (select 1 from public.engagement_event e"
        "   where e.kind = 'news_story' and e.ref = i.cluster_id::text and e.created_at >= $2)"
        "  or exists (select 1 from public.news_feedback f where f.kind = 'useful'"
        "   and ((f.target = 'story' and f.target_id = i.cluster_id::text)"
        "    or (f.target = 'item' and f.target_id = i.id::text)))) as used,"
        " count(*) filter (where not exists (select 1 from public.news_item o"
        "   where o.cluster_id = i.cluster_id and o.source_id <> i.source_id)) as uniq"
        " from public.news_item i where i.source_id = any($1::text[]) and i.first_seen_at >= $2"
        " group by i.source_id",
        ids,
        since,
    )
    return {
        r["source_id"]: SourceUse(r["source_id"], r["items"], r["used"], r["uniq"]) for r in rows
    }


async def tune_sources(pool: asyncpg.Pool, now: dt.datetime) -> dict[str, Any]:
    """Switch off one second- or third-tier feed none of whose items the owner used in 60 days."""
    stored = await load_active(pool, "news_sources", NewsSourcesConfig)
    config = stored.body if stored else NewsSourcesConfig()
    eligible = [
        s
        for s in config.sources
        if s.enabled and s.tier >= 2 and s.type in ("rss", "page") and not s.tickers and not s.macro
    ]
    use = await source_use(
        pool, [s.id for s in eligible], now - dt.timedelta(days=SOURCE_WINDOW_DAYS)
    )
    for s in eligible:
        row = use.get(s.id)
        edit = config_change.Edit("news_sources", f"sources[id={s.id}].enabled")
        if row is None or not unused(row) or await _recent_change(pool, edit.target, now):
            continue
        c = await config_change.propose(
            pool,
            "source_weight",
            edit,
            False,
            f"Switch off {s.name}: none of its {row.items} items in {SOURCE_WINDOW_DAYS} days "
            f"was opened or marked useful ({row.unique} carried by no other source)",
            "items you used",
            SOURCE_GATES,
        )
        baseline = Metrics(row.items, row.items, 0, 0, 0, 1.0)
        candidate = Metrics(row.items, row.items, row.used, 0, 0, 0.0)
        await change.record_results(pool, c.id, baseline, candidate)
        settled = await config_change.promote(pool, c, golden_passed=None)
        if settled.status == "promoted":
            await _tell(
                pool,
                settled,
                f"{s.name} is switched off",
                f"{settled.hypothesis}. Switch it back on in the news sources config, or undo "
                "the change from the engine ledger.",
                "/engine-ledger",
            )
            return {"switched_off": s.id}
        return {"rejected": s.id}
    return {"switched_off": None}


LOW_USE = 0.05  # a feed whose items you used this rarely stops carrying alerts on its own
TIER_GATES = Gates(
    min_n=SOURCE_MIN_ITEMS,
    min_reviewed=SOURCE_MIN_ITEMS,
    max_wrong_rate_increase=LOW_USE,
    max_cost_increase=1.0,
    max_escalation_drop=1.0,
    require_golden=False,
)


def barely_used(row: SourceUse) -> bool:
    """Used a little, but under 5% of its items: too rare to keep, too real to switch off."""
    return row.items >= SOURCE_MIN_ITEMS and 0 < row.used <= row.items * LOW_USE


async def tune_source_tiers(pool: asyncpg.Pool, now: dt.datetime) -> dict[str, Any]:
    """Move one second-tier feed to tier 3 (labelled, never alerts alone) when you used under 5%
    of its items in 60 days. Only ever downward; a feed nobody used is switched off instead."""
    stored = await load_active(pool, "news_sources", NewsSourcesConfig)
    config = stored.body if stored else NewsSourcesConfig()
    eligible = [
        s
        for s in config.sources
        if s.enabled and s.tier == 2 and s.type in ("rss", "page") and not s.tickers and not s.macro
    ]
    use = await source_use(
        pool, [s.id for s in eligible], now - dt.timedelta(days=SOURCE_WINDOW_DAYS)
    )
    for s in eligible:
        row = use.get(s.id)
        edit = config_change.Edit("news_sources", f"sources[id={s.id}].tier")
        if row is None or not barely_used(row) or await _recent_change(pool, edit.target, now):
            continue
        c = await config_change.propose(
            pool,
            "source_weight",
            edit,
            3,
            f"Move {s.name} to tier 3: you used {row.used} of its {row.items} items in "
            f"{SOURCE_WINDOW_DAYS} days, so it should no longer alert on its own",
            "share of its items you used",
            TIER_GATES,
        )
        baseline = Metrics(row.items, row.items, 0, 0, 0, 1.0)
        candidate = Metrics(row.items, row.items, row.used, 0, 0, 1.0)
        await change.record_results(pool, c.id, baseline, candidate)
        settled = await config_change.promote(pool, c, golden_passed=None)
        if settled.status == "promoted":
            await _tell(
                pool,
                settled,
                f"{s.name} now counts as tier 3",
                f"{settled.hypothesis}. Set it back in the news sources config, or undo the "
                "change from the engine ledger.",
                "/engine-ledger",
            )
            return {"demoted": s.id}
        return {"rejected": s.id}
    return {"demoted": None}


# ---------------------------------------------------------------------------------- schedules

NEWS_JOB = "news_ingest"
NEWS_PATH = "news_ingest.cron"
FIRST_HOUR, LAST_HOUR = 6, 22
LATE_HOURS = 3.0
SCHEDULE_DAYS = 14
SCHEDULE_MIN_ITEMS = 100
SPEED_UP_AT = 0.25  # more than this share of items waits over LATE_HOURS: run more often
SLOW_DOWN_AT = 0.05
SLOW_DOWN_LIMIT = 0.10  # ... and the slower cadence may leave at most this share late
CADENCES = (1, 2, 3, 4)
_CRON = re.compile(rf"^0 {FIRST_HOUR}-{LAST_HOUR}(?:/(\d))? \* \* \*$")
SCHEDULE_GATES = Gates(
    min_n=SCHEDULE_MIN_ITEMS,
    min_reviewed=SCHEDULE_MIN_ITEMS,
    max_wrong_rate_increase=0.05,
    max_cost_increase=1.0,
    max_escalation_drop=1.0,
    require_golden=False,
)


def cadence_of(cron: str) -> int | None:
    """Hours between ingest runs when the cron is of the supported form, else None."""
    m = _CRON.match(cron.strip())
    if m is None:
        return None
    n = int(m.group(1) or 1)
    return n if n in CADENCES else None


def cron_for(hours: int) -> str:
    return f"0 {FIRST_HOUR}-{LAST_HOUR}/{hours} * * *"


def slots(hours: int) -> list[int]:
    return list(range(FIRST_HOUR, LAST_HOUR + 1, hours))


def wait_hours(published: dt.datetime, hours: int) -> float:
    """How long after publication the next run at this cadence picks the item up."""
    local = published.astimezone(PRAGUE)
    at = local.hour + local.minute / 60
    later = [s for s in slots(hours) if s >= at]
    if later:
        return later[0] - at
    return 24 - at + slots(hours)[0]


def late_items(published: list[dt.datetime], hours: int) -> int:
    return sum(1 for p in published if wait_hours(p, hours) > LATE_HOURS)


def runs_per_day(hours: int) -> int:
    return len(slots(hours))


async def tune_schedule(pool: asyncpg.Pool, now: dt.datetime) -> dict[str, Any]:
    """Change the news ingest cadence by one step when the last two weeks say it is too slow for
    the stories it carries, or needlessly fast."""
    edit = config_change.Edit("jobs", NEWS_PATH)
    if await _recent_change(pool, edit.target, now):
        return {"schedule": "changed recently"}
    hours = cadence_of(str(await config_change.current(pool, edit)))
    if hours is None:
        return {"schedule": "not a cadence the engine tunes"}
    rows = await pool.fetch(
        "select published_at from public.news_item where first_seen_at >= $1 and tier <= 2",
        now - dt.timedelta(days=SCHEDULE_DAYS),
    )
    published = [r["published_at"] for r in rows]
    if len(published) < SCHEDULE_MIN_ITEMS:
        return {"schedule": f"only {len(published)} items to go on"}
    share = late_items(published, hours) / len(published)
    target = None
    if share > SPEED_UP_AT and hours > CADENCES[0]:
        faster = hours - 1
        if share - late_items(published, faster) / len(published) >= 0.10:
            target = faster
    elif share < SLOW_DOWN_AT and hours < CADENCES[-1]:
        slower = hours + 1
        if late_items(published, slower) / len(published) <= SLOW_DOWN_LIMIT:
            target = slower
    if target is None:
        return {"schedule": "unchanged"}
    n = len(published)
    verb = "faster" if target < hours else "slower"
    c = await config_change.propose(
        pool,
        "schedule",
        edit,
        cron_for(target),
        f"Run the news ingest every {target} h instead of {hours} h ({verb}): {share:.0%} of "
        f"stories from the last {SCHEDULE_DAYS} days waited over {LATE_HOURS:g} h to be picked up",
        f"share of stories picked up within {LATE_HOURS:g} hours",
        SCHEDULE_GATES,
    )
    baseline = Metrics(n, n, late_items(published, hours), 0, 0, float(runs_per_day(hours)))
    candidate = Metrics(n, n, late_items(published, target), 0, 0, float(runs_per_day(target)))
    await change.record_results(pool, c.id, baseline, candidate)
    settled = await config_change.promote(pool, c, golden_passed=None)
    if settled.status == "promoted":
        await _tell(
            pool,
            settled,
            f"News is now fetched every {target} hours",
            f"{settled.hypothesis}. It is rolled back by itself if runs start failing; you can "
            "also undo it from the engine ledger.",
            "/engine-ledger",
        )
    return {"schedule": settled.status, "hours": target}


# ------------------------------------------------------------------------------- daily times
# A job that runs once a day should have finished before the owner's first visit. Config name ->
# job type; the digest may not move before the first news ingest of the day.
DAILY_JOBS = {"portfolio_sync": "portfolio_sync", "daily_digest": "daily_digest"}
DAILY_EARLIEST = 6 * 60 + 30  # minutes after midnight, Prague
DAILY_DAYS = 21
DAILY_MIN_DAYS = 8
DAILY_LEAD = 45  # minutes the job should be done before the median first visit
DAILY_MIN_SHIFT = 15
DAILY_GATES = Gates(
    min_n=DAILY_MIN_DAYS,
    min_reviewed=DAILY_MIN_DAYS,
    max_wrong_rate_increase=-0.1,  # at least a tenth of the days must stop being late
    max_cost_increase=1.0,
    max_escalation_drop=1.0,
    require_golden=False,
)
_DAILY = re.compile(r"^(\d{1,2}) (\d{1,2}) (\* \* (?:\*|[0-6](?:-[0-6])?))$")


def parse_daily(cron: str) -> tuple[int, str] | None:
    """(minute of day, the day-of-month/month/weekday part) of a once-a-day cron, else None."""
    m = _DAILY.match(cron.strip())
    if m is None or int(m.group(1)) > 59 or int(m.group(2)) > 23:
        return None
    return int(m.group(2)) * 60 + int(m.group(1)), m.group(3)


def daily_cron(minute_of_day: int, tail: str) -> str:
    return f"{minute_of_day % 60} {minute_of_day // 60} {tail}"


def median_of(values: list[int]) -> float:
    ordered = sorted(values)
    half = len(ordered) // 2
    return float(ordered[half]) if len(ordered) % 2 else (ordered[half - 1] + ordered[half]) / 2


def better_minute(first_opens: list[int], duration: int, current: int) -> int | None:
    """When to start so the job is done `DAILY_LEAD` minutes before the median first visit; None
    when that is not at least `DAILY_MIN_SHIFT` minutes earlier than now."""
    target = int(median_of(first_opens)) - DAILY_LEAD - duration
    target = max(DAILY_EARLIEST, target - target % 5)
    return target if target <= current - DAILY_MIN_SHIFT else None


def late_days(first_opens: dict[dt.date, int], finishes: dict[dt.date, int]) -> int:
    """Days on which the job finished after the owner's first visit."""
    return sum(1 for d, opened in first_opens.items() if d in finishes and finishes[d] > opened)


def _minute(ts: dt.datetime) -> int:
    local = ts.astimezone(PRAGUE)
    return local.hour * 60 + local.minute


async def tune_daily_times(pool: asyncpg.Pool, now: dt.datetime) -> dict[str, Any]:
    """Move a daily job earlier when it keeps finishing after the owner has already looked."""
    since = now - dt.timedelta(days=DAILY_DAYS)
    opens: dict[dt.date, int] = {}
    for r in await pool.fetch(
        "select occurred_at from public.usage_event where occurred_at >= $1 order by occurred_at",
        since,
    ):
        local = r["occurred_at"].astimezone(PRAGUE)
        opens.setdefault(local.date(), local.hour * 60 + local.minute)
    if len(opens) < DAILY_MIN_DAYS:
        return {"daily": f"only {len(opens)} days of use to go on"}
    for name, job_type in DAILY_JOBS.items():
        edit = config_change.Edit("jobs", f"{name}.cron")
        if await _recent_change(pool, edit.target, now):
            continue
        current = str(await config_change.current(pool, edit))
        parsed = parse_daily(current)
        if parsed is None:
            continue
        minute, tail = parsed
        finishes: dict[dt.date, int] = {}
        durations: list[int] = []
        for r in await pool.fetch(
            "select started_at, finished_at from public.job where job_type = $1"
            " and status = 'succeeded' and finished_at >= $2 and started_at is not null"
            " order by finished_at",
            job_type,
            since,
        ):
            day = r["finished_at"].astimezone(PRAGUE).date()
            finishes.setdefault(day, _minute(r["finished_at"]))
            durations.append(
                max(1, round((r["finished_at"] - r["started_at"]).total_seconds() / 60))
            )
        days = [d for d in opens if d in finishes]
        if len(days) < DAILY_MIN_DAYS:
            continue
        new_minute = better_minute([opens[d] for d in days], int(median_of(durations)), minute)
        if new_minute is None:
            continue
        shift = minute - new_minute
        # What the finish times would have been had the job started `shift` minutes earlier.
        moved = {d: finishes[d] - shift for d in days}
        before = late_days({d: opens[d] for d in days}, finishes)
        after = late_days({d: opens[d] for d in days}, moved)
        c = await config_change.propose(
            pool,
            "schedule",
            edit,
            daily_cron(new_minute, tail),
            f"Run {name.replace('_', ' ')} at {new_minute // 60:02d}:{new_minute % 60:02d} instead "
            f"of {minute // 60:02d}:{minute % 60:02d}: it finished after your first visit on "
            f"{before} of {len(days)} days",
            "days on which the job finished before your first visit",
            DAILY_GATES,
        )
        await change.record_results(
            pool,
            c.id,
            Metrics(len(days), len(days), before, 0, 0, 0.0),
            Metrics(len(days), len(days), after, 0, 0, 0.0),
        )
        settled = await config_change.promote(pool, c, golden_passed=None)
        if settled.status == "promoted":
            await _tell(
                pool,
                settled,
                f"{name.replace('_', ' ').capitalize()} now runs earlier",
                f"{settled.hypothesis}. It is rolled back by itself if runs start failing; you "
                "can also undo it from the engine ledger.",
                "/engine-ledger",
            )
        return {"daily": name, "status": settled.status}
    return {"daily": None}


# -------------------------------------------------------------------------------------- models

MODEL_FIELDS: dict[str, tuple[str, ...]] = {
    # field -> the job types whose health shows a problem with it
    "news.classification_model": ("news_ingest",),
    "digest.model": ("daily_digest", "weekly_digest"),
    "thesis_settings.model": ("thesis",),
    "chat.model": (),  # chat is not a job: no health to watch, so no automatic put-back
}
# Calls to reserve from the run's budget for one candidate test (both models, all cases).
CALLS_NEEDED = {
    "news.classification_model": 40,
    "digest.model": 40,
    "thesis_settings.model": 30,
    "chat.model": 70,
}
CHEAPER_THAN = 0.7  # a candidate must cost at most this share of the model it replaces
CLASSIFICATION_DROP = 0.01  # accuracy points it may lose
MODEL_GATES = Gates(
    min_n=1,
    min_reviewed=20,
    max_wrong_rate_increase=CLASSIFICATION_DROP,
    max_cost_increase=0.0,
    max_escalation_drop=1.0,
    require_golden=False,
)


def price_of(table: BudgetConfig, model: str) -> float | None:
    """Per million tokens, input counted once and output three times (a rough mix)."""
    p = next((x for x in table.prices if x.model == model), None)
    return None if p is None else p.input_per_mtok + 3 * p.output_per_mtok


def cheaper_than(table: BudgetConfig, model: str, usable: set[str]) -> str | None:
    """The best candidate to replace `model`: the dearest model that costs at most CHEAPER_THAN of
    it, so the step down is the smallest that saves real money."""
    now = price_of(table, model)
    if now is None:
        return None
    options = [
        (price, p.model)
        for p in table.prices
        if p.model in usable
        and (price := price_of(table, p.model)) is not None
        and price <= now * CHEAPER_THAN
    ]
    return max(options)[1] if options else None


TOLERANCE = {
    "news.classification_model": CLASSIFICATION_DROP,
    "digest.model": 0.03,
    "thesis_settings.model": 0.0,
    "chat.model": 0.05,
}


async def _arm(
    pool: asyncpg.Pool, client: Any, field: str, model: str, prices: Any
) -> Metrics | None:
    """One model's numbers on the area's own test set, or None when it has no cases."""
    from steward.ai import Usage
    from steward.engine.evaluators import (
        ChatEvaluator,
        DigestEvaluator,
        NewsEvaluator,
        ThesisEvaluator,
    )

    usage = Usage(model=model)
    checked = bad = 0
    if field == "news.classification_model":
        ev = NewsEvaluator()
        golden = [c for c in await ev.cases(pool) if c.kind == "golden"]
        if not golden:
            return None
        out = await ev.run(pool, client, golden[0], "", usage, model=model)
        checked, bad = out.checked, out.bad
    else:
        dev: Any = {
            "digest.model": DigestEvaluator(),
            "thesis_settings.model": ThesisEvaluator(),
            "chat.model": ChatEvaluator(),
        }[field]
        cases = (await dev.cases(pool))[: 2 if field == "digest.model" else dev.max_cases]
        if not cases:
            return None
        for case in cases:
            out = await dev.run(pool, client, case, "", usage, model=model)
            checked += out.checked
            bad += out.bad
        if checked == 0:
            return None
    cost = prices.cost(model, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)
    return Metrics(1, checked, bad, 0, 0, float(cost) if cost is not None else 0.0)


async def tune_models(
    pool: asyncpg.Pool, client: Any, now: dt.datetime, budget: dict[str, int]
) -> dict[str, Any]:
    """Try a cheaper model for one task. It is used only when it is as good on the task's own
    test set and costs less on it."""
    from steward.costs import PriceTable

    stored = await load_active(pool, "budget", BudgetConfig)
    table = stored.body if stored else BudgetConfig()
    prices = PriceTable(table)
    usable = {p.model for p in table.prices if client.llm.configured(p.model)}
    for field in MODEL_FIELDS:
        edit = config_change.Edit("jobs", field)
        if await _recent_change(pool, edit.target, now):
            continue
        current = str(await config_change.current(pool, edit))
        candidate = cheaper_than(table, current, usable)
        if candidate is None or budget["calls"] < CALLS_NEEDED[field]:
            continue
        budget["calls"] -= CALLS_NEEDED[field]
        base = await _arm(pool, client, field, current, prices)
        cand = await _arm(pool, client, field, candidate, prices) if base else None
        if base is None or cand is None:
            continue
        gates = Gates(**{**MODEL_GATES.__dict__, "max_wrong_rate_increase": TOLERANCE[field]})
        c = await config_change.propose(
            pool,
            "model",
            edit,
            candidate,
            f"Use {candidate} instead of {current} for {field.split('.')[0]}: it costs "
            f"{(price_of(table, candidate) or 0) / (price_of(table, current) or 1):.0%} as much",
            "the task's own test set, and what it costs",
            gates,
        )
        await change.record_results(pool, c.id, base, cand)
        settled = await config_change.promote(pool, c, golden_passed=None)
        if settled.status == "promoted":
            await _tell(
                pool,
                settled,
                f"{candidate} now does {field.split('.')[0]}",
                f"{settled.hypothesis}. On the test set it got {cand.wrong} of {cand.reviewed} "
                f"wrong against {base.wrong} of {base.reviewed}. It is put back by itself if "
                "runs start failing; you can also undo it from the engine ledger.",
                "/engine-ledger",
            )
        return {"model": field, "candidate": candidate, "status": settled.status}
    return {"model": None}


async def health(
    pool: asyncpg.Pool, job_types: tuple[str, ...], start: dt.datetime, end: dt.datetime
) -> Metrics:
    """How the jobs ran in a window: n runs, `verifier_failures` the failed ones."""
    row = await pool.fetchrow(
        "select count(*) as n, count(*) filter (where status = 'failed') as failed"
        " from public.job where job_type = any($1::text[]) and created_at >= $2"
        " and created_at < $3 and status in ('succeeded', 'failed')",
        list(job_types),
        start,
        end,
    )
    return Metrics(row["n"], row["n"], 0, 0, row["failed"], 0.0)


WANTED_MARKS = 2  # stories you marked useful that a lowered alert level no longer sends at once
MISSING_MARKS = 2  # "a source is missing" marks after a feed was switched off or demoted
ENGAGEMENT_DROP = 0.5  # stories you opened or marked useful, after against before
ENGAGEMENT_MIN = 6  # ...and only judged when there were at least this many before


def rule_harm(wanted: int) -> list[str]:
    """Why a lowered alert level should go back: you marked stories of that type useful that it
    no longer sends at once."""
    if wanted < WANTED_MARKS:
        return []
    return [f"you marked {wanted} stories of this type useful since it stopped being instant"]


def source_harm(missing: int, unique_before: int, used_before: int, used_after: int) -> list[str]:
    """Why a switched-off or demoted feed should come back: you reported a missing source, or
    the feed carried stories nobody else did and what you open or mark useful fell by half."""
    reasons = []
    if missing >= MISSING_MARKS:
        reasons.append(f"you reported a missing source {missing} times since the change")
    if (
        unique_before > 0
        and used_before >= ENGAGEMENT_MIN
        and used_after <= used_before * ENGAGEMENT_DROP
    ):
        reasons.append(
            f"it carried {unique_before} stories no other feed did, and the stories you opened "
            f"or marked useful fell from {used_before} to {used_after}"
        )
    return reasons


async def _wanted_stories(
    pool: asyncpg.Pool, event_type: str, level: int, since: dt.datetime
) -> int:
    """Stories of one event type now at the lowered level that you marked useful."""
    return int(
        await pool.fetchval(
            "select count(distinct c.id) from public.story_cluster c"
            " join public.news_feedback f on f.target = 'story' and f.target_id = c.id::text"
            " and f.kind = 'useful' where c.event_type = $1 and c.severity = $2"
            " and c.first_seen >= $3 and f.created_at >= $3",
            event_type,
            level,
            since,
        )
        or 0
    )


async def _used_stories(pool: asyncpg.Pool, start: dt.datetime, end: dt.datetime) -> int:
    """Stories you opened or marked useful in the window."""
    used = await pool.fetchval(
        "select count(*) from (select e.ref from public.engagement_event e"
        " where e.kind = 'news_story' and e.created_at >= $1 and e.created_at < $2"
        " union select f.target_id from public.news_feedback f where f.target = 'story'"
        " and f.kind = 'useful' and f.created_at >= $1 and f.created_at < $2) u",
        start,
        end,
    )
    return int(used or 0)


async def _unique_stories(
    pool: asyncpg.Pool, source_id: str, start: dt.datetime, end: dt.datetime
) -> int:
    return int(
        await pool.fetchval(
            "select count(distinct i.cluster_id) from public.news_item i"
            " where i.source_id = $1 and i.first_seen_at >= $2 and i.first_seen_at < $3"
            " and not exists (select 1 from public.news_item o"
            "  where o.cluster_id = i.cluster_id and o.source_id <> i.source_id)",
            source_id,
            start,
            end,
        )
        or 0
    )


async def _content_reasons(
    pool: asyncpg.Pool, c: change.Change, decided: dt.datetime, now: dt.datetime
) -> list[str]:
    """The harm signal for a rule or source change: what you did with the news afterwards."""
    edit = config_change.parse_target(c.target)
    if c.change_kind == "rule":
        event_type = edit.path.split(".", 1)[-1]
        old = c.before.get("value") if isinstance(c.before, dict) else None
        new = c.after.get("value") if isinstance(c.after, dict) else None
        if not isinstance(old, int) or not isinstance(new, int) or new >= old:
            return []
        return rule_harm(await _wanted_stories(pool, event_type, new, decided))
    match = re.fullmatch(r"sources\[id=([^\]]+)\]\.(enabled|tier)", edit.path)
    if match is None:
        return []
    window = dt.timedelta(days=ROLLBACK_AFTER_DAYS)
    missing = await pool.fetchval(
        "select count(*) from public.news_feedback where kind = 'missing_source'"
        " and created_at >= $1",
        decided,
    )
    used_before = await _used_stories(pool, decided - window, decided)
    used_after = await _used_stories(pool, decided, now)
    unique = await _unique_stories(pool, match.group(1), decided - window, decided)
    return source_harm(int(missing or 0), unique, used_before, used_after)


async def check_rollbacks(pool: asyncpg.Pool, now: dt.datetime) -> dict[str, int]:
    """Undo a promoted change that did harm in its first two weeks: a schedule or model change
    whose jobs ran worse than the two weeks before, a lowered alert level for a type of story
    you then marked useful, a feed switched off or demoted that you then missed."""
    rows = await pool.fetch(
        "select " + change.COLUMNS + ", decided_at from public.engine_change"  # noqa: S608
        " where status = 'promoted'"
        " and change_kind in ('schedule', 'model', 'rule', 'source_weight')"
        " and target like 'config:%' and decided_at <= $1 and decided_at > $2",
        now - dt.timedelta(days=ROLLBACK_AFTER_DAYS),
        now - dt.timedelta(days=ROLLBACK_UNTIL_DAYS),
    )
    rolled = 0
    for rec in rows:
        c = change.from_row(rec)
        edit = config_change.parse_target(c.target)
        decided = rec["decided_at"]
        if c.change_kind in ("rule", "source_weight"):
            reasons = await _content_reasons(pool, c, decided, now)
            if not reasons or not await config_change.undo(pool, c, reasons):
                continue
            rolled += 1
            await _tell(
                pool,
                c.model_copy(update={"status": "rolled_back"}),
                "A change the engine made to itself was put back",
                f"{c.hypothesis}. " + "; ".join(reasons) + ".",
                "/engine-ledger",
            )
            continue
        jobs = (
            (edit.path.split(".")[0],)
            if c.change_kind == "schedule"
            else MODEL_FIELDS.get(edit.path, ())
        )
        if not jobs:
            continue
        window = dt.timedelta(days=ROLLBACK_AFTER_DAYS)
        before = await health(pool, jobs, decided - window, decided)
        after = await health(pool, jobs, decided, now)
        gates = Gates(**{**c.gates, "max_wrong_rate_increase": 0.0, "min_reviewed": 3})
        reasons = change.rollback_due(before, after, gates)
        if not reasons or not await config_change.undo(pool, c, reasons):
            continue
        rolled += 1
        await _tell(
            pool,
            c.model_copy(update={"status": "rolled_back"}),
            "A change the engine made to itself was put back",
            f"{c.hypothesis}. " + "; ".join(reasons) + ".",
            "/engine-ledger",
        )
    return {"rolled_back": rolled}
