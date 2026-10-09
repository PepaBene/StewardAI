"""Authenticated routes for risk, scenarios and rebalancing decisions (plan Phase 6).

All reads and estimates: nothing here writes to Trading212, and a saved item stores the request
and the result as they stood, with the method version, so a later change in the data never
rewrites what the owner saw."""

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import Owner
from api.db import Pool
from steward import saved
from steward.analytics.versions import version
from steward.decision import pretrade as pt
from steward.decision import rebalance as rb
from steward.decision import service as svc
from steward.decision import whatif
from steward.decision.risk_report import RiskReport, build_report
from steward.decision.series import load_bundle
from steward.decision.shocks import ShockAssumptions, ShockSet
from steward.decision.themes import ThemeDefinition, ThemeExposure, theme_exposure
from steward.lookthrough.read import UNCLASSIFIED
from steward.xray import service as xray_service
from steward.xray.scenario import ProposalError
from steward.xray.views import build_view

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

SAVED_KINDS = ("shock_scenario", "whatif_case", "rebalance_plan", "theme_exposure")


def _no_holdings() -> HTTPException:
    return HTTPException(404, "There is no portfolio snapshot yet.")


def _no_strategy() -> HTTPException:
    return HTTPException(409, "No strategy is stored; save one under Config first.")


async def _canonical_case(pool: Pool, case: whatif.WhatIfCase) -> whatif.WhatIfCase:
    """The case with its tickers in the broker's spelling (see `svc.resolve_tickers`)."""
    names = await svc.resolve_tickers(pool, [*([case.ticker] if case.ticker else []), *case.split])
    return case.model_copy(
        update={
            "ticker": names.get(case.ticker.strip(), case.ticker) if case.ticker else None,
            "split": {names.get(k.strip(), k): v for k, v in case.split.items()},
        }
    )


# ------------------------------------------------------------------------------------- risk


@router.get("/risk/report")
async def get_risk_report(
    pool: Pool,
    owner: Owner,
    days: Annotated[
        int, Query(ge=120, le=1100, description="Look-back window, calendar days.")
    ] = 365,
    rolling: Annotated[int, Query(ge=60, le=250, description="Rolling correlation window.")] = 90,
    benchmark: Annotated[str | None, Query(max_length=32)] = None,
) -> RiskReport:
    """Volatility, downside deviation, Sortino and Sharpe, historical VaR and expected shortfall,
    drawdown, beta, tracking error, rolling correlation with the benchmark, a correlation matrix of
    the largest holdings and a diversification benefit. Each figure carries its sample size and is
    withheld below the minimum."""
    return build_report(await load_bundle(pool, days, benchmark), rolling)


# --------------------------------------------------------------------------------- scenarios


class ShockRequest(BaseModel):
    shocks: ShockSet = Field(default_factory=ShockSet)
    assumptions: ShockAssumptions = Field(default_factory=ShockAssumptions)


@router.get("/scenarios/standard")
async def get_standard_shocks(pool: Pool, owner: Owner) -> svc.StandardTable:
    """The named starting shocks run on today's holdings: an equity fall, a rate rise, wider
    credit spreads and a fall in each foreign currency."""
    try:
        return await svc.run_standard(pool, owner.user_id)
    except svc.NoHoldings as err:
        raise _no_holdings() from err


@router.post("/scenarios/shock")
async def post_shock(body: ShockRequest, pool: Pool, owner: Owner) -> svc.ShockRun:
    """Move every part of the portfolio by the sensitivities that apply to it and reconcile to a
    complete shocked portfolio. Measured betas where the history supports them, your values
    otherwise; a holding with no valid sensitivity stays put and is listed."""
    assumptions = await svc.canonical_assumptions(pool, body.assumptions)
    try:
        return await svc.run_shock(pool, owner.user_id, body.shocks, assumptions)
    except svc.NoHoldings as err:
        raise _no_holdings() from err


class ThemeOption(BaseModel):
    value: str
    label: str
    weight: float


class ThemeOptions(BaseModel):
    options: dict[str, list[ThemeOption]]


THEME_KINDS = (
    "sector", "industry", "country", "domicile", "currency", "issuer", "revenue_country",
    "commodity", "trade_product",
)  # fmt: skip


@router.get("/scenarios/themes/options")
async def get_theme_options(pool: Pool, owner: Owner) -> ThemeOptions:
    """The values you can pick for a theme, taken from what the portfolio actually holds."""
    loaded = await xray_service.load_cached(pool, owner.user_id)
    ctx = xray_service.view_context(loaded)
    out: dict[str, list[ThemeOption]] = {}
    for kind in THEME_KINDS:
        view = build_view(kind, ctx)  # type: ignore[arg-type]
        out[kind] = [
            ThemeOption(value=b.key, label=b.label, weight=b.weight)
            for b in view.buckets
            if b.kind == "classified" and b.key != UNCLASSIFIED
        ]
    return ThemeOptions(options=out)


@router.post("/scenarios/themes")
async def post_theme(body: ThemeDefinition, pool: Pool, owner: Owner) -> ThemeExposure:
    """Exposure to a theme or event through issuer, fund, sector, country, trade and currency
    paths, each holding counted once, with the unknown weight and a stated confidence."""
    loaded = await xray_service.load_cached(pool, owner.user_id)
    if loaded.data.portfolio is None:
        raise _no_holdings()
    return theme_exposure(body, xray_service.view_context(loaded))


@router.post("/scenarios/whatif")
async def post_whatif(body: whatif.WhatIfCase, pool: Pool, owner: Owner) -> whatif.WhatIfResult:
    """A named case on today's holdings: a new security, a contribution or withdrawal, dividend
    reinvestment, an FX move, a benchmark change or a thesis-triggered exit. Applied to a copy."""
    try:
        return await whatif.run_case(pool, owner.user_id, await _canonical_case(pool, body))
    except svc.NoHoldings as err:
        raise _no_holdings() from err
    except (whatif.CaseError, ProposalError) as err:
        raise HTTPException(422, str(err)) from err


# ------------------------------------------------------------------------ rebalance, pre-trade


@router.post("/rebalance")
async def post_rebalance(
    body: rb.RebalanceRequest, pool: Pool, owner: Owner
) -> svc.RebalanceResponse:
    """Proposed buys and sells to bring positions back to the strategy's targets, with cash,
    assumed costs, tax-lot consequences of each sale and the drift and X-Ray after. Read-only:
    nothing is sent to Trading212."""
    try:
        return await svc.run_rebalance(
            pool, owner.user_id, await svc.canonical_overrides(pool, body)
        )
    except svc.NoHoldings as err:
        raise _no_holdings() from err
    except svc.NoStrategy as err:
        raise _no_strategy() from err
    except ValueError as err:
        raise HTTPException(422, str(err)) from err


@router.post("/pretrade")
async def post_pretrade(body: svc.PretradeRequest, pool: Pool, owner: Owner) -> pt.PretradeResult:
    """A pass/flag list for a proposed buy, add, sale or switch, citing the strategy's principles
    8, 9 and 10 and your own caps. It never blocks and never places a trade."""
    try:
        return await svc.run_pretrade(pool, owner.user_id, await svc.canonical_trades(pool, body))
    except svc.NoHoldings as err:
        raise _no_holdings() from err
    except svc.NoStrategy as err:
        raise _no_strategy() from err


# ------------------------------------------------------------------------------------- saved


class SaveShock(saved.SavedAnalysisRequest):
    shocks: ShockSet
    assumptions: ShockAssumptions = Field(default_factory=ShockAssumptions)


class SaveTheme(saved.SavedAnalysisRequest):
    theme: ThemeDefinition


class SaveWhatIf(saved.SavedAnalysisRequest):
    case: whatif.WhatIfCase


class SaveRebalance(saved.SavedAnalysisRequest):
    request: rb.RebalanceRequest = Field(default_factory=rb.RebalanceRequest)


class SavedOpen(BaseModel):
    item: saved.SavedAnalysisItem
    payload: dict[str, Any] = Field(
        description="{request, result}: the inputs and the result exactly as they stood when saved."
    )


async def _save(
    pool: Pool, owner: Owner, kind: saved.SavedKind, body: saved.SavedAnalysisRequest,
    request: BaseModel, result: BaseModel, method: str, as_of: Any = None,
) -> saved.SavedAnalysisItem:  # fmt: skip
    payload = {
        "request": request.model_dump(mode="json"),
        "result": result.model_dump(mode="json"),
    }
    return await saved.save(
        pool,
        owner.user_id,
        kind,
        saved.SavedAnalysisRequest(name=body.name, note=body.note),
        payload,
        method_version=method,
        as_of=as_of,
    )


@router.post("/scenarios/shock/save", status_code=201)
async def save_shock(body: SaveShock, pool: Pool, owner: Owner) -> saved.SavedAnalysisItem:
    """Keep a shock scenario: the server runs it now and stores the inputs with the result."""
    assumptions = await svc.canonical_assumptions(pool, body.assumptions)
    try:
        run = await svc.run_shock(pool, owner.user_id, body.shocks, assumptions)
    except svc.NoHoldings as err:
        raise _no_holdings() from err
    return await _save(
        pool, owner, "shock_scenario", body,
        ShockRequest(shocks=body.shocks, assumptions=assumptions), run,
        run.result.method_version,
    )  # fmt: skip


@router.post("/scenarios/themes/save", status_code=201)
async def save_theme(body: SaveTheme, pool: Pool, owner: Owner) -> saved.SavedAnalysisItem:
    loaded = await xray_service.load_cached(pool, owner.user_id)
    if loaded.data.portfolio is None:
        raise _no_holdings()
    result = theme_exposure(body.theme, xray_service.view_context(loaded))
    return await _save(
        pool, owner, "theme_exposure", body, body.theme, result, version("theme_exposure")
    )


@router.post("/scenarios/whatif/save", status_code=201)
async def save_whatif(body: SaveWhatIf, pool: Pool, owner: Owner) -> saved.SavedAnalysisItem:
    case = await _canonical_case(pool, body.case)
    try:
        result = await whatif.run_case(pool, owner.user_id, case)
    except svc.NoHoldings as err:
        raise _no_holdings() from err
    except (whatif.CaseError, ProposalError) as err:
        raise HTTPException(422, str(err)) from err
    return await _save(pool, owner, "whatif_case", body, case, result, version("whatif_case"))


@router.post("/rebalance/save", status_code=201)
async def save_rebalance(body: SaveRebalance, pool: Pool, owner: Owner) -> saved.SavedAnalysisItem:
    request = await svc.canonical_overrides(pool, body.request)
    try:
        result = await svc.run_rebalance(pool, owner.user_id, request)
    except svc.NoHoldings as err:
        raise _no_holdings() from err
    except svc.NoStrategy as err:
        raise _no_strategy() from err
    except ValueError as err:
        raise HTTPException(422, str(err)) from err
    return await _save(
        pool, owner, "rebalance_plan", body, request, result, version("rebalance"),
        as_of=dt.date.fromisoformat(result.as_of),
    )  # fmt: skip


def _kind(kind: str) -> saved.SavedKind:
    if kind not in SAVED_KINDS:
        raise HTTPException(422, f"kind must be one of {', '.join(SAVED_KINDS)}")
    return kind  # type: ignore[return-value]


@router.get("/decision/saved")
async def list_decision_saved(
    pool: Pool, owner: Owner, kind: Annotated[str, Query(max_length=30)]
) -> list[saved.SavedAnalysisItem]:
    """Saved shock scenarios, themes, what-if cases or rebalance plans, newest first."""
    return await saved.list_saved(pool, owner.user_id, _kind(kind))


@router.get("/decision/saved/{item_id}")
async def open_decision_saved(
    item_id: int, pool: Pool, owner: Owner, kind: Annotated[str, Query(max_length=30)]
) -> SavedOpen:
    """A saved item as it stood: the inputs and the result, not a recomputation."""
    found = await saved.load_saved(pool, owner.user_id, _kind(kind), item_id)
    if found is None:
        raise HTTPException(404, "No such saved item.")
    return SavedOpen(item=found[0], payload=found[1])


@router.delete("/decision/saved/{item_id}", status_code=204)
async def archive_decision_saved(
    item_id: int, pool: Pool, owner: Owner, kind: Annotated[str, Query(max_length=30)]
) -> None:
    if not await saved.archive(pool, owner.user_id, _kind(kind), item_id):
        raise HTTPException(404, "No such saved item.")
