"""Governed Phase-2 World Bank, OECD, BIS and UN Comtrade collection."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

import httpx

from steward import __version__
from steward.config.jobs import JobsConfig, ProviderJobSettings
from steward.config.store import load_active
from steward.jobs.runner import JobContext
from steward.macro.geo import ISO3_TO_ISO2
from steward.macro.providers import (
    MacroPoint,
    SdmxAmbiguousError,
    TradePoint,
    parse_comtrade,
    parse_sdmx_json,
    parse_world_bank,
    world_bank_header,
)
from steward.macro.sdmx_keys import SdmxKeyError, data_request, pin_key
from steward.macro.store import (
    MacroRow,
    store_macro_rows,
    store_trade_points,
    upsert_series_metadata,
)
from steward.settings import get_settings
from steward.sources.base import (
    AccessResult,
    AccessState,
    SourceObservation,
    access_from_http,
    retry_after_at,
)
from steward.sources.governor import Budget, PostgresRequestGovernor, QuotaExceeded
from steward.sources.store import (
    CoverageUpdate,
    provider_gate,
    publish_observations,
    record_access,
    update_coverage,
)

WORLD_BANK_MAX_PAGES = 5  # per_page is 20000; a second page means the request was too wide


@dataclass(slots=True)
class PublicDataDeps:
    http: httpx.AsyncClient
    now: dt.datetime | None = None
    comtrade_key: str | None = None


def _budget(settings: ProviderJobSettings) -> Budget:
    return Budget(
        daily=Decimal(settings.daily_quota) if settings.daily_quota else None,
        monthly=Decimal(settings.monthly_quota) if settings.monthly_quota else None,
        reserve=Decimal(settings.reserve_credits),
    )


async def _gate(ctx: JobContext, provider: str, endpoint: str) -> AccessState | None:
    """AVAILABLE when the shared gate rule lets a collector call the endpoint now (a keyless
    endpoint still `access_unverified` is probed by the call itself; a hard block, a durable
    paid_only/disputed state or a pending Retry-After keeps it shut), else the stored state."""
    decision = await provider_gate(ctx.pool, provider, endpoint)
    if decision.open:
        return AccessState.AVAILABLE
    value = await ctx.pool.fetchval(
        "select state from public.provider_access where provider=$1 and endpoint=$2",
        provider,
        endpoint,
    )
    return AccessState(value) if value else None


async def _skip_reason(
    ctx: JobContext, provider: str, endpoint: str, settings: ProviderJobSettings
) -> str | None:
    """None when the collector may call the endpoint; else which of the two switches is shut:
    the provider's `enabled` flag in the jobs config, or the access gate (and why)."""
    if not settings.enabled:
        return f"provider disabled in the jobs config (providers.{provider}.enabled is false)"
    decision = await provider_gate(ctx.pool, provider, endpoint)
    if decision.open:
        return None
    return f"access gate closed for {provider}/{endpoint}: {decision.reason}"


def _flat_dimensions(point: MacroPoint) -> dict[str, str]:
    nested = point.metadata.get("dimensions")
    source = nested if isinstance(nested, dict) else point.metadata
    return {str(k): str(v) for k, v in source.items() if isinstance(v, str)}


async def _save_macro(ctx: JobContext, points: list[MacroPoint], known_at: dt.datetime) -> int:
    """Store points as macro observations (new periods and revisions only) and publish exactly
    those to the reconciliation store. Returns the number of rows written."""
    result = await store_macro_rows(ctx.pool, [MacroRow.from_point(p, known_at) for p in points])
    by_key = {(r.series_id, r.source, r.geography, r.period): r for r in result.rows}
    written = [p for p in points if (p.series_id, p.source, p.geography, p.period) in by_key]
    await publish_observations(
        ctx.pool,
        [
            SourceObservation(
                domain="macro",
                metric=point.series_id,
                source=point.source,
                period_start=dt.datetime.combine(point.period, dt.time(), dt.UTC),
                period_end=dt.datetime.combine(point.period, dt.time(), dt.UTC),
                value=point.value,
                unit=point.unit,
                source_url=point.source_url,
                retrieved_at=known_at,
                publisher_at=point.release_at,
                geography=point.geography,
                vintage=point.vintage,
                scale=point.unit_multiplier,
                dimensions=_flat_dimensions(point),
                raw=point.metadata,
            )
            for point in written
        ],
        known_at=known_at,
        access=AccessResult(AccessState.AVAILABLE),
        source_priorities={
            "alfred": 1,
            "ecb": 10,
            "eurostat": 10,
            "fred": 10,
            "cnb": 10,
            "world_bank": 20,
            "oecd": 20,
            "bis": 20,
        },
        tolerance_by_metric={point.series_id: 0.0001 for point in written},
    )
    return result.written


async def _save_trade(
    ctx: JobContext, points: list[TradePoint], known_at: dt.datetime, source_url: str
) -> int:
    return (await store_trade_points(ctx.pool, points, known_at, source_url)).written


async def _archive(
    ctx: JobContext, source: str, dataset: str, url: str, body: Any, known_at: dt.datetime
) -> None:
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    await ctx.pool.execute(
        """insert into public.macro_payload_archive
           (source,dataset,content_hash,known_at,source_url,payload)
           values($1,$2,$3,$4,$5,$6::jsonb) on conflict do nothing""",
        source,
        dataset,
        hashlib.sha256(encoded).hexdigest(),
        known_at,
        url,
        encoded.decode(),
    )


def _sdmx_code_lists(body: dict[str, Any]) -> dict[str, Any]:
    nested = body.get("data")
    data: dict[str, Any] = nested if isinstance(nested, dict) else body
    structures = data.get("structures") or ([data["structure"]] if data.get("structure") else [])
    if not structures:
        return {}
    structure = structures[0]
    out: dict[str, Any] = {}
    for dimension_type, dimensions in (structure.get("dimensions") or {}).items():
        out[dimension_type] = [
            {
                "id": dimension.get("id"),
                "values": [
                    {"id": value.get("id"), "name": value.get("name")}
                    for value in dimension.get("values") or []
                ],
            }
            for dimension in dimensions
        ]
    return out


def missing_years(points: list[MacroPoint], first: int, last: int) -> list[int]:
    """Years in first..last with no finite value in `points` (one country, one indicator)."""
    have = {p.period.year for p in points if p.value is not None}
    return [year for year in range(first, last + 1) if year not in have]


def _missing_reason(years: list[int]) -> str | None:
    if not years:
        return None
    shown = ", ".join(str(y) for y in years[:12]) + (" ..." if len(years) > 12 else "")
    return f"{len(years)} year(s) without a value: {shown}"


async def _json(response: httpx.Response, empty: Any) -> Any:
    try:
        return response.json() if response.status_code == 200 else empty
    except ValueError:
        return empty


async def _world_bank_step(
    ctx: JobContext,
    deps: PublicDataDeps,
    jobs: JobsConfig,
    governor: PostgresRequestGovernor,
    now: dt.datetime,
    step: Any,
) -> None:
    settings = jobs.providers["world_bank"]
    reason = await _skip_reason(ctx, "world_bank", "indicators", settings)
    if reason:
        step.detail["skipped"] = reason
        return
    indicators = [item.key for item in jobs.public_data.world_bank]
    first_year, last_year = now.year - 10, now.year
    url = "https://api.worldbank.org/v2/country/{}/indicator/{}".format(
        ";".join(jobs.public_data.countries), ";".join(indicators)
    )
    page = 1
    body: Any = []
    rows: list[Any] = []
    response: httpx.Response | None = None
    access = AccessResult(AccessState.TEMPORARILY_FAILED)
    while True:
        try:
            await governor.reserve("world_bank", "indicators", Decimal(1), _budget(settings))
        except QuotaExceeded as exc:
            step.detail["skipped"] = str(exc)
            if response is None:
                return
            step.detail["partial"] = f"page {page} not fetched: quota"
            break
        response = await deps.http.get(
            url,
            params={
                "format": "json",
                "per_page": 20000,
                "date": f"{first_year}:{last_year}",
                "page": page,
            },
        )
        page_body = await _json(response, [])
        has_rows = isinstance(page_body, list) and len(page_body) > 1 and bool(page_body[1])
        access = access_from_http(
            response.status_code,
            has_rows=has_rows,
            retry_at=retry_after_at(response.headers.get("Retry-After")),
        )
        if response.status_code == 200 and page_body and not isinstance(page_body, list):
            access = access_from_http(502, retry_at=access.retry_at)
        await record_access(
            ctx.pool,
            "world_bank",
            "indicators",
            access,
            status_code=response.status_code,
            response_bytes=len(response.content),
            credits=1,
        )
        if access.state != AccessState.AVAILABLE:
            break
        if page == 1:
            body = page_body
        rows += page_body[1]
        pages = int(world_bank_header(page_body).get("pages") or 1)
        if page >= min(pages, WORLD_BANK_MAX_PAGES):
            if pages > WORLD_BANK_MAX_PAGES:
                step.detail["partial"] = f"{pages} pages offered, {WORLD_BANK_MAX_PAGES} fetched"
            break
        page += 1
    assert response is not None
    if access.state == AccessState.AVAILABLE and isinstance(body, list):
        body = [body[0], rows]
    points: list[MacroPoint] = (
        parse_world_bank(body, str(response.url)) if access.state == AccessState.AVAILABLE else []
    )
    unit_by_indicator = {item.key: item.unit for item in jobs.public_data.world_bank}
    points = [
        replace(point, unit=unit_by_indicator.get(point.series_id, point.unit)) for point in points
    ]
    if access.state == AccessState.AVAILABLE:
        await _archive(ctx, "world_bank", "indicators", str(response.url), body, now)
    step.detail.update(observations=len(points), inserted=await _save_macro(ctx, points, now))
    # The coverage and semantic records belong to this step, whatever SDMX series are enabled.
    for item in jobs.public_data.world_bank:
        sample = next((p for p in points if p.series_id == item.key), None)
        await upsert_series_metadata(
            ctx.pool,
            source="world_bank",
            series_id=item.key,
            label=item.label,
            definition=item.label,
            frequency="annual",
            unit=item.unit,
            consumer=item.consumer,
            geography_definition=sample.geography_definition if sample else None,
            release_calendar=sample.release_calendar if sample else None,
        )
    gaps: dict[str, list[int]] = {}
    for country in jobs.public_data.countries:
        for item in jobs.public_data.world_bank:
            selected = [p for p in points if p.geography == country and p.series_id == item.key]
            valued = [p for p in selected if p.value is not None]
            missing = missing_years(selected, first_year, last_year)
            state = access
            if access.state == AccessState.AVAILABLE and not valued:
                state = AccessResult(AccessState.NOT_COVERED, "no observations returned")
            elif access.state == AccessState.AVAILABLE and missing:
                state = AccessResult(AccessState.AVAILABLE, _missing_reason(missing))
                gaps[f"{country}:{item.key}"] = missing
            await update_coverage(
                ctx.pool,
                CoverageUpdate(
                    provider="world_bank",
                    dataset=item.key,
                    entity_key=country,
                    access=state,
                    expected_periods=last_year - first_year + 1,
                    available_periods=len(valued),
                    earliest_period=min((p.period for p in valued), default=None),
                    latest_period=max((p.period for p in valued), default=None),
                ),
            )
    if gaps:
        step.detail["missing_years"] = {k: v for k, v in list(gaps.items())[:50]}


async def _sdmx_step(
    ctx: JobContext,
    deps: PublicDataDeps,
    jobs: JobsConfig,
    governor: PostgresRequestGovernor,
    now: dt.datetime,
    series: Any,
    step: Any,
) -> None:
    provider_settings = jobs.providers[series.provider]
    reason = await _skip_reason(ctx, series.provider, "sdmx", provider_settings)
    if reason:
        step.detail["skipped"] = reason
        return

    async def fail(state: AccessState, reason: str) -> None:
        step.detail["error"] = reason[:300]
        await update_coverage(
            ctx.pool,
            CoverageUpdate(
                provider=series.provider,
                dataset=series.key,
                entity_key=series.geography,
                access=AccessResult(state, reason[:300]),
            ),
        )

    try:
        pinned = pin_key(series.provider, series.flow, series.series_key, series.geography)
    except SdmxKeyError as exc:
        await fail(AccessState.NOT_COVERED, str(exc))
        return
    step.detail["request_key"] = f"{pinned.flow}/{pinned.key}"
    if pinned.corrected:
        step.detail["key_corrected_from"] = f"{series.flow}/{series.series_key}"
    if not pinned.validated:
        step.detail["key_unvalidated"] = "dimension order not in the documented table"
    try:
        await governor.reserve(series.provider, "sdmx", Decimal(1), _budget(provider_settings))
    except QuotaExceeded as exc:
        step.detail["skipped"] = str(exc)
        return
    url, accept = data_request(series.provider, str(series.base_url), pinned.flow, pinned.key)
    unit = pinned.unit or series.unit
    definition = pinned.definition or series.definition
    response = await deps.http.get(
        url,
        params={"startPeriod": str(now.year - 10), "dimensionAtObservation": "AllDimensions"},
        headers={"Accept": accept},
    )
    body = await _json(response, {})
    if not isinstance(body, dict):
        body = {}
    access = access_from_http(
        response.status_code,
        has_rows=bool(body),
        retry_at=retry_after_at(response.headers.get("Retry-After")),
    )
    if response.status_code == 200 and not body:
        access = access_from_http(502, retry_at=access.retry_at)
    await record_access(
        ctx.pool,
        series.provider,
        "sdmx",
        access,
        status_code=response.status_code,
        response_bytes=len(response.content),
        credits=1,
    )
    points: list[MacroPoint] = []
    skipped: list[str] = []
    if access.state == AccessState.AVAILABLE:
        try:
            points = parse_sdmx_json(
                body,
                source=series.provider,
                series_id=series.key,
                geography=series.geography,
                unit=unit,
                frequency=series.frequency,
                source_url=str(response.url),
                vintage=now.date().isoformat(),
                expect_geography=pinned.area_code,
                geography_map=ISO3_TO_ISO2 if series.provider == "oecd" else None,
                skipped=skipped,
            )
        except SdmxAmbiguousError as exc:
            await fail(AccessState.TEMPORARILY_FAILED, str(exc))
            return
    if skipped:
        step.detail["skipped_rows"] = len(skipped)
        step.detail["skipped_first"] = skipped[0]
    if access.state == AccessState.AVAILABLE:
        await _archive(ctx, series.provider, series.key, str(response.url), body, now)
    sample = points[0] if points else None
    await upsert_series_metadata(
        ctx.pool,
        source=series.provider,
        series_id=series.key,
        label=series.label,
        definition=definition,
        frequency=series.frequency,
        unit=unit,
        consumer=series.consumer,
        unit_multiplier=sample.unit_multiplier if sample else Decimal(1),
        seasonal_adjustment=sample.seasonal_adjustment if sample else None,
        transformation=sample.transformation if sample else None,
        geography_definition=sample.geography_definition if sample else None,
        release_calendar=sample.release_calendar if sample else None,
        code_lists=_sdmx_code_lists(body),
    )
    step.detail.update(observations=len(points), inserted=await _save_macro(ctx, points, now))
    valued = [p for p in points if p.value is not None]
    state = access
    if access.state == AccessState.AVAILABLE and not valued:
        state = AccessResult(AccessState.NOT_COVERED, "no observations returned")
    await update_coverage(
        ctx.pool,
        CoverageUpdate(
            provider=series.provider,
            dataset=series.key,
            entity_key=series.geography,
            access=state,
            expected_periods=10,
            available_periods=len({p.period for p in valued}),
            earliest_period=min((p.period for p in valued), default=None),
            latest_period=max((p.period for p in valued), default=None),
        ),
    )


async def _comtrade_step(
    ctx: JobContext,
    deps: PublicDataDeps,
    jobs: JobsConfig,
    governor: PostgresRequestGovernor,
    now: dt.datetime,
    trade_item: Any,
    step: Any,
) -> None:
    comtrade = jobs.providers["un_comtrade"]
    if (
        not comtrade.enabled
        or not deps.comtrade_key
        or await _gate(ctx, "un_comtrade", "trade") != "available"
    ):
        step.detail["skipped"] = "provider disabled, key missing, or audit gate unavailable"
        return
    try:
        await governor.reserve("un_comtrade", "trade", Decimal(1), _budget(comtrade))
    except QuotaExceeded as exc:
        step.detail["skipped"] = str(exc)
        return
    url = "https://comtradeapi.un.org/data/v1/get/C/A/HS"
    response = await deps.http.get(
        url,
        params={
            "reporterCode": trade_item.reporter,
            "partnerCode": trade_item.partner,
            "period": ",".join(
                str(year) for year in range(now.year - trade_item.history_years, now.year)
            ),
            "cmdCode": trade_item.hs_code,
            "flowCode": trade_item.flow_code,
            # Totals only: without these a row per customs regime / transport mode / second
            # partner would collapse onto one key.
            "customsCode": "C00",
            "motCode": "0",
            "partner2Code": "0",
        },
        headers={"Ocp-Apim-Subscription-Key": deps.comtrade_key},
    )
    body = await _json(response, {})
    if not isinstance(body, dict):
        body = {}
    access = access_from_http(
        response.status_code,
        has_rows=bool(body.get("data")),
        retry_at=retry_after_at(response.headers.get("Retry-After")),
    )
    if response.status_code == 200 and not body:
        access = access_from_http(502, retry_at=access.retry_at)
    await record_access(
        ctx.pool,
        "un_comtrade",
        "trade",
        access,
        status_code=response.status_code,
        response_bytes=len(response.content),
        credits=1,
    )
    skipped: list[str] = []
    trade_points = parse_comtrade(body, skipped) if access.state == AccessState.AVAILABLE else []
    if skipped:
        step.detail["skipped_rows"] = len(skipped)
        step.detail["skipped_first"] = skipped[0]
    coverage_access = access
    if access.state == AccessState.AVAILABLE and not trade_points:
        coverage_access = AccessResult(AccessState.NOT_COVERED, "no observations returned")
    # Never retain response.url in the audit trail beyond the keyless query string.
    step.detail.update(
        observations=len(trade_points),
        inserted=await _save_trade(ctx, trade_points, now, str(response.url)),
    )
    await update_coverage(
        ctx.pool,
        CoverageUpdate(
            provider="un_comtrade",
            dataset=trade_item.hs_code,
            entity_key=f"{trade_item.reporter}:{trade_item.partner}",
            access=coverage_access,
            expected_periods=trade_item.history_years,
            available_periods=len({point.period for point in trade_points}),
            earliest_period=min((point.period for point in trade_points), default=None),
            latest_period=max((point.period for point in trade_points), default=None),
        ),
    )


async def run_public_data_collect(ctx: JobContext, deps: PublicDataDeps) -> None:
    stored = await load_active(ctx.pool, "jobs", JobsConfig)
    await ctx.record_config_version(stored.id if stored else None)
    jobs = stored.body if stored else JobsConfig()
    now = deps.now or dt.datetime.now(dt.UTC)
    governor = PostgresRequestGovernor(ctx.pool)

    async with ctx.step("world_bank:indicators") as step:
        await _world_bank_step(ctx, deps, jobs, governor, now, step)
    for series in jobs.public_data.sdmx:
        async with ctx.step(f"{series.provider}:{series.key}") as step:
            await _sdmx_step(ctx, deps, jobs, governor, now, series, step)
    for trade_item in jobs.public_data.comtrade:
        async with ctx.step(f"un_comtrade:{trade_item.reporter}:{trade_item.hs_code}") as step:
            await _comtrade_step(ctx, deps, jobs, governor, now, trade_item, step)


async def public_data_collect_handler(ctx: JobContext) -> None:
    settings = get_settings()
    async with httpx.AsyncClient(
        timeout=60, headers={"User-Agent": f"StewardAI/{__version__}"}
    ) as http:
        await run_public_data_collect(
            ctx,
            PublicDataDeps(
                http=http,
                comtrade_key=settings.un_comtrade_api_key.get_secret_value()
                if settings.un_comtrade_api_key
                else None,
            ),
        )
