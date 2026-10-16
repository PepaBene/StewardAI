"""Versioned sentence templates, one per check and state (insight plan I3.2).

The backend returns the final sentence and the structured values; the browser prints it as
is. Pass and fail templates use the check's own figures; `na` and `unknown` print the reason.
Changing a template is a version bump of `SENTENCES_VERSION` (the snapshot test pins every one).
"""

from __future__ import annotations

from decimal import Decimal

SENTENCES_VERSION = "1.0.0"

# id: (label, pass template, fail template). Fields: {v} value, {t} threshold, {basis}, {metric}.
SENTENCES: dict[str, tuple[str, str, str]] = {
    # value
    "value_multiple_vs_peers": (
        "Multiple vs peers",
        "{metric} ({v}) is at or below the peer median ({t}, {basis})",
        "{metric} ({v}) is above the peer median ({t}, {basis})",
    ),
    "value_price_vs_dcf": (
        "Price vs base DCF",
        "Price ({v}) is at or below the base DCF value ({t})",
        "Price ({v}) is above the base DCF value ({t})",
    ),
    "value_price_vs_owner": (
        "Price vs your fair value",
        "Price ({v}) is at or below {basis} ({t})",
        "Price ({v}) is above {basis} ({t})",
    ),
    "value_peg": ("PEG", "PEG ({v}) is at or below {t}", "PEG ({v}) is above {t}"),
    "value_fcf_yield": (
        "Free cash flow yield",
        "Free cash flow yield ({v}) is above the risk-free rate plus margin ({t})",
        "Free cash flow yield ({v}) is below the risk-free rate plus margin ({t})",
    ),
    "value_consensus": (
        "Price vs analyst target",
        "Price ({v}) is at or below the analyst consensus target ({t}, {basis})",
        "Price ({v}) is above the analyst consensus target ({t}, {basis})",
    ),
    # future
    "future_earnings_vs_risk_free": (
        "Earnings growth vs risk-free",
        "Forecast earnings growth ({v}) is above the risk-free rate ({t})",
        "Forecast earnings growth ({v}) is below the risk-free rate ({t})",
    ),
    "future_earnings_vs_peers": (
        "Earnings growth vs peers",
        "Forecast earnings growth ({v}) is above the peer median ({t}, {basis})",
        "Forecast earnings growth ({v}) is below the peer median ({t}, {basis})",
    ),
    "future_revenue_vs_peers": (
        "Revenue growth vs peers",
        "Forecast revenue growth ({v}) is above the peer median ({t}, {basis})",
        "Forecast revenue growth ({v}) is below the peer median ({t}, {basis})",
    ),
    "future_eps_positive": (
        "EPS forecast to grow",
        "Next year's EPS forecast ({v}) is above the last reported ({t})",
        "Next year's EPS forecast ({v}) is not above the last reported ({t})",
    ),
    "future_roe": (
        "Forecast return on equity",
        "Forecast return on equity ({v}) is at least {t}",
        "Forecast return on equity ({v}) is below {t}",
    ),
    "future_analysts": (
        "Analyst coverage",
        "{v} analysts cover the forecast (at least {t})",
        "Only {v} analysts cover the forecast (fewer than {t})",
    ),
    # past
    "past_earnings_growth": (
        "5-year earnings growth",
        "Earnings per share grew {v} a year over five years",
        "Earnings per share changed {v} a year over five years (not positive)",
    ),
    "past_earnings_vs_peers": (
        "Earnings growth vs peers",
        "Five-year EPS growth ({v}) is above the peer median ({t}, {basis})",
        "Five-year EPS growth ({v}) is not above the peer median ({t}, {basis})",
    ),
    "past_roe": (
        "Return on equity",
        "Return on equity ({v}) is at least {t}",
        "Return on equity ({v}) is below {t}",
    ),
    "past_roce": (
        "Return on capital employed",
        "Return on capital employed ({v}) is at least {t}",
        "Return on capital employed ({v}) is below {t}",
    ),
    "past_margin_up": (
        "Net margin trend",
        "Net margin rose to {v} from {t} a year earlier",
        "Net margin fell to {v} from {t} a year earlier",
    ),
    "past_earnings_quality": (
        "Earnings quality",
        "Earnings are backed by cash ({basis})",
        "Earnings are weakly backed by cash ({basis})",
    ),
    # health
    "health_short_term_assets": (
        "Short-term liquidity",
        "Short-term assets ({v}) exceed short-term liabilities ({t})",
        "Short-term assets ({v}) do not cover short-term liabilities ({t})",
    ),
    "health_long_term_liabilities": (
        "Long-term liabilities",
        "Short-term assets ({v}) exceed long-term liabilities ({t})",
        "Short-term assets ({v}) do not cover long-term liabilities ({t})",
    ),
    "health_debt_to_equity": (
        "Debt to equity",
        "Debt to equity ({v}) is {basis}",
        "Debt to equity ({v}) is {basis}",
    ),
    "health_cfo_covers_debt": (
        "Debt covered by cash flow",
        "Operating cash flow covers {v} of debt (at least {t})",
        "Operating cash flow covers only {v} of debt (less than {t})",
    ),
    "health_interest_coverage": (
        "Interest coverage",
        "Operating income covers interest {v} (at least {t})",
        "Operating income covers interest only {v} (less than {t})",
    ),
    "health_cash_vs_debt": ("Cash vs debt", "{basis}", "{basis}"),
    # dividend
    "dividend_payout_earnings": (
        "Payout of earnings",
        "Dividends take {v} of earnings (at most {t})",
        "Dividends take {v} of earnings (more than {t})",
    ),
    "dividend_payout_cash": (
        "Payout of free cash flow",
        "Dividends take {v} of free cash flow (at most {t})",
        "Dividends take {v} of free cash flow (more than {t})",
    ),
    "dividend_no_cut": (
        "Dividend record",
        "No dividend cut in {v} years (at least {t})",
        "{v} years without a cut (fewer than {t})",
    ),
    "dividend_growth": (
        "Dividend growth",
        "Dividend per share grew {v} a year over {basis}",
        "Dividend per share changed {v} a year over {basis} (not positive)",
    ),
    "dividend_yield_vs_peers": (
        "Yield vs peers",
        "Dividend yield ({v}) is at or above the peer median ({t}, {basis})",
        "Dividend yield ({v}) is below the peer median ({t}, {basis})",
    ),
    "dividend_shareholder_yield_covered": (
        "Shareholder yield covered",
        "Dividends and buybacks ({v} of value) are covered by the free cash flow yield ({t})",
        "Dividends and buybacks ({v} of value) exceed the free cash flow yield ({t})",
    ),
    # risks: pass = the risk is absent
    "risk_insider_selling": (
        "Insider selling",
        "No net insider selling in {basis}",
        "Insiders sold a net {v} in {basis}",
    ),
    "risk_dilution": (
        "Share dilution",
        "Share count change ({v}) is within {t} a year",
        "Share count rose {v} in a year (more than {t})",
    ),
    "risk_negative_equity": (
        "Negative equity",
        "Shareholders' equity is positive ({v})",
        "Shareholders' equity is negative ({v})",
    ),
    "risk_interest_coverage": (
        "Weak interest coverage",
        "Interest is covered {v} (at least {t})",
        "Interest is covered only {v} (less than {t})",
    ),
    "risk_accruals": (
        "High accruals",
        "Accruals ({v} of assets) are below {t}",
        "Accruals ({v} of assets) are above {t}",
    ),
    "risk_margin_decline": (
        "Margin decline",
        "Operating margin ({v}) did not fall over three years (from {t})",
        "Operating margin fell to {v} from {t} three years earlier",
    ),
    "risk_liquidity": (
        "Low trading liquidity",
        "Average daily turnover ({v}) is above {t}",
        "Average daily turnover ({v}) is below {t}",
    ),
    "risk_volatility": (
        "Price volatility",
        "One-year volatility ({v}) is within {t} ({basis})",
        "One-year volatility ({v}) is above {t} ({basis})",
    ),
    "risk_small_cap": (
        "Small company",
        "Market value ({v}) is above {t}",
        "Market value ({v}) is below {t}",
    ),
    "risk_negative_fcf": (
        "Negative free cash flow",
        "Free cash flow was positive in at least one of the last three years",
        "Free cash flow was negative in each of the last three years",
    ),
    "risk_debt_wall": (
        "Near-term debt maturities",
        "Debt due within a year is {v} of total debt (at most {t})",
        "Debt due within a year is {v} of total debt (more than {t})",
    ),
    "risk_customer_concentration": (
        "Customer concentration",
        "No disclosed customer above {t} of revenue",
        "A disclosed customer accounts for {v} of revenue (above {t})",
    ),
    "risk_restatement": (
        "Restatement",
        "No amended or restated annual figures in 12 months",
        "{v} amended or restated figures in 12 months ({basis})",
    ),
    "risk_earnings_soon": (
        "Earnings date",
        "No report expected within {t} days",
        "Next report on {basis} (within {t} days)",
    ),
    "risk_asset_growth": (
        "Asset growth",
        "Total assets grew {v} in a year (at most {t})",
        "Total assets grew {v} in a year (more than {t})",
    ),
    "risk_young_listing": (
        "Young listing",
        "Listed {v} years (at least {t})",
        "Listed only {v} years (less than {t})",
    ),
    "risk_short_runway": (
        "Cash runway",
        "{basis}",
        "Cash covers {v} years of cash burn (less than {t})",
    ),
}


def label(check_id: str) -> str:
    return SENTENCES[check_id][0]


def fmt(value: Decimal | int | str | None, unit: str | None) -> str:
    """Unit-aware text the frontend prints unchanged."""
    if value is None:
        return "n/a"
    if isinstance(value, str):
        return value
    v = Decimal(value)
    if unit == "fraction":
        return f"{v * 100:.1f}%"
    if unit == "multiple":
        return f"{v:.1f}x"
    if unit == "ratio":
        return f"{v:.2f}"
    if unit == "years":
        return f"{v:.1f}"
    if unit == "count":
        return f"{v:.0f}"
    if unit in ("money", "money_compact"):
        a = abs(v)
        for size, suffix in (
            (Decimal(10) ** 12, "tn"),
            (Decimal(10) ** 9, "bn"),
            (Decimal(10) ** 6, "m"),
            (Decimal(10) ** 3, "k"),
        ):
            if a >= size:
                return f"{v / size:.1f}{suffix}"
        return f"{v:.0f}"
    return f"{v:.2f}"


def sentence(
    check_id: str,
    state: str,
    *,
    v: str = "",
    t: str = "",
    basis: str = "",
    metric: str = "",
    reason: str = "",
) -> str:
    lbl, ok, bad = SENTENCES[check_id]
    if state == "pass":
        return ok.format(v=v, t=t, basis=basis, metric=metric)
    if state == "fail":
        return bad.format(v=v, t=t, basis=basis, metric=metric)
    if state == "na":
        return f"{lbl}: not applicable — {reason or 'does not apply'}"
    return f"{lbl}: unknown — {reason or 'input missing'}"
