"""Authenticated Phase-2 macro context and owner exposure mappings."""

import datetime as dt
from decimal import Decimal
from typing import Annotated, Literal, Self

import asyncpg
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator, model_validator

from api.auth import Owner
from api.db import DbId, Pool
from api.routes import _enqueue
from steward.macro.hs import normalize_hs_revision
from steward.macro.providers import TradePoint, flow_totals, trade_metrics
from steward.macro.read import HISTORY_MAX, MacroContext, context_for_holding
from steward.macro.trade_partners import (
    DEFAULT_HISTORY_PERIODS,
    TradeFlowPartners,
    partner_breakdown,
)
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

    @field_validator("hs_revision")
    @classmethod
    def _canonical_revision(cls, value: str | None) -> str | None:
        """'H6', 'hs 2022' and 'HS2022' are one revision; trade rows carry the canonical label."""
        return normalize_hs_revision(value) if value else None

    @model_validator(mode="after")
    def _product_needs_revision(self) -> Self:
        # Stored legacy rows (ExposureResult) may predate the requirement; new input may not.
        if type(self) is ExposureInput and self.hs_code is not None and self.hs_revision is None:
            raise ValueError("hs_revision is required with hs_code (e.g. HS2022)")
        return self


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


class ReporterWideTrade(BaseModel):
    """The same figures over every product stored for the reporter. They move when another
    product is collected, so they are context, not a property of the requested product."""

    periods: int
    hs_revision: str
    products: int = Field(description="Distinct HS codes stored for the reporter.")
    metrics: dict[str, Decimal | None]


class TradeContext(BaseModel):
    """Everything outside `reporter_wide` describes the requested `hs_code` only.

    Units: values are USD; growth, share, hhi and unmapped_share are fractions (0.18 = 18%);
    hhi is on a 0..1 scale (x10,000 for the classic index)."""

    reporter: str
    hs_code: str
    hs_revision: str  # revision(s) of this product's rows, "mixed" when several
    partner: str | None
    periods: int  # distinct periods stored for this product
    latest_period: dt.date | None
    latest_value: Decimal | None  # exports of the product in its latest period
    latest_value_by_flow: dict[str, Decimal] = Field(default_factory=dict)
    latest_value_currency: str = "USD"
    # Growth, partner concentration and export share of this product. `product_mix_change`
    # compares the mix of products, which one product cannot have: null here, see reporter_wide.
    metrics: dict[str, Decimal | None]
    source_url: str | None  # of this product's most recently known row
    known_at: dt.datetime | None  # when this product's most recent row became known
    reporter_wide: ReporterWideTrade | None = None
    partners: list[TradeFlowPartners] = Field(
        default_factory=list,
        description="Per flow: latest-period partner rows with share of the product's total, "
        "the unmapped remainder (never folded into a partner) and a per-period hhi history.",
    )
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
    history: Annotated[int, Query(ge=0, le=HISTORY_MAX)] = 0,
) -> MacroContext:
    """Latest point-in-time country context, ranges, provenance and mapped exposure.

    `history=N` (0 = off, max 60) adds per series the last N periods with first-release and
    latest values, known by `as_of` only."""
    return await context_for_holding(pool, ticker, as_of or dt.datetime.now(dt.UTC), history)


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
               and i.status='confirmed'
             left join public.security s on s.id=i.security_id
             where p.snapshot_date=$1
           ), by_dimension as (
             -- The cap is per dimension: a geography cut and a product cut each describe all
             -- of the issuer, so the assigned share is the best-covered dimension, not a sum.
             select p.ticker,p.issuer_id,sum(em.weight) dimension_weight
             from positions p join public.exposure_mapping em on em.issuer_id=p.issuer_id
               and em.valid_from <= $1 and (em.valid_to is null or em.valid_to >= $1)
               and em.created_at < ($1::date + 1)
             group by p.ticker,p.issuer_id,public.exposure_dimension(em.geography,em.hs_code)
           ), assigned as (
             select p.ticker,p.value_base,p.base_currency,
                    coalesce(max(d.dimension_weight),0) assigned_weight
             from positions p left join by_dimension d on d.ticker=p.ticker
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
               and i.status='confirmed'
             left join public.security s on s.id=i.security_id where p.snapshot_date=$1
           ), weights as (
             select p.ticker,p.value_base,'geography'::text exposure_type,
                    em.geography exposure_key,null::text hs_revision,em.basis,em.weight
             from positions p join public.exposure_mapping em on em.issuer_id=p.issuer_id
               and em.valid_from <= $1 and (em.valid_to is null or em.valid_to >= $1)
               and em.created_at < ($1::date + 1)
             where em.geography is not null
             union all
             select p.ticker,p.value_base,'hs_product'::text,em.hs_code,em.hs_revision,
                    em.basis,em.weight
             from positions p join public.exposure_mapping em on em.issuer_id=p.issuer_id
               and em.valid_from <= $1 and (em.valid_to is null or em.valid_to >= $1)
               and em.created_at < ($1::date + 1)
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
    try:
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
    except asyncpg.RaiseError as exc:  # the per-dimension 100% cap
        raise HTTPException(422, exc.message) from exc
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
    history_periods: Annotated[int, Query(ge=0, le=60)] = DEFAULT_HISTORY_PERIODS,
) -> TradeContext:
    """Trade of one product (value, metrics, partners, concentration trend); no issuer contract
    is inferred. Reporter-wide aggregates are kept apart under `reporter_wide`.

    `reporter` and `partner` may be an ISO alpha-2 code or the UN M49 number Comtrade uses.
    Only the latest revision of each observation is used."""
    rows = await pool.fetch(
        """select distinct on (reporter,partner,flow,hs_revision,hs_code,period)
                  reporter,partner,reporter_iso,partner_iso,flow,hs_revision,hs_code,period,
                  trade_value,quantity,quantity_unit,net_weight,known_at,source_url
           from public.trade_observation
           where (reporter=$1 or reporter_iso=$1) and ($2::text is null or partner=$2
                  or partner_iso=$2)
           order by reporter,partner,flow,hs_revision,hs_code,period,revision desc,known_at desc""",
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
            net_weight=row["net_weight"],
            reporter_iso=row["reporter_iso"],
            partner_iso=row["partner_iso"],
        )
        for row in rows
    ]
    product = [point for point in points if point.hs_code == hs_code]
    latest_period = max(point.period for point in product)
    by_flow: dict[str, Decimal] = {}
    for flow in sorted({point.flow for point in product}):
        totals = flow_totals(product, flow)
        if latest_period in totals:
            by_flow[flow] = totals[latest_period]
    product_rows = [row for row in rows if row["hs_code"] == hs_code]
    last_row = max(product_rows, key=lambda row: row["known_at"])
    revisions = {point.hs_revision for point in product}
    all_revisions = {point.hs_revision for point in points}
    # Product mix is a property of the whole reporter; a single product has none.
    product_metrics = trade_metrics(product) | {"product_mix_change": None}
    return TradeContext(
        reporter=reporter,
        hs_code=hs_code,
        hs_revision=next(iter(revisions)) if len(revisions) == 1 else "mixed",
        partner=partner,
        periods=len({point.period for point in product}),
        latest_period=latest_period,
        latest_value=by_flow.get("export"),
        latest_value_by_flow=by_flow,
        metrics=product_metrics,
        source_url=last_row["source_url"],
        known_at=last_row["known_at"],
        reporter_wide=ReporterWideTrade(
            periods=len({point.period for point in points}),
            hs_revision=next(iter(all_revisions)) if len(all_revisions) == 1 else "mixed",
            products=len({point.hs_code for point in points}),
            metrics=trade_metrics(points),
        ),
        partners=partner_breakdown(product, history_periods),
    )


@router.post("/jobs/source-audit", tags=["jobs"], status_code=status.HTTP_202_ACCEPTED)
async def run_source_audit(pool: Pool, owner: Owner) -> JobSummary:
    return await _enqueue(pool, "source_audit", owner.user_id)
