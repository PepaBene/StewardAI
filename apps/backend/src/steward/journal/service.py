"""Database-facing assembly of the decision scorecard: journal entries joined to the stored price
series that followed them. Reads only."""

from __future__ import annotations

import datetime as dt

import asyncpg

from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.store import load_active
from steward.journal.entries import JournalEntry, list_entries
from steward.journal.scorecard import DecisionOutcome, Scorecard, build_scorecard
from steward.reference.price_series import choose_series, holding_symbols
from steward.reference.read import load_series
from steward.reference.returns import series_currency
from steward.sources.market import quote_unit_of


def sell_target_of(entry: JournalEntry) -> tuple[float | None, str | None]:
    """The sell target and its currency as the thesis stood when the entry was written."""
    thesis = entry.snapshot.get("thesis") or {}
    target = thesis.get("price_target") or {}
    value = target.get("sell_target")
    return (float(value) if value is not None else None, target.get("currency"))


def bear_case_of(entry: JournalEntry) -> float | None:
    """The bear-case price the thesis named when the entry was written."""
    thesis = entry.snapshot.get("thesis") or {}
    value = (thesis.get("price_target") or {}).get("bear_case")
    return float(value) if value is not None else None


def first_hit(series: list[tuple[dt.date, float]], after: dt.date, target: float) -> dt.date | None:
    """First close strictly after `after` at or above `target`."""
    for day, close in series:
        if day > after and close >= target:
            return day
    return None


async def outcomes(pool: asyncpg.Pool, entries: list[JournalEntry]) -> list[DecisionOutcome]:
    tickers = sorted({e.ticker for e in entries if e.ticker})
    profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    symbols = await holding_symbols(
        pool, tickers, profiles.body if profiles else HoldingProfilesConfig()
    )
    chosen = await choose_series(pool, symbols)
    series_by: dict[str, list[tuple[dt.date, float]]] = {}
    currency_by: dict[str, str | None] = {}
    for ticker in tickers:
        ref = chosen.get(ticker)
        if ref is not None:
            series_by[ticker] = [(d, float(p)) for d, p in await load_series(pool, ref)]
            currency_by[ticker] = await series_currency(pool, ref, None)
    return [
        build_outcome(e, series_by.get(e.ticker or "", []), currency_by.get(e.ticker or ""))
        for e in entries
    ]


def same_unit(a: str | None, b: str | None) -> bool:
    """Whether two currency codes name the same quote unit (pence and pounds differ)."""
    return quote_unit_of(a) == quote_unit_of(b)


def build_outcome(
    e: JournalEntry, series: list[tuple[dt.date, float]], series_ccy: str | None
) -> DecisionOutcome:
    """One entry against the stored series that followed it. `series_ccy` is the currency of
    that series' bars; unknown (None) means no change is computed rather than assumed equal."""
    target, target_currency = sell_target_of(e)
    bear = bear_case_of(e)
    comparable_ccy = (
        target_currency is None
        or e.price_currency is None
        or same_unit(target_currency, e.price_currency)
    )
    bear_ok = bear is not None and comparable_ccy
    comparable = target is not None and comparable_ccy
    # Targets and the bear case count only from the day the entry was written: a back-dated
    # entry (decided_on earlier than created_at) must not claim a target hit before that day.
    start = max(e.decided_on, e.created_at.date())
    # The entry's recorded currency must equal the stored series' currency; an unknown series
    # currency counts as a mismatch (no change, hit or low is computed against it).
    series_known = e.price_currency is None or (
        series_ccy is not None and same_unit(e.price_currency, series_ccy)
    )
    return DecisionOutcome(
        id=e.id,
        ticker=e.ticker,
        decision=e.decision,
        decided_on=e.decided_on,
        price_at_decision=e.price_at_decision,
        price_now=series[-1][1] if series else None,
        price_now_date=series[-1][0] if series else None,
        currency_at_decision=e.price_currency,
        currency_now=series_ccy,
        conviction=e.conviction,
        main_reason=e.main_reason,
        sell_target=target if comparable else None,
        bear_case=bear if bear_ok else None,
        low_since=min((c for d, c in series if d > start), default=None)
        if bear_ok and series_known
        else None,
        hit_on=first_hit(series, start, target) if comparable and target and series_known else None,
    )


async def scorecard(
    pool: asyncpg.Pool,
    owner_id: str,
    since: dt.date | None = None,
    ticker: str | None = None,
    reason: str | None = None,
) -> Scorecard:
    today = dt.date.today()
    entries = await list_entries(pool, owner_id, limit=None)
    return build_scorecard(
        await outcomes(pool, entries),
        since=since or today - dt.timedelta(days=365),
        until=today,
        ticker=ticker,
        reason=reason,
    )
