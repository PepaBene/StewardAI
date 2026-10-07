"""Authenticated read-only routes for the Portfolio X-Ray (plan Phase 5)."""

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from api.auth import Owner
from api.db import Pool
from steward import saved
from steward.lookthrough.read import PortfolioIn
from steward.xray import service
from steward.xray.benchmark import XrayBenchmark
from steward.xray.exports import xray_csv
from steward.xray.intersection import XrayIntersection
from steward.xray.metrics import XrayFundamentals
from steward.xray.reports import (
    XrayConcentrationResponse,
    XrayResponse,
    concentration_response,
    intersection_response,
    xray_response,
)
from steward.xray.scenario import ProposalError, ProposedRequest, XrayProposal

router = APIRouter(prefix="/portfolio/xray", tags=["portfolio"])


AsOf = Annotated[
    dt.date | None,
    Query(description="Show the holdings as they were on this date (the latest snapshot before)."),
]


async def _loaded(pool: Pool, owner: Owner, as_of: dt.date | None) -> service.Loaded:
    """Today's X-Ray inputs (cached for a minute), or those of a past date."""
    if as_of is None:
        return await service.load_cached(pool, owner.user_id)
    if as_of > dt.datetime.now(dt.UTC).date():
        raise HTTPException(422, "The date is in the future.")
    try:
        return await service.load(pool, owner.user_id, as_of=as_of)
    except service.NoHoldingsThen as err:
        raise HTTPException(404, "No holdings were stored on or before that date.") from err


@router.get("")
async def get_xray(pool: Pool, owner: Owner, as_of: AsOf = None) -> XrayResponse:
    """Selectable look-through views of the whole portfolio (asset class, sector, industry,
    country, region, currency, issuer, fund, style proxy, market-cap band, fees, and country of
    revenue, commodity and trade product where data exist). Each view adds up to the whole
    portfolio with cash and unknown weight as their own buckets; each bucket names its
    contributors and how it was classified."""
    return xray_response(await _loaded(pool, owner, as_of))


@router.get("/export", response_class=PlainTextResponse)
async def export_xray(
    pool: Pool,
    owner: Owner,
    lens: Annotated[str | None, Query(max_length=30)] = None,
    as_of: AsOf = None,
) -> PlainTextResponse:
    """The X-Ray buckets as CSV, one lens or all of them."""
    response = xray_response(await _loaded(pool, owner, as_of))
    name = lens or "all"
    return PlainTextResponse(
        xray_csv(response.views, lens),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="xray-{name}.csv"'},
    )


@router.get("/overlap")
async def get_overlap(pool: Pool, owner: Owner, as_of: AsOf = None) -> XrayIntersection:
    """Stock intersection: issuers reached through more than one channel, each fund's
    contribution and the trail to the securities, and the concentration if a fund were removed."""
    return intersection_response(await _loaded(pool, owner, as_of))


@router.get("/concentration")
async def get_concentration(
    pool: Pool, owner: Owner, as_of: AsOf = None
) -> XrayConcentrationResponse:
    """Top-10, HHI and effective holdings at security, issuer, sector, country and fund level,
    with benchmark-relative over- and underweights where the classification is like-for-like."""
    return concentration_response(await _loaded(pool, owner, as_of))


@router.get("/fundamentals")
async def get_fundamentals(pool: Pool, owner: Owner, as_of: AsOf = None) -> XrayFundamentals:
    """Weighted earnings yield and P/E, price to book, dividend yield, profitability, leverage
    and fee drag, each with its coverage; meaningless aggregates are withheld with the reason."""
    return (await _loaded(pool, owner, as_of)).analysis.fundamentals


@router.get("/benchmark")
async def get_benchmark(pool: Pool, owner: Owner, as_of: AsOf = None) -> XrayBenchmark:
    """Equity X-Ray against the configured benchmark proxy (an ETF's holdings today, not the
    index's historical membership); only like-for-like classifications are compared."""
    result, _ = service.benchmark_result(await _loaded(pool, owner, as_of))
    return result


class XrayCandidates(BaseModel):
    items: list[service.XrayCandidate]


@router.get("/candidates")
async def get_candidates(
    pool: Pool,
    owner: Owner,
    q: Annotated[str | None, Query(max_length=60)] = None,
) -> XrayCandidates:
    """Watchlist securities, and a search of the investable universe, that can be added to a
    proposed portfolio."""
    loaded = await service.load_cached(pool, owner.user_id)
    pf: PortfolioIn | None = loaded.data.portfolio
    held = {p.ticker for p in pf.positions} if pf else set()
    funds = {f.isin: f.isin in loaded.data.snapshots for f in loaded.settings.funds}
    return XrayCandidates(items=await service.candidates(pool, owner.user_id, q, held, funds))


class XraySaveScenarioRequest(saved.SavedAnalysisRequest):
    scenario: ProposedRequest


class XraySavedScenario(BaseModel):
    item: saved.SavedAnalysisItem
    scenario: ProposedRequest


@router.get("/scenarios")
async def list_xray_scenarios(pool: Pool, owner: Owner) -> list[saved.SavedAnalysisItem]:
    """Your saved what-if scenarios, newest first."""
    return await saved.list_saved(pool, owner.user_id, "xray_scenario")


@router.post("/scenarios", status_code=201)
async def save_xray_scenario(
    pool: Pool, owner: Owner, body: XraySaveScenarioRequest
) -> saved.SavedAnalysisItem:
    """Keep a what-if. Only the proposed weights are stored; opening it runs them against the
    holdings you have then."""
    return await saved.save(
        pool,
        owner.user_id,
        "xray_scenario",
        body,
        body.scenario.model_dump(mode="json"),
    )


@router.get("/scenarios/{scenario_id}")
async def open_xray_scenario(pool: Pool, owner: Owner, scenario_id: int) -> XraySavedScenario:
    found = await saved.load_saved(pool, owner.user_id, "xray_scenario", scenario_id)
    if found is None:
        raise HTTPException(404, "No such saved scenario.")
    return XraySavedScenario(item=found[0], scenario=ProposedRequest.model_validate(found[1]))


@router.delete("/scenarios/{scenario_id}", status_code=204)
async def archive_xray_scenario(pool: Pool, owner: Owner, scenario_id: int) -> None:
    if not await saved.archive(pool, owner.user_id, "xray_scenario", scenario_id):
        raise HTTPException(404, "No such saved scenario.")


@router.post("/proposed")
async def post_proposed(pool: Pool, owner: Owner, body: ProposedRequest) -> XrayProposal:
    """Before and after X-Ray of hypothetical weights and added securities. Applied to a copy:
    nothing is written and the saved holdings are untouched."""
    try:
        loaded = await service.load_cached(pool, owner.user_id)
        return await service.propose(pool, owner.user_id, body, loaded=loaded)
    except ProposalError as err:
        raise HTTPException(422, str(err)) from err
