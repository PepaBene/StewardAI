"""The owner pilot (M8.3): a fixed window in which use, false alerts, source failures, data
coverage and owner corrections are counted from what the app already records.

Every figure is a count with its denominator. Time to answer a research question is not measured:
the app cannot see how long the owner spent, so the report says so instead of showing a guess."""

from __future__ import annotations

import datetime as dt

import asyncpg
from pydantic import BaseModel, Field

from steward.quality import snooze
from steward.quality.cockpit import QualityIssue, build_cockpit

DEFAULT_DAYS = 30
FAILED_STATES = ("temporarily_failed", "quota_exhausted", "access_unverified", "disputed")


class PilotWindow(BaseModel):
    id: int
    started_on: dt.date
    length_days: int
    ends_on: dt.date
    day: int = Field(description="Day of the pilot today, 1-based; past the length when over.")
    over: bool


class PathUse(BaseModel):
    path: str
    opens: int
    days: int = Field(description="Distinct days the page was opened.")


class AlertRate(BaseModel):
    sent: int
    rated: int = Field(description="Sent alerts the owner marked.")
    wrong_or_irrelevant: int
    false_rate: float | None = Field(
        description="Wrong or irrelevant over rated alerts; None when none were rated."
    )


class ProviderFailure(BaseModel):
    provider: str
    attempts: int
    failures: int


class JobFailures(BaseModel):
    runs: int
    failed: int


class Correction(BaseModel):
    target_kind: str
    count: int


class PilotReport(BaseModel):
    window: PilotWindow
    use: list[PathUse]
    days_used: int
    alerts: AlertRate
    providers: list[ProviderFailure]
    jobs: JobFailures
    corrections: list[Correction]
    affected_weight: float = Field(
        description="Today's share of the portfolio touched by an issue."
    )
    top_issues: list[QualityIssue]
    not_measured: list[str]


NOT_MEASURED = [
    "Time to answer a research question: the app cannot see how long you spent. "
    "Note it by hand beside each question you ask.",
    "Coverage and stale or unknown weight are today's figures, not an average over the pilot.",
]


def window_of(row: asyncpg.Record, today: dt.date) -> PilotWindow:
    start: dt.date = row["started_on"]
    length = int(row["length_days"])
    day = (today - start).days + 1
    return PilotWindow(
        id=row["id"],
        started_on=start,
        length_days=length,
        ends_on=start + dt.timedelta(days=length - 1),
        day=max(day, 1),
        over=day > length,
    )


def false_rate(rated: int, bad: int) -> float | None:
    return bad / rated if rated else None


async def start(pool: asyncpg.Pool, owner_id: str, length_days: int = DEFAULT_DAYS) -> PilotWindow:
    row = await pool.fetchrow(
        "insert into public.pilot_run (owner_id, length_days) values ($1, $2)"
        " returning id, started_on, length_days",
        owner_id,
        length_days,
    )
    return window_of(row, dt.date.today())


async def current(pool: asyncpg.Pool, owner_id: str) -> PilotWindow | None:
    row = await pool.fetchrow(
        "select id, started_on, length_days from public.pilot_run"
        " where owner_id = $1 order by id desc limit 1",
        owner_id,
    )
    return window_of(row, dt.date.today()) if row else None


async def log_use(pool: asyncpg.Pool, owner_id: str, path: str) -> None:
    await pool.execute(
        "insert into public.usage_event (owner_id, path) values ($1, $2)", owner_id, path
    )


async def build_report(pool: asyncpg.Pool, owner_id: str) -> PilotReport | None:
    window = await current(pool, owner_id)
    if window is None:
        return None
    lo = dt.datetime.combine(window.started_on, dt.time.min, tzinfo=dt.UTC)
    hi = lo + dt.timedelta(days=window.length_days)
    use_rows = await pool.fetch(
        "select path, count(*) as opens, count(distinct occurred_at::date) as days"
        " from public.usage_event where owner_id = $1 and occurred_at >= $2 and occurred_at < $3"
        " group by path order by opens desc, path",
        owner_id,
        lo,
        hi,
    )
    days_used = await pool.fetchval(
        "select count(distinct occurred_at::date) from public.usage_event"
        " where owner_id = $1 and occurred_at >= $2 and occurred_at < $3",
        owner_id,
        lo,
        hi,
    )
    sent = await pool.fetchval(
        "select count(*) from public.alert where status = 'sent' and kind <> 'test'"
        " and created_at >= $1 and created_at < $2",
        lo,
        hi,
    )
    rated = await pool.fetchrow(
        "select count(*) as rated,"
        " count(*) filter (where f.rating in ('wrong', 'irrelevant')) as bad"
        " from (select distinct on (fb.target_id) fb.target_id, fb.rating"
        "       from public.feedback fb"
        # Only alerts sent in the window, the same set `sent` counts, so rated <= sent.
        "       join public.alert a on a.id = fb.target_id"
        "       where fb.target_kind = 'alert' and a.status = 'sent' and a.kind <> 'test'"
        "       and a.created_at >= $1 and a.created_at < $2"
        "       order by fb.target_id, fb.created_at desc, fb.id desc) f",
        lo,
        hi,
    )
    providers = await pool.fetch(
        "select provider, count(*) as attempts,"
        " count(*) filter (where state = any($3::text[])) as failures"
        " from public.provider_fetch_audit where attempted_at >= $1 and attempted_at < $2"
        " group by provider order by failures desc, provider",
        lo,
        hi,
        list(FAILED_STATES),
    )
    jobs = await pool.fetchrow(
        "select count(*) as runs, count(*) filter (where status = 'failed') as failed"
        " from public.job where created_at >= $1 and created_at < $2",
        lo,
        hi,
    )
    corrections = await pool.fetch(
        "select target_kind, count(*) as n from public.feedback"
        " where rating = 'wrong' and created_at >= $1 and created_at < $2"
        " group by target_kind order by n desc, target_kind",
        lo,
        hi,
    )
    # The same snoozes as the cockpit page, so the report lists what the page lists.
    cockpit = snooze.with_snoozes(
        await build_cockpit(pool), await snooze.list_rows(pool, owner_id), dt.date.today()
    )
    return PilotReport(
        window=window,
        use=[PathUse(path=r["path"], opens=r["opens"], days=r["days"]) for r in use_rows],
        days_used=int(days_used or 0),
        alerts=AlertRate(
            sent=int(sent or 0),
            rated=int(rated["rated"]),
            wrong_or_irrelevant=int(rated["bad"]),
            false_rate=false_rate(int(rated["rated"]), int(rated["bad"])),
        ),
        providers=[
            ProviderFailure(provider=r["provider"], attempts=r["attempts"], failures=r["failures"])
            for r in providers
        ],
        jobs=JobFailures(runs=int(jobs["runs"]), failed=int(jobs["failed"])),
        corrections=[Correction(target_kind=r["target_kind"], count=r["n"]) for r in corrections],
        affected_weight=cockpit.affected_weight,
        top_issues=cockpit.issues[:10],
        not_measured=NOT_MEASURED,
    )
