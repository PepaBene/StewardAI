"""Persistence boundary for governed provider results and point-in-time observations."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import asyncpg

from steward.redact import redact
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
    job_id: int | None = None,
) -> None:
    """Record every attempt without replacing a prior successful observation.

    Only durable verdicts (available, paid_only, access_unverified, disputed) change the
    endpoint's `state`; a 429, 5xx, timeout or empty 2xx is kept as the last attempt and,
    when the provider sent one, as the Retry-After window the governor honors (A6)."""
    request_id = request_id or result.request_id
    await pool.execute(
        "select public.record_provider_attempt($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
        provider,
        endpoint,
        result.state.value,
        redact(result.detail),  # stored as error_redacted and provider_access.detail
        result.retry_at,
        status_code,
        request_id,
        elapsed_ms,
        response_bytes,
        credits,
        job_id,
    )


@dataclass(frozen=True, slots=True)
class GateDecision:
    open: bool
    reason: str | None = None
    state: str | None = None
    retry_at: dt.datetime | None = None


def gate_decision(row: asyncpg.Record | dict[str, Any] | None, now: dt.datetime) -> GateDecision:
    """May a collector call this endpoint now? The single rule behind every `_gate` check.

    Closed for: no provider_access row, a hard block (paid-tier or credential denial), a durable
    state of paid_only or disputed, and a provider Retry-After window still pending.  Open for
    `available` and for `access_unverified` that is not hard-blocked: the attempt is the probe.
    A transient failure (429, 5xx, timeout, empty 2xx) never closes the gate by itself (A6)."""
    if row is None:
        return GateDecision(False, "no provider_access row")
    state = row["state"]
    if row["hard_blocked"]:
        return GateDecision(False, "hard-blocked until the credential or plan changes", state)
    retry_at = row["retry_at"]
    if retry_at is not None and retry_at > now:
        return GateDecision(False, "provider Retry-After window", state, retry_at)
    if state not in ("available", "access_unverified"):
        return GateDecision(False, f"endpoint state is {state}", state)
    return GateDecision(True, None, state)


async def provider_gate(
    pool: asyncpg.Pool | asyncpg.Connection,
    provider: str,
    endpoint: str,
    *,
    now: dt.datetime | None = None,
) -> GateDecision:
    row = await pool.fetchrow(
        "select state,hard_blocked,retry_at,last_success_at from public.provider_access"
        " where provider=$1 and endpoint=$2",
        provider,
        endpoint,
    )
    return gate_decision(row, now or dt.datetime.now(dt.UTC))


# Index expressions of source_observation_identity (migration 20261005100100), minus revision.
_IDENTITY_WHERE = """domain=$1 and metric=$2 and coalesce(issuer_id,0)=$3
  and coalesce(security_id,0)=$4 and coalesce(listing_id,0)=$5
  and coalesce(geography,'')=$6 and period_start=$7 and period_end=$8 and unit=$9
  and coalesce(currency,'')=$10 and normalized=$11::jsonb and source=$12
  and coalesce(vintage,'')=$13"""


async def _fill_ids(conn: asyncpg.Connection, row: SourceObservation) -> tuple[int, int, int]:
    """issuer/security/listing ids as 0 for unknown, filling a wider id from a narrower one."""
    issuer_id, security_id, listing_id = row.issuer_id, row.security_id, row.listing_id
    if listing_id is not None and security_id is None:
        security_id = await conn.fetchval(
            "select security_id from public.security_listing where id=$1", listing_id
        )
    if security_id is not None and issuer_id is None:
        issuer_id = await conn.fetchval(
            "select issuer_id from public.security where id=$1", security_id
        )
    return issuer_id or 0, security_id or 0, listing_id or 0


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

    An observation's identity is (domain, metric, issuer/security/listing, geography,
    period_start, period_end, unit, currency, dimensions, source, vintage); `revision` counts
    the changed values stored under one identity: an identical re-fetch stores nothing, a
    restated value is stored as revision n+1 and supersedes (published=false) the earlier one.
    Only records with the same metric, period, unit, currency, geography, entity and dimensions
    are compared across sources. Callers choose source priority and an explicit metric
    tolerance; no conversion or concept-equivalence is guessed here.  A displaced preferred
    row is set published=false so readers never see two preferred values.
    """
    if access.state.value != "available":
        return []
    source_priorities = source_priorities or {}
    tolerance_by_metric = tolerance_by_metric or {}
    ids: list[int] = []
    async with pool.acquire() as conn, conn.transaction():
        for row in rows:
            priority = source_priorities.get(row.source, 10_000)
            issuer_id, security_id, listing_id = await _fill_ids(conn, row)
            dimensions = json.dumps(row.dimensions, sort_keys=True, default=str)
            key = (
                row.domain,
                row.metric,
                issuer_id,
                security_id,
                listing_id,
                row.geography or "",
                row.period_start.date(),
                row.period_end.date(),
                row.unit,
                row.currency or "",
                dimensions,
            )
            own = (*key, row.source, row.vintage or "")
            # Serialize writers of one identity so two workers cannot both claim revision n+1.
            await conn.execute(
                "select pg_advisory_xact_lock(hashtextextended($1, 0))",
                json.dumps(own, default=str),
            )
            latest = await conn.fetchrow(
                f"""select id,revision,value,scale,text_value from public.source_observation
                    where {_IDENTITY_WHERE} order by revision desc limit 1""",  # noqa: S608
                *own,
            )
            if latest is not None and (
                latest["value"] == row.value and latest["scale"] == row.scale
            ):
                continue  # identical re-fetch: nothing new to remember
            revision = 0 if latest is None else latest["revision"] + 1
            prior = await conn.fetch(
                """select distinct on (source) id,source,value,scale,source_priority
                   from public.source_observation
                   where domain=$1 and metric=$2 and coalesce(issuer_id,0)=$3
                     and coalesce(security_id,0)=$4 and coalesce(listing_id,0)=$5
                     and coalesce(geography,'')=$6 and period_start=$7 and period_end=$8
                     and unit=$9 and coalesce(currency,'')=$10 and normalized=$11::jsonb
                     and source<>$12 and value is not null
                   order by source, revision desc, known_at desc""",
                *key,
                row.source,
            )
            prior = sorted(
                prior,
                key=lambda p: (
                    p["source_priority"]
                    if p["source_priority"] is not None
                    else source_priorities.get(p["source"], 10_000),
                    p["id"],
                ),
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
                   (domain, metric, issuer_id, security_id, listing_id, geography, period_start,
                    period_end, value, unit, scale, currency, source, source_url, publisher_at,
                    retrieved_at, known_at, vintage, revision, access_state, raw, normalized,
                    source_priority, published)
                   values ($1,$2,nullif($3::bigint,0),nullif($4::bigint,0),
                           nullif($5::bigint,0),$6,$7,$8,$9,$10,$11,$12,
                           $13,$14,$15,$16,$17,$18,$19,$20,$21::jsonb,$22::jsonb,$23,$24)
                   on conflict do nothing returning id""",
                row.domain,
                row.metric,
                issuer_id,
                security_id,
                listing_id,
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
                revision,
                access.state.value,
                json.dumps(row.raw, default=str),
                dimensions,
                priority,
                incoming_preferred,
            )
            if observation_id is None:
                continue
            ids.append(observation_id)
            if latest is not None:
                # A restatement supersedes the earlier revisions of the same identity.
                await conn.execute(
                    f"""update public.source_observation set published=false
                        where {_IDENTITY_WHERE} and id<>$14 and published""",  # noqa: S608
                    *own,
                    observation_id,
                )
            if incoming_preferred and prior:
                # The displaced preferred rows stay as alternatives but are no longer published.
                await conn.execute(
                    "update public.source_observation set published=false"
                    " where id = any($1::bigint[]) and published",
                    [p["id"] for p in prior],
                )
            if not prior or incoming_scaled is None:
                continue
            if incoming_preferred:
                preferred_value = incoming_scaled
                preferred_id = observation_id
                alternatives = [
                    (p["id"], p["value"] * p["scale"]) for p in prior if p["value"] is not None
                ]
            else:
                assert preferred_old is not None
                preferred_value = preferred_old["value"] * preferred_old["scale"]
                preferred_id = preferred_old["id"]
                alternatives = [(observation_id, incoming_scaled)]
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
