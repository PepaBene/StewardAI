"""Owner-only read models for fund look-through beyond the base report (plan Phase 6):
issuer-level aggregation, fund overlap, the fund/index coverage ledger, factsheet history,
retention pins and the fund-characteristics parser-drift audit. Wiring: `app.include_router`
of `router` in api/main.py."""

import datetime as dt
import json
import logging
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, Field

from api.auth import Owner
from api.db import Pool
from api.typed_responses import (
    CharacteristicsAudit,
    FactsheetStored,
    FundFactsheets,
    FundLookThroughAsOf,
    SnapshotsPinned,
    typed,
)
from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.funds.aggregate import (
    AggregateReport,
    FundOverlapReport,
    aggregate_portfolio,
    fund_overlap,
)
from steward.funds.drift import read_audit
from steward.lookthrough.coverage import (
    CoverageLedger,
    LedgerEntry,
    delete_manual_entry,
    read_ledger,
    refresh_ledger,
    save_entry,
)
from steward.lookthrough.factsheet import ManualFactsheet, factsheet_history, store_manual_factsheet
from steward.lookthrough.pit import AsOfLookThrough, portfolio_look_through_as_of
from steward.lookthrough.read import load_portfolio
from steward.lookthrough.retention import pin_snapshot, pin_snapshots_as_of

router = APIRouter()
log = logging.getLogger(__name__)
IsinPath = Annotated[str, Path(pattern=r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")]
AsOf = Annotated[dt.date | None, Query(description="Answer as of this date (point-in-time).")]


@router.get("/funds/aggregate", tags=["funds"])
async def get_fund_aggregate(
    pool: Pool, _owner: Owner, as_of: AsOf = None, top: Annotated[int, Query(ge=1, le=200)] = 25
) -> AggregateReport:
    """Direct shares plus fund holdings by canonical issuer: top issuers and overlap,
    region/country/sector/currency exposure, fee drag and the explicit unknown fund weight
    (never redistributed), with every as-of date and staleness visible."""
    if as_of is not None and as_of > dt.datetime.now(dt.UTC).date():
        raise HTTPException(422, "as_of cannot be in the future")
    return await aggregate_portfolio(pool, as_of=as_of, top=top)


@router.get("/funds/overlap", tags=["funds"])
async def get_fund_overlap(pool: Pool, _owner: Owner, as_of: AsOf = None) -> FundOverlapReport:
    """Pairwise overlap between configured funds, per-fund concentration and coverage with
    reporting lag and unknown (unaccounted) weight."""
    return await fund_overlap(pool, as_of=as_of)


@router.get(
    "/funds/look-through",
    tags=["funds"],
    response_model=None,
    responses=typed(FundLookThroughAsOf),
)
async def get_look_through_as_of(
    pool: Pool,
    _owner: Owner,
    as_of: Annotated[dt.date, Query(description="Only records known by the end of this day.")],
    top: Annotated[int, Query(ge=1, le=100)] = 25,
) -> dict[str, Any]:
    """The base look-through report as it could have been computed at `as_of`."""
    result: AsOfLookThrough = await portfolio_look_through_as_of(pool, as_of, top=top)
    return {
        "as_of": result.as_of,
        "snapshot_ids": result.snapshot_ids,
        "report": result.report.model_dump(mode="json"),
    }


@router.get("/funds/coverage-ledger", tags=["funds"])
async def get_coverage_ledger(pool: Pool, _owner: Owner) -> CoverageLedger:
    """Where each fund's/index's constituents come from, and which benchmarks lack total
    return or historical constituents before performance/backtest use."""
    return await read_ledger(pool)


@router.post("/funds/coverage-ledger/refresh", tags=["funds"])
async def refresh_coverage_ledger(pool: Pool, _owner: Owner) -> CoverageLedger:
    """Rebuild the derived ledger rows from the Jobs config and stored snapshots."""
    stored = await load_active(pool, "jobs", JobsConfig)
    config = stored.body if stored else JobsConfig()
    await refresh_ledger(
        pool, config.look_through, config.prices.benchmark_symbols, await load_portfolio(pool)
    )
    return await read_ledger(pool)


class LedgerEntryInput(BaseModel):
    subject_kind: Literal["fund", "index"]
    subject_key: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=120)
    tier: Literal[
        "official_holdings", "issuer_downloadable", "regulatory_filing", "proxy_fund", "unavailable"
    ]
    reason: str = Field(min_length=3, max_length=1000)
    proxy_fund_isin: str | None = Field(default=None, pattern=r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
    total_return_available: bool | None = None
    historical_constituents: bool | None = None
    source_url: str | None = Field(default=None, max_length=2000)


@router.put("/funds/coverage-ledger", tags=["funds"])
async def put_coverage_entry(pool: Pool, _owner: Owner, body: LedgerEntryInput) -> CoverageLedger:
    """Record an owner-verified ledger row; a refresh never overwrites it (DELETE the same
    subject to revert to the derived row)."""
    log.info("coverage ledger manual row saved %s/%s", body.subject_kind, body.subject_key)
    await save_entry(
        pool,
        LedgerEntry(
            subject_kind=body.subject_kind,
            subject_key=body.subject_key,
            label=body.label,
            tier=body.tier,
            reason=body.reason,
            origin="manual",
            proxy_fund_isin=body.proxy_fund_isin,
            total_return_available=body.total_return_available,
            historical_constituents=body.historical_constituents,
            source_url=body.source_url,
        ),
    )
    return await read_ledger(pool)


@router.delete("/funds/coverage-ledger", tags=["funds"])
async def delete_coverage_entry(
    pool: Pool,
    _owner: Owner,
    subject_kind: Annotated[Literal["fund", "index"], Query()],
    subject_key: Annotated[str, Query(min_length=1, max_length=80)],
) -> CoverageLedger:
    """Remove an owner-entered ledger row and restore the derived row (re-derived from the
    Jobs config and the stored snapshots). Idempotent: with no manual row for the subject
    nothing is deleted (a derived row is never removed here) and the ledger is returned
    unchanged apart from the refresh of derived rows."""
    removed = await delete_manual_entry(pool, subject_kind, subject_key)
    stored = await load_active(pool, "jobs", JobsConfig)
    config = stored.body if stored else JobsConfig()
    await refresh_ledger(
        pool, config.look_through, config.prices.benchmark_symbols, await load_portfolio(pool)
    )
    log.info(
        "coverage ledger manual row %s %s/%s",
        "reverted" if removed else "not present",
        subject_kind,
        subject_key,
    )
    return await read_ledger(pool)


@router.get(
    "/funds/{isin}/factsheet",
    tags=["funds"],
    response_model=None,
    responses=typed(FundFactsheets),
)
async def get_factsheet(
    pool: Pool, _owner: Owner, isin: IsinPath, limit: Annotated[int, Query(ge=1, le=120)] = 24
) -> list[dict[str, Any]]:
    """Factsheet history of a fund: charges, benchmark, distribution policy, NAV basis, the
    cash/derivative remainder and whether the rows reconcile."""
    rows = await factsheet_history(pool, isin, limit=limit)
    out = []
    for r in rows:
        d = dict(r)
        evidence = d.pop("evidence", None)
        if isinstance(evidence, str):
            evidence = json.loads(evidence)
        evidence = evidence or {}
        # D6.1: the issuer payload field names are unverified candidates; say so per row.
        d["field_names_verified"] = not evidence.get("names_unverified", d["source"] != "manual")
        d["field_provenance"] = evidence.get("field_provenance") or {}
        d["extraction_warnings"] = evidence.get("warnings") or []
        out.append(d)
    return out


class ManualFactsheetInput(BaseModel):
    as_of: dt.date
    share_class: str | None = Field(default=None, max_length=80)
    total_assets: Decimal | None = Field(default=None, ge=0)
    total_assets_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    expense_ratio: Decimal | None = Field(
        default=None, ge=0, lt=Decimal("0.2"), description="Fraction per year: 0.0007 = 0.07%."
    )
    benchmark_name: str | None = Field(default=None, max_length=200)
    distribution_policy: Literal["accumulating", "distributing"] | None = None
    nav_basis: str | None = Field(default=None, max_length=200)
    charges: dict[str, Any] = Field(default_factory=dict)
    note: str | None = Field(default=None, max_length=1000)


@router.put(
    "/funds/{isin}/factsheet",
    tags=["funds"],
    response_model=None,
    responses=typed(FactsheetStored),
)
async def put_factsheet(
    pool: Pool, _owner: Owner, isin: IsinPath, body: ManualFactsheetInput
) -> dict[str, int]:
    """Owner-entered factsheet figures (KID/issuer factsheet) for gaps the issuer feed leaves."""
    sheet_id = await store_manual_factsheet(
        pool, ManualFactsheet(fund_isin=isin, **body.model_dump())
    )
    return {"id": sheet_id}


class PinInput(BaseModel):
    ref_kind: Literal["report", "decision", "manual"]
    ref_id: str = Field(min_length=1, max_length=200)
    reason: str | None = Field(default=None, max_length=500)
    snapshot_id: int | None = Field(default=None, description="Pin this snapshot...")
    as_of: dt.date | None = Field(
        default=None, description="...or every configured fund's snapshot known at this date."
    )


@router.post(
    "/funds/snapshots/pin",
    tags=["funds"],
    response_model=None,
    responses=typed(SnapshotsPinned),
)
async def pin_snapshots(pool: Pool, _owner: Owner, body: PinInput) -> dict[str, list[int]]:
    """Protect the holdings snapshots a saved report/decision rests on from retention pruning."""
    if (body.snapshot_id is None) == (body.as_of is None):
        raise HTTPException(422, "give exactly one of snapshot_id or as_of")
    if body.snapshot_id is not None:
        if not await pin_snapshot(pool, body.snapshot_id, body.ref_kind, body.ref_id, body.reason):
            raise HTTPException(404, "snapshot not found")
        return {"pinned": [body.snapshot_id]}
    assert body.as_of is not None
    stored = await load_active(pool, "jobs", JobsConfig)
    config = stored.body if stored else JobsConfig()
    pinned = await pin_snapshots_as_of(
        pool,
        [f.isin for f in config.look_through.funds],
        body.as_of,
        body.ref_kind,
        body.ref_id,
        body.reason,
    )
    return {"pinned": pinned}


@router.get(
    "/funds/characteristics-audit",
    tags=["funds"],
    response_model=None,
    responses=typed(CharacteristicsAudit),
)
async def get_characteristics_audit(
    pool: Pool,
    _owner: Owner,
    fund_key: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9._-]{1,32}$")] = None,
    alerts_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[dict[str, Any]]:
    """Parser-drift audit of issuer fund P/E and P/B: verdict per observation (real value or
    methodology change vs parser drift) with the shape fingerprint and stated rules."""
    return await read_audit(pool, fund_key, limit=limit, alerts_only=alerts_only)
