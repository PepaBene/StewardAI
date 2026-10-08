# ruff: noqa: E501 - response text stays on one line each
"""Owner-only company workbench (plan Phase 2): statements, ratios, peers, valuation scenarios
and the evidence tabs for one company. Reads stored facts and computes figures in the backend;
the only writes are the owner's saved peer groups, valuation scenarios and cited case items.
"""

import datetime as dt
from functools import partial
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Response, status
from pydantic import BaseModel, Field

from api.auth import Owner
from api.db import Pool
from steward.company import (
    autoevidence,
    evidence,
    ratios,
    report,
    rubric,
    segments,
    service,
    valuation,
)
from steward.company import currency as fxview
from steward.company.statements import (
    StatementsView,
    build_view,
    csv_text,
    load_rows,
)
from steward.fx.history import load_fx_history

router = APIRouter(prefix="/companies", tags=["companies"])

TickerPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,32}$")]
Scope = Literal["none", "confirmed", "group", "home_market", "global"]


async def _company(pool: Pool, ticker: str) -> service.Company:
    company = await service.load_company(pool, ticker)
    if company is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{ticker} is not in the covered universe")
    return company


@router.get("/{ticker}/overview", response_model=evidence.Overview)
async def get_overview(ticker: TickerPath, pool: Pool, owner: Owner) -> evidence.Overview:
    """Issuer facts, the other listings of the same issuer, provider profile, latest segments,
    what the company sells and where (as it reports them), legal group, confirmed competitors and
    peers, upcoming events and a coverage score that says what is missing."""
    out = await evidence.overview(pool, ticker)
    if out is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{ticker} is not in the covered universe")
    analysis = await segments.analysis(pool, owner.user_id, ticker, "annual")
    for b in analysis.breakdowns:
        lines = [
            evidence.OperationLine(label=m.label, revenue=m.latest_value, share=m.latest_share)
            for m in b.members
            if m.role == "operating" and m.latest_value is not None
        ]
        if lines:
            out.operations.append(
                evidence.OperationBreakdown(
                    kind=b.kind,
                    label=b.label,
                    period=b.latest_period,
                    currency=analysis.currency,
                    lines=sorted(lines, key=lambda x: -(x.revenue or 0))[:12],
                    note="; ".join(b.notes) or None,
                )
            )
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
    currency: Annotated[
        str | None,
        Query(
            pattern=r"^[A-Z]{3}$",
            description="Show the figures in this base currency at stated reference rates; omit for the filed currency.",
        ),
    ] = None,
    fx_rates: Literal["period_end", "average"] = "period_end",
    fx_source: Literal["ecb", "cnb"] = "ecb",
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
        convert=await _convert(pool, rows, currency, fx_rates, fx_source),
    )


async def _convert(
    pool: Pool,
    rows: list[dict[str, Any]],
    target: str | None,
    basis: fxview.RateBasis,
    source: Literal["ecb", "cnb"],
) -> tuple[str, Any] | None:
    """The translator for a base-currency view, built from the stored reference rates."""
    if not target:
        return None
    natives = {r["currency"] for r in rows if r.get("currency")}
    today = dt.datetime.now(dt.UTC).date()
    start = min((r["period_end"] for r in rows), default=today)
    history = await load_fx_history(pool, source, [*natives, target], start, today)
    translator = fxview.Translator(target, basis, source, history)
    return target, partial(fxview.translate, translator=translator)


@router.get("/{ticker}/statements.csv")
async def get_statements_csv(
    ticker: TickerPath,
    pool: Pool,
    _owner: Owner,
    basis: Literal["standardized", "reported"] = "standardized",
    frequency: Literal["annual", "quarterly"] = "annual",
    view: Literal["restated", "original"] = "restated",
    units: Literal["1", "1000", "1000000"] = "1000000",
    currency: Annotated[str | None, Query(pattern=r"^[A-Z]{3}$")] = None,
    fx_rates: Literal["period_end", "average"] = "period_end",
    fx_source: Literal["ecb", "cnb"] = "ecb",
) -> Response:
    rows = await load_rows(pool, ticker)
    built = build_view(
        ticker,
        rows,
        basis=basis,
        frequency=frequency,
        view=view,
        units=int(units),
        convert=await _convert(pool, rows, currency, fx_rates, fx_source),
    )
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
    return await _ratios_view(pool, company, owner.user_id, peers, group_id)


class ThesisPrefill(BaseModel):
    ticker: str
    implied_growth: float | None = Field(
        description="Yearly growth the current price implies (a fraction), from the reverse DCF "
        "at the valuation tab's base scenario; null when it cannot be worked out."
    )
    reason: str | None = Field(description="Why there is no figure, when there is none.")


@router.get("/{ticker}/thesis-prefill", response_model=ThesisPrefill)
async def get_thesis_prefill(ticker: TickerPath, pool: Pool, owner: Owner) -> ThesisPrefill:
    """The figure the thesis editor's "growth the price implies" field can be filled from, so
    it is not typed from memory. Nothing is saved: you decide whether to keep it."""
    company = await _company(pool, ticker)
    val = valuation.build_valuation(
        company.ticker,
        company.annual,
        company.quarterly,
        company.market,
        None,
        valuation.ValuationRequest(selected="base"),
    )
    implied = valuation.implied_growth_value(val)
    return ThesisPrefill(
        ticker=company.ticker,
        implied_growth=None if implied.value is None else float(implied.value),
        reason=implied.reason,
    )


async def _ratios_view(
    pool: Pool, company: service.Company, owner_id: object, peers: Scope, group_id: int | None
) -> ratios.RatiosView:
    ticker = company.ticker
    peer_values = None
    scope_label = None
    excluded: list[ratios.PeerExcluded] = []
    if peers != "none":
        peer_values, scope_label, excluded = await service.peer_values_for(
            pool, company, peers, owner_id, group_id
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


async def _rubric(
    pool: Pool,
    company: service.Company,
    owner_id: object,
    view: ratios.RatiosView | None = None,
    case: evidence.CaseView | None = None,
) -> rubric.Rubric:
    view = view or await _ratios_view(pool, company, owner_id, "none", None)
    cited = None
    case = case or await evidence.case_view(pool, owner_id, company.ticker)
    if case.items:
        cited = (sum(1 for i in case.items if i.evidence), len(case.items))
    implied = next(
        (
            r.latest.value
            for g in view.groups
            for r in g.ratios
            if r.id == "reverse_dcf_growth" and r.latest is not None
        ),
        None,
    )
    return rubric.build_rubric(
        company.ticker,
        view,
        company.annual,
        cited,
        implied,
        dt.datetime.now(dt.UTC).date(),
    )


@router.get("/{ticker}/segments", response_model=segments.SegmentAnalysis)
async def get_segments(
    ticker: TickerPath,
    pool: Pool,
    owner: Owner,
    basis: Literal["annual", "quarter"] = "annual",
) -> segments.SegmentAnalysis:
    """Reported segments, geography and products with eliminations and unallocated revenue kept
    visible, changed definitions flagged, and your own estimate mapping to other contexts."""
    await _company(pool, ticker)
    return await segments.analysis(pool, owner.user_id, ticker, basis)


@router.put("/{ticker}/segment-map", response_model=segments.SegmentAnalysis)
async def save_segment_map(
    ticker: TickerPath, body: segments.MapIn, pool: Pool, owner: Owner
) -> segments.SegmentAnalysis:
    """Records one mapping of a reported member to a country, commodity, patent technology or
    trade product as a new row (a share of 0 removes it). It is an estimate, labelled as one."""
    await _company(pool, ticker)
    await segments.save_mapping(pool, owner.user_id, ticker, body)
    return await segments.analysis(pool, owner.user_id, ticker, "annual")


@router.get("/{ticker}/assessment", response_model=rubric.Rubric)
async def get_assessment(ticker: TickerPath, pool: Pool, owner: Owner) -> rubric.Rubric:
    """StewardAI's own compact rubric: four component scores from published indicators, thresholds
    and weights, a stated missing-data penalty, abstention when evidence is thin, and no rating."""
    company = await _company(pool, ticker)
    return await _rubric(pool, company, owner.user_id)


class ReportIn(BaseModel):
    conclusion: str = Field(default="", max_length=8000)


async def _report(pool: Pool, company: service.Company, owner_id: object) -> report.CompanyReport:
    view = await _ratios_view(pool, company, owner_id, "none", None)
    case = await evidence.case_view(pool, owner_id, company.ticker)
    return report.assemble(
        ticker=company.ticker,
        overview=await evidence.overview(pool, company.ticker),
        ratios=view,
        rubric=await _rubric(pool, company, owner_id, view, case),
        segments=await segments.analysis(pool, owner_id, company.ticker, "annual"),
        annual=company.annual,
        case=case,
        research=await report.latest_research(pool, company.ticker),
        disputed_edges=await report.disputed_edge_count(pool, company.ticker),
        today=dt.datetime.now(dt.UTC).date(),
    )


@router.get("/{ticker}/report", response_model=report.CompanyReport)
async def get_report(ticker: TickerPath, pool: Pool, owner: Owner) -> report.CompanyReport:
    """The report as it stands now: code-computed tables, the assessment rubric, the latest verified
    research prose with its citations, your cited case items and the conflicts between them."""
    company = await _company(pool, ticker)
    return await _report(pool, company, owner.user_id)


@router.post(
    "/{ticker}/report", response_model=report.ReportVersion, status_code=status.HTTP_201_CREATED
)
async def save_report(
    ticker: TickerPath, body: ReportIn, pool: Pool, owner: Owner
) -> report.ReportVersion:
    """Stores the report as it stands with your conclusion as a new version; nothing is overwritten."""
    company = await _company(pool, ticker)
    built = await _report(pool, company, owner.user_id)
    return await report.save_version(pool, owner.user_id, built, body.conclusion.strip())


@router.get("/{ticker}/report/versions", response_model=list[report.ReportVersionSummary])
async def list_report_versions(
    ticker: TickerPath, pool: Pool, owner: Owner
) -> list[report.ReportVersionSummary]:
    return await report.list_versions(pool, owner.user_id, ticker)


@router.get("/{ticker}/report/versions/{number}", response_model=report.ReportVersion)
async def get_report_version(
    ticker: TickerPath, number: int, pool: Pool, owner: Owner
) -> report.ReportVersion:
    found = await report.load_version(pool, owner.user_id, ticker, number)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such report version")
    return found


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
    base_currency: Annotated[
        str | None,
        Query(
            pattern=r"^[A-Z]{3}$",
            description="Convert size figures and the index chart to this currency; omit for native currencies.",
        ),
    ] = None,
    fx_rates: Literal["period_end", "average"] = "period_end",
    fx_source: Literal["ecb", "cnb"] = "ecb",
) -> service.PeerCompare:
    """The company beside its peers on the same ratios and fiscal basis, with median and range,
    fiscal-date distance, comparability warnings and an indexed history relative to each first year."""
    company = await _company(pool, ticker)
    ids = tuple(m for m in (metrics or "").split(",") if m) or service.COMPARE_DEFAULT
    translator = None
    if base_currency:
        today = dt.datetime.now(dt.UTC).date()
        stored = await pool.fetch(
            "select distinct currency from public.fx_rate where source=$1", fx_source
        )
        history = await load_fx_history(
            pool,
            fx_source,
            [base_currency, *(r["currency"] for r in stored)],
            today - dt.timedelta(days=365 * 12),
            today,
        )
        translator = fxview.Translator(base_currency, fx_rates, fx_source, history)
    return await service.compare(
        pool, company, scope, owner.user_id, group_id, ids, index_metric, years, translator
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


@router.get("/{ticker}/case", response_model=autoevidence.CaseWithObserved)
async def get_case(ticker: TickerPath, pool: Pool, owner: Owner) -> autoevidence.CaseWithObserved:
    """Your cited items, plus concentration and capital-allocation figures the app computed from
    filings (kept apart from your items)."""
    company = await _company(pool, ticker)
    case = await evidence.case_view(pool, owner.user_id, ticker)
    allocation = await evidence.capital_allocation(pool, ticker, company.annual)
    seg = await segments.analysis(pool, owner.user_id, ticker, "annual")
    return autoevidence.CaseWithObserved(
        **case.model_dump(),
        observed=[
            *autoevidence.concentration_observations(seg, case.concentration),
            *autoevidence.capital_observations(allocation.years),
        ],
    )


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
