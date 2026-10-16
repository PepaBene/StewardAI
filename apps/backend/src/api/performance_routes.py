"""Owner-only portfolio accounting and performance (plan Phase 4): return windows with a ledger
health badge, lots and holding periods, attribution and the satellites-versus-core comparison.
Read-only against the broker data; the only write is the owner's own settings. Wiring:
`app.include_router` of `router` in api/main.py."""

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from api.auth import Owner
from api.db import Pool
from steward import saved
from steward.performance import curves, evaluation, exports, service
from steward.performance.reports import (
    AttributionResponse,
    LotsResponse,
    PerformanceResponse,
    PositionsResponse,
    SleevesResponse,
)
from steward.performance.settings import PerformanceSettings, load_settings, save_settings
from steward.portfolio.factors import factor_exposures
from steward.portfolio.read import portfolio_history

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


@router.get("/portfolio/performance/positions", tags=["portfolio"])
async def get_positions(pool: Pool, owner: Owner) -> PositionsResponse:
    """Open and closed positions per ticker with their transactions (buys, sells, dividends,
    fees and entered splits), realised and unrealised gain and the annualised return."""
    settings, _ = await load_settings(pool, owner.user_id)
    return await service.positions_report(pool, settings)


@router.get("/portfolio/performance/attribution", tags=["portfolio"])
async def get_attribution(
    pool: Pool, owner: Owner, days: Annotated[int, Query(ge=7, le=3650)] = 365
) -> AttributionResponse:
    """Where the gain over the last `days` came from: price, FX, income and fees per holding and
    per group, reconciled to the total with a disclosed residual."""
    settings, _ = await load_settings(pool, owner.user_id)
    return await service.attribution_report(pool, settings, days)


@router.get("/portfolio/performance/drawdown", tags=["portfolio"])
async def get_drawdown(
    pool: Pool, _owner: Owner, days: Annotated[int, Query(ge=7, le=3700)] = 365
) -> curves.DrawdownResponse:
    """The underwater curve of the flow-adjusted portfolio index over `days` (deposits and
    withdrawals excluded), with the maximum drawdown and its peak, trough and recovery dates.
    Empty with a reason before two snapshots exist."""
    return curves.drawdown_response(await portfolio_history(pool, days), days)


@router.get("/portfolio/performance/waterfall", tags=["portfolio"])
async def get_waterfall(
    pool: Pool, owner: Owner, days: Annotated[int, Query(ge=7, le=3650)] = 365
) -> curves.WaterfallResponse:
    """Start value to end value over `days`: net deposits, price return, FX effect, income, costs
    and what the attribution could not place. A step it cannot compute is null with a reason."""
    settings, _ = await load_settings(pool, owner.user_id)
    return curves.waterfall_response(await service.attribution_report(pool, settings, days))


@router.get("/portfolio/performance/sleeves", tags=["portfolio"])
async def get_sleeves(
    pool: Pool, owner: Owner, years: Annotated[int | None, Query(ge=1, le=10)] = None
) -> SleevesResponse:
    """Stocks against the core ETFs, each as a rolling time-weighted return after fees, over
    `years` (default: your saved comparison window)."""
    settings, _ = await load_settings(pool, owner.user_id)
    return await service.sleeves_report(pool, settings, years)


ExportKind = Literal["ledger", "lots", "realized", "attribution", "closed_positions", "positions"]


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
        body = exports.ledger_csv(await service.ledger_entries(pool, settings))
    elif kind == "attribution":
        body = exports.attribution_csv(await service.attribution_report(pool, settings, days))
    elif kind in ("closed_positions", "positions"):
        positions = await service.positions_report(pool, settings)
        body = exports.positions_csv(
            positions.closed if kind == "closed_positions" else [*positions.open, *positions.closed]
        )
    else:
        report = await service.lots_report(pool, settings)
        body = exports.lots_csv(report) if kind == "lots" else exports.realized_csv(report)
    return PlainTextResponse(
        body,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="performance-{kind}.csv"'},
    )


class PerformanceSavedReport(BaseModel):
    item: saved.SavedAnalysisItem
    report: PerformanceResponse


@router.get("/portfolio/performance/saved", tags=["portfolio"])
async def list_saved_performance_reports(pool: Pool, owner: Owner) -> list[saved.SavedAnalysisItem]:
    """Performance reports you kept, newest first."""
    return await saved.list_saved(pool, owner.user_id, "performance_report")


@router.post("/portfolio/performance/saved", tags=["portfolio"], status_code=201)
async def save_performance_report(
    pool: Pool, owner: Owner, body: saved.SavedAnalysisRequest
) -> saved.SavedAnalysisItem:
    """Keep today's report as it is now (the server builds it, so a saved figure is never one the
    browser supplied). Open it later to see the figures as they stood."""
    settings, _ = await load_settings(pool, owner.user_id)
    report = await service.performance_report(pool, settings)
    return await saved.save(
        pool,
        owner.user_id,
        "performance_report",
        body,
        report.model_dump(mode="json"),
        method_version=report.explain.formula_version,
        as_of=report.as_of,
    )


@router.get("/portfolio/performance/saved/{report_id}", tags=["portfolio"])
async def open_saved_performance_report(
    pool: Pool, owner: Owner, report_id: int
) -> PerformanceSavedReport:
    found = await saved.load_saved(pool, owner.user_id, "performance_report", report_id)
    if found is None:
        raise HTTPException(404, "No such saved report.")
    return PerformanceSavedReport(
        item=found[0], report=PerformanceResponse.model_validate(found[1])
    )


@router.delete("/portfolio/performance/saved/{report_id}", tags=["portfolio"], status_code=204)
async def archive_saved_performance_report(pool: Pool, owner: Owner, report_id: int) -> None:
    if not await saved.archive(pool, owner.user_id, "performance_report", report_id):
        raise HTTPException(404, "No such saved report.")


@router.get("/portfolio/performance/evaluation", tags=["portfolio"])
async def get_evaluation(
    pool: Pool,
    owner: Owner,
    dimension: Annotated[
        str, Query(max_length=40, description="A strategy classification, e.g. sector or risk.")
    ] = "sector",
) -> evaluation.Evaluation:
    """Is the process adding value? Brinson-Fachler attribution by the chosen classification,
    monthly and since inception, reconciled to the excess return over the benchmark; the
    Probabilistic Sharpe Ratio against the benchmark (with the record length that would settle
    it); and the factor-adjusted alpha with its t-statistic. Each part says when it cannot be
    computed."""
    settings, _ = await load_settings(pool, owner.user_id)
    out = await evaluation.evaluation(pool, dimension, settings)
    try:
        exposures = await factor_exposures(pool)
        if exposures.portfolio is not None:
            fit = exposures.portfolio
            out.factor_alpha = evaluation.factor_alpha(
                fit.alpha_annual, fit.alpha_t, fit.observations
            )
        else:
            out.notes.append(f"Factor alpha not computed: {exposures.reason or 'no factor fit'}.")
    except Exception:
        out.notes.append("Factor alpha could not be computed.")
    return out


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
