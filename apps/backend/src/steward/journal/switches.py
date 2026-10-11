"""The switch log (plan Q8.9): switches the owner confirmed, append-only.

A switch is a stock sold and another bought with the proceeds. The row keeps the prices at the
time and the figures that justified it (expected returns, risk profiles, hurdle, lots and tax),
so the yearly scorecard can follow the bought stock against the sold one from the switch date and
say whether the safety margin was high enough. Nothing here trades."""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any

import asyncpg
from pydantic import BaseModel, Field, field_validator

from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.store import load_active
from steward.earnings.check import local_today
from steward.journal.entries import JournalError, price_on_or_before
from steward.journal.scorecard import SwitchOutcome
from steward.reference.price_series import choose_series, holding_symbols
from steward.reference.read import load_series
from steward.reference.returns import series_currency

log = logging.getLogger(__name__)

_COLUMNS = (
    "id, switched_on, sold_ticker, bought_ticker, sold_price, sold_currency, bought_price,"
    " bought_currency, sold_value_base, bought_value_base, snapshot, note, created_at"
)


class SwitchIn(BaseModel):
    sold_ticker: str = Field(min_length=1, max_length=40)
    bought_ticker: str = Field(min_length=1, max_length=40)
    switched_on: dt.date | None = Field(default=None, description="Defaults to today.")
    sold_price: float | None = Field(
        default=None, gt=0, description="Defaults to the stored close on that day."
    )
    bought_price: float | None = Field(
        default=None, gt=0, description="Defaults to the stored close on that day."
    )
    sold_value_base: float | None = Field(default=None, gt=0)
    bought_value_base: float | None = Field(default=None, gt=0)
    line: dict[str, Any] | None = Field(
        default=None,
        description="The suggestion the switch followed, as the switches list returned it: both "
        "expected returns and risk profiles, the hurdle, the lots and the tax.",
    )
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("sold_ticker", "bought_ticker")
    @classmethod
    def _clean(cls, value: str) -> str:
        return value.strip()


class SwitchEntry(BaseModel):
    id: int
    switched_on: dt.date
    sold_ticker: str
    bought_ticker: str
    sold_price: float | None
    sold_currency: str | None
    bought_price: float | None
    bought_currency: str | None
    sold_value_base: float | None
    bought_value_base: float | None
    snapshot: dict[str, Any]
    note: str | None
    created_at: dt.datetime


def _entry(row: asyncpg.Record) -> SwitchEntry:
    data = dict(row)
    snapshot = data["snapshot"]
    data["snapshot"] = json.loads(snapshot) if isinstance(snapshot, str) else snapshot
    for key in ("sold_price", "bought_price", "sold_value_base", "bought_value_base"):
        if data[key] is not None:
            data[key] = float(data[key])
    return SwitchEntry(**data)


async def record(pool: asyncpg.Pool, owner_id: str, body: SwitchIn) -> SwitchEntry:
    if body.sold_ticker == body.bought_ticker:
        raise JournalError("A switch needs two different stocks.")
    today = local_today()
    day = body.switched_on or today
    if day > today:
        raise JournalError("The switch date cannot be in the future.")
    # The stored close fixes the currency even when the owner gives the price, so the scorecard
    # can still check that the price it compares with is in the same currency.
    sold_found, sold_ccy = await price_on_or_before(pool, body.sold_ticker, day)
    bought_found, bought_ccy = await price_on_or_before(pool, body.bought_ticker, day)
    sold_price = body.sold_price if body.sold_price is not None else sold_found
    bought_price = body.bought_price if body.bought_price is not None else bought_found
    snapshot: dict[str, Any] = {"line": body.line} if body.line else {}
    try:
        from steward.decision import ranking_service

        ranking = await ranking_service.expected_returns(pool)
        snapshot["ranking"] = [
            r.model_dump(mode="json")
            for r in ranking.rows
            if r.ticker in (body.sold_ticker, body.bought_ticker)
        ]
        snapshot["method_version"] = ranking.method_version
    except Exception as exc:  # the log must not depend on the ranking being computable
        log.warning("ranking snapshot for the switch log failed: %s", exc)
        snapshot["ranking_unavailable"] = str(exc)
    row = await pool.fetchrow(
        "insert into public.switch_log (owner_id, switched_on, sold_ticker, bought_ticker,"  # noqa: S608
        " sold_price, sold_currency, bought_price, bought_currency, sold_value_base,"
        " bought_value_base, snapshot, note)"
        " values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12)"
        f" returning {_COLUMNS}",
        owner_id,
        day,
        body.sold_ticker,
        body.bought_ticker,
        sold_price,
        sold_ccy,
        bought_price,
        bought_ccy,
        body.sold_value_base,
        body.bought_value_base,
        json.dumps(snapshot, default=str),
        body.note,
    )
    assert row is not None
    return _entry(row)


async def list_switches(pool: asyncpg.Pool, owner_id: str, limit: int = 200) -> list[SwitchEntry]:
    rows = await pool.fetch(
        f"select {_COLUMNS} from public.switch_log"  # noqa: S608 - constant column list
        " where owner_id=$1 order by switched_on desc, id desc limit $2",
        owner_id,
        limit,
    )
    return [_entry(r) for r in rows]


WINDOW_DAYS = 3  # a buy this soon after a sale is read as bought with its proceeds
MIN_SHARE, MAX_SHARE = 0.5, 1.15  # the buy as a share of the sale: much less or more is not it


class Fill(BaseModel):
    ticker: str
    side: str
    on: dt.date
    amount: float  # absolute value in the account currency


class DetectedSwitch(BaseModel):
    switched_on: dt.date
    sold_ticker: str
    bought_ticker: str
    sold_value_base: float
    bought_value_base: float


def pair_switches(fills: list[Fill], logged: set[tuple[str, str, dt.date]]) -> list[DetectedSwitch]:
    """Pair each sale with the nearest later-or-same-day buy of a different stock that fits it
    (within the window, and between MIN_SHARE and MAX_SHARE of the proceeds). Each buy is used
    once, and a pair already in the switch log is left out."""
    # An order filled in several rows is one trade: add up the rows of a ticker and side per day.
    merged: dict[tuple[str, str, dt.date], float] = {}
    for f in fills:
        if f.amount > 0:
            merged[(f.ticker, f.side, f.on)] = merged.get((f.ticker, f.side, f.on), 0.0) + f.amount
    whole = [Fill(ticker=t, side=s, on=d, amount=a) for (t, s, d), a in merged.items()]
    sells = sorted((f for f in whole if f.side == "SELL"), key=lambda f: f.on)
    buys = [f for f in whole if f.side == "BUY"]
    used: set[int] = set()
    out: list[DetectedSwitch] = []
    for sell in sells:
        best: tuple[float, int] | None = None
        for i, buy in enumerate(buys):
            gap = (buy.on - sell.on).days
            share = buy.amount / sell.amount
            if i in used or buy.ticker == sell.ticker or not 0 <= gap <= WINDOW_DAYS:
                continue
            if not MIN_SHARE <= share <= MAX_SHARE:
                continue
            score = gap + abs(1.0 - share)
            if best is None or score < best[0]:
                best = (score, i)
        if best is None:
            continue
        buy = buys[best[1]]
        key = (sell.ticker, buy.ticker, buy.on)
        used.add(best[1])
        if key in logged:
            continue
        out.append(
            DetectedSwitch(
                switched_on=buy.on,
                sold_ticker=sell.ticker,
                bought_ticker=buy.ticker,
                sold_value_base=sell.amount,
                bought_value_base=buy.amount,
            )
        )
    return out


async def detect(pool: asyncpg.Pool, owner_id: str) -> list[DetectedSwitch]:
    """Switches visible in the Trading212 transactions that are not in the switch log yet. They
    are candidates: confirm one by posting it to the log."""
    rows = await pool.fetch(
        "select ticker, subtype, occurred_at::date as day, abs(amount) as amount"
        " from public.\"transaction\" where type = 'order' and subtype in ('BUY', 'SELL')"
        " and ticker is not null and amount is not null order by occurred_at"
    )
    fills = [
        Fill(ticker=r["ticker"], side=r["subtype"], on=r["day"], amount=float(r["amount"]))
        for r in rows
    ]
    logged = {
        (e.sold_ticker, e.bought_ticker, e.switched_on)
        for e in await list_switches(pool, owner_id, limit=1000)
    }
    return pair_switches(fills, logged)


async def outcomes(pool: asyncpg.Pool, entries: list[SwitchEntry]) -> list[SwitchOutcome]:
    """Each switch against the stored closes that followed it: the price change of the stock
    bought and of the stock sold, from the switch date to the latest close."""
    tickers = sorted({t for e in entries for t in (e.sold_ticker, e.bought_ticker)})
    profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    symbols = await holding_symbols(
        pool, tickers, profiles.body if profiles else HoldingProfilesConfig()
    )
    chosen = await choose_series(pool, symbols)
    latest: dict[str, tuple[float, str | None]] = {}
    for ticker in tickers:
        ref = chosen.get(ticker)
        if ref is None:
            continue
        series = await load_series(pool, ref)
        if series:
            latest[ticker] = (float(series[-1][1]), await series_currency(pool, ref, None))
    out: list[SwitchOutcome] = []
    for e in entries:
        sold, bought = latest.get(e.sold_ticker), latest.get(e.bought_ticker)
        out.append(
            SwitchOutcome(
                id=e.id,
                switched_on=e.switched_on,
                sold=e.sold_ticker,
                bought=e.bought_ticker,
                sold_price=e.sold_price,
                sold_currency=e.sold_currency,
                sold_now=sold[0] if sold else None,
                sold_currency_now=sold[1] if sold else None,
                bought_price=e.bought_price,
                bought_currency=e.bought_currency,
                bought_now=bought[0] if bought else None,
                bought_currency_now=bought[1] if bought else None,
            )
        )
    return out
