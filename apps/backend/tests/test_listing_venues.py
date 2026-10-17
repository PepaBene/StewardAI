"""Listings on the venue a fund trades on, and none from FIRDS records London no longer files."""

import datetime as dt
from typing import Any

from steward.reference.listings import broker_root, derive_candidate
from steward.reference.price_series import PriceSymbols

SYMBOLS = PriceSymbols(stooq=None, yahoo=None)


def fake_pool(*answers: Any) -> Any:
    it = iter(answers)

    class FakePool:
        async def fetchrow(self, _sql: str, *_args: Any) -> Any:
            return next(it)

    return FakePool()


def instrument(**over: Any) -> dict[str, Any]:
    return {
        "t212_ticker": "SXRVd_EQ",
        "isin": "IE00B53SZB19",
        "name": "iShares NASDAQ 100 (Acc)",
        "currency": "EUR",
        "home_mic": "XSWX",
        "price_symbol": None,
        "issuer_lei": None,
        "security_type": "Mutual Fund",
        "share_class_figi": None,
        **over,
    }


def test_broker_root() -> None:
    assert broker_root("SXRVd_EQ") == ("SXRV", "d")
    assert broker_root("CSCO_US_EQ") == ("CSCO", None)


async def test_a_xetra_fund_is_its_xetra_line_in_euro() -> None:
    # FIRDS files the fund on Xetra's segments in its base currency (USD).
    firds = {"currency": "USD", "status": "UNCH", "terminated_at": None, "first_trade_at": None}
    c = await derive_candidate(
        fake_pool(instrument(), firds, {"figi": "BBG000", "listing_ticker": "SXRV"}),
        "SXRVd_EQ",
        SYMBOLS,
    )
    assert c is not None
    assert (c.mic, c.listing_ticker, c.currency, c.status) == ("XETR", "SXRV", "EUR", "active")
    assert c.confirmable and c.gaps == []


async def test_a_fund_line_under_another_ticker_waits_for_the_owner() -> None:
    firds = {"currency": "USD", "status": "UNCH", "terminated_at": None, "first_trade_at": None}
    c = await derive_candidate(
        fake_pool(instrument(), firds, {"figi": "BBG000", "listing_ticker": "CSNDX"}),
        "SXRVd_EQ",
        SYMBOLS,
    )
    assert c is not None and not c.confirmable


async def test_a_london_fund_needs_no_firds_record() -> None:
    inst = instrument(t212_ticker="BRNTl_EQ", isin="JE00B78CGV99", currency="USD", home_mic=None)
    c = await derive_candidate(
        fake_pool(inst, {"figi": "BBG001", "listing_ticker": "BRNT"}), "BRNTl_EQ", SYMBOLS
    )
    assert c is not None
    assert (c.mic, c.listing_ticker, c.currency) == ("XLON", "BRNT", "USD")
    assert c.confirmable


async def test_a_london_home_listing_is_not_ended_by_the_brexit_firds_record() -> None:
    inst = instrument(
        t212_ticker="AZN_US_EQ",
        isin="GB0009895292",
        name="AstraZeneca",
        currency="USD",
        home_mic="XLON",
        security_type="Common Stock",
    )
    c = await derive_candidate(
        fake_pool(inst, {"figi": "BBG002", "listing_ticker": "AZN"}),
        "AZN_US_EQ",
        SYMBOLS,
        today=dt.date(2026, 10, 17),
    )
    assert c is not None
    assert (c.mic, c.currency, c.status, c.valid_to) == ("XLON", "GBP", "active", None)
    assert c.confirmable
