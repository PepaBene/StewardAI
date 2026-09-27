"""Read model for the transactions view: orders, dividends and cash movements, filtered by
type, subtype, ticker and period, with totals over the whole filtered set (not just the page).

Paging is keyset on (occurred_at, id), newest first, so every row appears exactly once
across pages even when rows share a timestamp.
"""

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

TransactionType = Literal["order", "dividend", "cash"]


class TransactionOut(BaseModel):
    id: str
    type: TransactionType
    subtype: str | None
    ticker: str | None
    name: str | None
    quantity: float | None
    price: float | None
    amount: float = Field(description="In `currency`, as Trading212 reports it.")
    currency: str | None
    occurred_at: dt.datetime


class TotalByCurrency(BaseModel):
    type: TransactionType
    subtype: str | None
    currency: str | None
    count: int
    amount: float = Field(description="Sum of `amount` over the filtered rows.")


class TransactionsPage(BaseModel):
    items: list[TransactionOut]
    total_count: int = Field(description="Rows matching the filters, across all pages.")
    totals: list[TotalByCurrency] = Field(
        description="Sums per type, subtype and currency over every matching row."
    )
    dividends: list[TotalByCurrency] = Field(
        description="Dividend sums per currency over every matching row."
    )
    next_cursor: str | None = Field(
        description="Pass as `cursor` for the next (older) page; null on the last page."
    )


@dataclass(frozen=True)
class TransactionFilters:
    types: list[str] = field(default_factory=list)
    ticker: str | None = None
    start: dt.datetime | None = None
    end: dt.datetime | None = None
    cursor: tuple[dt.datetime, str] | None = None
    limit: int = 100
    # Trading212's subtypes as stored (BUY/SELL for orders, DEPOSIT, FEE, ... for cash).
    subtypes: list[str] = field(default_factory=list)


def encode_cursor(occurred_at: dt.datetime, row_id: str) -> str:
    return f"{occurred_at.isoformat()}|{row_id}"


def decode_cursor(cursor: str) -> tuple[dt.datetime, str]:
    stamp, _, row_id = cursor.partition("|")
    if not row_id:
        raise ValueError("malformed cursor")
    return dt.datetime.fromisoformat(stamp), row_id


def _where(f: TransactionFilters) -> tuple[str, list[Any]]:
    clauses = [
        "(cardinality($1::text[]) = 0 or t.type = any($1))",
        "($2::text is null or t.ticker = $2)",
        "($3::timestamptz is null or t.occurred_at >= $3)",
        "($4::timestamptz is null or t.occurred_at < $4)",
        "(cardinality($5::text[]) = 0 or t.subtype = any($5))",
    ]
    return " and ".join(clauses), [f.types, f.ticker, f.start, f.end, f.subtypes]


def _float(value: Any) -> float | None:
    return None if value is None else float(value)


async def list_transactions(pool: asyncpg.Pool, f: TransactionFilters) -> TransactionsPage:
    where, args = _where(f)
    cursor_at, cursor_id = f.cursor if f.cursor else (None, None)
    rows = await pool.fetch(
        "select t.id, t.type, t.subtype, t.ticker, i.name, t.quantity, t.price, t.amount,"  # noqa: S608
        " t.currency, t.occurred_at from public.transaction t"
        " left join public.instrument i on i.t212_ticker = t.ticker"
        f" where {where}"
        " and ($6::timestamptz is null or (t.occurred_at, t.id) < ($6, $7::text))"
        " order by t.occurred_at desc, t.id desc limit $8",
        *args,
        cursor_at,
        cursor_id,
        f.limit + 1,
    )
    more = len(rows) > f.limit
    rows = rows[: f.limit]
    totals = await pool.fetch(
        "select t.type, t.subtype, t.currency, count(*) as n, sum(t.amount) as amount"  # noqa: S608
        f" from public.transaction t where {where}"
        " group by t.type, t.subtype, t.currency order by t.type, t.subtype, t.currency",
        *args,
    )
    dividends = await pool.fetch(
        "select t.currency, count(*) as n, sum(t.amount) as amount"  # noqa: S608
        f" from public.transaction t where {where} and t.type = 'dividend'"
        " group by t.currency order by t.currency",
        *args,
    )
    return TransactionsPage(
        items=[
            TransactionOut(
                id=r["id"],
                type=r["type"],
                subtype=r["subtype"],
                ticker=r["ticker"],
                name=r["name"],
                quantity=_float(r["quantity"]),
                price=_float(r["price"]),
                amount=float(r["amount"]),
                currency=r["currency"],
                occurred_at=r["occurred_at"],
            )
            for r in rows
        ],
        total_count=sum(int(t["n"]) for t in totals),
        totals=[
            TotalByCurrency(
                type=t["type"],
                subtype=t["subtype"],
                currency=t["currency"],
                count=t["n"],
                amount=float(t["amount"]),
            )
            for t in totals
        ],
        dividends=[
            TotalByCurrency(
                type="dividend",
                subtype=None,
                currency=d["currency"],
                count=d["n"],
                amount=float(d["amount"]),
            )
            for d in dividends
        ],
        next_cursor=encode_cursor(rows[-1]["occurred_at"], rows[-1]["id"]) if more else None,
    )
