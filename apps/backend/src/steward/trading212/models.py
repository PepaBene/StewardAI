"""Response models for the Trading212 Public API (v0).

The API is in beta, so every response is validated: a changed or missing field fails
loudly at the boundary instead of propagating bad data. Unknown extra fields are
ignored so additive API changes don't break the sync. Money uses Decimal, parsed
straight from the JSON text so no float rounding happens on the way in.
"""

import datetime as dt
from decimal import Decimal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class T212Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


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
