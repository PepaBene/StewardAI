"""Shared dividend records. Pure: no I/O.

A *declared event* is a `corporate_action` row of type `cash_dividend` (per share, in the
currency the provider or the owner stated). A *received payment* is a Trading212 dividend
transaction (the amount credited, net of withholding, in the account currency). An event's
status follows the corporate-action workflow: `suggested` events are proposals and never enter a
total; `confirmed` events do; `rejected` events are ignored.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

ZERO = Decimal(0)
METHOD = "dividend_income"
SPECIAL_WORDS = ("special", "extra", "bonus", "supplement", "one-off", "one off")


@dataclass(frozen=True)
class DeclaredEvent:
    id: int
    ticker: str
    symbol: str
    ex_date: dt.date
    payment_date: dt.date | None
    record_date: dt.date | None
    amount: Decimal  # per share, in `currency`
    currency: str | None
    source: str
    source_url: str
    status: str  # suggested | confirmed | rejected
    known_at: dt.datetime | None = None
    flagged_special: bool = False

    @property
    def pay_or_ex(self) -> dt.date:
        return self.payment_date or self.ex_date


@dataclass(frozen=True)
class ReceivedPayment:
    id: str
    ticker: str
    paid_on: dt.date
    amount: Decimal  # credited, account currency (net of withholding)
    currency: str | None  # account currency
    quantity: Decimal | None
    gross_per_share: Decimal | None  # as Trading212 reports it (instrument currency)


@dataclass(frozen=True)
class Holding:
    ticker: str
    name: str | None
    quantity: Decimal
    price: Decimal | None  # instrument currency, as quoted (GBX possible)
    instrument_currency: str | None
    value_account: Decimal | None
    cost_account: Decimal | None
    account_currency: str | None
    listing_currency: str | None = None


@dataclass
class PaymentPoint:
    """One past regular or special payment per share, from a confirmed event or a receipt."""

    pay_date: dt.date
    ex_date: dt.date | None
    per_share: Decimal
    currency: str | None
    origin: str  # "event" | "receipt" | "single_source"
    special: bool = False
    ref: str = ""


@dataclass
class ScheduleRow:
    ticker: str
    kind: str  # "declared" | "estimated" | "unconfirmed"
    ex_date: dt.date | None
    pay_date: dt.date
    per_share: Decimal
    currency: str | None
    quantity: Decimal
    amount: Decimal | None  # gross, account currency
    amount_net: Decimal | None  # after the holding's observed withholding share, when known
    fx_rate: Decimal | None
    fx_date: dt.date | None
    source: str
    note: str | None = None
    event_id: int | None = None
    ex_date_estimated: bool = False


@dataclass
class HoldingSchedule:
    ticker: str
    frequency: str  # monthly | quarterly | semi_annual | annual | irregular | none | unknown
    payments_seen: int
    rows: list[ScheduleRow] = field(default_factory=list)
    specials: list[PaymentPoint] = field(default_factory=list)
    status: str = "complete"  # complete | partial | unavailable
    reason: str | None = None
    withholding_share: Decimal | None = None
    last_amount: Decimal | None = None
    growth_used: Decimal | None = None
