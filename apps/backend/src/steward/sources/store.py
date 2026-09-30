"""Persistence boundary for governed provider results and point-in-time observations."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal

import asyncpg

from steward.sources.base import AccessResult, SourceObservation
from steward.sources.reconcile import record_conflicts


@dataclass(frozen=True, slots=True)
class CoverageUpdate:
    provider: str
    dataset: str
    entity_key: str
    access: AccessResult
    expected_periods: int | None = None
    available_periods: int | None = None
    earliest_period: dt.date | None = None
    latest_period: dt.date | None = None
    last_good_observation_id: int | None = None


async def record_access(
    pool: asyncpg.Pool,
    provider: str,
    endpoint: str,
    result: AccessResult,
    *,
    status_code: int | None = None,
    request_id: str | None = None,
    elapsed_ms: int | None = None,
    response_bytes: int | None = None,
    credits: float = 0,
) -> None:
    """Record every attempt without replacing a prior successful observation."""
    request_id = request_id or result.request_id
    await pool.execute(
        "select public.record_provider_attempt($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
        provider,
        endpoint,
        result.state.value,
        result.detail,
        result.retry_at,
        status_code,
        request_id,
        elapsed_ms,
        response_bytes,
        credits,
    )


async def publish_observations(
    pool: asyncpg.Pool,
    rows: list[SourceObservation],
    *,
    known_at: dt.datetime,
    access: AccessResult,
    source_priorities: dict[str, int] | None = None,
    tolerance_by_metric: dict[str, float] | None = None,
) -> list[int]:
    """Append observations and persist comparable-source conflicts.

    Only records with identical metric, period, unit, currency and geography are compared.
    Callers choose source priority and an explicit metric tolerance; no conversion or
    concept-equivalence is guessed here.
    """
    if access.state.value != "available":
        return []
    source_priorities = source_priorities or {}
    tolerance_by_metric = tolerance_by_metric or {}
    ids: list[int] = []
    async with pool.acquire() as conn, conn.transaction():
        for row in rows:
            priority = source_priorities.get(row.source, 10_000)
            prior = await conn.fetch(
                """select id,source,value,scale,source_priority from public.source_observation
                   where domain=$1 and metric=$2 and geography is not distinct from $3
                     and period_start=$4 and period_end=$5 and unit=$6
                     and currency is not distinct from $7 and source<>$8 and value is not null
                   order by coalesce(source_priority,10000),known_at desc""",
                row.domain,
                row.metric,
                row.geography,
                row.period_start.date(),
                row.period_end.date(),
                row.unit,
                row.currency,
                row.source,
            )
            tolerance = Decimal(str(tolerance_by_metric.get(row.metric, 0)))
            incoming_scaled = row.value * row.scale if row.value is not None else None
            preferred_old = prior[0] if prior else None
            incoming_preferred = preferred_old is None or priority < (
                preferred_old["source_priority"]
                if preferred_old["source_priority"] is not None
                else source_priorities.get(preferred_old["source"], 10_000)
            )
            observation_id = await conn.fetchval(
                """insert into public.source_observation
                   (domain, metric, geography, period_start, period_end, value, unit, scale,
                    currency, source, source_url, publisher_at, retrieved_at, known_at, vintage,
                    access_state, raw, normalized, source_priority, published)
                   values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,
                           $17::jsonb,$18::jsonb,$19,$20)
                   on conflict do nothing returning id""",
                row.domain,
                row.metric,
                row.geography,
                row.period_start.date(),
                row.period_end.date(),
                row.value,
                row.unit,
                row.scale,
                row.currency,
                row.source,
                row.source_url,
                row.publisher_at,
                row.retrieved_at,
                known_at,
                row.vintage,
                access.state.value,
                json.dumps(row.raw, default=str),
                json.dumps(row.dimensions, default=str),
                priority,
                incoming_preferred,
            )
            if observation_id is not None:
                ids.append(observation_id)
            if observation_id is None or not prior or incoming_scaled is None:
                continue
            if incoming_preferred:
                preferred_value = incoming_scaled
                preferred_id = observation_id
                alternatives = [
                    (p["id"], p["value"] * p["scale"])
                    for p in prior
                    if p["value"] is not None
                ]
            else:
                preferred_value = preferred_old["value"] * preferred_old["scale"]
                preferred_id = preferred_old["id"]
                alternatives = [(observation_id, incoming_scaled)]
            if preferred_value is not None:
                await record_conflicts(
                    conn,
                    preferred_id,
                    alternatives,
                    metric=row.metric,
                    preferred_value=preferred_value,
                    tolerance=tolerance,
                )
    return ids


async def update_coverage(pool: asyncpg.Pool, update: CoverageUpdate) -> None:
    await pool.execute(
        """insert into public.coverage_status
           (provider,dataset,entity_key,state,expected_periods,available_periods,
            earliest_period,latest_period,last_good_observation_id,reason)
           values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
           on conflict (provider,dataset,entity_key) do update set
             state=excluded.state,
             expected_periods=coalesce(excluded.expected_periods,coverage_status.expected_periods),
             available_periods=coalesce(excluded.available_periods,coverage_status.available_periods),
             earliest_period=coalesce(excluded.earliest_period,coverage_status.earliest_period),
             latest_period=coalesce(excluded.latest_period,coverage_status.latest_period),
             last_good_observation_id=coalesce(excluded.last_good_observation_id,
                                               coverage_status.last_good_observation_id),
             checked_at=now(), reason=excluded.reason""",
        update.provider,
        update.dataset,
        update.entity_key,
        update.access.state.value,
        update.expected_periods,
        update.available_periods,
        update.earliest_period,
        update.latest_period,
        update.last_good_observation_id,
        update.access.detail,
    )
