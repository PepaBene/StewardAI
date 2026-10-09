"""Cross-listing comparison (ADR vs ordinary share). A missing ratio blocks a merged price
chart: the two listings are never put on one axis without a documented conversion."""

from __future__ import annotations

from decimal import Decimal

from steward.analytics.contract import AnalyticsResult, result, unavailable
from steward.analytics.versions import version

ONE = Decimal(1)


def adr_parity_premium(
    adr_price: Decimal,
    ordinary_per_adr: Decimal | None,
    local_price: Decimal,
    usd_per_local: Decimal | None,
) -> AnalyticsResult:
    """`ADR price / (ordinary shares per ADR x local price x FX) - 1`, in the ADR's currency."""
    method = version("price_return")
    inputs = {
        "adr": adr_price,
        "ratio": ordinary_per_adr,
        "local": local_price,
        "fx": usd_per_local,
    }
    if ordinary_per_adr is None or ordinary_per_adr <= 0:
        return unavailable(
            "fraction", method, "ADR ratio missing: listings are not merged", inputs=inputs
        )
    if usd_per_local is None or usd_per_local <= 0:
        return unavailable("fraction", method, "FX rate missing", inputs=inputs)
    if adr_price <= 0 or local_price <= 0:
        return unavailable("fraction", method, "non-positive price", inputs=inputs)
    return result(
        adr_price / (ordinary_per_adr * local_price * usd_per_local) - ONE,
        "fraction",
        method,
        inputs=inputs,
    )
