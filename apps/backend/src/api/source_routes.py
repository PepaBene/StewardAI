"""Owner-only read models for provider access, usage, and data coverage.

Nothing here returns a credential, a credential variable value or a raw provider payload:
free-text fields (provider detail, denial text, coverage reasons) pass through `redact`, and
only typed columns are selected.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from api.auth import Owner
from api.db import Pool
from steward.config.jobs import COVERAGE_PROVIDER_JOB, JobsConfig, coverage_stale_after_hours
from steward.config.store import load_active
from steward.geo_codes import has_iso_columns
from steward.redact import redact

router = APIRouter()

DENIAL_STATES = ("paid_only", "access_unverified", "quota_exhausted")
USAGE_WINDOWS = ("minute", "day", "month")


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
    last_denial_status_code: int | None = None
    last_denial_detail: str | None = None
    quota_window_start: datetime | None = Field(
        default=None, description="Start of the current usage window; null if none is recorded."
    )
    quota_used: Decimal | None = Field(
        default=None,
        description="Credits spent in the current window (the denominator is `quota`). Null "
        "when the period is not tracked per window or no quota is known.",
    )
    quota_remaining: Decimal | None = Field(
        default=None, description="quota - quota_used, never below zero; null when unknown."
    )
    quota_headroom: Decimal | None = Field(
        default=None, description="quota_remaining / quota in 0..1; null when unknown."
    )
    headroom_reason: str | None = Field(
        default=None, description="Why headroom is null (no quota, untracked period, ...)."
    )
    freshness: Literal["never_succeeded", "recent", "stale"]
    last_success_age_seconds: int | None = None
    stale_after_hours: int = 72


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
    entity_kind: Literal["security", "geography", "other"] = "other"
    metric: str | None = None
    source: str | None = None
    coverage_ratio: Decimal | None = Field(
        default=None,
        description="available_periods / expected_periods; null unless both are known and the "
        "expected count is positive.",
    )
    age_days: int | None = Field(
        default=None, description="Days from latest_period to today; null without a period."
    )
    stale_after_hours: int | None = Field(
        default=None,
        description="Hours after `checked_at` at which the row counts as stale: twice the "
        "longest gap between scheduled runs of the job that refreshes this provider "
        "(config/jobs.py, see COVERAGE_PROVIDER_JOB). Null when no enabled job is known to "
        "refresh the provider.",
    )
    stale: bool | None = Field(
        default=None,
        description="True when `checked_at` is older than `stale_after_hours`; null (unknown, "
        "not false) when there is no expected interval.",
    )


class CoverageGroup(BaseModel):
    group_by: str
    key: str | None
    entries: int
    by_state: dict[str, int]
    expected_periods: int | None = Field(
        description="Sum of known expected periods; null if no entry states one."
    )
    available_periods: int | None = Field(
        description="Sum of available periods over the entries that state an expected count, "
        "so it is comparable with expected_periods; null if there is none."
    )
    entries_with_expected: int = Field(
        description="Entries that state an expected-period count (the ratio's denominator)."
    )
    available_entries: int
    coverage_ratio: Decimal | None = Field(
        default=None,
        description="available_periods / expected_periods over the entries that state both; "
        "null unless the expected total is positive. Entries without an expected count are "
        "in neither term.",
    )


class CoverageSummary(BaseModel):
    group_by: str
    total_entries: int
    groups: list[CoverageGroup]
    note: str = (
        "Counts are of stored coverage-ledger entries, not of the global universe. An entity "
        "with no ledger entry is unknown, not covered or uncovered."
    )


def entity_kind_sql(has_iso: bool) -> str:
    # Comtrade reporters are M49 numbers; `reporter_iso` holds the ISO code a geography key uses.
    reporter = "t.reporter=c.entity_key" + (" or t.reporter_iso=c.entity_key" if has_iso else "")
    return f"""
case when exists(select 1 from public.instrument i
                  where i.t212_ticker=c.entity_key or i.price_symbol=c.entity_key)
       or exists(select 1 from public.security_listing l where l.ticker=c.entity_key)
     then 'security'
     when exists(select 1 from public.macro_observation m where m.geography=c.entity_key)
       or exists(select 1 from public.trade_observation t where {reporter})
     then 'geography'
     else 'other' end
"""  # noqa: S608 - constant SQL


def coverage_base(has_iso: bool) -> str:
    kind = entity_kind_sql(has_iso).replace("c.entity_key", "s.entity_key")
    return f"""
with c as (
  select s.provider,s.dataset,s.entity_key,s.state,s.expected_periods,s.available_periods,
         s.earliest_period,s.latest_period,s.last_good_observation_id,s.checked_at,s.reason,
         {kind} as entity_kind
  from public.coverage_status s
  where ($1::text is null or s.provider=$1)
    and ($2::text is null or s.dataset=$2)
    and ($3::text is null or s.entity_key=$3)
    and ($4::text is null or s.state=$4)
    and ($5::date is null or ((s.earliest_period is null or s.earliest_period<=$5)
         and (s.latest_period is null or s.latest_period>=$5)))
)
"""  # noqa: S608 - constant SQL, values bound as parameters


def _filters(
    provider: str | None,
    dataset: str | None,
    entity_key: str | None,
    state: str | None,
    period: date | None,
) -> tuple[object, ...]:
    return provider, dataset, entity_key, state, period


@router.get("/sources/access", response_model=list[SourceAccess], tags=["sources"])
async def get_source_access(
    pool: Pool,
    _owner: Owner,
    provider: str | None = None,
    endpoint: str | None = None,
    state: str | None = None,
    denied_only: Annotated[
        bool, Query(description="Only endpoints with a recorded denial.")
    ] = False,
    stale_after_hours: Annotated[int, Query(ge=1, le=24 * 365)] = 72,
) -> list[SourceAccess]:
    rows = await pool.fetch(
        """select a.provider,a.endpoint,a.account_tier,a.state,a.quota,a.quota_period,
                  a.reset_timezone,a.request_weight,a.tested_at,a.retry_at,a.last_success_at,
                  a.hard_blocked,a.detail,d.attempted_at last_denial_at,d.state last_denial_state,
                  d.status_code last_denial_status_code,d.error_redacted last_denial_detail,
                  u.window_start quota_window_start,u.credits quota_used,
                  extract(epoch from now()-a.last_success_at)::bigint last_success_age_seconds
           from public.provider_access a
           left join lateral (
             select attempted_at,state,status_code,error_redacted
             from public.provider_fetch_audit f
             where f.provider=a.provider and f.endpoint=a.endpoint
               and f.state in ('paid_only','access_unverified','quota_exhausted')
             order by attempted_at desc limit 1
           ) d on true
           left join lateral (
             select window_start,credits from public.provider_quota_usage q
             where q.provider=a.provider and q.endpoint=a.endpoint
               and q.window_kind=a.quota_period
               and q.window_start<=now() and q.window_end>now()
             order by window_start desc limit 1
           ) u on true
           where ($1::text is null or a.provider=$1)
             and ($2::text is null or a.endpoint=$2)
             and ($3::text is null or a.state=$3)
             and (not $4::boolean or d.attempted_at is not null)
           order by a.provider,a.endpoint""",
        provider,
        endpoint,
        state,
        denied_only,
    )
    out: list[SourceAccess] = []
    for row in rows:
        item = dict(row)
        quota: Decimal | None = item["quota"]
        used: Decimal | None = item["quota_used"]
        period: str | None = item["quota_period"]
        reason: str | None = None
        remaining: Decimal | None = None
        headroom: Decimal | None = None
        if quota is None or quota <= 0:
            reason = "no quota is recorded for this endpoint"
            used = None
        elif period not in USAGE_WINDOWS:
            reason = f"usage is not tracked per {period or 'unspecified'} window"
            used = None
        else:
            # No ledger row for the current window means nothing was spent in it.
            used = used if used is not None else Decimal(0)
            remaining = max(quota - used, Decimal(0))
            headroom = remaining / quota
        age: int | None = item.pop("last_success_age_seconds")
        if age is None:
            freshness: Literal["never_succeeded", "recent", "stale"] = "never_succeeded"
        elif age > stale_after_hours * 3600:
            freshness = "stale"
        else:
            freshness = "recent"
        item.update(
            detail=redact(item["detail"]),
            last_denial_detail=redact(item["last_denial_detail"]),
            quota_used=used,
            quota_remaining=remaining,
            quota_headroom=headroom,
            headroom_reason=reason,
            freshness=freshness,
            last_success_age_seconds=age,
            stale_after_hours=stale_after_hours,
        )
        out.append(SourceAccess(**item))
    return out


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
    provider: Annotated[str | None, Query(description="Source/provider.")] = None,
    dataset: Annotated[str | None, Query(description="Dataset or metric/series id.")] = None,
    entity_key: Annotated[str | None, Query(description="Security or geography key.")] = None,
    state: str | None = None,
    period: Annotated[date | None, Query(description="Entries whose span contains it.")] = None,
    entity_kind: Literal["security", "geography", "other"] | None = None,
) -> list[DataCoverage]:
    sql = (
        coverage_base(await has_iso_columns(pool))  # noqa: S608 - constant SQL, values bound
        + """select * from c where ($6::text is null or entity_kind=$6)
             order by provider,dataset,entity_key"""
    )
    rows = await pool.fetch(
        sql,
        *_filters(provider, dataset, entity_key, state, period),
        entity_kind,
    )
    today = date.today()
    now = datetime.now(UTC)
    stored = await load_active(pool, "jobs", JobsConfig)
    jobs = stored.body if stored else JobsConfig()
    after_by_provider = {p: coverage_stale_after_hours(jobs, p) for p in COVERAGE_PROVIDER_JOB}
    out: list[DataCoverage] = []
    for row in rows:
        item = dict(row)
        stale_after = after_by_provider.get(item["provider"])
        checked: datetime = item["checked_at"]
        expected, available = item["expected_periods"], item["available_periods"]
        latest: date | None = item["latest_period"]
        out.append(
            DataCoverage(
                **{**item, "reason": redact(item["reason"])},
                metric=item["dataset"],
                source=item["provider"],
                coverage_ratio=(
                    Decimal(available) / Decimal(expected)
                    if expected and expected > 0 and available is not None
                    else None
                ),
                age_days=(today - latest).days if latest else None,
                stale_after_hours=stale_after,
                stale=(
                    (now - checked).total_seconds() > stale_after * 3600
                    if stale_after is not None
                    else None
                ),
            )
        )
    return out


SUMMARY_SQL = """select __KEY__ as key,state,count(*)::int n,
  sum(expected_periods)::int expected,
  sum(available_periods) filter(where expected_periods is not null)::int available,
  count(*) filter(where expected_periods is not null)::int with_expected
  from c group by 1,2 order by 1,2"""
GROUP_KEYS = {
    "security": "case when entity_kind='security' then entity_key end",
    "geography": "case when entity_kind='geography' then entity_key end",
    "metric": "dataset",
    "source": "provider",
    "period": "latest_period::text",
    "state": "state",
}


@router.get("/data/coverage/summary", response_model=CoverageSummary, tags=["data"])
async def get_data_coverage_summary(
    pool: Pool,
    _owner: Owner,
    group_by: Literal["security", "geography", "metric", "period", "source", "state"],
    provider: str | None = None,
    dataset: str | None = None,
    entity_key: str | None = None,
    state: str | None = None,
    period: date | None = None,
    entity_kind: Annotated[
        Literal["security", "geography", "other"] | None,
        Query(description="Same meaning as on GET /data/coverage."),
    ] = None,
) -> CoverageSummary:
    sql = coverage_base(await has_iso_columns(pool)) + SUMMARY_SQL.replace(
        "__KEY__", GROUP_KEYS[group_by]
    ).replace("from c group", "from c where ($6::text is null or entity_kind=$6) group")
    rows = await pool.fetch(
        sql,
        *_filters(provider, dataset, entity_key, state, period),
        entity_kind,
    )
    groups: dict[str | None, CoverageGroup] = {}
    for row in rows:
        group = groups.setdefault(
            row["key"],
            CoverageGroup(
                group_by=group_by,
                key=row["key"],
                entries=0,
                by_state={},
                expected_periods=None,
                available_periods=None,
                entries_with_expected=0,
                available_entries=0,
            ),
        )
        group.entries += row["n"]
        group.by_state[row["state"]] = row["n"]
        group.entries_with_expected += row["with_expected"]
        if row["state"] == "available":
            group.available_entries += row["n"]
        if row["expected"] is not None:
            group.expected_periods = (group.expected_periods or 0) + row["expected"]
        if row["available"] is not None:
            group.available_periods = (group.available_periods or 0) + row["available"]
    for group in groups.values():
        if group.expected_periods and group.expected_periods > 0:
            group.coverage_ratio = Decimal(group.available_periods or 0) / Decimal(
                group.expected_periods
            )
    ordered = sorted(groups.values(), key=lambda g: (g.key is None, g.key or ""))
    return CoverageSummary(
        group_by=group_by, total_entries=sum(g.entries for g in ordered), groups=ordered
    )
