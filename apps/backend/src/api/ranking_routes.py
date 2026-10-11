"""Authenticated routes for the decision loop (plan Q8): theses ranked by risk-adjusted expected
return, and the switches the ranking suggests. Reads and estimates only; nothing is traded."""

from fastapi import APIRouter, HTTPException

from api.auth import Owner
from api.db import Pool
from steward.decision import ranking as rk
from steward.decision import ranking_service as svc
from steward.decision.service import NoHoldings, NoStrategy
from steward.journal import switches
from steward.journal.entries import JournalError

router = APIRouter(prefix="/theses", tags=["theses"])


def _no_strategy() -> HTTPException:
    return HTTPException(409, "No strategy is stored; save one under Config first.")


@router.get("/expected-returns")
async def get_expected_returns(pool: Pool, _owner: Owner) -> rk.RankingResponse:
    """Every thesis, held or on the bench, ranked by its risk-adjusted expected return: the
    probability-weighted scenario value over the price, annualised, less the cash rate, over
    the stock's volatility. A row with a missing input says why instead of showing a number."""
    try:
        return await svc.expected_returns(pool)
    except NoStrategy as err:
        raise _no_strategy() from err


@router.get("/switches")
async def get_switches(pool: Pool, owner: Owner) -> rk.SwitchResponse:
    """For each holding, the best-ranked idea that beats it, the expected-return gap against
    the switching hurdle (tax on the lots sold, costs, safety margin) and the suggested size at
    equal risk, evaluated on the post-trade book. Flags only: nothing is traded."""
    try:
        return await svc.switch_suggestions(pool, owner.user_id)
    except NoStrategy as err:
        raise _no_strategy() from err
    except NoHoldings as err:
        raise HTTPException(404, "There is no portfolio snapshot yet.") from err


@router.get("/switches/log")
async def list_switch_log(pool: Pool, owner: Owner) -> list[switches.SwitchEntry]:
    """The switches you confirmed, newest first, with the figures behind each."""
    return await switches.list_switches(pool, owner.user_id)


@router.get("/switches/detected")
async def detected_switches(pool: Pool, owner: Owner) -> list[switches.DetectedSwitch]:
    """Sales followed within three days by a buy of another stock in your Trading212 history, not
    yet in the log. Candidates only: confirm one with the log call below."""
    return await switches.detect(pool, owner.user_id)


@router.post("/switches/log", status_code=201)
async def add_switch_log(body: switches.SwitchIn, pool: Pool, owner: Owner) -> switches.SwitchEntry:
    """Record a switch you made in Trading212 (stock sold, stock bought), with the prices of the
    day and the suggestion it followed. The log is append-only. The yearly scorecard follows the
    two stocks from the switch date and says whether the safety margin should be raised."""
    try:
        return await switches.record(pool, owner.user_id, body)
    except JournalError as err:
        raise HTTPException(422, str(err)) from err
