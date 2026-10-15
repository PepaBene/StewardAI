"""What is being collected for fine-tuning (data plan 1.3): calls and labels, by kind."""

import asyncpg
from pydantic import BaseModel

DAYS = 30


class CallCount(BaseModel):
    job_type: str
    calls: int
    last_30_days: int


class LabelCount(BaseModel):
    subject_kind: str
    right: int
    wrong: int
    corrected: int


class TrainingSummary(BaseModel):
    calls: int
    last_30_days: int
    size_mb: float
    by_job_type: list[CallCount]
    labels: int
    owner_labels: int
    by_subject: list[LabelCount]


async def summary(pool: asyncpg.Pool) -> TrainingSummary:
    calls = await pool.fetch(
        "select job_type, count(*) as calls,"
        " count(*) filter (where created_at >= now() - make_interval(days => $1)) as recent"
        " from public.llm_call group by job_type order by calls desc",
        DAYS,
    )
    size = await pool.fetchval("select pg_total_relation_size('public.llm_call')")
    labelled = await pool.fetch(
        "select subject_kind, count(*) filter (where label = 'right') as right,"
        " count(*) filter (where label = 'wrong') as wrong,"
        " count(*) filter (where label = 'corrected') as corrected"
        " from public.training_label group by subject_kind order by count(*) desc"
    )
    owner = await pool.fetchval("select count(*) from public.training_label where by_owner")
    return TrainingSummary(
        calls=sum(r["calls"] for r in calls),
        last_30_days=sum(r["recent"] for r in calls),
        size_mb=round(float(size or 0) / (1024 * 1024), 1),
        by_job_type=[
            CallCount(job_type=r["job_type"], calls=r["calls"], last_30_days=r["recent"])
            for r in calls
        ],
        labels=sum(r["right"] + r["wrong"] + r["corrected"] for r in labelled),
        owner_labels=int(owner or 0),
        by_subject=[
            LabelCount(
                subject_kind=r["subject_kind"],
                right=r["right"],
                wrong=r["wrong"],
                corrected=r["corrected"],
            )
            for r in labelled
        ],
    )
