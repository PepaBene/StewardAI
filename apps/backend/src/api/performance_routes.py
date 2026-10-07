"""Owner-only portfolio accounting and performance (plan Phase 4): return windows with a ledger
health badge, lots and holding periods, attribution and the satellites-versus-core comparison.
Read-only against the broker data; the only write is the owner's own settings. Wiring:
`app.include_router` of `router` in api/main.py."""

from typing import Annotated, Literal

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from api.auth import Owner
from api.db import Pool
from steward.performance import exports, service
from steward.performance.reports import (
    AttributionResponse,
    LotsResponse,
    PerformanceResponse,
    SleevesResponse,
)
from steward.performance.settings import PerformanceSettings, load_settings, save_settings

router = APIRouter()


class SettingsResponse(BaseModel):
    settings: PerformanceSettings
    version: int | None


@router.get("/portfolio/performance", tags=["portfolio"])
async def get_performance(pool: Pool, owner: Owner) -> PerformanceResponse:
    """Time- and money-weighted return over 1M, YTD, 1Y, 3Y, 5Y and since inception, each with
    its benchmark comparison, plus the ledger health that decides whether a precise figure is
    shown."""
    settings, _ = await load_settings(pool, owner.user_id)
    return await service.performance_report(pool, settings)


@router.get("/portfolio/performance/lots", tags=["portfolio"])
async def get_lots(pool: Pool, owner: Owner) -> LotsResponse:
    """Open tax lots with their holding-period dates, realized gains, dividends, fees and the
    year's exemption headroom. Facts and your own thresholds, not tax advice."""
    settings, _ = await load_settings(pool, owner.user_id)
    return await service.lots_report(pool, settings)


@router.get("/portfolio/performance/attribution", tags=["portfolio"])
async def get_attribution(
    pool: Pool, owner: Owner, days: Annotated[int, Query(ge=7, le=3650)] = 365
) -> AttributionResponse:
    """Where the gain over the last `days` came from: price, FX, income and fees per holding and
    per group, reconciled to the total with a disclosed residual."""
    settings, _ = await load_settings(pool, owner.user_id)
    return await service.attribution_report(pool, settings, days)


@router.get("/portfolio/performance/sleeves", tags=["portfolio"])
async def get_sleeves(pool: Pool, owner: Owner) -> SleevesResponse:
    """Stocks against the core ETFs, each as a rolling time-weighted return after fees."""
    settings, _ = await load_settings(pool, owner.user_id)
    return await service.sleeves_report(pool, settings)


ExportKind = Literal["ledger", "lots", "realized", "attribution"]


@router.get("/portfolio/performance/export", tags=["portfolio"], response_class=PlainTextResponse)
async def export_performance(
    pool: Pool,
    owner: Owner,
    kind: ExportKind,
    days: Annotated[int, Query(ge=7, le=3650)] = 365,
) -> PlainTextResponse:
    """One table as CSV: the dated ledger, open lots, realized gains or attribution (the last
    over `days`). The same rows the pages show."""
    settings, _ = await load_settings(pool, owner.user_id)
    if kind == "ledger":
        body = exports.ledger_csv(await service.ledger_entries(pool))
    elif kind == "attribution":
        body = exports.attribution_csv(await service.attribution_report(pool, settings, days))
    else:
        report = await service.lots_report(pool, settings)
        body = exports.lots_csv(report) if kind == "lots" else exports.realized_csv(report)
    return PlainTextResponse(
        body,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="performance-{kind}.csv"'},
    )


@router.get("/portfolio/performance/settings", tags=["portfolio"])
async def get_performance_settings(pool: Pool, owner: Owner) -> SettingsResponse:
    settings, version = await load_settings(pool, owner.user_id)
    return SettingsResponse(settings=settings, version=version)


@router.put("/portfolio/performance/settings", tags=["portfolio"])
async def put_performance_settings(
    pool: Pool, owner: Owner, body: PerformanceSettings
) -> SettingsResponse:
    """Save new settings as a new version; earlier versions are kept."""
    version = await save_settings(pool, owner.user_id, body)
    return SettingsResponse(settings=body, version=version)
