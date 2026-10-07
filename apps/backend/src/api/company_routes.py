# ruff: noqa: E501 - response text stays on one line each
"""Owner-only company workbench (plan Phase 2): statements, ratios, peers, valuation scenarios
and the evidence tabs for one company. Reads stored facts and computes figures in the backend;
the only writes are the owner's saved peer groups, valuation scenarios and cited case items.
"""

import datetime as dt
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Response, status

from api.auth import Owner
from api.db import Pool
from steward.company import evidence, ratios, service, valuation
from steward.company.statements import (
    StatementsView,
    build_view,
    csv_text,
    load_rows,
)

router = APIRouter(prefix="/companies", tags=["companies"])

TickerPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,32}$")]
Scope = Literal["none", "confirmed", "group", "home_market", "global"]


async def _company(pool: Pool, ticker: str) -> service.Company:
    company = await service.load_company(pool, ticker)
    if company is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{ticker} is not in the covered universe")
    return company


@router.get("/{ticker}/overview", response_model=evidence.Overview)
async def get_overview(ticker: TickerPath, pool: Pool, _owner: Owner) -> evidence.Overview:
    """Issuer facts, the other listings of the same issuer, provider profile, latest segments,
    confirmed competitors and peers, upcoming events and a coverage score that says what is missing."""
    out = await evidence.overview(pool, ticker)
    if out is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{ticker} is not in the covered universe")
    return out


@router.get("/{ticker}/statements", response_model=StatementsView)
async def get_statements(
    ticker: TickerPath,
    pool: Pool,
    _owner: Owner,
    basis: Literal["standardized", "reported"] = "standardized",
    frequency: Literal["annual", "quarterly"] = "annual",
    view: Literal["restated", "original"] = "restated",
    units: Literal["1", "1000", "1000000"] = "1000000",
    transform: Literal["none", "common_size", "yoy"] = "none",
    per_share: bool = False,
    known_by: Annotated[
        dt.date | None, Query(description="Point-in-time cutoff on the filing date.")
    ] = None,
) -> StatementsView:
    """Income statement, balance sheet and cash-flow statement, built from stored filed facts.
    Every cell names its filing and concept or how it was derived; restated figures keep the original."""
    cutoff = dt.datetime.combine(known_by, dt.time.max, tzinfo=dt.UTC) if known_by else None
    rows = await load_rows(pool, ticker, cutoff)
    return build_view(
        ticker,
        rows,
        basis=basis,
        frequency=frequency,
        view=view,
        units=int(units),
        transform=transform,
        per_share=per_share,
    )


@router.get("/{ticker}/statements.csv")
async def get_statements_csv(
    ticker: TickerPath,
    pool: Pool,
    _owner: Owner,
    basis: Literal["standardized", "reported"] = "standardized",
    frequency: Literal["annual", "quarterly"] = "annual",
    view: Literal["restated", "original"] = "restated",
    units: Literal["1", "1000", "1000000"] = "1000000",
) -> Response:
    rows = await load_rows(pool, ticker)
    built = build_view(ticker, rows, basis=basis, frequency=frequency, view=view, units=int(units))
    return Response(
        csv_text(built),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{ticker}-{basis}-{frequency}.csv"'},
    )


@router.get("/{ticker}/ratios", response_model=ratios.RatiosView)
async def get_ratios(
    ticker: TickerPath,
    pool: Pool,
    owner: Owner,
    peers: Scope = "none",
    group_id: int | None = None,
) -> ratios.RatiosView:
    """Every ratio from the shared library per fiscal year and TTM, with the company's own
    medians, the peer median and percentile (withheld below five comparable peers) and the inputs."""
    company = await _company(pool, ticker)
    peer_values = None
    scope_label = None
    excluded: list[ratios.PeerExcluded] = []
    if peers != "none":
        peer_values, scope_label, excluded = await service.peer_values_for(
            pool, company, peers, owner.user_id, group_id
        )
    fwd = await evidence.forward_eps(pool, ticker)
    out = ratios.build_ratios(
        ticker,
        company.annual,
        company.quarterly,
        company.market,
        sector=company.sector,
        industry=company.industry,
        peers=peer_values,
        peer_scope=scope_label,
        peer_excluded=excluded,
        forward_eps=fwd,
    )
    # Reverse-DCF growth comes from the valuation tab's default scenario, at its stated discount rate.
    val = valuation.build_valuation(
        ticker,
        company.annual,
        company.quarterly,
        company.market,
        None,
        valuation.ValuationRequest(selected="base"),
    )
    implied = valuation.implied_growth_value(val)
    for g in out.groups:
        for r in g.ratios:
            if r.id == "reverse_dcf_growth" and r.latest is not None and r.points:
                r.latest.value = implied.value
                r.latest.reason = implied.reason
                r.points[-1].value, r.points[-1].reason = implied.value, implied.reason
    return out


@router.get("/{ticker}/peers", response_model=service.PeerCompare)
async def get_peer_compare(
    ticker: TickerPath,
    pool: Pool,
    owner: Owner,
    scope: Literal["confirmed", "group", "home_market", "global"] = "global",
    group_id: int | None = None,
    metrics: Annotated[str | None, Query(description="Comma-separated ratio ids.")] = None,
    index_metric: Literal["revenue", "operating_income", "net_income", "fcf"] = "revenue",
    years: Annotated[int, Query(ge=2, le=10)] = 5,
) -> service.PeerCompare:
    """The company beside its peers on the same ratios and fiscal basis, with median and range,
    fiscal-date distance, comparability warnings and an indexed history relative to each first year."""
    company = await _company(pool, ticker)
    ids = tuple(m for m in (metrics or "").split(",") if m) or service.COMPARE_DEFAULT
    return await service.compare(
        pool, company, scope, owner.user_id, group_id, ids, index_metric, years
    )


@router.get("/{ticker}/peer-groups", response_model=list[service.PeerGroup])
async def list_peer_groups(ticker: TickerPath, pool: Pool, owner: Owner) -> list[service.PeerGroup]:
    return await service.list_groups(pool, owner.user_id, ticker)


@router.post(
    "/{ticker}/peer-groups", response_model=service.PeerGroup, status_code=status.HTTP_201_CREATED
)
async def save_peer_group(
    ticker: TickerPath, body: service.PeerGroupIn, pool: Pool, owner: Owner
) -> service.PeerGroup:
    return await service.save_group(pool, owner.user_id, ticker, body)


@router.delete("/{ticker}/peer-groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_peer_group(ticker: TickerPath, group_id: int, pool: Pool, owner: Owner) -> None:
    if not await service.delete_group(pool, owner.user_id, ticker, group_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such peer group")


@router.get("/{ticker}/valuation/defaults", response_model=list[valuation.Assumptions])
async def valuation_defaults(
    ticker: TickerPath, pool: Pool, _owner: Owner
) -> list[valuation.Assumptions]:
    """Starting scenarios built from the company's own filed growth. They are assumptions to edit."""
    company = await _company(pool, ticker)
    return valuation.default_scenarios(company.annual)


@router.post("/{ticker}/valuation", response_model=valuation.CompanyValuation)
async def run_valuation(
    ticker: TickerPath, body: valuation.ValuationRequest, pool: Pool, owner: Owner
) -> valuation.CompanyValuation:
    """Scenario DCF with an explicit discount-rate build-up, a sensitivity grid, a reverse DCF and
    reference multiples. No single value: a margin of safety is computed only for `selected`."""
    company = await _company(pool, ticker)
    peer_values, label, excluded = await service.peer_values_for(
        pool, company, "global", owner.user_id, None
    )
    view = ratios.build_ratios(
        ticker,
        company.annual,
        company.quarterly,
        company.market,
        sector=company.sector,
        industry=company.industry,
        peers=peer_values,
        peer_scope=label,
        peer_excluded=excluded,
    )
    return valuation.build_valuation(
        ticker, company.annual, company.quarterly, company.market, view, body
    )


@router.get("/{ticker}/valuation/saved", response_model=list[service.SavedScenario])
async def list_saved_scenarios(
    ticker: TickerPath, pool: Pool, owner: Owner
) -> list[service.SavedScenario]:
    return await service.list_scenarios(pool, owner.user_id, ticker)


@router.post(
    "/{ticker}/valuation/saved",
    response_model=service.SavedScenario,
    status_code=status.HTTP_201_CREATED,
)
async def save_scenarios(
    ticker: TickerPath, body: service.SaveScenarioIn, pool: Pool, owner: Owner
) -> service.SavedScenario:
    """Saves an immutable version of the scenario set with the method version it was valued under."""
    company = await _company(pool, ticker)
    result = valuation.build_valuation(
        ticker, company.annual, company.quarterly, company.market, None, body.request
    )
    from steward.analytics.contract import input_hash

    return await service.save_scenario(
        pool,
        owner.user_id,
        ticker,
        body,
        input_hash(
            {
                "statements": company.annual.input_hash,
                "request": body.request.model_dump(mode="json"),
                "price": result.price,
            }
        ),
    )


@router.get("/{ticker}/estimates", response_model=evidence.Estimates)
async def get_estimates(ticker: TickerPath, pool: Pool, _owner: Owner) -> evidence.Estimates:
    """Forward consensus (only with provider access and a full metadata record), event-calendar
    estimates and company guidance, kept apart; a surprise only when the basis matches the actual."""
    company = await _company(pool, ticker)
    return await evidence.estimates(pool, ticker, company.annual)


@router.get("/{ticker}/network", response_model=evidence.Network)
async def get_network(
    ticker: TickerPath,
    pool: Pool,
    _owner: Owner,
    since: Annotated[
        dt.date | None, Query(description="Mark rows first seen on or after this date.")
    ] = None,
) -> evidence.Network:
    """Confirmed relationships and deal events, and the patent-family mix, with ownership-mapping caveats."""
    return await evidence.network(pool, ticker, since)


@router.get("/{ticker}/capital-allocation", response_model=evidence.CapitalAllocation)
async def get_capital_allocation(
    ticker: TickerPath, pool: Pool, _owner: Owner
) -> evidence.CapitalAllocation:
    """Observed free cash flow, dividends, buybacks, debt and share count per fiscal year, with
    per-share growth beside aggregate growth, and the company's stated actions listed separately."""
    company = await _company(pool, ticker)
    return await evidence.capital_allocation(pool, ticker, company.annual)


@router.get("/{ticker}/governance", response_model=evidence.Governance)
async def get_governance(ticker: TickerPath, pool: Pool, _owner: Owner) -> evidence.Governance:
    """Dated executive and board roles from confirmed graph edges and disclosed concentration.
    Insider transactions and 13F holders are served by their own endpoints and linked in the tab."""
    return await evidence.governance(pool, ticker)


@router.get("/{ticker}/case", response_model=evidence.CaseView)
async def get_case(ticker: TickerPath, pool: Pool, owner: Owner) -> evidence.CaseView:
    return await evidence.case_view(pool, owner.user_id, ticker)


@router.post(
    "/{ticker}/case", response_model=evidence.CaseItem, status_code=status.HTTP_201_CREATED
)
async def add_case_item(
    ticker: TickerPath, body: evidence.CaseItemIn, pool: Pool, owner: Owner
) -> evidence.CaseItem:
    if (problem := evidence.case_input_problem(body)) is not None:
        raise HTTPException(422, problem)
    item = await evidence.save_case_item(pool, owner.user_id, ticker, body)
    assert item is not None
    return item


@router.put("/{ticker}/case/{item_id}", response_model=evidence.CaseItem)
async def update_case_item(
    ticker: TickerPath, item_id: int, body: evidence.CaseItemIn, pool: Pool, owner: Owner
) -> evidence.CaseItem:
    if (problem := evidence.case_input_problem(body)) is not None:
        raise HTTPException(422, problem)
    item = await evidence.save_case_item(pool, owner.user_id, ticker, body, item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such case item")
    return item


@router.delete("/{ticker}/case/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_case(ticker: TickerPath, item_id: int, pool: Pool, owner: Owner) -> None:
    if not await evidence.archive_case_item(pool, owner.user_id, ticker, item_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such case item")
