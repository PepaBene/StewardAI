"""AI spend against the Budget config: one budget month's cost by job type and by model.

Every figure is a sum of `job_step.cost_usd` computed in Postgres, so the month's total is
exactly the sum of its steps' costs (and of the rows by job type, and of those by model).
The budget month is the calendar month in Europe/Prague, as the budget guard counts it. The
Budget config itself is read and saved through `/config/budget` in `api.routes`.
"""

import datetime as dt
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from api.auth import Owner
from api.db import Pool
from steward.budget_guard import month_bounds, month_of
from steward.config.budget import AtCap, BudgetConfig
from steward.config.store import load_active
from steward.llm.types import provider_of

router = APIRouter()

MonthParam = Annotated[
    str | None,
    Query(
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
        description="Budget month YYYY-MM (Europe/Prague); default the current one.",
        examples=["2026-09"],
    ),
]


class JobTypeCost(BaseModel):
    job_type: str
    runs: int = Field(description="Runs with at least one costed step in the month.")
    cost_usd: float
    job_cap_usd: float | None = Field(description="The cap of one run of this type, if any.")
    critical: bool = Field(description="Keeps running at the monthly cap.")
    cache_hit_rate: float | None = Field(
        default=None,
        description="Cache-read tokens / all input tokens of the month's steps; a silent drop"
        " means a prompt prefix stopped being stable. Null: no input tokens.",
    )


class ModelCost(BaseModel):
    model: str
    steps: int
    cost_usd: float
    provider: str = Field(
        default="unknown", description="anthropic, openai or google, from the model id."
    )


class CostSummary(BaseModel):
    month: str
    period_start: dt.datetime = Field(
        description="Start of the budget month (midnight on the 1st, Europe/Prague), inclusive."
    )
    period_end: dt.datetime = Field(
        description="End of the budget month (midnight on the 1st of the next), exclusive."
    )
    total_usd: float = Field(description="Sum of `job_step.cost_usd` of steps started in month.")
    cap_usd: float | None
    fraction: float | None = Field(description="total / cap; null without a cap.")
    warn_at_fraction: float
    at_cap: AtCap
    by_job_type: list[JobTypeCost]
    by_model: list[ModelCost]


def _f(value: Decimal) -> float:
    return float(value)


def _provider(model: str) -> str:
    try:
        return provider_of(model)
    except ValueError:
        return "unknown"


@router.get("/costs/summary", tags=["costs"])
async def cost_summary(pool: Pool, _owner: Owner, month: MonthParam = None) -> CostSummary:
    """A month's AI spend against the Budget config's monthly cap, by job type and by
    model."""
    month = month or month_of(dt.datetime.now(dt.UTC))
    start, end = month_bounds(month)
    stored = await load_active(pool, "budget", BudgetConfig)
    cfg = stored.body if stored else BudgetConfig()
    in_month = (
        "from public.job_step s join public.job j on j.id = s.job_id"
        " where s.cost_usd is not null and s.started_at >= $1 and s.started_at < $2"
    )
    total: Decimal = await pool.fetchval(
        f"select coalesce(sum(s.cost_usd), 0) {in_month}", start, end
    )
    by_type = await pool.fetch(
        "select j.job_type, count(distinct s.job_id) as runs, sum(s.cost_usd) as cost,"
        " sum(s.cache_read_tokens) as cache_read,"
        " sum(coalesce(s.input_tokens, 0) + coalesce(s.cache_read_tokens, 0)"
        f" + coalesce(s.cache_write_tokens, 0)) as all_input {in_month}"
        " group by j.job_type order by cost desc, j.job_type",
        start,
        end,
    )
    by_model = await pool.fetch(
        f"select coalesce(s.model, 'unknown') as model, count(*) as steps,"
        f" sum(s.cost_usd) as cost {in_month} group by 1 order by cost desc, 1",
        start,
        end,
    )
    cap = cfg.monthly_cap_usd
    return CostSummary(
        month=month,
        period_start=start,
        period_end=end,
        total_usd=_f(total),
        cap_usd=cap,
        fraction=float(total / Decimal(str(cap))) if cap else None,
        warn_at_fraction=cfg.warn_at_fraction,
        at_cap=cfg.at_cap,
        by_job_type=[
            JobTypeCost(
                job_type=r["job_type"],
                runs=r["runs"],
                cost_usd=_f(r["cost"]),
                job_cap_usd=cfg.job_cap(r["job_type"]),
                critical=r["job_type"] in cfg.critical_job_types,
                cache_hit_rate=(
                    float(r["cache_read"] or 0) / float(r["all_input"]) if r["all_input"] else None
                ),
            )
            for r in by_type
        ],
        by_model=[
            ModelCost(
                model=r["model"],
                steps=r["steps"],
                cost_usd=_f(r["cost"]),
                provider=_provider(r["model"]),
            )
            for r in by_model
        ],
    )
