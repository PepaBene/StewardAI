# ruff: noqa: E501 - response text stays on one line each
"""Owner-only fund and ETF research (plan Phase 3): profile, holdings, concentration and style
proxies, performance against a documented benchmark, comparison, bond-fund characteristics and a
sourced due-diligence checklist. Reads stored issuer data; the only write is the owner's sourced
fund facts. Wiring: `app.include_router` of `router` in api/main.py."""

import datetime as dt
import re
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Response, status

from api.auth import Owner
from api.db import Pool
from steward.fundresearch import analysis, compare, performance, service
from steward.fundresearch.data import (
    FACT_KEYS,
    NUMERIC_FACTS,
    CatalogRow,
    Fact,
    FactIn,
    FundData,
    NavIn,
    NavPoint,
    SavedComparison,
    SavedComparisonIn,
    archive_comparison,
    list_comparisons,
    list_funds,
    load_fact_history,
    load_fund,
    save_comparison,
    save_fact,
    save_nav,
)

router = APIRouter(tags=["fund research"])

IsinPath = Annotated[str, Path(pattern=r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")]
AsOf = Annotated[
    dt.date | None, Query(description="Answer as of this date, using only data fetched by then.")
]


def _check_as_of(as_of: dt.date | None) -> None:
    if as_of is not None and as_of > dt.datetime.now(dt.UTC).date():
        raise HTTPException(422, "as_of cannot be in the future")


async def _fund(pool: Pool, isin: str, owner: Owner, as_of: dt.date | None) -> FundData:
    _check_as_of(as_of)
    data = await load_fund(pool, isin, owner.user_id, as_of)
    if data is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"{isin} is not a fund the app holds data for"
        )
    return data


@router.get("/funds/catalog", response_model=list[CatalogRow])
async def fund_catalog(pool: Pool, _owner: Owner) -> list[CatalogRow]:
    """Every fund with a stored factsheet or holdings snapshot: the covered fund universe, not all funds."""
    return await list_funds(pool)


@router.get("/funds/compare", response_model=compare.FundComparison)
async def compare_funds(
    pool: Pool,
    owner: Owner,
    isins: Annotated[str, Query(description="Two to five fund ISINs, comma-separated.")],
    as_of: AsOf = None,
) -> compare.FundComparison:
    """Costs, overlap (sum of the smaller issuer weight, plus the shared holdings), geography, sector,
    concentration, return and look-through completeness. Share classes of one portfolio are not compared with each other."""
    _check_as_of(as_of)
    wanted = list(dict.fromkeys(s.strip().upper() for s in isins.split(",") if s.strip()))
    if not 2 <= len(wanted) <= 5:
        raise HTTPException(422, "choose two to five funds")
    for isin in wanted:
        if (
            len(isin) != 12
            or not isin[:2].isalpha()
            or not isin[2:11].isalnum()
            or not isin[11].isdigit()
        ):
            raise HTTPException(422, f"{isin} is not an ISIN")
    result, missing = await service.comparison(pool, wanted, owner.user_id, as_of)
    if result is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "fewer than two of those funds have stored data"
            + (f" (no data for {', '.join(missing)})" if missing else ""),
        )
    if missing:
        result.warnings.append(f"No stored data for {', '.join(missing)}; left out.")
    return result


@router.get("/funds/{isin}/profile", response_model=analysis.FundProfile)
async def fund_profile(
    isin: IsinPath, pool: Pool, owner: Owner, as_of: AsOf = None
) -> analysis.FundProfile:
    return analysis.build_profile(await _fund(pool, isin, owner, as_of))


@router.get("/funds/{isin}/holdings", response_model=analysis.HoldingsView)
async def fund_holdings(
    isin: IsinPath,
    pool: Pool,
    owner: Owner,
    as_of: AsOf = None,
    top: Annotated[int, Query(ge=5, le=200)] = 25,
) -> analysis.HoldingsView:
    """Issuer-normalised top holdings with asset-class, sector, country and currency weights; the weight
    the published rows do not account for stays its own unknown bucket."""
    data = await _fund(pool, isin, owner, as_of)
    _, view = await service.holdings(pool, data, top)
    return view


@router.get("/funds/{isin}/holdings.csv")
async def fund_holdings_csv(
    isin: IsinPath, pool: Pool, owner: Owner, as_of: AsOf = None
) -> Response:
    """The derived exposure table with provenance on every row (not the issuer's own file)."""
    data = await _fund(pool, isin, owner, as_of)
    rows, view = await service.holdings(pool, data)
    if not rows:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "no holdings snapshot is stored for this fund"
        )
    return Response(
        analysis.exposure_csv(view, rows),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{isin}-holdings-{view.as_of}.csv"'},
    )


@router.get("/funds/{isin}/style", response_model=analysis.StyleView)
async def fund_style(
    isin: IsinPath, pool: Pool, owner: Owner, as_of: AsOf = None
) -> analysis.StyleView:
    """FundConcentration, size mix and weighted valuation and profitability proxies from the holdings."""
    data = await _fund(pool, isin, owner, as_of)
    rows, view = await service.holdings(pool, data)
    return await service.style(pool, data, rows, view, as_of)


@router.get("/funds/{isin}/performance", response_model=performance.PerformanceView)
async def fund_performance(
    isin: IsinPath,
    pool: Pool,
    owner: Owner,
    ticker: Annotated[
        str | None,
        Query(description="The exchange listing to use; default is the first with prices."),
    ] = None,
    benchmark: Annotated[
        str | None, Query(description="A benchmark symbol from the options in the response.")
    ] = None,
    as_of: AsOf = None,
) -> performance.PerformanceView:
    """FundListing price return, total return where distributions are recorded, and tracking difference against a
    benchmark only when both are on the same basis and currency."""
    data = await _fund(pool, isin, owner, as_of)
    return await service.performance_view(pool, data, ticker, benchmark, as_of)


@router.get("/funds/{isin}/bond", response_model=analysis.BondView)
async def fund_bond(
    isin: IsinPath, pool: Pool, owner: Owner, as_of: AsOf = None
) -> analysis.BondView:
    """Maturity, duration and credit quality only where the issuer data provide them; otherwise unavailable."""
    data = await _fund(pool, isin, owner, as_of)
    mix, source = await service.nport_mix(pool, data)
    return analysis.build_bond(data, mix, source, await service.nport_risk(pool, data))


@router.get("/funds/{isin}/checklist", response_model=analysis.Checklist)
async def fund_checklist(
    isin: IsinPath, pool: Pool, owner: Owner, as_of: AsOf = None
) -> analysis.Checklist:
    """Each due-diligence criterion with its rule and source; no score, and no summary when a critical input is missing."""
    data = await _fund(pool, isin, owner, as_of)
    return await service.checklist(pool, data, as_of)


@router.get("/funds/{isin}/facts", response_model=dict[str, Fact])
async def fund_facts(isin: IsinPath, pool: Pool, owner: Owner) -> dict[str, Fact]:
    """The owner's current sourced facts about the fund (newest per key)."""
    data = await _fund(pool, isin, owner, None)
    return data.facts


@router.get("/funds/{isin}/facts/history", response_model=list[Fact])
async def fund_fact_history(isin: IsinPath, pool: Pool, owner: Owner) -> list[Fact]:
    await _fund(pool, isin, owner, None)
    return await load_fact_history(pool, owner.user_id, isin)


@router.put("/funds/{isin}/facts", response_model=Fact, status_code=status.HTTP_201_CREATED)
async def put_fund_fact(isin: IsinPath, body: FactIn, pool: Pool, owner: Owner) -> Fact:
    """Record a sourced fact. The record is append-only: a correction is a new entry and the newest wins."""
    await _fund(pool, isin, owner, None)
    if body.key not in FACT_KEYS:
        raise HTTPException(422, f"key must be one of {', '.join(FACT_KEYS)}")
    if not body.source_url.startswith(("http://", "https://")):
        raise HTTPException(422, "source_url must be a web address")
    if body.key == "replication_method" and body.value not in (
        "physical_full",
        "physical_sampled",
        "synthetic",
    ):
        raise HTTPException(
            422, "replication_method must be physical_full, physical_sampled or synthetic"
        )
    if body.key in NUMERIC_FACTS:
        try:
            number = Decimal(body.value)
        except ArithmeticError:
            number = None
        if number is None or not number.is_finite() or number < 0:
            raise HTTPException(422, f"{body.key} must be a non-negative number")
        if body.key == "lending_revenue_share" and number > 1:
            raise HTTPException(422, "lending_revenue_share is a fraction between 0 and 1")
    if body.key == "benchmark_basis" and body.value not in ("price", "total"):
        raise HTTPException(422, "benchmark_basis must be price or total")
    if body.key == "benchmark_currency" and not re.fullmatch(r"[A-Z]{3}", body.value):
        raise HTTPException(422, "benchmark_currency must be a three-letter currency code")
    return await save_fact(pool, owner.user_id, isin, body)


@router.get("/funds/{isin}/nav", response_model=list[NavPoint])
async def fund_nav(isin: IsinPath, pool: Pool, owner: Owner, as_of: AsOf = None) -> list[NavPoint]:
    """The net asset values you entered, newest entry per date."""
    data = await _fund(pool, isin, owner, as_of)
    return data.nav


@router.put("/funds/{isin}/nav", response_model=int, status_code=status.HTTP_201_CREATED)
async def put_fund_nav(isin: IsinPath, body: list[NavIn], pool: Pool, owner: Owner) -> int:
    """Record NAV points from the issuer's own page. No provider supplies NAV, so these are the only source; the
    count saved is returned. Append-only: re-entering a date replaces it as the newest."""
    await _fund(pool, isin, owner, None)
    if not 1 <= len(body) <= 400:
        raise HTTPException(422, "send between 1 and 400 NAV points")
    today = dt.datetime.now(dt.UTC).date()
    for p in body:
        if p.as_of > today:
            raise HTTPException(422, "a NAV date cannot be in the future")
        if not p.source_url.startswith(("http://", "https://")):
            raise HTTPException(422, "source_url must be a web address")
    return await save_nav(pool, owner.user_id, isin, body)


@router.get("/fund-comparisons", response_model=list[SavedComparison])
async def saved_comparisons(pool: Pool, owner: Owner) -> list[SavedComparison]:
    return await list_comparisons(pool, owner.user_id)


@router.post(
    "/fund-comparisons", response_model=SavedComparison, status_code=status.HTTP_201_CREATED
)
async def create_saved_comparison(
    body: SavedComparisonIn, pool: Pool, owner: Owner
) -> SavedComparison:
    """Keep a set of two to five funds to reopen later; the figures are recomputed each time, not stored."""
    isins = list(dict.fromkeys(i.strip().upper() for i in body.isins))
    if not 2 <= len(isins) <= 5:
        raise HTTPException(422, "choose two to five different funds")
    for isin in isins:
        if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", isin):
            raise HTTPException(422, f"{isin} is not an ISIN")
    return await save_comparison(
        pool, owner.user_id, SavedComparisonIn(name=body.name, isins=isins, note=body.note)
    )


@router.delete("/fund-comparisons/{comparison_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_saved_comparison(comparison_id: int, pool: Pool, owner: Owner) -> Response:
    if not await archive_comparison(pool, owner.user_id, comparison_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such saved comparison")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
