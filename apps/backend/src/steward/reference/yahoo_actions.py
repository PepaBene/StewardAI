"""Yahoo's dividends and splits as unconfirmed corporate-action evidence, with coverage.

`collect_yahoo` asks for `events=div,splits` with the daily bars (one request, one answer).
This module stores what that answer lists:

* every dividend and split becomes a `corporate_action` row with `status='suggested'`,
  `source='yahoo'`, a stable per-event `source_url` (a re-run updates it, never duplicates it) and
  `raw` stating that the amount is split-adjusted. A decided row (confirmed or rejected by the
  owner) is left exactly as decided, whatever Yahoo says later;
* a `coverage_status` row (`yahoo` / `dividend_events` / symbol) records the range Yahoo was
  asked about: `earliest_period` is the first session of a full-history fetch, `latest_period`
  the last bar of the answer. An answer with no `events` is a real "none listed in this range"
  only because this row says which range that is.

Nothing here confirms anything: Yahoo is unofficial and its list cannot be checked against the
issuer. `steward.reference.adjusted_total_return` is the one reader, and it says so.
"""

from __future__ import annotations

import datetime as dt
import json

import asyncpg

from steward.sources.base import AccessResult, AccessState
from steward.sources.store import CoverageUpdate, update_coverage
from steward.sources.yahoo_events import YahooEvents

PROVIDER = "yahoo"
DATASET = "dividend_events"
SOURCE = "yahoo"
HISTORY_URL = "https://finance.yahoo.com/quote/{symbol}/history/"
# A stored history that starts within this many days of the wanted start is complete enough:
# the first session of a range is rarely on its first calendar day.
START_SLACK_DAYS = 7


def event_url(symbol: str, kind: str, ex_date: dt.date) -> str:
    """Stable per-event URL: the natural key of the row (Yahoo keys its events by date, so one
    ex-date is one event), so a re-run or a revised amount is an update, never a second row."""
    return f"{HISTORY_URL.format(symbol=symbol)}#{kind}-{ex_date.isoformat()}"


async def events_cover(
    pool: asyncpg.Pool | asyncpg.Connection, symbol: str, start: dt.date, through: dt.date
) -> bool:
    """Whether Yahoo's events were collected for `symbol` from `start` through `through`."""
    row = await pool.fetchrow(
        "select state, earliest_period, latest_period from public.coverage_status"
        " where provider=$1 and dataset=$2 and entity_key=$3",
        PROVIDER,
        DATASET,
        symbol,
    )
    return bool(
        row is not None
        and row["state"] == "available"
        and row["earliest_period"] is not None
        and row["latest_period"] is not None
        and row["earliest_period"] <= start + dt.timedelta(days=START_SLACK_DAYS)
        and row["latest_period"] >= through
    )


async def needs_history_refetch(
    pool: asyncpg.Pool | asyncpg.Connection, symbol: str, full_start: dt.date
) -> bool:
    """True when no events were ever collected from `full_start`, so the next fetch must cover
    the whole history again (bars are an idempotent upsert). A symbol whose events cannot be
    stored (no confirmed listing) is recorded `not_covered` and does not ask again."""
    row = await pool.fetchrow(
        "select state, earliest_period from public.coverage_status"
        " where provider=$1 and dataset=$2 and entity_key=$3",
        PROVIDER,
        DATASET,
        symbol,
    )
    if row is None:
        return True
    return bool(
        row["state"] == "available"
        and (
            row["earliest_period"] is None
            or row["earliest_period"] > full_start + dt.timedelta(days=START_SLACK_DAYS)
        )
    )


async def store_events(
    pool: asyncpg.Pool,
    symbol: str,
    events: YahooEvents,
    *,
    listing_id: int | None,
    first_session: dt.date | None,
    last_session: dt.date | None,
    full_history: bool,
) -> dict[str, int | str]:
    """Write one answer's events and the coverage of its range; returns what was done.

    `full_history` says the answer started at the history start (so `first_session` is the
    start of the covered range); an incremental answer only extends `latest_period`."""
    if listing_id is None:
        await update_coverage(
            pool,
            CoverageUpdate(
                PROVIDER,
                DATASET,
                symbol,
                AccessResult(
                    AccessState.NOT_COVERED,
                    "no confirmed canonical listing for this symbol: dividends and splits are "
                    "not stored (confirm the holding's listing first)",
                ),
            ),
        )
        return {"stored": 0, "coverage": "not_covered: no confirmed listing"}
    stored = 0
    async with pool.acquire() as conn, conn.transaction():
        for dividend in events.dividends:
            await _upsert(
                conn,
                listing_id,
                symbol,
                "cash_dividend",
                dividend.ex_date,
                cash=dividend.amount,
                ratio=None,
                currency=dividend.currency,
                url=event_url(symbol, "dividend", dividend.ex_date),
            )
            stored += 1
        for split in events.splits:
            await _upsert(
                conn,
                listing_id,
                symbol,
                "split",
                split.ex_date,
                cash=None,
                ratio=split.ratio,
                currency=None,
                url=event_url(symbol, "split", split.ex_date),
            )
            stored += 1
    await update_coverage(
        pool,
        CoverageUpdate(
            PROVIDER,
            DATASET,
            symbol,
            AccessResult(
                AccessState.AVAILABLE,
                "Yahoo (unofficial) lists these dividends and splits; completeness is not "
                "verified against the issuer",
            ),
            earliest_period=first_session if full_history else None,
            latest_period=last_session,
        ),
    )
    return {"stored": stored, "coverage": "available"}


async def _upsert(
    conn: asyncpg.Connection,
    listing_id: int,
    symbol: str,
    kind: str,
    ex_date: dt.date,
    *,
    cash: object,
    ratio: object,
    currency: str | None,
    url: str,
) -> None:
    raw = {
        "origin": "yahoo_chart_events",
        "amount_basis": "split_adjusted",
        "tier": 3,
        "note": "unofficial vendor list; unconfirmed",
    }
    await conn.execute(
        """insert into public.corporate_action
           (listing_id,symbol,action_type,ex_date,ratio,cash_amount,currency,source,source_url,
            status,raw)
           values($1,$2,$3,$4,$5,$6,$7,$8,$9,'suggested',$10::jsonb)
           on conflict(listing_id,action_type,ex_date,source,source_url) do update set
             ratio=excluded.ratio, cash_amount=excluded.cash_amount,
             currency=excluded.currency, raw=corporate_action.raw || excluded.raw
           where corporate_action.status = 'suggested'""",
        listing_id,
        symbol,
        kind,
        ex_date,
        ratio,
        cash,
        currency,
        SOURCE,
        url,
        json.dumps(raw),
    )
