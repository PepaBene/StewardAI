"""Owner-only read models for provider access, usage, and data coverage."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

from api.auth import Owner
from api.db import Pool

router = APIRouter()


class SourceAccess(BaseModel):
    provider: str
    endpoint: str
    account_tier: str
    state: str
    quota: Decimal | None
    quota_period: str | None
    reset_timezone: str | None
    request_weight: Decimal
    tested_at: datetime | None
    retry_at: datetime | None
    last_success_at: datetime | None
    hard_blocked: bool
    detail: str | None
    last_denial_at: datetime | None
    last_denial_state: str | None


class SourceUsage(BaseModel):
    provider: str
    endpoint: str
    window_kind: str
    window_start: datetime
    window_end: datetime
    credits: Decimal
    requests: int


class DataCoverage(BaseModel):
    provider: str
    dataset: str
    entity_key: str
    state: str
    expected_periods: int | None
    available_periods: int | None
    earliest_period: date | None
    latest_period: date | None
    last_good_observation_id: int | None
    checked_at: datetime
    reason: str | None


@router.get("/sources/access", response_model=list[SourceAccess], tags=["sources"])
async def get_source_access(pool: Pool, _owner: Owner) -> list[SourceAccess]:
    rows = await pool.fetch(
        """select a.provider,a.endpoint,a.account_tier,a.state,a.quota,a.quota_period,
                  a.reset_timezone,a.request_weight,a.tested_at,a.retry_at,a.last_success_at,
                  a.hard_blocked,a.detail,d.attempted_at last_denial_at,d.state last_denial_state
           from public.provider_access a
           left join lateral (
             select attempted_at,state from public.provider_fetch_audit f
             where f.provider=a.provider and f.endpoint=a.endpoint
               and f.state in ('paid_only','access_unverified','quota_exhausted')
             order by attempted_at desc limit 1
           ) d on true
           order by a.provider,a.endpoint"""
    )
    return [SourceAccess(**dict(row)) for row in rows]


@router.get("/sources/usage", response_model=list[SourceUsage], tags=["sources"])
async def get_source_usage(
    pool: Pool,
    _owner: Owner,
    since: Annotated[datetime | None, Query()] = None,
) -> list[SourceUsage]:
    rows = await pool.fetch(
        """select provider,endpoint,window_kind,window_start,window_end,credits,requests
           from public.provider_quota_usage
           where ($1::timestamptz is null or window_start >= $1)
           order by window_start desc,provider,endpoint""",
        since,
    )
    return [SourceUsage(**dict(row)) for row in rows]


@router.get("/data/coverage", response_model=list[DataCoverage], tags=["data"])
async def get_data_coverage(
    pool: Pool,
    _owner: Owner,
    provider: str | None = None,
    dataset: str | None = None,
    entity_key: str | None = None,
) -> list[DataCoverage]:
    rows = await pool.fetch(
        """select provider,dataset,entity_key,state,expected_periods,available_periods,
                  earliest_period,latest_period,last_good_observation_id,checked_at,reason
           from public.coverage_status
           where ($1::text is null or provider=$1)
             and ($2::text is null or dataset=$2)
             and ($3::text is null or entity_key=$3)
           order by provider,dataset,entity_key""",
        provider,
        dataset,
        entity_key,
    )
    return [DataCoverage(**dict(row)) for row in rows]
