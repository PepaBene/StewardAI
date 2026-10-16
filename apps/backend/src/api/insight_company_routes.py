"""Company insight blocks (insight I13.3-I13.5): ownership by holder type, CEO pay and tenure,
and the cited earnings-release summary."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, status

from api.auth import Owner
from api.db import Pool
from api.routes import Storage
from steward.ai import AINotConfigured
from steward.company.pay_tenure import PayAndTenure, pay_and_tenure
from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.earnings import summary as earnings_summary
from steward.ownership.investors import managers
from steward.ownership.mix import OwnershipMix, ownership_mix

router = APIRouter(tags=["companies"])

TickerPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,32}$")]


async def _jobs(pool: Pool) -> JobsConfig:
    config = await load_active(pool, "jobs", JobsConfig)
    return config.body if config else JobsConfig()


@router.get("/companies/{ticker}/ownership-mix")
async def get_ownership_mix(ticker: TickerPath, pool: Pool, _owner: Owner) -> OwnershipMix:
    """Institutions (13F), insiders (Form 4) and the rest as shares of shares outstanding, with
    the top 25 institutional holders and what each figure covers."""
    tracked = [(m.cik, m.name) for m in (await _jobs(pool)).ownership.tracked_managers]
    books = {
        m.cik: (m.period, m.reported_value_usd)
        for m in await managers(pool, tracked)
        if m.period is not None and m.reported_value_usd is not None
    }
    return await ownership_mix(pool, ticker, books)


@router.get("/companies/{ticker}/pay-tenure")
async def get_pay_tenure(ticker: TickerPath, pool: Pool, _owner: Owner) -> PayAndTenure:
    """The CEO's pay (proxy statement), tenure and holding, and the management and board
    average tenure from Form 4; `unavailable` with the reason outside SEC registrants."""
    return await pay_and_tenure(pool, ticker)


@router.get("/companies/{ticker}/earnings-summary")
async def get_earnings_summary(
    ticker: TickerPath, pool: Pool, owner: Owner
) -> earnings_summary.EarningsSummaryView:
    """The latest cited note on the stored earnings release, and whether a newer release is
    stored than the one it was written from."""
    return await earnings_summary.summary_view(pool, owner.user_id, ticker)


@router.post("/companies/{ticker}/earnings-summary")
async def create_earnings_summary(
    ticker: TickerPath, pool: Pool, storage: Storage, owner: Owner
) -> earnings_summary.EarningsNote:
    """Write a cited note on the latest stored earnings release: summary sentences and three to
    five questions, each with its verbatim quote. One budgeted model call, cached per release."""
    from steward.llm.client import create_llm
    from steward.settings import get_settings

    model = (await _jobs(pool)).chat.model
    try:
        llm = create_llm(get_settings())
    except AINotConfigured as err:
        raise HTTPException(status.HTTP_409_CONFLICT, str(err)) from err
    try:
        return await earnings_summary.summarise(
            pool, storage, owner.user_id, ticker, llm=llm, model=model
        )
    except earnings_summary.SummaryBudgetRefused as err:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(err)) from err
    except earnings_summary.SummaryUnavailable as err:
        raise HTTPException(status.HTTP_409_CONFLICT, str(err)) from err
