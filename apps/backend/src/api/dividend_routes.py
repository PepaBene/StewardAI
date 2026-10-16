# ruff: noqa: E501 - response text stays on one line each
"""Dividend income engine reads and the confirmation workflow (insight plan I1.8, I1.2)."""

import re
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel, Field

from api.auth import Owner
from api.db import Pool
from steward.checks.contract import axis_score
from steward.checks.service import company_checks
from steward.dividends import service as div

router = APIRouter()
TickerPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,32}$")]
MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


@router.get("/portfolio/dividends", response_model=div.PortfolioDividends, tags=["portfolio"])
async def portfolio_dividends(pool: Pool, owner: Owner) -> div.PortfolioDividends:
    """Answer-strip figures (next 12 months declared + estimated, last 12 months received, yield,
    yield on cost), 36 months of received income and 12 of forecast, contributors, quality tiers,
    coverage. Estimated rows are flagged; nothing is shown as zero when it is unknown."""
    return await div.portfolio_dividends(pool, owner.user_id)


@router.get(
    "/portfolio/dividends/calendar", response_model=div.DividendCalendar, tags=["portfolio"]
)
async def dividend_calendar(
    pool: Pool,
    _owner: Owner,
    month: Annotated[str, Query(description="YYYY-MM")],
) -> div.DividendCalendar:
    """Ex-dividend and payment dates of one month (declared, estimated and unconfirmed variants)
    and the holdings' earnings dates as a separate kind."""
    if not MONTH.match(month):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "month must be YYYY-MM")
    return await div.calendar(pool, month)


@router.get(
    "/companies/{ticker}/dividends", response_model=div.CompanyDividends, tags=["companies"]
)
async def company_dividends(ticker: TickerPath, pool: Pool, owner: Owner) -> div.CompanyDividends:
    """Payment history, forward schedule, received payments with their declared counterpart,
    the six dividend checks, yield against peers and payout on earnings and cash."""
    out = await div.company_dividends(pool, ticker)
    checks = await company_checks(pool, owner.user_id, ticker)
    if checks is None:
        return out
    dividend = [c for c in checks.checks if c.axis == "dividend"]
    out.checks = dividend
    out.dividend_axis = axis_score("dividend", dividend)
    by_id = {c.id: c for c in dividend}
    y = by_id.get("dividend_yield_vs_peers")
    rules = await div.rules(pool)
    from steward.checks.load import load_inputs

    inp = await load_inputs(pool, owner.user_id, ticker)
    if inp is not None:
        yr = inp.r("dividend_yield")
        out.yield_vs_peers = div.YieldVsPeers(
            company=yr.latest, peer_median=yr.peer_median, peer_count=yr.peer_count,
            basis=yr.peer_basis() if yr.peer_median is not None else None,
            reason=None if yr.peer_median is not None else (yr.peer_reason or (y.sentence if y else None)),
        )  # fmt: skip
        pe = by_id.get("dividend_payout_earnings")
        pc = by_id.get("dividend_payout_cash")
        out.payout = div.Payout(
            earnings=pe.value if pe else None, cash=pc.value if pc else None,
            limit_earnings=Decimal(str(rules.limits.payout_limit_earnings)), limit_cash=Decimal(str(rules.limits.payout_limit_cash)),
            fiscal_year=inp.fiscal_year,
        )  # fmt: skip
        filed = dict((yy, v) for yy, v in inp.series.get("dividends_per_share", []) if v)
        events = {a.year: a.per_share for a in out.annual}
        common = sorted(set(filed) & set(events))
        if common:
            yy = common[-1]
            gap = abs(events[yy] - filed[yy]) / filed[yy] if filed[yy] else None
            agree = gap is not None and gap <= Decimal("0.05")
            out.filed_dps_check = (
                f"{yy}: payments on record sum to {events[yy]:.4f} per share, the filing states {filed[yy]:.4f} "
                f"({'agree' if agree else f'differ by {gap:.0%}' if gap is not None else 'not comparable'}; calendar vs fiscal year may differ)."
            )
    return out


class DividendConfirmIn(BaseModel):
    event_ids: list[int] | None = Field(
        default=None,
        max_length=200,
        description="Suggested events to confirm; null confirms every one a Trading212 payment matches.",
    )


class ConfirmOut(BaseModel):
    confirmed: list[int]
    skipped: list[int]


@router.post("/companies/{ticker}/dividends/confirm", response_model=ConfirmOut, tags=["companies"])
async def confirm_dividends(
    ticker: TickerPath, body: DividendConfirmIn, pool: Pool, owner: Owner
) -> ConfirmOut:
    """Bulk-confirm suggested dividend events of one holding, each with an evidence row (the
    matched payment, or the provider's stored record). Already decided events are skipped."""
    ctx = await div.load_context(pool, extra=[ticker])
    done = await div.confirm_events(
        pool, ctx, ticker, event_ids=body.event_ids, owner_id=owner.user_id, auto=False
    )
    asked = body.event_ids or []
    return ConfirmOut(confirmed=done, skipped=[i for i in asked if i not in done])
