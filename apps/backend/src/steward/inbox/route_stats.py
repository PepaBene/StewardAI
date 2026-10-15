"""Cost and latency per route (agentic workflows and RAG plan, AG5).

A route is a kind of work the assistant does: `chat`, `research:<profile>`, `inbox_agent`,
`event_escalation`, ... (the job type, plus the profile for research runs). Figures come from
the job log: cost is the sum of the job's step costs, latency is started to finished. A job
whose steps carry no price (a model with no price table) counts in `unpriced_runs` and adds
nothing to the cost, so the cost column is a floor, not an estimate.
"""

import asyncpg
from pydantic import BaseModel, Field


class RouteStat(BaseModel):
    route: str
    runs: int
    failed: int
    unpriced_runs: int = Field(description="Runs with steps that carry no cost.")
    cost_total_usd: float
    cost_median_usd: float | None
    cost_p95_usd: float | None
    seconds_median: float | None
    seconds_p95: float | None
    queue_seconds_median: float | None = Field(
        default=None, description="Median wait between a job being created and starting."
    )


async def route_stats(pool: asyncpg.Pool, days: int, ai_only: bool = True) -> list[RouteStat]:
    rows = await pool.fetch(
        """
        with runs as (
          select j.id,
                 j.job_type || case when j.job_type = 'research' and j.params ? 'profile'
                                    then ':' || (j.params ->> 'profile') else '' end as route,
                 j.status,
                 extract(epoch from (j.finished_at - j.started_at)) as seconds,
                 extract(epoch from (j.started_at - j.created_at)) as queue_seconds,
                 coalesce(sum(s.cost_usd), 0)::float8 as cost,
                 bool_or(s.model is not null) as used_model,
                 bool_or(s.model is not null and s.cost_usd is null) as unpriced
            from public.job j left join public.job_step s on s.job_id = j.id
           where j.created_at > now() - make_interval(days => $1)
             and j.status in ('succeeded', 'failed')
           group by j.id
        )
        select route,
               count(*) as runs,
               count(*) filter (where status = 'failed') as failed,
               count(*) filter (where unpriced) as unpriced_runs,
               sum(cost)::float8 as cost_total,
               (percentile_cont(0.5) within group (order by cost))::float8 as cost_median,
               (percentile_cont(0.95) within group (order by cost))::float8 as cost_p95,
               (percentile_cont(0.5) within group (order by seconds))::float8 as sec_median,
               (percentile_cont(0.95) within group (order by seconds))::float8 as sec_p95,
               (percentile_cont(0.5) within group (order by queue_seconds))::float8 as queue_median
          from runs
         where ($2::boolean is false or used_model)
         group by route order by sum(cost) desc, route
        """,
        days,
        ai_only,
    )
    return [
        RouteStat(
            route=r["route"],
            runs=r["runs"],
            failed=r["failed"],
            unpriced_runs=r["unpriced_runs"],
            cost_total_usd=round(r["cost_total"] or 0.0, 4),
            cost_median_usd=_round(r["cost_median"], 4),
            cost_p95_usd=_round(r["cost_p95"], 4),
            seconds_median=_round(r["sec_median"], 1),
            seconds_p95=_round(r["sec_p95"], 1),
            queue_seconds_median=_round(r["queue_median"], 1),
        )
        for r in rows
    ]


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)
