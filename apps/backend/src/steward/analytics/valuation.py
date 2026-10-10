"""DCF scenario and reverse DCF (methodology.md section 10). The inputs are the owner's
forecast; nothing here observes a fact. Missing base cash flow, share count or a material
balance-sheet item blocks a per-share figure.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from steward.analytics.contract import AnalyticsResult, result, unavailable
from steward.analytics.versions import version

ONE = Decimal(1)
ZERO = Decimal(0)


@dataclass(frozen=True)
class DcfInputs:
    forecast_fcf: Sequence[Decimal]  # year 1..N free cash flow, one currency
    discount_rate: Decimal
    terminal_growth: Decimal
    net_debt: Decimal | None  # debt - cash at a stated date
    minorities: Decimal | None
    diluted_shares: Decimal | None


def enterprise_value(
    fcf: Sequence[Decimal], rate: Decimal, terminal_growth: Decimal
) -> Decimal | None:
    """Sum of `fcf_t / (1+r)^t` plus the Gordon terminal value at year N, discounted N years.
    Needs `rate > terminal_growth`; otherwise the terminal value is not defined."""
    if not fcf or rate <= terminal_growth or rate <= -ONE:
        return None
    pv = ZERO
    for t, flow in enumerate(fcf, start=1):
        pv += flow / (ONE + rate) ** t
    terminal = fcf[-1] * (ONE + terminal_growth) / (rate - terminal_growth)
    return pv + terminal / (ONE + rate) ** len(fcf)


def dcf_per_share(inputs: DcfInputs) -> AnalyticsResult:
    method = version("dcf")
    if inputs.net_debt is None or inputs.minorities is None:
        return unavailable(
            "per_share",
            method,
            "net debt or minorities missing: no precise per-share value",
            inputs=inputs,
        )
    if inputs.diluted_shares is None or inputs.diluted_shares <= 0:
        return unavailable("per_share", method, "diluted share count missing", inputs=inputs)
    ev = enterprise_value(inputs.forecast_fcf, inputs.discount_rate, inputs.terminal_growth)
    if ev is None:
        return unavailable(
            "per_share",
            method,
            "needs a forecast and discount rate above terminal growth",
            inputs=inputs,
        )
    equity = ev - inputs.net_debt - inputs.minorities
    return result(equity / inputs.diluted_shares, "per_share", method, inputs=inputs)


def margin_of_safety(value_per_share: Decimal | None, price: Decimal | None) -> Decimal | None:
    """`(value - price) / value`, only against a user-selected scenario value above zero."""
    if value_per_share is None or price is None or value_per_share <= 0:
        return None
    return (value_per_share - price) / value_per_share


def reverse_dcf_growth(
    *,
    price: Decimal,
    base_fcf: Decimal | None,
    years: int,
    discount_rate: Decimal,
    terminal_growth: Decimal,
    net_debt: Decimal | None,
    minorities: Decimal | None,
    diluted_shares: Decimal | None,
) -> AnalyticsResult:
    """The constant annual FCF growth over `years` that makes the DCF per-share value equal
    `price`. Solves an assumption (bisection on -50 %..+100 %), not an observed fact."""
    method = version("reverse_dcf")
    inputs = {
        "price": price,
        "base_fcf": base_fcf,
        "years": years,
        "rate": discount_rate,
        "terminal": terminal_growth,
        "net_debt": net_debt,
        "minorities": minorities,
        "shares": diluted_shares,
    }
    if base_fcf is None or base_fcf <= 0:
        return unavailable(
            "fraction", method, "n/m: base free cash flow missing or not positive", inputs=inputs
        )
    if None in (net_debt, minorities) or diluted_shares is None or diluted_shares <= 0 or years < 1:
        return unavailable(
            "fraction", method, "net debt, minorities or share count missing", inputs=inputs
        )

    def value_at(g: Decimal) -> Decimal | None:
        flows = [base_fcf * (ONE + g) ** t for t in range(1, years + 1)]
        ev = enterprise_value(flows, discount_rate, terminal_growth)
        if ev is None:
            return None
        assert net_debt is not None and minorities is not None and diluted_shares is not None
        return (ev - net_debt - minorities) / diluted_shares

    low, high = Decimal("-0.5"), Decimal(1)
    v_low, v_high = value_at(low), value_at(high)
    if v_low is None or v_high is None:
        return unavailable(
            "fraction", method, "discount rate must exceed terminal growth", inputs=inputs
        )
    if not (v_low <= price <= v_high):
        return unavailable(
            "fraction",
            method,
            "price is outside the value range reachable with growth of -50 %..+100 %",
            inputs=inputs,
        )
    for _ in range(200):
        mid = (low + high) / 2
        v_mid = value_at(mid)
        assert v_mid is not None
        if v_mid < price:
            low = mid
        else:
            high = mid
        if high - low < Decimal("1e-14"):
            break
    return result((low + high) / 2, "fraction", method, inputs=inputs)
