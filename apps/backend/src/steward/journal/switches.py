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
    today = dt.date.today()
    day = body.switched_on or today
    if day > today:
        raise JournalError("The switch date cannot be in the future.")
    sold_price, sold_ccy = body.sold_price, None
    bought_price, bought_ccy = body.bought_price, None
    if sold_price is None:
        sold_price, sold_ccy = await price_on_or_before(pool, body.sold_ticker, day)
    if bought_price is None:
        bought_price, bought_ccy = await price_on_or_before(pool, body.bought_ticker, day)
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
