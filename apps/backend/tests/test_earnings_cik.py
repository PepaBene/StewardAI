"""A CIK that comes from the holding profile is written to the instrument row."""

from typing import Any

from steward.config.holding_profiles import HoldingProfile
from steward.earnings.check import resolve_cik


class _Pool:
    def __init__(self) -> None:
        self.executed: list[tuple[Any, ...]] = []

    async def execute(self, *args: Any) -> None:
        self.executed.append(args)


async def test_profile_cik_is_saved_on_an_instrument_without_one() -> None:
    pool = _Pool()
    profile = HoldingProfile(ticker="FB_US_EQ", sec_cik="1326801", sec_ticker="META")
    cik = await resolve_cik(pool, None, profile, {"cik": None})  # type: ignore[arg-type]
    assert cik == "0001326801"
    assert len(pool.executed) == 1
    assert pool.executed[0][1:] == ("FB_US_EQ", "0001326801")


async def test_nothing_is_written_when_the_instrument_has_a_cik_or_is_missing() -> None:
    pool = _Pool()
    profile = HoldingProfile(ticker="FB_US_EQ", sec_cik="1326801")
    await resolve_cik(pool, None, profile, {"cik": "0001326801"})  # type: ignore[arg-type]
    await resolve_cik(pool, None, profile, None)  # type: ignore[arg-type]
    assert pool.executed == []
