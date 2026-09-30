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
from steward.macro.providers import (
    MacroPoint,
    TradePoint,
    parse_comtrade,
    parse_sdmx_json,
    parse_world_bank,
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
    publish_observations,
    record_access,
    update_coverage,
)


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
    value = await ctx.pool.fetchval(
        "select state from public.provider_access where provider=$1 and endpoint=$2",
        provider,
        endpoint,
    )
    return AccessState(value) if value else None


async def _save_macro(ctx: JobContext, points: list[MacroPoint], known_at: dt.datetime) -> int:
    valid_points = [point for point in points if point.value is not None]
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
                dimensions=point.metadata,
                raw=point.metadata,
            )
            for point in valid_points
        ],
        known_at=known_at,
        access=AccessResult(AccessState.AVAILABLE),
        source_priorities={"alfred": 1, "ecb": 10, "eurostat": 10, "fred": 10,
                           "cnb": 10, "world_bank": 20, "oecd": 20, "bis": 20},
        tolerance_by_metric={point.series_id: 0.0001 for point in valid_points},
    )
    count = 0
    for point in points:
        result = await ctx.pool.execute(
            """insert into public.macro_observation
               (series_id,source,geography,period,value,unit,frequency,release_at,known_at,
                vintage,source_url,metadata)
               values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb)
               on conflict do nothing""",
            point.series_id,
            point.source,
            point.geography,
            point.period,
            point.value,
            point.unit,
            point.frequency,
            point.release_at,
            known_at,
            known_at.isoformat(),
            point.source_url,
            json.dumps(point.metadata, default=str),
        )
        count += result.endswith("1")
    return count


async def _save_trade(
    ctx: JobContext, points: list[TradePoint], known_at: dt.datetime, source_url: str
) -> int:
    count = 0
    for point in points:
        result = await ctx.pool.execute(
            """insert into public.trade_observation
               (reporter,partner,flow,hs_revision,hs_code,period,trade_value,quantity,
                quantity_unit,trade_currency,known_at,source_url,raw)
               values($1,$2,$3,$4,$5,$6,$7,$8,$9,'USD',$10,$11,$12::jsonb)
               on conflict do nothing""",
            point.reporter,
            point.partner,
            point.flow,
            point.hs_revision,
            point.hs_code,
            point.period,
            point.value,
            point.quantity,
            point.quantity_unit,
            known_at,
            source_url,
            json.dumps({"national_trade_not_issuer_contract": True}),
        )
        count += result.endswith("1")
    return count


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
    data = body.get("data") or body
    structures = data.get("structures") or []
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


async def run_public_data_collect(ctx: JobContext, deps: PublicDataDeps) -> None:
    stored = await load_active(ctx.pool, "jobs", JobsConfig)
    await ctx.record_config_version(stored.id if stored else None)
    jobs = stored.body if stored else JobsConfig()
    now = deps.now or dt.datetime.now(dt.UTC)
    governor = PostgresRequestGovernor(ctx.pool)

    wb_settings = jobs.providers["world_bank"]
    async with ctx.step("world_bank:indicators") as step:
        if not wb_settings.enabled or await _gate(ctx, "world_bank", "indicators") != "available":
            step.detail["skipped"] = "provider disabled or access gate not available"
        else:
            await governor.reserve("world_bank", "indicators", Decimal(1), _budget(wb_settings))
            indicators = [item.key for item in jobs.public_data.world_bank]
            url = "https://api.worldbank.org/v2/country/{}/indicator/{}".format(
                ";".join(jobs.public_data.countries), ";".join(indicators)
            )
            response = await deps.http.get(
                url,
                params={"format": "json", "per_page": 20000, "date": f"{now.year - 10}:{now.year}"},
            )
            try:
                body = response.json() if response.status_code == 200 else []
            except ValueError:
                body = []
            has_rows = isinstance(body, list) and len(body) > 1 and bool(body[1])
            access = access_from_http(
                response.status_code,
                has_rows=has_rows,
                retry_at=retry_after_at(response.headers.get("Retry-After")),
            )
            if response.status_code == 200 and body and not isinstance(body, list):
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
            points = (
                parse_world_bank(body, str(response.url))
                if access.state == AccessState.AVAILABLE
                else []
            )
            unit_by_indicator = {item.key: item.unit for item in jobs.public_data.world_bank}
            points = [
                replace(point, unit=unit_by_indicator.get(point.series_id, point.unit))
                for point in points
            ]
            if access.state == AccessState.AVAILABLE:
                await _archive(ctx, "world_bank", "indicators", str(response.url), body, now)
            step.detail.update(
                observations=len(points), inserted=await _save_macro(ctx, points, now)
            )

    for series in jobs.public_data.sdmx:
        provider_settings = jobs.providers[series.provider]
        async with ctx.step(f"{series.provider}:{series.key}") as step:
            if (
                not provider_settings.enabled
                or await _gate(ctx, series.provider, "sdmx") != "available"
            ):
                step.detail["skipped"] = "provider disabled or access gate not available"
                continue
            try:
                await governor.reserve(
                    series.provider, "sdmx", Decimal(1), _budget(provider_settings)
                )
            except QuotaExceeded as exc:
                step.detail["skipped"] = str(exc)
                continue
            url = f"{str(series.base_url).rstrip('/')}/data/{series.flow}/{series.series_key}"
            response = await deps.http.get(
                url,
                params={"startPeriod": str(now.year - 10)},
                headers={"Accept": "application/vnd.sdmx.data+json;version=2.0.0"},
            )
            try:
                body = response.json() if response.status_code == 200 else {}
            except ValueError:
                body = {}
            access = access_from_http(
                response.status_code,
                has_rows=bool(body),
                retry_at=retry_after_at(response.headers.get("Retry-After")),
            )
            if response.status_code == 200 and not body:
                access = access_from_http(502, retry_at=access.retry_at)
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
            points = (
                parse_sdmx_json(
                    body,
                    source=series.provider,
                    series_id=series.key,
                    geography=series.geography,
                    unit=series.unit,
                    frequency=series.frequency,
                    source_url=str(response.url),
                    vintage=now.date().isoformat(),
                )
                if access.state == AccessState.AVAILABLE
                else []
            )
            for item in jobs.public_data.world_bank:
                await ctx.pool.execute(
                    """insert into public.macro_series_metadata
                       (source,series_id,label,definition,frequency,unit,consumer)
                       values('world_bank',$1,$2,$2,'annual',$3,$4)
                       on conflict(source,series_id) do update set label=excluded.label,
                         definition=excluded.definition,unit=excluded.unit,
                         consumer=excluded.consumer""",
                    item.key,
                    item.label,
                    item.unit,
                    item.consumer,
                )
            for country in jobs.public_data.countries:
                for item in jobs.public_data.world_bank:
                    selected = [
                        point
                        for point in points
                        if point.geography == country and point.series_id == item.key
                    ]
                    state = access
                    if access.state == AccessState.AVAILABLE and not selected:
                        state = type(access)(AccessState.NOT_COVERED, "no observations returned")
                    await update_coverage(
                        ctx.pool,
                        CoverageUpdate(
                            provider="world_bank",
                            dataset=item.key,
                            entity_key=country,
                            access=state,
                            expected_periods=11,
                            available_periods=len(selected),
                            earliest_period=min((p.period for p in selected), default=None),
                            latest_period=max((p.period for p in selected), default=None),
                        ),
                    )
            if access.state == AccessState.AVAILABLE:
                await _archive(ctx, series.provider, series.key, str(response.url), body, now)
            await ctx.pool.execute(
                """insert into public.macro_series_metadata
                   (source,series_id,label,definition,frequency,unit,code_lists,consumer)
                   values($1,$2,$3,$4,$5,$6,$7::jsonb,$8)
                   on conflict(source,series_id) do update set label=excluded.label,
                     definition=excluded.definition,frequency=excluded.frequency,
                     unit=excluded.unit,code_lists=excluded.code_lists,
                     consumer=excluded.consumer""",
                series.provider,
                series.key,
                series.label,
                series.definition,
                series.frequency,
                series.unit,
                json.dumps(_sdmx_code_lists(body)),
                series.consumer,
            )
            step.detail.update(
                observations=len(points), inserted=await _save_macro(ctx, points, now)
            )
            state = access
            if access.state == AccessState.AVAILABLE and not points:
                state = type(access)(AccessState.NOT_COVERED, "no observations returned")
            await update_coverage(
                ctx.pool,
                CoverageUpdate(
                    provider=series.provider,
                    dataset=series.key,
                    entity_key=series.geography,
                    access=state,
                    expected_periods=10,
                    available_periods=len(points),
                    earliest_period=min((point.period for point in points), default=None),
                    latest_period=max((point.period for point in points), default=None),
                ),
            )

    comtrade = jobs.providers["un_comtrade"]
    for item in jobs.public_data.comtrade:
        async with ctx.step(f"un_comtrade:{item.reporter}:{item.hs_code}") as step:
            if (
                not comtrade.enabled
                or not deps.comtrade_key
                or await _gate(ctx, "un_comtrade", "trade") != "available"
            ):
                step.detail["skipped"] = "provider disabled, key missing, or audit gate unavailable"
                continue
            await governor.reserve("un_comtrade", "trade", Decimal(1), _budget(comtrade))
            url = "https://comtradeapi.un.org/data/v1/get/C/A/HS"
            response = await deps.http.get(
                url,
                params={
                    "reporterCode": item.reporter,
                    "partnerCode": item.partner,
                    "period": ",".join(
                        str(year)
                        for year in range(now.year - item.history_years, now.year)
                    ),
                    "cmdCode": item.hs_code,
                    "flowCode": item.flow_code,
                },
                headers={"Ocp-Apim-Subscription-Key": deps.comtrade_key},
            )
            try:
                body = response.json() if response.status_code == 200 else {}
            except ValueError:
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
            trade_points = parse_comtrade(body) if access.state == AccessState.AVAILABLE else []
            coverage_access = access
            if access.state == AccessState.AVAILABLE and not trade_points:
                coverage_access = type(access)(AccessState.NOT_COVERED, "no observations returned")
            step.detail.update(
                observations=len(trade_points),
                inserted=await _save_trade(ctx, trade_points, now, str(response.url)),
            )
            await update_coverage(
                ctx.pool,
                CoverageUpdate(
                    provider="un_comtrade",
                    dataset=item.hs_code,
                    entity_key=f"{item.reporter}:{item.partner}",
                    access=coverage_access,
                    expected_periods=item.history_years,
                    available_periods=len({point.period for point in trade_points}),
                    earliest_period=min((point.period for point in trade_points), default=None),
                    latest_period=max((point.period for point in trade_points), default=None),
                ),
            )


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
