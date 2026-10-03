"""Total return for Stooq/Yahoo series (audit: unavailable because only EODHD/Alpha Vantage write
raw closes): computed on split-adjusted closes when the dividends and their completeness exist,
with the basis stated, and unknown (with the reason) when they do not."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal as D
from typing import Any

import asyncpg
import httpx
import pytest

from steward.config.holding_profiles import HoldingProfile, HoldingProfilesConfig
from steward.free_sources import compute_returns
from steward.market_analysis import PricePoint
from steward.reference.adjusted_total_return import (
    BASIS,
    closes_show_a_split,
    split_adjusted_total_return,
)
from steward.reference.listings import derive_listings
from steward.reference.prices import PriceDeps, collect_yahoo
from steward.reference.yahoo_actions import events_cover, needs_history_refetch
from steward.sources.base import AccessResult, AccessState
from steward.sources.store import CoverageUpdate, update_coverage
from steward.sources.yahoo_events import parse_events
from tests.test_prices_stream import firds_and_figi, instrument, put_bars, weekdays

START, END = dt.date(2026, 7, 1), dt.date(2026, 8, 28)
DAYS = weekdays(START, END)
EX = DAYS[20]


def closes(last: str = "110", ex: str = "100") -> dict[dt.date, str]:
    """100 until the ex-date, then 100 -> `last` at the end (so price return = last/100 - 1)."""
    out = {d: "100" for d in DAYS}
    out[EX] = ex
    out[DAYS[-1]] = last
    return out


async def action(
    pool: asyncpg.Pool,
    symbol: str,
    kind: str,
    ex_date: dt.date,
    *,
    cash: str | None = None,
    ratio: str | None = None,
    status: str = "confirmed",
    source: str = "owner",
    raw: dict[str, Any] | None = None,
    currency: str | None = "USD",
) -> None:
    await pool.execute(
        "insert into public.corporate_action (symbol, action_type, ex_date, ratio, cash_amount,"
        " currency, source, source_url, status, raw)"
        " values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)",
        symbol,
        kind,
        ex_date,
        D(ratio) if ratio else None,
        D(cash) if cash else None,
        currency if cash else None,
        source,
        f"https://example.test/{source}/{kind}/{ex_date}",
        status,
        json.dumps(raw or {}),
    )


async def covered(pool: asyncpg.Pool, symbol: str) -> None:
    await update_coverage(
        pool,
        CoverageUpdate(
            "yahoo",
            "dividend_events",
            symbol,
            AccessResult(AccessState.AVAILABLE, "test"),
            earliest_period=START - dt.timedelta(days=5),
            latest_period=DAYS[-1],
        ),
    )


async def returns(
    pool: asyncpg.Pool, symbol: str, source: str, *, verified: bool = False
) -> dict[str, object]:
    result = await compute_returns(
        pool,
        symbol=symbol,
        start=START,
        end=END,
        source=source,
        currency="USD",
        actions_complete=verified,
        mic="XNYS",
    )
    return {**result.output, "row": result.rows[2]}


# ------------------------------------------------------------------------------ pure
def test_a_confirmed_split_that_the_closes_still_show_means_they_are_not_adjusted() -> None:
    from steward.market_analysis import Action

    jump = [PricePoint(DAYS[0], D("100"), "USD"), PricePoint(DAYS[1], D("50"), "USD")]
    split = Action("split", DAYS[1], ratio=D(2))
    assert "not split-adjusted" in (closes_show_a_split(jump, [split]) or "")
    flat = [PricePoint(DAYS[0], D("50"), "USD"), PricePoint(DAYS[1], D("50"), "USD")]
    assert closes_show_a_split(flat, [split]) is None


def test_yahoo_events_parse_the_live_shape_in_the_exchange_calendar() -> None:
    body = {
        "chart": {
            "result": [
                {
                    "meta": {"currency": "GBp", "exchangeTimezoneName": "Europe/London"},
                    "events": {
                        "dividends": {
                            "1763625600": {"amount": 1.9512, "date": 1763625600},
                            "1780556400": {"amount": 2.0074635, "date": 1780556400},
                            "1": {"amount": -1, "date": 1},
                            "2": {"amount": 1.0},
                        },
                        "splits": {
                            "1718026200": {
                                "date": 1718026200,
                                "numerator": 10.0,
                                "denominator": 1.0,
                                "splitRatio": "10:1",
                            },
                            "3": {"date": 3, "numerator": 0, "denominator": 1},
                        },
                    },
                }
            ]
        }
    }
    events = parse_events(body)
    # 1780556400 is 2026-06-04 07:00 Europe/London (06:00 UTC): the ex-date is that London day.
    assert [(d.ex_date, d.currency) for d in events.dividends] == [
        (dt.date(2025, 11, 20), "GBp"),
        (dt.date(2026, 6, 4), "GBp"),
    ]
    assert events.dividends[0].amount == D("1.9512")
    assert [(s.ex_date, s.ratio) for s in events.splits] == [(dt.date(2024, 6, 10), D(10))]
    # No events block: an empty answer, not an error.
    none = parse_events({"chart": {"result": [{"meta": {"currency": "USD"}}]}})
    assert none.dividends == () and none.splits == ()
    with pytest.raises(ValueError):
        parse_events({"chart": {"result": None}})


# ------------------------------------------------------------------------------ Stooq
@pytest.mark.db
async def test_stooq_total_return_from_owner_confirmed_dividends_and_verified_coverage(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    await put_bars(pool, "a.us", "stooq", closes(), currency="USD")
    await action(pool, "a.us", "cash_dividend", EX, cash="2")
    out = await returns(pool, "a.us", "stooq", verified=True)
    # 110 / 100 * (1 + 2/100) - 1
    assert out["total_return"] == D("110") / D("100") * D("1.02") - 1
    assert out["price_adjusted"] == D("0.1") and out["total_return_reason"] is None
    details = out["row"].details  # type: ignore[attr-defined]
    assert details["basis"] == BASIS and details["bar_type"] == "adjusted"
    assert details["dividend_source"] == "owner-confirmed" and details["vendor_dividends"] == 0
    assert details["completeness"] == "owner-verified corporate-action coverage"


@pytest.mark.db
async def test_stooq_without_verified_coverage_is_unknown_and_says_why(
    backend_pool: asyncpg.Pool,
) -> None:
    await put_bars(backend_pool, "a.us", "stooq", closes(), currency="USD")
    await action(backend_pool, "a.us", "cash_dividend", EX, cash="2")
    out = await returns(backend_pool, "a.us", "stooq", verified=False)
    assert out["total_return"] is None
    assert "Stooq publishes no dividend history" in str(out["total_return_reason"])
    assert out["price_adjusted"] == D("0.1")  # the price return is unaffected


@pytest.mark.db
async def test_a_confirmed_split_is_applied_to_an_as_declared_dividend_not_to_the_closes(
    backend_pool: asyncpg.Pool,
) -> None:
    """Closes are already split-adjusted; the 4.00 dividend declared before a later 2:1 split
    is 2.00 on the closes' basis."""
    pool = backend_pool
    await put_bars(pool, "a.us", "stooq", closes(), currency="USD")
    await action(pool, "a.us", "cash_dividend", EX, cash="4")
    await action(pool, "a.us", "split", DAYS[30], ratio="2")
    out = await returns(pool, "a.us", "stooq", verified=True)
    assert out["total_return"] == D("1.1") * D("1.02") - 1
    assert out["row"].details["splits_applied"] == []  # type: ignore[attr-defined]


@pytest.mark.db
async def test_closes_that_show_a_confirmed_split_are_unavailable_not_adjusted_twice(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    unadjusted = {d: ("100" if d < DAYS[30] else "50") for d in DAYS}
    await put_bars(pool, "a.us", "stooq", unadjusted, currency="USD")
    await action(pool, "a.us", "split", DAYS[30], ratio="2")
    out = await returns(pool, "a.us", "stooq", verified=True)
    assert out["total_return"] is None
    assert "not split-adjusted" in str(out["total_return_reason"])


@pytest.mark.db
async def test_a_confirmed_spin_off_makes_total_return_unavailable(
    backend_pool: asyncpg.Pool,
) -> None:
    await put_bars(backend_pool, "a.us", "stooq", closes(), currency="USD")
    await action(backend_pool, "a.us", "spin_off", DAYS[25])
    out = await returns(backend_pool, "a.us", "stooq", verified=True)
    assert out["total_return"] is None and "spin_off" in str(out["total_return_reason"])


@pytest.mark.db
async def test_series_that_are_not_split_adjusted_closes_keep_the_old_answer(
    backend_pool: asyncpg.Pool,
) -> None:
    await put_bars(backend_pool, "a", "twelve_data", closes(), currency="USD")
    out = await returns(backend_pool, "a", "twelve_data", verified=True)
    assert out["total_return"] is None
    assert out["total_return_reason"] == "raw close history is unavailable"


# ------------------------------------------------------------------------------ Yahoo
@pytest.mark.db
async def test_yahoo_total_return_uses_its_own_dividends_flagged_unconfirmed(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    await put_bars(pool, "A", "yahoo", closes(), currency="USD")
    await action(
        pool, "A", "cash_dividend", EX, cash="2", status="suggested", source="yahoo",
        raw={"amount_basis": "split_adjusted"},
    )  # fmt: skip
    # Without a record of the range Yahoo was asked about, "no dividend" would be a guess.
    unknown = await returns(pool, "A", "yahoo")
    assert unknown["total_return"] is None
    assert "Yahoo's dividend list was not collected" in str(unknown["total_return_reason"])

    await covered(pool, "A")
    out = await returns(pool, "A", "yahoo")
    assert out["total_return"] == D("1.1") * D("1.02") - 1
    details = out["row"].details  # type: ignore[attr-defined]
    assert details["vendor_dividends"] == 1 and details["confirmed_dividends"] == 0
    assert details["dividend_source"] == "owner-confirmed plus Yahoo (unconfirmed)"
    assert any("unofficial" in w for w in details["warnings"])


@pytest.mark.db
async def test_a_rejected_yahoo_dividend_is_a_veto_and_a_confirmed_one_wins_the_same_day(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    await put_bars(pool, "A", "yahoo", closes(), currency="USD")
    await covered(pool, "A")
    await action(
        pool, "A", "cash_dividend", EX, cash="9", status="rejected", source="yahoo",
        raw={"amount_basis": "split_adjusted"},
    )  # fmt: skip
    rejected = await returns(pool, "A", "yahoo")
    assert rejected["total_return"] == D("0.1")  # no dividend left: equals the price return
    assert any("lists no dividend" in w for w in rejected["row"].details["warnings"])  # type: ignore[attr-defined]

    # Yahoo says 2.0001, the owner confirmed 2.00 for the same ex-date: no false conflict.
    await action(
        pool, "A", "cash_dividend", DAYS[40], cash="2.0001", status="suggested", source="yahoo",
        raw={"amount_basis": "split_adjusted"},
    )  # fmt: skip
    await action(pool, "A", "cash_dividend", DAYS[40], cash="2")
    out = await returns(pool, "A", "yahoo")
    assert out["total_return"] == D("1.1") * D("1.02") - 1
    assert out["row"].details["vendor_dividends"] == 0  # type: ignore[attr-defined]


@pytest.mark.db
async def test_nothing_is_substituted_when_the_ex_date_close_is_missing(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    partial = closes()
    del partial[EX]
    await put_bars(pool, "A", "yahoo", partial, currency="USD")
    await covered(pool, "A")
    await action(
        pool, "A", "cash_dividend", EX, cash="2", status="suggested", source="yahoo",
        raw={"amount_basis": "split_adjusted"},
    )  # fmt: skip
    out = await returns(pool, "A", "yahoo")
    assert out["total_return"] is None
    assert "ex-date close is unavailable" in str(out["total_return_reason"])


@pytest.mark.db
async def test_the_helper_refuses_a_series_without_enough_closes(
    backend_pool: asyncpg.Pool,
) -> None:
    result = await split_adjusted_total_return(
        backend_pool, source="stooq", symbol="a.us", points=[PricePoint(DAYS[0], D(1), "USD")],
        confirmed=[], owner_verified=True, action_symbols=["a.us"], listing_id=None,
        start=START, end=END,
    )  # fmt: skip
    assert result.value is None and "two closes" in str(result.reason)


# ------------------------------------------------------------------------------ collector
def chart_body(symbol: str, *, dividends: dict[int, float] | None = None) -> str:
    """A Yahoo daily chart of CZK closes with the events block of the live shape."""
    stamps = [int(dt.datetime.combine(d, dt.time(7, 0), dt.UTC).timestamp()) for d in DAYS[:30]]
    quote: dict[str, list[float]] = {
        k: [100.0] * len(stamps) for k in ("open", "high", "low", "close", "volume")
    }
    result: dict[str, Any] = {
        "meta": {
            "currency": "CZK",
            "symbol": symbol,
            "exchangeTimezoneName": "Europe/Prague",
            "dataGranularity": "1d",
            "priceHint": 2,
        },
        "timestamp": stamps,
        "indicators": {"quote": [quote]},
    }
    if dividends:
        result["events"] = {
            "dividends": {str(ts): {"amount": amt, "date": ts} for ts, amt in dividends.items()}
        }
    return json.dumps({"chart": {"result": [result], "error": None}})


async def run_collector(pool: asyncpg.Pool, body: str, now: dt.datetime) -> tuple[int, str | None]:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        deps = PriceDeps(http=http, today=now.date(), now=now)
        return await collect_yahoo(pool, deps, "CEZ.PR", 1)


@pytest.mark.db
async def test_the_yahoo_collector_stores_events_as_unconfirmed_and_records_their_range(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    await instrument(pool, "CEZ_EQ")
    await firds_and_figi(pool, "CEZ_EQ", "CZ0005112300", "XPRA", "CZK", "CEZ", "CP")
    profiles = HoldingProfilesConfig(
        profiles=[HoldingProfile(ticker="CEZ_EQ", yahoo_symbol="CEZ.PR")]
    )
    await derive_listings(pool, ["CEZ_EQ"], profiles)

    ex_ts = int(dt.datetime.combine(DAYS[10], dt.time(7, 0), dt.UTC).timestamp())
    # `today` is chosen so that the history start (today - 1 year - 5 days) is the first bar.
    now = dt.datetime.combine(DAYS[0] + dt.timedelta(days=370), dt.time(20, 0), dt.UTC)
    full_start = now.date() - dt.timedelta(days=365 + 5)
    assert await needs_history_refetch(pool, "CEZ.PR", full_start)  # nothing collected yet

    stored, gap = await run_collector(pool, chart_body("CEZ.PR", dividends={ex_ts: 12.5}), now)
    assert (stored, gap) == (30, None)
    rows = await pool.fetch(
        "select action_type, ex_date, cash_amount, currency, source, status, raw"
        " from public.corporate_action where symbol = 'CEZ.PR'"
    )
    assert len(rows) == 1
    row = rows[0]
    assert (row["action_type"], row["ex_date"], row["cash_amount"]) == (
        "cash_dividend", DAYS[10], D("12.5")
    )  # fmt: skip
    assert (row["currency"], row["source"], row["status"]) == ("CZK", "yahoo", "suggested")
    assert json.loads(row["raw"])["amount_basis"] == "split_adjusted"
    # The range Yahoo was asked about is recorded; "none listed" now means something.
    assert await events_cover(pool, "CEZ.PR", DAYS[0], DAYS[29])
    assert not await events_cover(pool, "CEZ.PR", DAYS[0], DAYS[35])  # beyond what was fetched
    assert not await needs_history_refetch(pool, "CEZ.PR", full_start)

    # A second run updates the same row (a revised amount, not a second dividend) ...
    await run_collector(pool, chart_body("CEZ.PR", dividends={ex_ts: 12.75}), now)
    amounts = [
        r["cash_amount"]
        for r in await pool.fetch("select cash_amount from public.corporate_action")
    ]
    assert amounts == [D("12.75")]
    # ... but a decision of the owner is final: Yahoo never changes a confirmed dividend.
    await pool.execute("update public.corporate_action set status = 'confirmed'")
    await run_collector(pool, chart_body("CEZ.PR", dividends={ex_ts: 99}), now)
    kept = await pool.fetchrow("select cash_amount, status from public.corporate_action")
    assert (kept["cash_amount"], kept["status"]) == (D("12.75"), "confirmed")


@pytest.mark.db
async def test_without_a_confirmed_listing_events_are_not_stored_and_coverage_says_so(
    backend_pool: asyncpg.Pool,
) -> None:
    pool = backend_pool
    ex_ts = int(dt.datetime.combine(DAYS[10], dt.time(7, 0), dt.UTC).timestamp())
    now = dt.datetime.combine(DAYS[40], dt.time(20, 0), dt.UTC)
    await run_collector(pool, chart_body("CEZ.PR", dividends={ex_ts: 12.5}), now)
    assert await pool.fetchval("select count(*) from public.corporate_action") == 0
    state = await pool.fetchrow(
        "select state, reason from public.coverage_status where provider='yahoo'"
        " and dataset='dividend_events' and entity_key='CEZ.PR'"
    )
    assert state["state"] == "not_covered" and "confirmed canonical listing" in state["reason"]
    assert not await events_cover(pool, "CEZ.PR", DAYS[0], DAYS[29])
    # A symbol that cannot store events does not ask for its whole history on every run.
    assert not await needs_history_refetch(pool, "CEZ.PR", now.date() - dt.timedelta(days=370))


@pytest.mark.db
async def test_the_profiles_withholding_rate_reduces_reinvested_dividends(
    backend_pool: asyncpg.Pool,
) -> None:
    from steward.reference.price_series import SeriesRef
    from steward.reference.returns import ReturnInputs, compute_for

    pool = backend_pool
    await put_bars(pool, "a.us", "stooq", closes(), currency="USD")
    await action(pool, "a.us", "cash_dividend", EX, cash="2")
    await pool.execute(
        "insert into public.coverage_status (provider, dataset, entity_key, state, latest_period)"
        " values ('owner_review', 'corporate_actions', 'a.us', 'available', $1)",
        END,
    )
    inputs = ReturnInputs(
        ticker="A_US_EQ", ref=SeriesRef("a.us", "stooq"), currency="USD", mic="XNYS",
        listing_id=None, action_symbols=["a.us"], ads_ratio=None, withholding_rate=D("0.15"),
    )  # fmt: skip
    taxed = await compute_for(pool, inputs, START, END, store=False)
    assert taxed["total_return"] == D("1.1") * (1 + D("2") * D("0.85") / 100) - 1
    gross = await compute_for(pool, inputs, START, END, store=False, withholding_rate=D(0))
    assert gross["total_return"] == D("1.1") * D("1.02") - 1
