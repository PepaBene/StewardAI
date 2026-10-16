# ruff: noqa: E501
"""One event model for the Inbox, News and the dashboard feed (insight I13.1).

Every update the app already produces is mapped onto one card: a type (news, earnings, analyst,
valuation, dividend, insider, risk, other), the ticker with its latest close and one-day move,
a "what changed" line, when it happened and whether the owner has read it. Nothing is generated
here: the sources are inbox items, news stories, the what-changed comparison and dividend events
as stored, and the type is a fixed mapping from each source's own classification (`TYPE_OF_*`
below, versioned with `updates_feed`).

Read state: an inbox item is read when its status is no longer `new` (marking it read here sets
that status); everything else is read when its key is in `feed_read`.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from collections import Counter
from collections.abc import Awaitable, Iterable, Mapping, Sequence
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.analytics.versions import version

logger = logging.getLogger(__name__)

UpdateType = Literal[
    "news", "earnings", "analyst", "valuation", "dividend", "insider", "risk", "other"
]
UPDATE_TYPES: tuple[UpdateType, ...] = (
    "news",
    "earnings",
    "analyst",
    "valuation",
    "dividend",
    "insider",
    "risk",
    "other",
)
Source = Literal["inbox", "news", "changes", "dividends"]

# News story event types (config/event_rules.EVENT_TYPES) -> feed type.
TYPE_OF_EVENT: dict[str, UpdateType] = {
    "earnings_release": "earnings",
    "guidance_change": "earnings",
    "analyst_rating": "analyst",
    "dividend_cut": "dividend",
    "dividend_change": "dividend",
    "dividend": "dividend",
    "insider_transaction": "insider",
    "credit_rating": "risk",
    "credit_stress": "risk",
    "regulatory_legal": "risk",
    "delisting": "risk",
    "entity_status": "risk",
    "capital_allocation": "news",
    "m_and_a": "news",
    "management_change": "news",
    "product_business": "news",
    "sector_competitor": "news",
    "index_change": "news",
    "trade_exposure": "risk",
    "patent_status": "news",
    "central_bank": "other",
    "macro_surprise": "other",
}
# Inbox trigger kinds -> feed type.
TYPE_OF_TRIGGER: dict[str, UpdateType] = {
    "breach": "risk",
    "alert": "risk",
    "verdict": "earnings",
    "disclosure": "insider",
    "escalation": "news",
}
# What-changed areas -> feed type.
TYPE_OF_AREA: dict[str, UpdateType] = {
    "fundamentals": "earnings",
    "estimates": "analyst",
    "price": "valuation",
    "ownership": "insider",
    "insiders": "insider",
    "fund_holdings": "other",
    "graph": "news",
    "macro": "other",
    "patents": "news",
}


def update_type(source: Source, label: str | None) -> UpdateType:
    if source == "dividends":
        return "dividend"
    table = {"news": TYPE_OF_EVENT, "inbox": TYPE_OF_TRIGGER, "changes": TYPE_OF_AREA}[source]
    return table.get(label or "", "other" if source != "news" else "news")


class Update(BaseModel):
    key: str = Field(description="Stable id: the read state is stored against it.")
    type: UpdateType
    source: Source
    ticker: str | None
    name: str | None = None
    headline: str
    what_changed: str | None
    occurred_at: dt.datetime
    age_days: int
    price: float | None = Field(default=None, description="Latest stored close of the holding.")
    price_currency: str | None = None
    change_1d: float | None = Field(
        default=None, description="One-day move of that close, fraction."
    )
    severity: int | None = None
    href: str
    read: bool


class TypeCount(BaseModel):
    type: UpdateType
    total: int
    unread: int


class UpdatesFeed(BaseModel):
    items: list[Update]
    counts: list[TypeCount] = Field(
        description="Per type over the whole window, before the type filter."
    )
    unread: int
    window_days: int
    method_version: str
    notes: list[str]


def _key(*parts: object) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def counts(items: Iterable[Update]) -> list[TypeCount]:
    total: Counter[str] = Counter()
    unread: Counter[str] = Counter()
    for u in items:
        total[u.type] += 1
        if not u.read:
            unread[u.type] += 1
    return [TypeCount(type=t, total=total[t], unread=unread[t]) for t in UPDATE_TYPES if total[t]]


def _match_price(
    ticker: str | None, prices: Mapping[str, tuple[float | None, float | None, str | None]]
) -> tuple[float | None, float | None, str | None]:
    """A feed ticker may be the company ticker (AAPL) or the broker's (AAPL_US_EQ)."""
    if not ticker:
        return None, None, None
    if ticker in prices:
        return prices[ticker]
    bare = ticker.split("_")[0].upper()
    for t, v in prices.items():
        if t.split("_")[0].upper() == bare:
            return v
    return None, None, None


def assemble(
    raw: Sequence[Update],
    prices: Mapping[str, tuple[float | None, float | None, str | None]],
    *,
    type_filter: UpdateType | None,
    unread_only: bool,
    limit: int,
) -> tuple[list[Update], list[TypeCount]]:
    """Attach prices, newest first, then filter. Counts are taken before the filters so each
    chip shows its own size."""
    items = []
    for u in sorted(raw, key=lambda x: x.occurred_at, reverse=True):
        price, move, ccy = _match_price(u.ticker, prices)
        items.append(
            u.model_copy(update={"price": price, "change_1d": move, "price_currency": ccy})
        )
    c = counts(items)
    shown = [
        u
        for u in items
        if (type_filter is None or u.type == type_filter) and (not unread_only or not u.read)
    ]
    return shown[:limit], c


def bare_ticker(ticker: str) -> str:
    """The company part of a feed ticker: AAPL for AAPL_US_EQ and for AAPL."""
    return ticker.split("_")[0].upper()


# The feed's ticker filter in SQL, so the per-source caps apply after it (`$2` is the bare ticker).
_TICKER_SQL = "($2::text is null or upper(split_part(ticker, '_', 1)) = $2)"


async def _inbox(
    pool: asyncpg.Pool, since: dt.datetime, now: dt.datetime, bare: str | None = None
) -> list[Update]:
    rows = await pool.fetch(
        f"""select id, trigger_kind, ticker, severity, headline, body, status, created_at from public.inbox_item
           where created_at >= $1 and status <> 'dismissed' and {_TICKER_SQL}
           order by created_at desc limit 400""",  # noqa: S608 - constant fragment
        since,
        bare,
    )
    out = []
    for r in rows:
        body = r["body"]
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except ValueError:
                body = None
        detail = None
        if isinstance(body, dict):
            detail = body.get("summary") or body.get("detail")
        out.append(
            Update(key=f"inbox:{r['id']}", type=update_type("inbox", r["trigger_kind"]), source="inbox",
                   ticker=r["ticker"], headline=r["headline"], what_changed=str(detail)[:300] if detail else None,
                   occurred_at=r["created_at"], age_days=(now - r["created_at"]).days, severity=r["severity"],
                   href=f"/inbox?tab=all&item={r['id']}", read=r["status"] != "new")
        )  # fmt: skip
    return out


async def _news(
    pool: asyncpg.Pool, since: dt.datetime, now: dt.datetime, bare: str | None = None
) -> list[Update]:
    rows = await pool.fetch(
        f"""select c.id, c.ticker, c.event_type, c.severity, c.last_seen, c.source_count, c.status,
                  (select i.headline from public.news_item i where i.cluster_id = c.id order by i.tier, i.published_at limit 1) as headline
           from public.story_cluster c where c.last_seen >= $1 and {_TICKER_SQL.replace("ticker", "c.ticker")}
           order by c.last_seen desc limit 300""",  # noqa: S608 - constant fragment
        since,
        bare,
    )
    out = []
    for r in rows:
        if not r["headline"]:
            continue
        what = f"{r['source_count']} source{'s' if r['source_count'] != 1 else ''}; {str(r['status']).replace('_', ' ')}"
        out.append(
            Update(key=f"news:{r['id']}:{r['last_seen'].date().isoformat()}", type=update_type("news", r["event_type"]),
                   source="news", ticker=r["ticker"], headline=r["headline"], what_changed=what,
                   occurred_at=r["last_seen"], age_days=(now - r["last_seen"]).days, severity=r["severity"],
                   href=f"/news/stories/{r['id']}", read=False)
        )  # fmt: skip
    return out


async def _changes(pool: asyncpg.Pool, now: dt.datetime) -> list[Update]:
    from steward.changes.feed import build_feed

    feed = await build_feed(pool, None)
    if feed is None:
        return []
    at = dt.datetime.combine(feed.compared_with, dt.time(18), tzinfo=dt.UTC)
    out = []
    for c in feed.items[:200]:
        what = c.what
        if c.before is not None or c.after is not None:
            what = f"{c.what}: {c.before or 'none'} → {c.after or 'none'}"
        out.append(
            Update(key=f"change:{_key(c.area, c.kind, c.subject, c.after, feed.compared_with)}",
                   type=update_type("changes", c.area), source="changes",
                   ticker=c.subject if c.area not in ("macro",) else None,
                   headline=f"{c.subject}: {c.what}", what_changed=what, occurred_at=at,
                   age_days=max(0, (now - at).days), href="/insights", read=False)
        )  # fmt: skip
    return out


async def _dividends(pool: asyncpg.Pool, since: dt.datetime, now: dt.datetime) -> list[Update]:
    """Dividend events recorded in the window for the holdings (corporate actions are stored by
    symbol or listing; the dividend service maps them to the broker's tickers)."""
    from steward.dividends.service import _symbol_map

    held = [
        r["ticker"]
        for r in await pool.fetch(
            "select ticker from public.position_snapshot where snapshot_date ="
            " (select max(snapshot_date) from public.position_snapshot)"
        )
    ]
    if not held:
        return []
    by_symbol, by_listing = await _symbol_map(pool, held)
    rows = await pool.fetch(
        """select id, listing_id, symbol, ex_date, payment_date, cash_amount, currency, status, source, known_at
           from public.corporate_action
           where action_type = 'cash_dividend' and status <> 'rejected' and known_at >= $1
             and cash_amount is not null and (symbol = any($2::text[]) or listing_id = any($3::bigint[]))
           order by known_at desc limit 200""",
        since, list(by_symbol), list(by_listing),
    )  # fmt: skip
    out = []
    for r in rows:
        ticker = by_listing.get(r["listing_id"]) or by_symbol.get(r["symbol"])
        if ticker is None:
            continue
        pay = f", paid {r['payment_date'].isoformat()}" if r["payment_date"] else ""
        state = (
            "confirmed" if r["status"] == "confirmed" else f"suggested by {r['source']}, to confirm"
        )
        out.append(
            Update(key=f"dividend:{r['id']}", type="dividend", source="dividends", ticker=ticker,
                   headline=f"{ticker} dividend of {r['cash_amount'].normalize():f} {r['currency'] or ''}".strip(),
                   what_changed=f"ex-date {r['ex_date'].isoformat()}{pay}; {state}",
                   occurred_at=r["known_at"], age_days=(now - r["known_at"]).days,
                   href=f"/companies/{ticker}/dividends", read=False)
        )  # fmt: skip
    return out


async def updates_feed(
    pool: asyncpg.Pool,
    owner_id: Any,
    *,
    days: int = 14,
    type_filter: UpdateType | None = None,
    unread_only: bool = False,
    ticker: str | None = None,
    limit: int = 100,
    now: dt.datetime | None = None,
) -> UpdatesFeed:
    now = now or dt.datetime.now(dt.UTC)
    since = now - dt.timedelta(days=days)
    notes: list[str] = []
    raw: list[Update] = []
    loaders: list[tuple[str, Awaitable[list[Update]]]] = [
        ("inbox", _inbox(pool, since, now, bare_ticker(ticker) if ticker else None)),
        ("news", _news(pool, since, now, bare_ticker(ticker) if ticker else None)),
        ("what changed", _changes(pool, now)),
        ("dividend events", _dividends(pool, since, now)),
    ]
    for name, loader in loaders:
        try:
            raw.extend(await loader)
        except Exception:
            logger.exception("updates feed: %s failed", name)
            notes.append(f"The {name} updates could not be read just now.")
    if ticker:
        bare = bare_ticker(ticker)
        raw = [u for u in raw if u.ticker and bare_ticker(u.ticker) == bare]
    read_keys = {
        r["item_key"]
        for r in await pool.fetch(
            "select item_key from public.feed_read where owner_id = $1 and item_key = any($2::text[])",
            owner_id, [u.key for u in raw if u.source != "inbox"],
        )
    }  # fmt: skip
    raw = [
        u if u.source == "inbox" or u.key not in read_keys else u.model_copy(update={"read": True})
        for u in raw
    ]
    prices: dict[str, tuple[float | None, float | None, str | None]] = {}
    try:
        from steward.portfolio.quotes import holdings_prices

        for h in (await holdings_prices(pool, 1, now)).holdings:
            prices[h.ticker] = (h.last_close, h.day_change_pct, None)
    except Exception:
        logger.exception("updates feed: prices failed")
        notes.append("Prices could not be read; cards show no price.")
    items, c = assemble(raw, prices, type_filter=type_filter, unread_only=unread_only, limit=limit)
    return UpdatesFeed(
        items=items, counts=c, unread=sum(x.unread for x in c), window_days=days,
        method_version=version("updates_feed"), notes=notes,
    )  # fmt: skip


async def mark_read(pool: asyncpg.Pool, owner_id: Any, keys: Sequence[str]) -> int:
    """Mark updates read. Inbox keys set the item's status to read; the rest go to `feed_read`."""
    inbox_ids = [
        int(k.split(":", 1)[1])
        for k in keys
        if k.startswith("inbox:") and k.split(":", 1)[1].isdigit()
    ]
    others = [k for k in keys if not k.startswith("inbox:")]
    n = 0
    if inbox_ids:
        n += len(await pool.fetch(
            "update public.inbox_item set status = 'read' where id = any($1::bigint[]) and status = 'new' returning id",
            inbox_ids,
        ))  # fmt: skip
    if others:
        n += len(await pool.fetch(
            """insert into public.feed_read (owner_id, item_key) select $1, k from unnest($2::text[]) as k
               on conflict (owner_id, item_key) do nothing returning item_key""",
            owner_id, others,
        ))  # fmt: skip
    return n
