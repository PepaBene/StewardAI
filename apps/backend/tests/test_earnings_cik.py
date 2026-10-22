"""A CIK that comes from the holding profile is written to the instrument row."""

from typing import Any

from steward.config.holding_profiles import HoldingProfile
from steward.earnings.check import resolve_cik


class _Pool:
    def __init__(self, price_symbol: str | None = None) -> None:
        self.executed: list[tuple[Any, ...]] = []
        self.price_symbol = price_symbol

    async def execute(self, *args: Any) -> None:
        self.executed.append(args)

    async def fetchval(self, *args: Any) -> str | None:
        return self.price_symbol


class _Edgar:
    async def cik_for_ticker(self, ticker: str) -> str | None:
        return {"META": "0001326801"}.get(ticker)


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


async def test_a_renamed_ticker_is_found_under_its_listing_symbol() -> None:
    # Trading212 holds Meta as FB_US_EQ; SEC lists it only as META.
    pool = _Pool(price_symbol="meta.us")
    profile = HoldingProfile(ticker="FB_US_EQ")
    cik = await resolve_cik(pool, _Edgar(), profile, {"cik": None})  # type: ignore[arg-type]
    assert cik == "0001326801"
    assert pool.executed[0][1:] == ("FB_US_EQ", "0001326801")


async def test_no_listing_symbol_leaves_the_cik_unknown() -> None:
    pool = _Pool()
    profile = HoldingProfile(ticker="FB_US_EQ")
    assert await resolve_cik(pool, _Edgar(), profile, {"cik": None}) is None  # type: ignore[arg-type]
    assert pool.executed == []
