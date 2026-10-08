"""Authenticated read-only routes for the Portfolio X-Ray (plan Phase 5)."""

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from api.auth import Owner
from api.db import Pool
from steward import saved
from steward.events import feed as events_feed
from steward.events.feed import EventFeed
from steward.events.paths import Direction, EventImpact, EventState, event_paths
from steward.lookthrough.read import PortfolioIn
from steward.xray import (
    derive,
    fund_relative,
    index_weights,
    owner_input,
    service,
    supply_chain,
)
from steward.xray.benchmark import XrayBenchmark
from steward.xray.exports import xray_csv
from steward.xray.fund_relative import FundRelative
from steward.xray.index_weights import IndexComparisons
from steward.xray.intersection import XrayIntersection
from steward.xray.metrics import XrayFundamentals
from steward.xray.owner_input import (
    IndexWeightsRequest,
    OwnerInput,
    RegionSplitRequest,
    SupplyEdgeRequest,
)
from steward.xray.reports import (
    XrayConcentrationResponse,
    XrayResponse,
    concentration_response,
    intersection_response,
    xray_response,
)
from steward.xray.scenario import ProposalError, ProposedRequest, XrayProposal
from steward.xray.supply_chain import SupplyChain

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


@router.get("/funds-vs-benchmark")
async def get_funds_vs_benchmark(pool: Pool, owner: Owner, as_of: AsOf = None) -> FundRelative:
    """Each held fund's annualised tracking difference against the benchmark you recorded for it,
    same basis and currency only, plus the benchmark proxy fund against its own benchmark."""
    return await fund_relative.build(pool, owner.user_id, await _loaded(pool, owner, as_of))


@router.get("/supply-chain")
async def get_supply_chain(pool: Pool, owner: Owner, as_of: AsOf = None) -> SupplyChain:
    """The supply-chain graph from the edges you entered, each company placed against what the
    portfolio holds in it, and the edges where you hold both ends."""
    loaded = await _loaded(pool, owner, as_of)
    return supply_chain.build(
        loaded.leafset,
        await owner_input.current(pool, owner.user_id),
        await supply_chain.filing_edges(pool, loaded.leafset),
    )


@router.get("/event-paths")
async def get_event_paths(
    pool: Pool,
    owner: Owner,
    symbol: Annotated[str, Query(min_length=1, max_length=20)],
    direction: Direction = "downstream",
    state: EventState = "reported",
) -> EventImpact:
    """Where an event at one company reaches the portfolio along the supply-chain links, with
    each link's source and confidence, the weight at the end of each path and the thesis
    indicators to watch. A path is only as confident as its weakest link."""
    chain, indicators = await _chain_and_indicators(pool, owner)
    return event_paths(chain, symbol, direction, state, indicators)


@router.get("/event-feed")
async def get_event_feed(
    pool: Pool, owner: Owner, days: Annotated[int, Query(ge=1, le=90)] = events_feed.DEFAULT_DAYS
) -> EventFeed:
    """Recent classified news about a company, followed along the supply-chain links to what you
    hold. Only stories that reach another holding are listed; nothing is sent as an alert."""
    chain, indicators = await _chain_and_indicators(pool, owner)
    today = dt.date.today()
    since = today - dt.timedelta(days=days)
    stories = await events_feed.load_stories(pool, since)
    return events_feed.build(stories, chain, indicators, today, since)


async def _chain_and_indicators(
    pool: Pool, owner: Owner
) -> tuple[SupplyChain, dict[str, list[str]]]:
    return await events_feed.chain_and_indicators(pool, owner.user_id)


@router.get("/index-weights")
async def get_index_weights(pool: Pool, owner: Owner, as_of: AsOf = None) -> IndexComparisons:
    """Active share against each list of index weights you entered."""
    loaded = await _loaded(pool, owner, as_of)
    return index_weights.build(loaded.leafset, await owner_input.current(pool, owner.user_id))


@router.get("/inputs")
async def list_owner_inputs(pool: Pool, owner: Owner) -> list[OwnerInput]:
    """Everything you entered by hand for the X-Ray: regional splits, supply-chain edges and index
    weights, each with its source and date. The newest entry for each name is the one in force."""
    return await owner_input.current(pool, owner.user_id)


@router.post("/inputs/regions", status_code=201)
async def save_region_split(body: RegionSplitRequest, pool: Pool, owner: Owner) -> OwnerInput:
    """Spread a region such as "Europe" over countries, so a company's reported "Europe" revenue
    reaches the revenue-country view. It is your estimate, labelled as yours there."""
    result = await owner_input.save(
        pool, owner.user_id, "region_split", body.region, {"countries": body.countries}, body
    )
    service.drop_cache(owner.user_id)
    return result


@router.post("/inputs/supply-edges", status_code=201)
async def save_supply_edge(body: SupplyEdgeRequest, pool: Pool, owner: Owner) -> OwnerInput:
    """One supplier-to-customer link, with the product and where you read it."""
    return await owner_input.save(
        pool, owner.user_id, "supply_edge", f"{body.supplier}>{body.customer}",
        {"supplier": body.supplier, "customer": body.customer, "product": body.product,
         "customer_share": body.customer_share}, body,
    )  # fmt: skip


@router.post("/inputs/index-weights", status_code=201)
async def save_index_weights(body: IndexWeightsRequest, pool: Pool, owner: Owner) -> OwnerInput:
    """The constituents and weights of an index, pasted from its provider's factsheet."""
    return await owner_input.save(
        pool, owner.user_id, "index_weights", body.label,
        {"constituents": [c.model_dump() for c in body.constituents]}, body,
    )  # fmt: skip


class DeriveIndexRequest(BaseModel):
    fund_isin: str = Field(pattern=r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
    label: str | None = Field(
        default=None, max_length=120, description="Defaults to the fund's label."
    )


class DeriveRegionRequest(BaseModel):
    region: str = Field(min_length=1, max_length=60)
    fund_isin: str = Field(pattern=r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


class DeriveResult(BaseModel):
    item: OwnerInput | None = None
    changed: bool = False
    saved: int = 0
    message: str | None = None


@router.post("/inputs/derive/index-weights")
async def derive_index_weights(body: DeriveIndexRequest, pool: Pool, owner: Owner) -> DeriveResult:
    """Fill an index's weights from the stored holdings of a fund that tracks it (equity rows,
    renormalised), with the fund's data address and snapshot date. Recomputed whenever the fund's
    holdings are refreshed."""
    try:
        item, changed = await derive.derive_index_weights(
            pool, owner.user_id, body.fund_isin, body.label
        )
    except derive.DeriveError as err:
        raise HTTPException(422, str(err)) from err
    return DeriveResult(item=item, changed=changed)


@router.post("/inputs/derive/regions")
async def derive_region(body: DeriveRegionRequest, pool: Pool, owner: Owner) -> DeriveResult:
    """Spread a region over countries the way a fund you name for it spreads its equity."""
    try:
        item, changed = await derive.derive_region_split(
            pool, owner.user_id, body.region, body.fund_isin
        )
    except derive.DeriveError as err:
        raise HTTPException(422, str(err)) from err
    if changed:
        service.drop_cache(owner.user_id)
    return DeriveResult(item=item, changed=changed)


@router.post("/inputs/derive/benchmarks")
async def derive_benchmarks(pool: Pool, owner: Owner) -> DeriveResult:
    """Index weights for every configured benchmark proxy that has stored holdings."""
    saved = await derive.derive_benchmark_indexes(pool, owner.user_id)
    message = None if saved else "Nothing new: no benchmark proxy has newer stored holdings."
    return DeriveResult(saved=saved, message=message)


@router.delete("/inputs/{input_id}", status_code=204)
async def archive_owner_input(input_id: int, pool: Pool, owner: Owner) -> None:
    if not await owner_input.archive(pool, owner.user_id, input_id):
        raise HTTPException(404, "no such entry")
    service.drop_cache(owner.user_id)


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
