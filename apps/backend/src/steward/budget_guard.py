"""The Budget config's spend limits, checked before every AI request of a job.

`steward.ai.create_client` returns a client whose every Messages request (single and batch)
first asks `before_request` here, for the job running in the current task
(`steward.jobs.runner.current_job`; requests outside a job, such as the eval scripts, are not
limited). The check is one small query per request:

- Month-to-date spend is the sum of `job_step.cost_usd` of steps started in the current
  calendar month in Europe/Prague (the app's timezone, the one the owner reads dates in),
  plus what this run has spent on requests whose step has not finished yet (priced from each
  response's usage, as the step will be).
- A run whose own spend has reached its cap (`per_job_cap_usd`, else `default_job_cap_usd`)
  fails before its next request.
- At the monthly cap, a job type not in `critical_job_types` fails before its next request
  (`pause_noncritical`) or has the request sent to `downgrade_model`
  (`downgrade_noncritical`); `alert_only` only alerts.
- Passing `warn_at_fraction` of the cap, and reaching the cap, each raise one system alert
  per month (dedupe keys `budget:warn:YYYY-MM` and `budget:cap:YYYY-MM`).

The limit is soft by up to one request per concurrent run: a request is checked before it is
sent, and its cost is only known after.
"""

import datetime as dt
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg

from steward.config.budget import BudgetConfig
from steward.config.notifications import NotificationsConfig
from steward.config.store import load_active
from steward.costs import PriceTable
from steward.jobs.runner import JobContext, JobFailed
from steward.notify.alerts import create_alerts
from steward.notify.route import AlertRequest

logger = logging.getLogger(__name__)

BUDGET_TZ = ZoneInfo("Europe/Prague")


class BudgetExceeded(JobFailed):
    """A request refused by the Budget config's caps. It is not an `anthropic.APIError`, so
    call sites that retry or skip on API errors let it fail the step and the job."""


def month_of(moment: dt.datetime) -> str:
    """The budget month (YYYY-MM, Europe/Prague) of a moment."""
    return moment.astimezone(BUDGET_TZ).strftime("%Y-%m")


def month_bounds(month: str) -> tuple[dt.datetime, dt.datetime]:
    """[start, end) of budget month `month` (YYYY-MM) as aware datetimes."""
    year, mon = (int(part) for part in month.split("-"))
    start = dt.datetime(year, mon, 1, tzinfo=BUDGET_TZ)
    end = dt.datetime(year + mon // 12, mon % 12 + 1, 1, tzinfo=BUDGET_TZ)
    return start, end


async def month_spend(conn: asyncpg.Connection | asyncpg.Pool, month: str) -> Decimal:
    """The recorded AI spend of budget month `month`: the sum of its steps' `cost_usd`."""
    start, end = month_bounds(month)
    total: Decimal = await conn.fetchval(
        "select coalesce(sum(cost_usd), 0) from public.job_step"
        " where cost_usd is not null and started_at >= $1 and started_at < $2",
        start,
        end,
    )
    return total


@dataclass
class RunBudget:
    """The guard's state for one run (kept on `JobContext.budget`)."""

    config: BudgetConfig
    prices: PriceTable
    # This job's recorded spend when the run made its first request (a resumed job's
    # earlier attempts), and what its responses have cost since.
    recorded_at_start: Decimal | None = None
    tracked: Decimal = Decimal(0)
    alerts_checked: set[str] = field(default_factory=set)
    # Requested model -> the model the guard sent it as (read by the runner, which records
    # and prices the step as the model actually used).
    downgraded: dict[str, str] = field(default_factory=dict)

    def run_spend(self, recorded: Decimal) -> Decimal:
        """This run's spend: its finished steps' costs, or what its responses cost, whichever
        is more (a response's cost reaches `job_step` only when its step finishes; a batch's
        cost only then)."""
        start = self.recorded_at_start if self.recorded_at_start is not None else recorded
        return max(recorded, start + self.tracked)


async def _state(ctx: JobContext) -> RunBudget:
    if ctx.budget is None:
        prices = await ctx.price_table()
        ctx.budget = RunBudget(config=prices.config, prices=prices)
    state: RunBudget = ctx.budget
    return state


def _usd(value: Decimal | float) -> str:
    return f"${Decimal(str(value)).quantize(Decimal('0.01'))}"


async def before_request(ctx: JobContext, model: str, *, now: dt.datetime | None = None) -> str:
    """The model to send a request of `ctx`'s job with (`model`, or the downgrade model at
    the monthly cap); raises `BudgetExceeded` when the request must not be sent."""
    state = await _state(ctx)
    cfg = state.config
    now = now or dt.datetime.now(dt.UTC)
    month = month_of(now)
    start, end = month_bounds(month)
    row = await ctx.pool.fetchrow(
        "select coalesce(sum(cost_usd) filter (where job_id <> $1), 0) as others,"
        " coalesce(sum(cost_usd) filter (where job_id = $1), 0) as this_job"
        " from public.job_step where cost_usd is not null"
        " and (job_id = $1 or (started_at >= $2 and started_at < $3))",
        ctx.job_id,
        start,
        end,
    )
    others: Decimal = row["others"]
    recorded: Decimal = row["this_job"]
    if state.recorded_at_start is None:
        state.recorded_at_start = recorded
    spent = state.run_spend(recorded)

    job_cap = cfg.job_cap(ctx.job_type)
    if job_cap is not None and spent >= Decimal(str(job_cap)):
        raise BudgetExceeded(
            f"AI budget of one {ctx.job_type} run reached ({_usd(spent)} of {_usd(job_cap)});"
            " the run is stopped"
        )
    cap = cfg.monthly_cap_usd
    if cap is None:
        return model
    total = others + spent
    await _maybe_alert(ctx, state, month, total, now)
    if total < Decimal(str(cap)) or cfg.at_cap == "alert_only":
        return model
    if ctx.job_type in cfg.critical_job_types:
        return model
    if cfg.at_cap == "pause_noncritical":
        raise BudgetExceeded(
            f"monthly AI budget reached ({_usd(total)} of {_usd(cap)}); non-critical jobs paused"
        )
    if model != cfg.downgrade_model:
        state.downgraded[model] = cfg.downgrade_model
    return cfg.downgrade_model


def after_response(ctx: JobContext, model: str, usage: Any, *, batch: bool = False) -> None:
    """Add a response's cost (from its `usage` block) to this run's tracked spend."""
    state: RunBudget | None = ctx.budget
    if state is None or usage is None:
        return
    server = getattr(usage, "server_tool_use", None)
    cost = state.prices.cost(
        model,
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        searches=(getattr(server, "web_search_requests", 0) or 0) if server else 0,
        batch=batch,
    )
    if cost is not None:
        state.tracked += cost


async def _maybe_alert(
    ctx: JobContext, state: RunBudget, month: str, total: Decimal, now: dt.datetime
) -> None:
    """One system alert per month when spend passes the warning fraction, one when it
    reaches the cap. An alert failure never fails the job."""
    cfg = state.config
    cap = cfg.monthly_cap_usd
    if cap is None:
        return
    cap_d = Decimal(str(cap))
    if total >= cap_d:
        kind = "cap"
        at_cap = {
            "alert_only": "jobs keep running (alert only)",
            "pause_noncritical": "non-critical jobs are paused",
            "downgrade_noncritical": f"non-critical jobs now use {cfg.downgrade_model}",
        }[cfg.at_cap]
        text = f"monthly AI budget reached: {_usd(total)} of {_usd(cap)} in {month}; {at_cap}."
    elif total >= cap_d * Decimal(str(cfg.warn_at_fraction)):
        kind = "warn"
        text = (
            f"monthly AI spend passed {round(cfg.warn_at_fraction * 100)} % of the budget:"
            f" {_usd(total)} of {_usd(cap)} in {month}."
        )
    else:
        return
    key = f"budget:{kind}:{month}"
    if key in state.alerts_checked:
        return
    state.alerts_checked.add(key)
    try:
        await raise_budget_alert(ctx.pool, key, text, now=now, job_id=ctx.job_id)
    except Exception:
        logger.exception("could not raise the budget alert %s", key)


async def raise_budget_alert(
    pool: asyncpg.Pool, key: str, text: str, *, now: dt.datetime, job_id: int | None
) -> bool:
    """Create the system alert `key` unless it exists; True when created."""
    if await pool.fetchval("select exists (select 1 from public.alert where dedupe_key = $1)", key):
        return False
    stored = await load_active(pool, "notifications", NotificationsConfig)
    notify = stored.body if stored else NotificationsConfig()
    severity = notify.system_alert_severity
    if severity == 0:
        return False
    await create_alerts(
        pool,
        AlertRequest("system", severity, None, "budget"),
        f"[Severity {severity}] StewardAI budget: {text}",
        notify,
        now=now,
        config_version_id=stored.id if stored else None,
        job_id=job_id,
        dedupe_key=key,
    )
    return True
