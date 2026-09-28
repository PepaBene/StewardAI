"""Trading212 fees and taxes on fills: classification and how they relate to netValue.

Pure: no I/O. Trading212 reports every charge on a fill in `walletImpact.taxes[]` (see
`trading212.models.FillTax`). Two categories are shown in the app:

- **FX conversion**: `CURRENCY_CONVERSION_FEE`, the 0.15% Trading212 adds to the spot rate
  when an order's instrument currency differs from the account currency;
- **Taxes & levies**: every other name (stamp duty, SDRT, French FTT, FINRA fee, PTM levy,
  turnover commission, transaction fee), and any name Trading212 adds later.

Signs are not consistent across reports, so a charge's amount is always `abs(quantity)`.

Whether `walletImpact.netValue` already contains the fees matters for cash-flow math: if it
excludes them, a buy took netValue + fees out of cash; if it includes them, adding the fees
again would count them twice. Trading212's documented example excludes them, but that is
one illustrative payload, so `fee_basis` checks each fill: it rebuilds the principal from
quantity x price (x fxRate, either direction, whichever is nearer) and asks which of
"netValue = principal" or "netValue = principal +/- fees" fits better, by a clear margin.
Undecidable fills are `unknown` and are treated like `excluded` (the documented reading).
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from steward.trading212.models import FillTax

FeeCategory = Literal["fx", "tax"]
FeeBasis = Literal["excluded", "included", "unknown"]

FX_FEE_NAMES = frozenset({"CURRENCY_CONVERSION_FEE"})

# Display labels for the two categories (the API and the UI use the same words).
CATEGORY_LABEL: dict[FeeCategory, str] = {"fx": "FX conversion", "tax": "Taxes & levies"}


def fee_category(name: str) -> FeeCategory:
    return "fx" if name.upper() in FX_FEE_NAMES else "tax"


@dataclass(frozen=True)
class FeeCharge:
    seq: int
    name: str
    category: FeeCategory
    # abs(quantity): a fee is a cost whichever sign Trading212 reported.
    amount: Decimal
    quantity: Decimal
    currency: str | None
    charged_at: dt.datetime | None


def fee_charges(taxes: list[FillTax] | None, fallback_currency: str | None) -> list[FeeCharge]:
    """The fill's charges in reported order. A charge without a currency is taken to be in
    the fill's walletImpact currency (the account currency), which is what Trading212's
    example shows. Zero charges are kept: they say a fee line existed and was zero."""
    return [
        FeeCharge(
            seq=i,
            name=t.name,
            category=fee_category(t.name),
            amount=abs(t.quantity),
            quantity=t.quantity,
            currency=t.currency or fallback_currency,
            charged_at=t.charged_at,
        )
        for i, t in enumerate(taxes or [])
    ]


def fee_basis(
    *,
    side: str | None,
    quantity: Decimal | None,
    price: Decimal | None,
    fx_rate: Decimal | None,
    net_value: Decimal | None,
    fees: Decimal,
) -> FeeBasis | None:
    """Whether `net_value` includes `fees` (all in the account currency). None when the
    fill has no fees (nothing to decide)."""
    if fees <= 0:
        return None
    if quantity is None or price is None or net_value is None:
        return "unknown"
    gross = abs(quantity * price)
    candidates = [gross]
    if fx_rate:
        candidates = [gross * abs(fx_rate), gross / abs(fx_rate)]
    net = abs(net_value)
    principal = min(candidates, key=lambda c: abs(c - net))
    if principal <= 0:
        return "unknown"
    err_excluded = abs(net - principal)
    # A buy costs principal + fees; a sale pays out principal - fees.
    if side == "BUY":
        err_included = abs(net - (principal + fees))
    elif side == "SELL":
        err_included = abs(net - (principal - fees))
    else:
        err_included = min(abs(net - (principal + fees)), abs(net - (principal - fees)))
    # Decide only with a clear margin: the better fit must be within half the fees and
    # the other hypothesis must be off by more than that.
    half = fees / 2
    if err_excluded <= half < err_included:
        return "excluded"
    if err_included <= half < err_excluded:
        return "included"
    return "unknown"
