"""Daily portfolio history for the days before the first Trading212 snapshot.

The app synced for the first time on 2026-09-27, but the ledger reaches back to January, so Risk
and Performance had too few days. Quantities and cash follow from the ledger alone, and they
reproduce the first real snapshot exactly; each day is valued at the stored close of the
holding's chosen series and the ECB cross rate of the day. The rows go to their own tables
(`*_reconstructed`) and are served through the `account_history` / `position_history` views, which
use them only before the first real snapshot. A holding without a stored series is valued at its
average cost and says so (`price_basis = 'cost'`).
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from zoneinfo import ZoneInfo

import asyncpg

from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.store import load_active
from steward.fx.history import FxHistory, load_fx_history
from steward.jobs.runner import JobContext, JobFailed
from steward.reference.price_series import choose_series, holding_symbols

PRAGUE = ZoneInfo("Europe/Prague")
ZERO = Decimal(0)
CARRY_DAYS = 6  # a close this old still prices the day (holidays, a late listing)
QUANTITY_TOLERANCE = Decimal("0.000001")
CASH_TOLERANCE = Decimal("0.01")
# The only ledger kinds that exist before the first snapshot; anything else is not reconstructed.
SUPPORTED = {("order", "BUY"), ("cash", "DEPOSIT"), ("dividend", "DIVIDEND")}


@dataclass(frozen=True)
class Tx:
    day: dt.date
    type: str
    subtype: str | None
    ticker: str | None
    quantity: Decimal
    price: Decimal
    amount: Decimal


@dataclass
class Lot:
    quantity: Decimal = ZERO
    cost_instrument: Decimal = ZERO  # quantity x price in the instrument currency
    cost_account: Decimal = ZERO  # what the buys took out of the account

    @property
    def avg_price(self) -> Decimal:
        return self.cost_instrument / self.quantity if self.quantity else ZERO


@dataclass(frozen=True)
class PositionRow:
    day: dt.date
    ticker: str
    quantity: Decimal
    avg_price: Decimal
    price: Decimal
    currency: str
    fx_rate: Decimal
    value_base: Decimal
    price_basis: str


@dataclass(frozen=True)
class AccountRow:
    day: dt.date
    cash: Decimal
    invested: Decimal
    total: Decimal
    cost: Decimal


@dataclass
class Rebuilt:
    accounts: list[AccountRow] = field(default_factory=list)
    positions: list[PositionRow] = field(default_factory=list)
    skipped: dict[dt.date, str] = field(default_factory=dict)


def _carried(closes: Mapping[dt.date, Decimal], day: dt.date) -> Decimal | None:
    for back in range(CARRY_DAYS + 1):
        close = closes.get(day - dt.timedelta(days=back))
        if close is not None:
            return close
    return None


def weekdays(start: dt.date, end: dt.date) -> list[dt.date]:
    days = [start + dt.timedelta(days=n) for n in range((end - start).days + 1)]
    return [d for d in days if d.weekday() < 5]


def rebuild(
    txs: Sequence[Tx],
    closes: Mapping[str, Mapping[dt.date, Decimal]],
    close_currency: Mapping[str, str],
    instrument_currency: Mapping[str, str],
    fx: FxHistory,
    account_currency: str,
    days: Sequence[dt.date],
) -> Rebuilt:
    """Account and position rows for `days`, from the ledger and the stored closes. A day whose
    holding cannot be converted to the account currency is skipped, never guessed."""
    unsupported = {(t.type, t.subtype) for t in txs} - SUPPORTED
    if unsupported:
        raise ValueError(
            f"ledger kinds that are not reconstructed: {sorted(map(str, unsupported))}"
        )
    by_day: dict[dt.date, list[Tx]] = defaultdict(list)
    for t in sorted(txs, key=lambda t: t.day):
        by_day[t.day].append(t)
    lots: dict[str, Lot] = defaultdict(Lot)
    cash = ZERO
    out = Rebuilt()
    applied = sorted(by_day)
    cursor = 0
    for day in days:
        while cursor < len(applied) and applied[cursor] <= day:
            for t in by_day[applied[cursor]]:
                if t.type == "cash":
                    cash += t.amount
                elif t.type == "dividend":
                    cash += t.amount
                elif t.ticker:
                    lot = lots[t.ticker]
                    lot.quantity += t.quantity
                    lot.cost_instrument += t.quantity * t.price
                    lot.cost_account += t.amount
                    cash -= t.amount
            cursor += 1
        rows: list[PositionRow] = []
        problem: str | None = None
        for ticker, lot in sorted(lots.items()):
            if lot.quantity <= 0:
                continue
            currency = instrument_currency[ticker]
            rate = fx.conversion(currency, account_currency, day)
            if rate is None:
                problem = f"no {currency}/{account_currency} rate for {ticker}"
                break
            price, basis = lot.avg_price, "cost"
            raw = _carried(closes.get(ticker, {}), day)
            if raw is not None:
                bar_rate = fx.conversion(close_currency.get(ticker, currency), currency, day)
                if bar_rate is not None:
                    price, basis = raw * bar_rate, "close"
            rows.append(
                PositionRow(
                    day,
                    ticker,
                    lot.quantity,
                    lot.avg_price,
                    price,
                    currency,
                    rate,
                    lot.quantity * price * rate,
                    basis,
                )
            )
        if problem:
            out.skipped[day] = problem
            continue
        invested = sum((r.value_base for r in rows), ZERO)
        cost = sum((lots[r.ticker].cost_account for r in rows), ZERO)
        out.positions.extend(rows)
        out.accounts.append(AccountRow(day, cash, invested, cash + invested, cost))
    return out


def prague_day(moment: dt.datetime) -> dt.date:
    return moment.astimezone(PRAGUE).date()


async def _transactions(pool: asyncpg.Pool, before: dt.date) -> list[Tx]:
    rows = await pool.fetch(
        "select type, subtype, ticker, quantity, price, amount, occurred_at"
        " from public.transaction order by occurred_at, id"
    )
    return [
        Tx(
            prague_day(r["occurred_at"]),
            r["type"],
            r["subtype"],
            r["ticker"],
            r["quantity"] or ZERO,
            r["price"] or ZERO,
            r["amount"] or ZERO,
        )
        for r in rows
        if prague_day(r["occurred_at"]) < before
    ]


async def _closes(
    pool: asyncpg.Pool, tickers: list[str]
) -> tuple[dict[str, dict[dt.date, Decimal]], dict[str, str]]:
    stored = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    symbols = await holding_symbols(
        pool, tickers, stored.body if stored else HoldingProfilesConfig()
    )
    chosen = await choose_series(pool, symbols)
    closes: dict[str, dict[dt.date, Decimal]] = {}
    currency: dict[str, str] = {}
    for ticker, ref in chosen.items():
        if ref is None:
            continue
        rows = await pool.fetch(
            "select date, close, currency from public.price_bar where symbol=$1 and source=$2"
            " and bar_type='adjusted' and access_state='available' and close is not null",
            ref.symbol,
            ref.source,
        )
        closes[ticker] = {r["date"]: Decimal(r["close"]) for r in rows}
        stated = [r["currency"] for r in rows if r["currency"]]
        if stated:
            currency[ticker] = stated[-1]
    return closes, currency


async def rebuild_portfolio_history(ctx: JobContext) -> None:
    """Rebuild the days before the first real snapshot, checked against that snapshot."""
    pool = ctx.pool
    async with ctx.step("portfolio_history") as step:
        first = await pool.fetchrow(
            "select snapshot_date, account_currency, base_currency, cash_base, total_base"
            " from public.account_snapshot order by snapshot_date limit 1"
        )
        if first is None:
            step.detail["skipped"] = "no real snapshot yet"
            return
        if first["account_currency"] != first["base_currency"]:
            step.detail["skipped"] = "the base currency differs from the account currency"
            return
        account_currency: str = first["account_currency"]
        first_day: dt.date = first["snapshot_date"]
        txs = await _transactions(pool, first_day + dt.timedelta(days=1))
        if not txs:
            step.detail["skipped"] = "no transactions"
            return
        tickers = sorted({t.ticker for t in txs if t.ticker})
        currencies = {
            r["ticker"]: r["currency"]
            for r in await pool.fetch(
                "select i.t212_ticker as ticker, coalesce(p.instrument_currency, i.currency)"
                " as currency from public.instrument i left join lateral ("
                "  select instrument_currency from public.position_snapshot x"
                "  where x.ticker = i.t212_ticker order by snapshot_date desc limit 1) p on true"
                " where i.t212_ticker = any($1::text[])",
                tickers,
            )
        }
        missing = [t for t in tickers if not currencies.get(t)]
        if missing:
            raise JobFailed(f"no trading currency known for {missing}")
        closes, close_currency = await _closes(pool, tickers)
        wanted = {*currencies.values(), *close_currency.values(), account_currency}
        start = min(t.day for t in txs)
        fx = await load_fx_history(pool, "ecb", wanted, start, first_day)
        # The first real day is built even on a weekend: it is the check against the broker.
        days = sorted({*weekdays(start, first_day), first_day})
        try:
            built = rebuild(txs, closes, close_currency, currencies, fx, account_currency, days)
        except ValueError as exc:
            raise JobFailed(str(exc)) from exc

        # The first real day, rebuilt the same way, must agree with what Trading212 reported.
        real_positions = {
            r["ticker"]: r["quantity"]
            for r in await pool.fetch(
                "select ticker, quantity from public.position_snapshot where snapshot_date=$1",
                first_day,
            )
        }
        check_row = next((a for a in built.accounts if a.day == first_day), None)
        if check_row is None:
            why = built.skipped.get(first_day)
            raise JobFailed(f"the first real day {first_day} could not be rebuilt: {why}")
        mine = {p.ticker: p.quantity for p in built.positions if p.day == first_day}
        quantity_gap = max(
            (
                abs(mine.get(t, ZERO) - real_positions.get(t, ZERO))
                for t in {*mine, *real_positions}
            ),
            default=ZERO,
        )
        cash_gap = abs(check_row.cash - first["cash_base"])
        total_gap = (
            (check_row.total - first["total_base"]) / first["total_base"]
            if first["total_base"]
            else ZERO
        )
        step.detail["check"] = {
            "day": str(first_day),
            "max_quantity_gap": str(quantity_gap),
            "cash_gap": str(cash_gap),
            "total_gap_fraction": f"{total_gap:.4f}",
        }
        if quantity_gap > QUANTITY_TOLERANCE or cash_gap > CASH_TOLERANCE:
            raise JobFailed("the ledger does not reproduce the first real snapshot; nothing stored")

        accounts = [a for a in built.accounts if a.day < first_day]
        positions = [p for p in built.positions if p.day < first_day]
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("delete from public.position_snapshot_reconstructed")
            await conn.execute("delete from public.account_snapshot_reconstructed")
            await conn.executemany(
                "insert into public.account_snapshot_reconstructed (snapshot_date,"
                " account_currency, base_currency, fx_source, fx_rate, cash_base, invested_base,"
                " total_base, invested_cost, unrealized_ppl)"
                " values ($1,$2,$2,'ecb',1,$3,$4,$5,$6,$7)",
                [
                    (
                        a.day,
                        account_currency,
                        a.cash,
                        a.invested,
                        a.total,
                        a.cost,
                        a.invested - a.cost,
                    )
                    for a in accounts
                ],
            )
            await conn.executemany(
                "insert into public.position_snapshot_reconstructed (snapshot_date, ticker,"
                " quantity, avg_price, current_price, instrument_currency, fx_rate, value_base,"
                " base_currency, price_basis) values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
                [
                    (
                        p.day,
                        p.ticker,
                        p.quantity,
                        p.avg_price,
                        p.price,
                        p.currency,
                        p.fx_rate,
                        p.value_base,
                        account_currency,
                        p.price_basis,
                    )
                    for p in positions
                ],
            )
        step.detail.update(
            days=len(accounts),
            positions=len(positions),
            first=str(accounts[0].day) if accounts else None,
            last=str(accounts[-1].day) if accounts else None,
            skipped={str(d): why for d, why in built.skipped.items() if d < first_day},
            valued_at_cost=sorted({p.ticker for p in positions if p.price_basis == "cost"}),
        )
