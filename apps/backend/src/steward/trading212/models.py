"""Response models for the Trading212 Public API (v0).

The API is in beta, so every response is validated: a changed or missing field fails
loudly at the boundary instead of propagating bad data. Unknown extra fields are
ignored so additive API changes don't break the sync. Money uses Decimal, parsed
straight from the JSON text so no float rounding happens on the way in.
"""

import datetime as dt
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, PrivateAttr
from pydantic.alias_generators import to_camel


class T212Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")

    # The response item exactly as Trading212 sent it (numbers as Decimal), when the client
    # parsed this model from a response. Stored as `transaction.raw` so fields the model
    # does not read (or that Trading212 adds later) are never lost.
    _source: dict[str, Any] | None = PrivateAttr(default=None)

    @property
    def source_payload(self) -> dict[str, Any] | None:
        return self._source

    def attach_source(self, payload: object) -> None:
        self._source = payload if isinstance(payload, dict) else None


class AccountCash(T212Model):
    available_to_trade: Decimal
    in_pies: Decimal
    reserved_for_orders: Decimal


class AccountInvestments(T212Model):
    current_value: Decimal
    realized_profit_loss: Decimal
    total_cost: Decimal
    unrealized_profit_loss: Decimal


class AccountSummary(T212Model):
    """GET /equity/account/summary. All values are in the account's primary currency."""

    id: int
    currency: str
    total_value: Decimal
    cash: AccountCash
    investments: AccountInvestments


class PositionInstrument(T212Model):
    ticker: str
    name: str
    isin: str | None = None
    currency: str


class WalletImpact(T212Model):
    """Position values converted to the account currency by Trading212."""

    currency: str
    current_value: Decimal
    total_cost: Decimal
    unrealized_profit_loss: Decimal
    fx_impact: Decimal | None = None


class Position(T212Model):
    """GET /equity/positions. Prices are in the instrument's currency."""

    instrument: PositionInstrument
    quantity: Decimal
    average_price_paid: Decimal
    current_price: Decimal
    created_at: dt.datetime | None = None
    quantity_available_for_trading: Decimal | None = None
    quantity_in_pies: Decimal | None = None
    wallet_impact: WalletImpact


class Instrument(T212Model):
    """GET /equity/metadata/instruments. Only `ticker` is guaranteed; the rest is best effort."""

    ticker: str
    isin: str | None = None
    name: str | None = None
    short_name: str | None = None
    type: str | None = None
    currency_code: str | None = None
    working_schedule_id: int | None = None


# ---------------------------------------------------------------------------
# History (GET /equity/history/...): paginated, newest first.
# ---------------------------------------------------------------------------


class HistoryInstrument(T212Model):
    ticker: str
    name: str | None = None
    isin: str | None = None
    currency: str | None = None


class HistoryOrder(T212Model):
    id: int
    ticker: str | None = None
    side: str | None = None
    type: str | None = None
    status: str | None = None
    # The account currency (not the instrument's).
    currency: str | None = None
    created_at: dt.datetime | None = None
    instrument: HistoryInstrument | None = None
    quantity: Decimal | None = None
    filled_quantity: Decimal | None = None
    value: Decimal | None = None
    filled_value: Decimal | None = None
    initiated_from: str | None = None


class FillTax(T212Model):
    """One fee or tax charged on a fill (walletImpact.taxes[]).

    `name` is one of CURRENCY_CONVERSION_FEE, STAMP_DUTY, STAMP_DUTY_RESERVE_TAX,
    FRENCH_TRANSACTION_TAX, FINRA_FEE, PTM_LEVY, COMMISSION_TURNOVER, TRANSACTION_FEE (kept
    as a free string: a new name must not fail the sync). The sign of `quantity` is not
    consistent across reports (Trading212's example is positive, live responses have been
    seen negative), so consumers use its absolute value.
    """

    name: str
    quantity: Decimal
    currency: str | None = None
    charged_at: dt.datetime | None = None


class FillWalletImpact(T212Model):
    """The fill's effect on the account, in the account currency.

    `net_value` is the converted principal; in Trading212's documented example it EXCLUDES
    `taxes` (5 x 185.5 USD x 0.79 = 732.7 = netValue, stamp duty listed separately). The
    sync checks this per fill rather than assuming it (see portfolio.fees)."""

    currency: str | None = None
    net_value: Decimal | None = None
    realised_profit_loss: Decimal | None = None
    fx_rate: Decimal | None = None
    taxes: list[FillTax] | None = None


class Fill(T212Model):
    id: int
    filled_at: dt.datetime
    quantity: Decimal | None = None
    price: Decimal | None = None
    type: str | None = None
    # TOTV (on a trading venue) or OTC.
    trading_method: str | None = None
    wallet_impact: FillWalletImpact | None = None


class HistoricalOrder(T212Model):
    """An order and, once executed, its fill. Cancelled or rejected orders have no fill."""

    order: HistoryOrder
    fill: Fill | None = None


class HistoryDividend(T212Model):
    reference: str
    ticker: str | None = None
    type: str | None = None
    quantity: Decimal | None = None
    amount: Decimal
    currency: str | None = None
    gross_amount_per_share: Decimal | None = None
    paid_on: dt.datetime
    instrument: HistoryInstrument | None = None


class HistoryTransaction(T212Model):
    """A cash movement: deposit, withdrawal, fee, transfer or interest."""

    reference: str
    type: str
    amount: Decimal
    currency: str | None = None
    date_time: dt.datetime
