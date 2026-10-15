"""The decision journal (M7.1): append-only entries with the evidence and methods they rested on.

An entry is never edited or deleted. A later review is a new entry pointing at the one it
revisits, so the reasons written at the time stay exactly as written."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field, field_validator

from steward.analytics.versions import METHOD_VERSIONS
from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.store import load_active
from steward.config.strategy import StrategyConfig
from steward.config.thesis import MainReason, ThesesConfig
from steward.decision.service import volatility_and_price
from steward.reference.price_series import choose_series, holding_symbols
from steward.reference.read import load_series
from steward.reference.returns import series_currency

Decision = Literal["buy", "add", "hold", "trim", "sell", "pass", "watch", "review"]
Scope = Literal["security", "portfolio"]

__all__ = ["Decision", "JournalEntry", "JournalEntryIn", "MainReason", "Scope"]


class JournalEntryIn(BaseModel):
    scope: Scope = "security"
    ticker: str | None = Field(default=None, max_length=40)
    decision: Decision
    parent_id: int | None = Field(
        default=None, description="The entry this one reviews; its reasons stay untouched."
    )
    decided_on: dt.date | None = Field(default=None, description="Defaults to today.")
    hypothesis: str = Field(min_length=1, max_length=4000)
    valuation_assumptions: str | None = Field(default=None, max_length=4000)
    catalysts: list[str] = Field(default_factory=list, max_length=12)
    disconfirming: list[str] = Field(default_factory=list, max_length=12)
    review_date: dt.date | None = None
    conviction: int | None = Field(default=None, ge=1, le=5)
    main_reason: MainReason | None = None
    price_at_decision: float | None = Field(
        default=None, gt=0, description="Defaults to the latest stored price."
    )

    @field_validator("catalysts", "disconfirming")
    @classmethod
    def _lines(cls, value: list[str]) -> list[str]:
        return [line.strip() for line in value if line.strip()]

    @field_validator("ticker")
    @classmethod
    def _ticker(cls, value: str | None) -> str | None:
        return value.strip() or None if value else None


class JournalEntry(BaseModel):
    id: int
    scope: Scope
    ticker: str | None
    decision: Decision
    parent_id: int | None
    decided_on: dt.date
    hypothesis: str
    valuation_assumptions: str | None
    catalysts: list[str]
    disconfirming: list[str]
    review_date: dt.date | None
    conviction: int | None
    main_reason: str | None
    price_at_decision: float | None
    price_currency: str | None
    snapshot: dict[str, Any]
    created_at: dt.datetime


_COLUMNS = (
    "id, scope, ticker, decision, parent_id, decided_on, hypothesis, valuation_assumptions,"
    " catalysts, disconfirming, review_date, conviction, main_reason, price_at_decision,"
    " price_currency, snapshot, created_at"
)


def _entry(row: asyncpg.Record) -> JournalEntry:
    data = dict(row)
    snap = data["snapshot"]
    data["snapshot"] = json.loads(snap) if isinstance(snap, str) else snap
    if data["price_at_decision"] is not None:
        data["price_at_decision"] = float(data["price_at_decision"])
    return JournalEntry(**data)


class JournalError(Exception):
    """The entry cannot be recorded; the message says why in the owner's terms."""


async def build_snapshot(
    pool: asyncpg.Pool, ticker: str | None
) -> tuple[dict[str, Any], float | None, str | None]:
    """The evidence at this moment: the thesis, holding, price and the config and method
    versions in force. Returns (snapshot, last price, price currency)."""
    theses = await load_active(pool, "theses", ThesesConfig)
    strategy = await load_active(pool, "strategy", StrategyConfig)
    profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    snapshot: dict[str, Any] = {
        "taken_at": dt.datetime.now(dt.UTC).isoformat(),
        "config_versions": {
            "theses": theses.id if theses else None,
            "strategy": strategy.id if strategy else None,
            "holding_profiles": profiles.id if profiles else None,
        },
        "method_versions": dict(METHOD_VERSIONS),
    }
    if ticker is None:
        total = await pool.fetchval(
            "select sum(value_base) from public.position_snapshot"
            " where snapshot_date = (select max(snapshot_date) from public.position_snapshot)"
        )
        snapshot["portfolio_value_base"] = float(total) if total is not None else None
        return snapshot, None, None

    thesis = next((t for t in (theses.body.theses if theses else []) if t.ticker == ticker), None)
    snapshot["thesis"] = thesis.model_dump(mode="json") if thesis else None
    snapshot["thesis_gaps"] = thesis.missing_requirements() if thesis else None
    row = await pool.fetchrow(
        "select p.quantity, p.current_price, p.instrument_currency, p.value_base,"
        " p.snapshot_date,"
        " (select sum(value_base) from public.position_snapshot x"
        "   where x.snapshot_date = p.snapshot_date) as total"
        " from public.position_snapshot p where p.ticker = $1"
        " and p.snapshot_date = (select max(snapshot_date) from public.position_snapshot)",
        ticker,
    )
    price: float | None = None
    currency: str | None = None
    if row is not None:
        price = float(row["current_price"])
        currency = row["instrument_currency"]
        total = float(row["total"] or 0)
        snapshot["holding"] = {
            "as_of": row["snapshot_date"].isoformat(),
            "quantity": float(row["quantity"]),
            "value_base": float(row["value_base"]),
            "weight": float(row["value_base"]) / total if total > 0 else None,
        }
    else:
        snapshot["holding"] = None
    market = (await volatility_and_price(pool, [ticker])).get(ticker)
    if market is not None:
        vol, last_close, last_day = market
        snapshot["market"] = {
            "volatility_1y": vol,
            "last_close": last_close,
            "last_close_date": last_day.isoformat() if last_day else None,
        }
        if price is None:
            price = last_close
    return snapshot, price, currency


def last_close_on_or_before(series: list[tuple[dt.date, float]], day: dt.date) -> float | None:
    """The close on `day`, else the last close before it."""
    best: float | None = None
    for d, close in series:
        if d > day:
            break
        best = close
    return best


async def price_on_or_before(
    pool: asyncpg.Pool, ticker: str, day: dt.date
) -> tuple[float | None, str | None]:
    """(close on or before `day`, its currency) from the stored series the scorecard uses."""
    profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    symbols = await holding_symbols(
        pool, [ticker], profiles.body if profiles else HoldingProfilesConfig()
    )
    ref = (await choose_series(pool, symbols)).get(ticker)
    if ref is None:
        return None, None
    series = await load_series(pool, ref, since=day - dt.timedelta(days=14))
    price = last_close_on_or_before(series, day)
    if price is None:
        return None, None
    return price, await series_currency(pool, ref, None)


async def record(
    pool: asyncpg.Pool, owner_id: str, body: JournalEntryIn, origin: str = "owner"
) -> JournalEntry:
    if body.scope == "security" and not body.ticker:
        raise JournalError("Name the security this decision is about.")
    if body.scope == "portfolio" and body.ticker:
        raise JournalError("A portfolio entry has no ticker.")
    if body.parent_id is not None:
        parent = await pool.fetchrow(
            "select scope, ticker from public.decision_journal where id=$1 and owner_id=$2",
            body.parent_id,
            owner_id,
        )
        if parent is None:
            raise JournalError("The entry you are reviewing does not exist.")
        if parent["ticker"] != body.ticker or parent["scope"] != body.scope:
            raise JournalError("A review must be about the same security as the entry it revisits.")
    today = dt.date.today()
    if body.decided_on is not None and body.decided_on > today:
        raise JournalError("The decision date cannot be in the future.")
    snapshot, last_price, currency = await build_snapshot(pool, body.ticker)
    if origin != "owner":
        snapshot["origin"] = origin
    price = body.price_at_decision
    if price is None:
        if body.decided_on is None or body.decided_on >= today or body.ticker is None:
            price = last_price
        else:
            # A back-dated entry takes the close on (or the last one before) the decision day,
            # not today's price; with no stored series the price stays empty rather than wrong.
            price, currency = await price_on_or_before(pool, body.ticker, body.decided_on)
    row = await pool.fetchrow(
        "insert into public.decision_journal"  # noqa: S608 - constant column list
        " (owner_id, scope, ticker, decision, parent_id, decided_on, hypothesis,"
        "  valuation_assumptions, catalysts, disconfirming, review_date, conviction,"
        "  main_reason, price_at_decision, price_currency, snapshot)"
        " values ($1,$2,$3,$4,$5,coalesce($6, current_date),$7,$8,$9,$10,$11,$12,$13,$14,$15,"
        " $16::jsonb)"
        f" returning {_COLUMNS}",
        owner_id,
        body.scope,
        body.ticker,
        body.decision,
        body.parent_id,
        body.decided_on,
        body.hypothesis.strip(),
        body.valuation_assumptions,
        body.catalysts,
        body.disconfirming,
        body.review_date,
        body.conviction,
        body.main_reason,
        price,
        currency,
        json.dumps(snapshot, default=str),
    )
    assert row is not None
    return _entry(row)


def written_by_owner(entry: JournalEntry) -> bool:
    """False for an entry the app logged from a detected trade: the owner has not written the
    reasons, so the scorecard and the process review leave it out."""
    return bool(entry.snapshot.get("origin", "owner") == "owner")


async def list_entries(
    pool: asyncpg.Pool,
    owner_id: str,
    ticker: str | None = None,
    limit: int | None = 200,
    offset: int = 0,
) -> list[JournalEntry]:
    """Newest first. `limit=None` returns every entry (an export must not cut the list off);
    `offset` skips that many newer entries, to read the next page."""
    rows = await pool.fetch(
        f"select {_COLUMNS} from public.decision_journal"  # noqa: S608 - constant column list
        " where owner_id=$1 and ($2::text is null or ticker=$2)"
        " order by decided_on desc, id desc limit $3 offset $4",
        owner_id,
        ticker,
        limit,
        offset,
    )
    return [_entry(r) for r in rows]


async def get_entry(pool: asyncpg.Pool, owner_id: str, entry_id: int) -> JournalEntry | None:
    row = await pool.fetchrow(
        f"select {_COLUMNS} from public.decision_journal where id=$1 and owner_id=$2",  # noqa: S608 - constant column list
        entry_id,
        owner_id,
    )
    return _entry(row) if row else None
