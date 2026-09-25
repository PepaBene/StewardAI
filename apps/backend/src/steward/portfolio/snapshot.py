"""Turning one Trading212 read (account summary + positions) into a day's snapshot.

Pure: no I/O. Every base-currency figure is quantity x price x reference rate, so it can
be recomputed by hand from the stored columns.
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from zoneinfo import ZoneInfo

from steward.config.strategy import StrategyConfig
from steward.fx.convert import FxSourceName, FxTable
from steward.portfolio.drift import weight_denominator
from steward.trading212.models import AccountSummary, Position

# The day a snapshot belongs to is the owner's calendar day.
SNAPSHOT_TIMEZONE = ZoneInfo("Europe/Prague")


def snapshot_date_for(captured_at: dt.datetime) -> dt.date:
    return captured_at.astimezone(SNAPSHOT_TIMEZONE).date()


@dataclass(frozen=True)
class PositionRow:
    ticker: str
    name: str
    isin: str | None
    quantity: Decimal
    avg_price: Decimal
    current_price: Decimal
    instrument_currency: str
    value: Decimal
    account_currency: str
    value_account: Decimal
    cost_account: Decimal
    ppl: Decimal
    fx_impact_account: Decimal | None
    fx_rate: Decimal
    fx_rate_date: dt.date | None
    value_base: Decimal
    weight: Decimal | None


@dataclass(frozen=True)
class AccountRow:
    t212_account_id: int
    account_currency: str
    total_value: Decimal
    cash_available: Decimal
    cash_in_pies: Decimal
    cash_reserved: Decimal
    invested_value: Decimal
    invested_cost: Decimal
    unrealized_ppl: Decimal
    realized_ppl: Decimal
    base_currency: str
    fx_source: FxSourceName
    fx_rate: Decimal
    fx_rate_date: dt.date | None
    cash_base: Decimal
    invested_base: Decimal
    total_base: Decimal
    weight_basis: str


@dataclass(frozen=True)
class Snapshot:
    snapshot_date: dt.date
    captured_at: dt.datetime
    account: AccountRow
    positions: list[PositionRow]


def build_snapshot(
    summary: AccountSummary,
    positions: list[Position],
    fx: FxTable,
    strategy: StrategyConfig,
    captured_at: dt.datetime,
) -> Snapshot:
    """Raises MissingRateError if any currency involved has no reference rate."""
    base = strategy.base_currency
    rows: list[tuple[Position, Decimal, Decimal, dt.date | None, Decimal]] = []
    for p in positions:
        value = p.quantity * p.current_price
        conversion = fx.conversion(p.instrument.currency, base)
        rows.append((p, value, conversion.rate, conversion.rate_date, value * conversion.rate))

    cash_account = summary.cash.available_to_trade + summary.cash.in_pies
    cash_account += summary.cash.reserved_for_orders
    cash_conversion = fx.conversion(summary.currency, base)
    cash_base = cash_account * cash_conversion.rate
    invested_base = sum((r[4] for r in rows), Decimal(0))
    denominator = weight_denominator(invested_base, cash_base, strategy.weight_basis)

    position_rows = [
        PositionRow(
            ticker=p.instrument.ticker,
            name=p.instrument.name,
            isin=p.instrument.isin,
            quantity=p.quantity,
            avg_price=p.average_price_paid,
            current_price=p.current_price,
            instrument_currency=p.instrument.currency,
            value=value,
            account_currency=p.wallet_impact.currency,
            value_account=p.wallet_impact.current_value,
            cost_account=p.wallet_impact.total_cost,
            ppl=p.wallet_impact.unrealized_profit_loss,
            fx_impact_account=p.wallet_impact.fx_impact,
            fx_rate=rate,
            fx_rate_date=rate_date,
            value_base=value_base,
            weight=value_base / denominator if denominator > 0 else None,
        )
        for p, value, rate, rate_date, value_base in rows
    ]
    account = AccountRow(
        t212_account_id=summary.id,
        account_currency=summary.currency,
        total_value=summary.total_value,
        cash_available=summary.cash.available_to_trade,
        cash_in_pies=summary.cash.in_pies,
        cash_reserved=summary.cash.reserved_for_orders,
        invested_value=summary.investments.current_value,
        invested_cost=summary.investments.total_cost,
        unrealized_ppl=summary.investments.unrealized_profit_loss,
        realized_ppl=summary.investments.realized_profit_loss,
        base_currency=base,
        fx_source=fx.source,
        fx_rate=cash_conversion.rate,
        fx_rate_date=cash_conversion.rate_date,
        cash_base=cash_base,
        invested_base=invested_base,
        total_base=invested_base + cash_base,
        weight_basis=strategy.weight_basis,
    )
    return Snapshot(
        snapshot_date=snapshot_date_for(captured_at),
        captured_at=captured_at,
        account=account,
        positions=position_rows,
    )
