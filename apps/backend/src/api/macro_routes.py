"""Authenticated Phase-2 macro context and owner exposure mappings."""

import datetime as dt
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from api.auth import Owner
from api.db import DbId, Pool
from api.routes import _enqueue
from steward.macro.providers import TradePoint, trade_metrics
from steward.macro.read import MacroContext, context_for_holding
from steward.portfolio.read import JobSummary

router = APIRouter()


class ExposureInput(BaseModel):
    issuer_id: DbId
    segment: str = Field(min_length=1, max_length=160)
    geography: str | None = Field(default=None, pattern=r"^[A-Z0-9]{2,5}$")
    hs_revision: str | None = Field(default=None, max_length=20)
    hs_code: str | None = Field(default=None, pattern=r"^[0-9]{2,6}$")
    basis: Literal["reported", "estimated", "user_assigned"]
    weight: Decimal = Field(ge=0, le=1)
    valid_from: dt.date
    valid_to: dt.date | None = None


class ExposureResult(ExposureInput):
    id: int


class ProviderStatus(BaseModel):
    provider: str
    endpoint: str
    state: str
    hard_blocked: bool
    quota: Decimal | None
    quota_period: str | None
    last_success_at: dt.datetime | None
    retry_at: dt.datetime | None
    day_credits: Decimal
    month_credits: Decimal


class TradeContext(BaseModel):
    reporter: str
    hs_code: str
    hs_revision: str
    partner: str | None
    periods: int
    latest_period: dt.date | None
    latest_value: Decimal | None
    latest_value_currency: str = "USD"
    metrics: dict[str, Decimal | None]
    source_url: str | None
    known_at: dt.datetime | None
    notice: str = "National trade statistics do not establish an issuer contract."


class PortfolioMacroCoverage(BaseModel):
    as_of: dt.date
    base_currency: str
    total_value: Decimal
    mapped_value: Decimal
    unknown_value: Decimal
    mapped_weight: Decimal
    unknown_weight: Decimal
    by_exposure: list[dict[str, object]]
    gaps: list[str]


@router.get("/macro/context/{ticker}", tags=["macro"])
async def get_macro_context(
    ticker: str,
    pool: Pool,
    _owner: Owner,
    as_of: Annotated[dt.datetime | None, Query()] = None,
) -> MacroContext:
    """Latest point-in-time country context, ranges, provenance and mapped exposure."""
    return await context_for_holding(pool, ticker, as_of or dt.datetime.now(dt.UTC))


@router.get("/macro/coverage/portfolio", response_model=PortfolioMacroCoverage, tags=["macro"])
async def get_portfolio_macro_coverage(pool: Pool, _owner: Owner) -> PortfolioMacroCoverage:
    """Measure mapped versus unknown portfolio value and country-series coverage."""
    as_of = await pool.fetchval("select max(snapshot_date) from public.position_snapshot")
    if as_of is None:
        raise HTTPException(404, "No portfolio position snapshot is available")
    rows = await pool.fetch(
        """with positions as (
             select p.ticker,p.value_base,p.base_currency,s.issuer_id
             from public.position_snapshot p
             left join public.instrument_security_link i on i.ticker=p.ticker
             left join public.security s on s.id=i.security_id
             where p.snapshot_date=$1
           ), assigned as (
             select p.ticker,p.value_base,p.base_currency,
                    coalesce(sum(em.weight),0) assigned_weight
             from positions p left join public.exposure_mapping em on em.issuer_id=p.issuer_id
               and em.valid_from <= $1 and (em.valid_to is null or em.valid_to >= $1)
             group by p.ticker,p.value_base,p.base_currency
           )
           select base_currency,sum(value_base) total_value,
             sum(value_base*least(1,coalesce(assigned_weight,0))) mapped_value,
             sum(value_base*greatest(0,1-coalesce(assigned_weight,0))) unknown_value
           from assigned
           group by base_currency""",
        as_of,
    )
    if not rows:
        raise HTTPException(404, "No linked position snapshot is available")
    currencies = {r["base_currency"] for r in rows}
    if len(currencies) > 1:
        raise HTTPException(409, "Position snapshot mixes base currencies; totals are incomparable")
    total = sum((r["total_value"] for r in rows), Decimal(0))
    mapped_value = sum((r["mapped_value"] for r in rows), Decimal(0))
    unknown_value = sum((r["unknown_value"] for r in rows), Decimal(0))
    currency = next(iter(currencies))
    exposure_rows = await pool.fetch(
        """with positions as (
             select p.ticker,p.value_base,s.issuer_id
             from public.position_snapshot p
             left join public.instrument_security_link i on i.ticker=p.ticker
             left join public.security s on s.id=i.security_id where p.snapshot_date=$1
           ), weights as (
             select p.ticker,p.value_base,'geography'::text exposure_type,
                    em.geography exposure_key,null::text hs_revision,em.basis,em.weight
             from positions p join public.exposure_mapping em on em.issuer_id=p.issuer_id
               and em.valid_from <= $1 and (em.valid_to is null or em.valid_to >= $1)
             where em.geography is not null
             union all
             select p.ticker,p.value_base,'hs_product'::text,em.hs_code,em.hs_revision,
                    em.basis,em.weight
             from positions p join public.exposure_mapping em on em.issuer_id=p.issuer_id
               and em.valid_from <= $1 and (em.valid_to is null or em.valid_to >= $1)
             where em.hs_code is not null
           )
           select w.exposure_type,w.exposure_key,w.hs_revision,w.basis,
             sum(w.value_base*w.weight) mapped_value,
             count(distinct w.ticker)::integer holding_count,
             max(coalesce(m.latest_period,t.latest_period)) latest_period,
             max(coalesce(m.available_series,0)+coalesce(t.available_series,0))::integer
               available_series
           from weights w
           left join lateral (
             select max(period) latest_period,count(distinct series_id)::integer available_series
             from public.macro_observation
             where geography=w.exposure_key and known_at <= now()
           ) m on w.exposure_type='geography'
           left join lateral (
             select max(period) latest_period,count(distinct flow)::integer available_series
             from public.trade_observation
             where hs_code=w.exposure_key and hs_revision=w.hs_revision and known_at <= now()
           ) t on w.exposure_type='hs_product'
           group by w.exposure_type,w.exposure_key,w.hs_revision,w.basis
           order by mapped_value desc""",
        as_of,
    )
    gaps = []
    if unknown_value > 0:
        gaps.append("Some portfolio value has no active country/product exposure mapping.")
    uncovered = [
        f"{r['exposure_type']}:{r['exposure_key']}"
        for r in exposure_rows
        if not r["available_series"]
    ]
    if uncovered:
        gaps.append("No stored public series for mapped exposures: " + ", ".join(uncovered))
    return PortfolioMacroCoverage(
        as_of=as_of,
        base_currency=currency,
        total_value=total,
        mapped_value=mapped_value,
        unknown_value=unknown_value,
        mapped_weight=mapped_value / total if total else Decimal(0),
        unknown_weight=unknown_value / total if total else Decimal(0),
        by_exposure=[dict(row) for row in exposure_rows],
        gaps=gaps,
    )


@router.get("/macro/exposures", tags=["macro"])
async def list_exposures(pool: Pool, _owner: Owner) -> list[ExposureResult]:
    rows = await pool.fetch(
        "select id,issuer_id,segment,geography,hs_revision,hs_code,basis,weight,"
        "valid_from,valid_to "
        "from public.exposure_mapping order by issuer_id,segment"
    )
    return [ExposureResult(**dict(row)) for row in rows]


@router.post("/macro/exposures", tags=["macro"], status_code=status.HTTP_201_CREATED)
async def create_exposure(body: ExposureInput, pool: Pool, owner: Owner) -> ExposureResult:
    if body.geography is None and body.hs_code is None:
        raise HTTPException(422, "geography or hs_code is required")
    row = await pool.fetchrow(
        """insert into public.exposure_mapping
           (issuer_id,segment,geography,hs_revision,hs_code,basis,weight,valid_from,valid_to,created_by)
           values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) returning id,issuer_id,segment,geography,
           hs_revision,hs_code,basis,weight,valid_from,valid_to""",
        body.issuer_id,
        body.segment,
        body.geography,
        body.hs_revision,
        body.hs_code,
        body.basis,
        body.weight,
        body.valid_from,
        body.valid_to,
        owner.user_id,
    )
    assert row is not None
    return ExposureResult(**dict(row))


@router.delete("/macro/exposures/{mapping_id}", tags=["macro"], status_code=204)
async def delete_exposure(mapping_id: DbId, pool: Pool, _owner: Owner) -> None:
    deleted = await pool.fetchval(
        "delete from public.exposure_mapping where id=$1 returning id", mapping_id
    )
    if deleted is None:
        raise HTTPException(404, "Exposure mapping not found")


@router.post("/jobs/public-data", tags=["jobs"], status_code=status.HTTP_202_ACCEPTED)
async def run_public_data(pool: Pool, owner: Owner) -> JobSummary:
    return await _enqueue(pool, "public_data_collect", owner.user_id)


@router.get("/sources/status", tags=["sources"])
async def source_status(pool: Pool, _owner: Owner) -> list[ProviderStatus]:
    rows = await pool.fetch("select * from public.provider_quota_status order by provider,endpoint")
    return [ProviderStatus(**dict(row)) for row in rows]


@router.get("/macro/trade/{reporter}/{hs_code}", response_model=TradeContext, tags=["macro"])
async def get_trade_context(
    reporter: str,
    hs_code: str,
    pool: Pool,
    _owner: Owner,
    partner: str | None = None,
) -> TradeContext:
    """Product value plus reporter-wide trade metrics; no issuer contract is inferred."""
    rows = await pool.fetch(
        """select reporter,partner,flow,hs_revision,hs_code,period,trade_value,quantity,
                  quantity_unit,known_at,source_url
           from public.trade_observation
           where reporter=$1 and ($2::text is null or partner=$2)
           order by period,known_at""",
        reporter,
        partner,
    )
    if not rows or not any(row["hs_code"] == hs_code for row in rows):
        raise HTTPException(404, "No trade observations for this reporter and product")
    points = [
        TradePoint(
            reporter=row["reporter"],
            partner=row["partner"],
            flow=row["flow"],
            hs_revision=row["hs_revision"],
            hs_code=row["hs_code"],
            period=row["period"],
            value=row["trade_value"],
            quantity=row["quantity"],
            quantity_unit=row["quantity_unit"],
        )
        for row in rows
    ]
    latest_period = max(point.period for point in points)
    latest = [
        point for point in points if point.period == latest_period and point.hs_code == hs_code
    ]
    latest_value = sum((point.value or Decimal(0) for point in latest), Decimal(0))
    last_row = max(rows, key=lambda row: row["known_at"])
    revisions = {point.hs_revision for point in points}
    return TradeContext(
        reporter=reporter,
        hs_code=hs_code,
        hs_revision=next(iter(revisions)) if len(revisions) == 1 else "mixed",
        partner=partner,
        periods=len({point.period for point in points}),
        latest_period=latest_period,
        latest_value=latest_value,
        metrics=trade_metrics(points),
        source_url=last_row["source_url"],
        known_at=last_row["known_at"],
    )


@router.post("/jobs/source-audit", tags=["jobs"], status_code=status.HTTP_202_ACCEPTED)
async def run_source_audit(pool: Pool, owner: Owner) -> JobSummary:
    return await _enqueue(pool, "source_audit", owner.user_id)
