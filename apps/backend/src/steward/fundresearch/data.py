"""Database reads for the fund workbench. Everything is point in time: with `as_of`, only rows
fetched by the end of that day are read, so an answer can be reproduced later."""

# ruff: noqa: E501
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import asyncpg
from pydantic import BaseModel, Field

from steward.lookthrough.read import SnapshotIn, hydrate_snapshots

FACT_KEYS = (
    "replication_method",
    "securities_lending",
    "lending_revenue_share",
    "inception_date",
    "domicile",
    "distribution_tax_note",
    "liquidity_note",
    "tracking_index",
    "ucits",
)


class FundListing(BaseModel):
    ticker: str
    name: str | None = None
    exchange: str | None = None
    currency: str | None = None
    price_symbol: str | None = None
    has_prices: bool = False


class Fact(BaseModel):
    key: str
    value: str
    source_url: str
    passage: str | None = None
    as_of: dt.date | None = None
    recorded_at: dt.datetime


class FactIn(BaseModel):
    key: str
    value: str = Field(min_length=1, max_length=500)
    source_url: str = Field(min_length=8, max_length=2000)
    passage: str | None = Field(default=None, max_length=1000)
    as_of: dt.date | None = None


@dataclass
class FundData:
    isin: str
    factsheet: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None  # latest etf_snapshot row (no payload columns)
    snapshot: SnapshotIn | None = None
    listings: list[FundListing] = field(default_factory=list)
    siblings: list[dict[str, Any]] = field(default_factory=list)
    facts: dict[str, Fact] = field(default_factory=dict)

    @property
    def name(self) -> str | None:
        if self.factsheet and self.factsheet.get("fund_name"):
            return str(self.factsheet["fund_name"])
        return self.meta["fund_name"] if self.meta else None


def _row(record: asyncpg.Record | None) -> dict[str, Any] | None:
    """A row as a dict, with jsonb columns (returned as text) parsed."""
    if record is None:
        return None
    out = dict(record)
    for key in ("charges", "evidence"):
        if isinstance(out.get(key), str):
            out[key] = json.loads(out[key])
    return out


def _cutoff(as_of: dt.date | None) -> dt.datetime | None:
    if as_of is None:
        return None
    return dt.datetime.combine(as_of + dt.timedelta(days=1), dt.time.min, tzinfo=dt.UTC)


async def load_fund(
    pool: asyncpg.Pool, isin: str, owner_id: Any, as_of: dt.date | None = None
) -> FundData | None:
    """Latest factsheet, holdings snapshot, listings and owner facts of one fund; `None` when the
    app holds nothing at all for the ISIN."""
    cutoff = _cutoff(as_of)
    meta = await pool.fetchrow(
        """select * from public.etf_snapshot where fund_isin=$1
             and ($2::timestamptz is null or (as_of <= $3 and fetched_at < $2))
           order by as_of desc, fetched_at desc, id desc limit 1""",
        isin,
        cutoff,
        as_of or dt.date.max,
    )
    sheet = await pool.fetchrow(
        """select * from public.fund_factsheet where fund_isin=$1
             and ($2::timestamptz is null or (as_of <= $3 and fetched_at < $2))
           order by as_of desc, fetched_at desc, id desc limit 1""",
        isin,
        cutoff,
        as_of or dt.date.max,
    )
    listings = await pool.fetch(
        """select i.t212_ticker, i.name, i.exchange, i.currency, i.price_symbol,
                  case when i.price_symbol is null then false else exists
                    (select 1 from public.price_bar b where b.symbol = i.price_symbol) end as has_prices
           from public.instrument i where i.isin=$1 order by i.t212_ticker""",
        isin,
    )
    if meta is None and sheet is None and not listings:
        return None
    snapshot = None
    siblings: list[asyncpg.Record] = []
    if meta is not None:
        snapshot = (await hydrate_snapshots(pool, [meta])).get(isin)
        siblings = await pool.fetch(
            """select distinct on (fund_isin) fund_isin, fund_name, as_of from public.etf_snapshot
               where portfolio_id=$1 and provider=$2 and fund_isin<>$3
               order by fund_isin, as_of desc""",
            meta["portfolio_id"],
            meta["provider"],
            isin,
        )
    facts = await load_facts(pool, owner_id, isin)
    return FundData(
        isin=isin,
        factsheet=_row(sheet),
        meta=dict(meta) if meta else None,
        snapshot=snapshot,
        listings=[
            FundListing(
                ticker=r["t212_ticker"],
                name=r["name"],
                exchange=r["exchange"],
                currency=r["currency"],
                price_symbol=r["price_symbol"],
                has_prices=bool(r["has_prices"]),
            )
            for r in listings
        ],
        siblings=[dict(r) for r in siblings],
        facts=facts,
    )


async def load_facts(pool: asyncpg.Pool, owner_id: Any, isin: str) -> dict[str, Fact]:
    """The newest recorded value per key."""
    rows = await pool.fetch(
        """select distinct on (key) key, value, source_url, passage, as_of, created_at
           from public.fund_owner_fact where owner_id=$1 and fund_isin=$2
           order by key, id desc""",
        owner_id,
        isin,
    )
    return {
        r["key"]: Fact(
            key=r["key"],
            value=r["value"],
            source_url=r["source_url"],
            passage=r["passage"],
            as_of=r["as_of"],
            recorded_at=r["created_at"],
        )
        for r in rows
    }


async def load_fact_history(pool: asyncpg.Pool, owner_id: Any, isin: str) -> list[Fact]:
    """Every recorded value, newest first, so an earlier checklist can be explained."""
    rows = await pool.fetch(
        """select key, value, source_url, passage, as_of, created_at from public.fund_owner_fact
           where owner_id=$1 and fund_isin=$2 order by id desc""",
        owner_id,
        isin,
    )
    return [
        Fact(
            key=r["key"],
            value=r["value"],
            source_url=r["source_url"],
            passage=r["passage"],
            as_of=r["as_of"],
            recorded_at=r["created_at"],
        )
        for r in rows
    ]


async def save_fact(pool: asyncpg.Pool, owner_id: Any, isin: str, fact: FactIn) -> Fact:
    row = await pool.fetchrow(
        """insert into public.fund_owner_fact (owner_id, fund_isin, key, value, source_url, passage, as_of)
           values ($1,$2,$3,$4,$5,$6,$7) returning created_at""",
        owner_id,
        isin,
        fact.key,
        fact.value.strip(),
        fact.source_url.strip(),
        fact.passage,
        fact.as_of,
    )
    assert row is not None
    return Fact(
        key=fact.key,
        value=fact.value.strip(),
        source_url=fact.source_url.strip(),
        passage=fact.passage,
        as_of=fact.as_of,
        recorded_at=row["created_at"],
    )


class CatalogRow(BaseModel):
    isin: str
    name: str | None
    provider: str | None
    currency: str | None
    holdings_as_of: dt.date | None
    expense_ratio: Decimal | None
    total_assets: Decimal | None
    total_assets_currency: str | None
    has_holdings: bool
    listing_count: int
    tickers: list[str]


async def list_funds(pool: asyncpg.Pool) -> list[CatalogRow]:
    """Every fund the app holds a factsheet or holdings snapshot for."""
    rows = await pool.fetch(
        """with snaps as (
             select distinct on (fund_isin) fund_isin, fund_name, provider, fund_currency, as_of
             from public.etf_snapshot order by fund_isin, as_of desc, fetched_at desc, id desc),
           sheets as (
             select distinct on (fund_isin) fund_isin, fund_name, provider, fund_currency,
                    expense_ratio, total_assets, total_assets_currency
             from public.fund_factsheet order by fund_isin, as_of desc, fetched_at desc, id desc),
           ids as (select fund_isin from snaps union select fund_isin from sheets)
           select ids.fund_isin,
                  coalesce(sh.fund_name, sn.fund_name) as name,
                  coalesce(sh.provider, sn.provider) as provider,
                  coalesce(sh.fund_currency, sn.fund_currency) as currency,
                  sn.as_of as holdings_as_of, sh.expense_ratio, sh.total_assets, sh.total_assets_currency,
                  sn.fund_isin is not null as has_holdings,
                  coalesce((select array_agg(i.t212_ticker order by i.t212_ticker) from public.instrument i where i.isin = ids.fund_isin), '{}') as tickers
           from ids left join snaps sn using (fund_isin) left join sheets sh using (fund_isin)
           order by name nulls last, ids.fund_isin""",
    )
    return [
        CatalogRow(
            isin=r["fund_isin"],
            name=r["name"],
            provider=r["provider"],
            currency=r["currency"],
            holdings_as_of=r["holdings_as_of"],
            expense_ratio=r["expense_ratio"],
            total_assets=r["total_assets"],
            total_assets_currency=r["total_assets_currency"],
            has_holdings=bool(r["has_holdings"]),
            listing_count=len(r["tickers"]),
            tickers=list(r["tickers"]),
        )
        for r in rows
    ]
