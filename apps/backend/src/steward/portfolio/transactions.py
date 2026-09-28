"""Read model for the transactions view: orders, dividends and cash movements, filtered by
type, subtype, ticker and period, with totals over the whole filtered set (not just the page).

Paging is keyset on (occurred_at, id), newest first, so every row appears exactly once
across pages even when rows share a timestamp.

Fees: every order row carries its Trading212 charges (public.transaction_fee), and the page
carries `fees`, the filtered set's fees converted to the base currency and split into FX
conversion, taxes & levies and account fees (cash rows of type FEE), plus how much of the
fill fees Trading212 had already netted into the orders' amounts (so a cash-flow view can
show fees without counting them twice) and how many fills have had their fees read at all.
"""

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.config.store import load_active
from steward.config.strategy import StrategyConfig
from steward.fx.convert import FxSourceName
from steward.fx.history import load_fx_history
from steward.portfolio.fees import FeeCategory

TransactionType = Literal["order", "dividend", "cash"]


class TransactionFeeOut(BaseModel):
    name: str = Field(description="Trading212's name, e.g. CURRENCY_CONVERSION_FEE.")
    category: FeeCategory = Field(description="fx: FX conversion; tax: taxes & levies.")
    amount: float = Field(description="abs(quantity) as Trading212 reported it, in `currency`.")
    currency: str | None


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
    fees: list[TransactionFeeOut] = Field(
        default_factory=list, description="Orders: the fill's fees and taxes (walletImpact.taxes)."
    )
    fees_captured: bool | None = Field(
        default=None,
        description="Orders: whether the sync has read this fill's fees yet (false: stored "
        "before fee capture; the one-time order-history re-read fills it in). Null for "
        "dividends and cash.",
    )
    fee_basis: Literal["excluded", "included", "unknown"] | None = Field(
        default=None,
        description="Orders with fees: whether `amount` (walletImpact.netValue) already "
        "includes them; unknown is read as excluded, Trading212's documented behaviour.",
    )


class FeeNameTotal(BaseModel):
    name: str
    category: Literal["fx", "tax", "account"]
    count: int
    amount: float = Field(description="In the fee summary's `currency`.")


class FeeTotals(BaseModel):
    currency: str | None = Field(
        description="Base currency the amounts are converted to; null before the first sync."
    )
    fx_source: Literal["cnb", "ecb"] | None
    fx_conversion: float = Field(description="CURRENCY_CONVERSION_FEE on fills.")
    taxes_levies: float = Field(description="Every other fill charge (stamp duty, FTT, ...).")
    account_fees: float = Field(description="Cash transactions of type FEE (abs(amount)).")
    total: float
    included_in_buys: float = Field(
        description="Part of the fill fees that buys' amounts already include (fee_basis "
        "'included'): subtract it from buys before showing fees separately."
    )
    included_in_sales: float = Field(
        description="Part of the fill fees already deducted from sales' amounts: add it back "
        "to sales before showing fees separately."
    )
    by_name: list[FeeNameTotal]
    unconverted: list[str] = Field(
        description="Currencies of fees left out of the sums for lack of a stored FX rate."
    )
    fills: int = Field(description="Order rows matching the filters.")
    fills_captured: int = Field(description="Of those, fills whose fees the sync has read.")
    fills_with_fees: int
    status: Literal["captured", "partial", "not_captured", "no_fills"] = Field(
        description="not_captured: no fill in the filtered set has had its fees read yet "
        "(stored before fee capture; the next sync re-reads the order history)."
    )


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
    fees: FeeTotals = Field(
        description="Fees and taxes over every matching row, converted to the base currency."
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
        " t.currency, t.occurred_at, t.fees_captured_at, t.fee_basis from public.transaction t"
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
    fee_rows = await pool.fetch(
        "select f.transaction_id, f.name, f.category, f.amount, f.currency, t.fees_captured_at,"
        " t.fee_basis from public.transaction t join public.transaction_fee f"
        " on f.transaction_id = t.id where t.id = any($1::text[]) order by f.seq",
        [r["id"] for r in rows if r["type"] == "order"],
    )
    row_fees: dict[str, list[TransactionFeeOut]] = defaultdict(list)
    for fr in fee_rows:
        row_fees[fr["transaction_id"]].append(
            TransactionFeeOut(
                name=fr["name"],
                category=fr["category"],
                amount=float(fr["amount"]),
                currency=fr["currency"],
            )
        )
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
                fees=row_fees.get(r["id"], []),
                fees_captured=(r["fees_captured_at"] is not None) if r["type"] == "order" else None,
                fee_basis=r["fee_basis"],
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
        fees=await fee_totals(pool, where, args),
    )


async def _base_and_source(pool: asyncpg.Pool) -> tuple[str | None, FxSourceName | None]:
    """The active strategy's base currency and FX source, else the latest snapshot's."""
    stored = await load_active(pool, "strategy", StrategyConfig)
    latest = await pool.fetchrow(
        "select base_currency, fx_source from public.account_snapshot"
        " order by snapshot_date desc limit 1"
    )
    base = stored.body.base_currency if stored else (latest["base_currency"] if latest else None)
    source: FxSourceName | None = (stored.body.fx_source if stored else None) or (
        latest["fx_source"] if latest else None
    )
    return base, source or ("cnb" if base == "CZK" else "ecb")


def _day(stamp: dt.datetime) -> dt.date:
    return stamp.astimezone(dt.UTC).date()


async def fee_totals(pool: asyncpg.Pool, where: str, args: list[Any]) -> FeeTotals:
    """Fees over the filtered rows, each converted at its own day's reference rate."""
    fills = await pool.fetchrow(
        "select count(*) as fills, count(t.fees_captured_at) as captured,"  # noqa: S608
        " count(*) filter (where exists (select 1 from public.transaction_fee f"
        " where f.transaction_id = t.id and f.amount > 0)) as with_fees"
        f" from public.transaction t where {where} and t.type = 'order'",
        *args,
    )
    charges = await pool.fetch(
        "select f.name, f.category, f.currency, t.subtype, t.fee_basis, t.currency as"  # noqa: S608
        " order_currency, t.occurred_at, f.amount from public.transaction_fee f"
        " join public.transaction t on t.id = f.transaction_id"
        f" where {where} and t.type = 'order' and f.amount <> 0",
        *args,
    )
    account = await pool.fetch(
        "select t.currency, t.occurred_at, abs(t.amount) as amount"  # noqa: S608
        f" from public.transaction t where {where} and t.type = 'cash' and t.subtype = 'FEE'",
        *args,
    )
    base, source = await _base_and_source(pool)
    n_fills, n_captured = int(fills["fills"]), int(fills["captured"])
    status: Literal["captured", "partial", "not_captured", "no_fills"] = (
        "no_fills"
        if n_fills == 0
        else "not_captured"
        if n_captured == 0
        else "partial"
        if n_captured < n_fills
        else "captured"
    )
    sums: dict[str, Decimal] = defaultdict(Decimal)
    names: dict[tuple[str, str], list[Any]] = {}
    unconverted: set[str] = set()
    items = [(r["currency"] or r["order_currency"], r["occurred_at"], r["amount"]) for r in charges]
    items += [(r["currency"], r["occurred_at"], r["amount"]) for r in account]
    if base is not None and source is not None and items:
        stamps = [_day(stamp) for _, stamp, _ in items]
        fx = await load_fx_history(
            pool, source, {c for c, _, _ in items if c}, min(stamps), max(stamps)
        )
    else:
        fx = None

    def to_base(currency: str | None, stamp: dt.datetime, amount: Decimal) -> Decimal | None:
        if fx is None or base is None or currency is None:
            if currency is not None:
                unconverted.add(currency)
            return None
        rate = fx.conversion(currency, base, _day(stamp))
        if rate is None:
            unconverted.add(currency)
            return None
        return amount * rate

    for r in charges:
        value = to_base(r["currency"] or r["order_currency"], r["occurred_at"], r["amount"])
        if value is None:
            continue
        sums[r["category"]] += value
        if r["fee_basis"] == "included":
            sums["included_" + ("sales" if r["subtype"] == "SELL" else "buys")] += value
        entry = names.setdefault((r["category"], r["name"]), [0, Decimal(0)])
        entry[0] += 1
        entry[1] += value
    for r in account:
        value = to_base(r["currency"], r["occurred_at"], r["amount"])
        if value is None:
            continue
        sums["account"] += value
        entry = names.setdefault(("account", "FEE"), [0, Decimal(0)])
        entry[0] += 1
        entry[1] += value

    total = sums["fx"] + sums["tax"] + sums["account"]
    return FeeTotals(
        currency=base,
        fx_source=source if base else None,
        fx_conversion=float(sums["fx"]),
        taxes_levies=float(sums["tax"]),
        account_fees=float(sums["account"]),
        total=float(total),
        included_in_buys=float(sums["included_buys"]),
        included_in_sales=float(sums["included_sales"]),
        by_name=sorted(
            (
                FeeNameTotal(name=name, category=cat, count=n, amount=float(amount))
                for (cat, name), (n, amount) in names.items()
            ),
            key=lambda x: -x.amount,
        ),
        unconverted=sorted(unconverted),
        fills=n_fills,
        fills_captured=n_captured,
        fills_with_fees=int(fills["with_fees"]),
        status=status,
    )
