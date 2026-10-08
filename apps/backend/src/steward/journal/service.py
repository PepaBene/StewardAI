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


def sell_target_of(entry: JournalEntry) -> tuple[float | None, str | None]:
    """The sell target and its currency as the thesis stood when the entry was written."""
    thesis = entry.snapshot.get("thesis") or {}
    target = thesis.get("price_target") or {}
    value = target.get("sell_target")
    return (float(value) if value is not None else None, target.get("currency"))


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
    for ticker in tickers:
        ref = chosen.get(ticker)
        if ref is not None:
            series_by[ticker] = [(d, float(p)) for d, p in await load_series(pool, ref)]
    out: list[DecisionOutcome] = []
    for e in entries:
        series = series_by.get(e.ticker or "", [])
        target, target_currency = sell_target_of(e)
        comparable = target is not None and (
            target_currency is None
            or e.price_currency is None
            or target_currency == e.price_currency
        )
        out.append(
            DecisionOutcome(
                id=e.id,
                ticker=e.ticker,
                decision=e.decision,
                decided_on=e.decided_on,
                price_at_decision=e.price_at_decision,
                price_now=series[-1][1] if series else None,
                price_now_date=series[-1][0] if series else None,
                currency_at_decision=e.price_currency,
                currency_now=e.price_currency,
                conviction=e.conviction,
                main_reason=e.main_reason,
                sell_target=target if comparable else None,
                hit_on=first_hit(series, e.decided_on, target) if comparable and target else None,
            )
        )
    return out


async def scorecard(
    pool: asyncpg.Pool,
    owner_id: str,
    since: dt.date | None = None,
    ticker: str | None = None,
    reason: str | None = None,
) -> Scorecard:
    today = dt.date.today()
    entries = await list_entries(pool, owner_id, limit=2000)
    return build_scorecard(
        await outcomes(pool, entries),
        since=since or today - dt.timedelta(days=365),
        until=today,
        ticker=ticker,
        reason=reason,
    )
