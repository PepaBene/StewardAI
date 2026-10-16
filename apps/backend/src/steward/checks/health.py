# ruff: noqa: E501 - check sentences and calls stay on one line each
"""Health axis, six checks (insight plan I3.6). Banks and insurers: checks 1, 2, 4 and 6 do not
apply (their balance sheet is the business), with the reason in the sentence."""

from __future__ import annotations

from decimal import Decimal

from steward.checks.contract import Check
from steward.checks.inputs import CheckInputs, compare, mk
from steward.config.checks import CheckLimits

D = Decimal
FIN = "not meaningful for banks and insurers"


def net_interest_earner(i: CheckInputs) -> bool:
    """Interest income at least interest expense, or no interest expense and no debt."""
    inc, exp = i.fig.get("interest_income"), i.fig.get("interest_expense")
    if exp is not None and inc is not None and abs(inc) >= abs(exp):
        return True
    return (
        (exp is None or exp == 0)
        and (i.debt is None or i.debt == 0)
        and i.fig.get("cash") is not None
    )


def health_checks(i: CheckInputs, lim: CheckLimits) -> list[Check]:
    sec = f"/companies/{i.ticker}/financials"
    ca, cl, tl = (
        i.fig.get("current_assets"),
        i.fig.get("current_liabilities"),
        i.fig.get("total_liabilities"),
    )
    out: list[Check] = []
    if i.is_financial:
        out.append(mk("health_short_term_assets", "health", "na", reason=FIN, section=sec))
        out.append(mk("health_long_term_liabilities", "health", "na", reason=FIN, section=sec))
    else:
        out.append(
            compare(
                "health_short_term_assets",
                "health",
                ca,
                cl,
                good=">",
                unit="money",
                missing="current assets or liabilities not filed",
                section=sec,
            )
        )
        ltl = tl - cl if tl is not None and cl is not None else None
        out.append(compare("health_long_term_liabilities", "health", ca, ltl, good=">", unit="money",
                           missing="current assets, total or current liabilities not filed", section=sec))  # fmt: skip
    de = i.r("debt_to_equity")
    limit = D(str(lim.debt_to_equity_max))
    series = [v for _, v in de.series if v is not None]
    if de.latest is None:
        out.append(
            mk(
                "health_debt_to_equity",
                "health",
                "unknown",
                reason=de.reason or "no debt to equity",
                section=sec,
            )
        )
    elif de.latest < limit:
        out.append(
            mk(
                "health_debt_to_equity",
                "health",
                "pass",
                value=de.latest,
                threshold=limit,
                unit="fraction",
                basis=f"below {limit:.0%}",
                section=sec,
            )
        )
    elif len(series) >= 5 and series[-1] < series[-5]:
        out.append(mk("health_debt_to_equity", "health", "pass", value=de.latest, threshold=limit, unit="fraction",
                      basis=f"above {limit:.0%} but down from {series[-5]:.0%} five years earlier", section=sec))  # fmt: skip
    else:
        out.append(mk("health_debt_to_equity", "health", "fail", value=de.latest, threshold=limit, unit="fraction",
                      basis=f"above {limit:.0%} and not falling over five years", section=sec))  # fmt: skip
    cfo = i.fig.get("cash_from_operations")
    if i.is_financial:
        out.append(mk("health_cfo_covers_debt", "health", "na", reason=FIN, section=sec))
    elif i.debt is not None and i.debt == 0:
        out.append(
            mk(
                "health_cfo_covers_debt",
                "health",
                "pass",
                value=None,
                threshold=D(str(lim.cfo_to_debt_min)),
                unit="fraction",
                v="all (no debt)",
                section=sec,
            )
        )
    else:
        cov = cfo / i.debt if cfo is not None and i.debt else None
        out.append(compare("health_cfo_covers_debt", "health", cov, D(str(lim.cfo_to_debt_min)), good=">=", unit="fraction",
                           missing="operating cash flow or debt not filed", section=sec))  # fmt: skip
    ic = i.r("interest_coverage")
    if net_interest_earner(i):
        out.append(
            mk(
                "health_interest_coverage",
                "health",
                "na",
                reason="earns more interest than it pays",
                section=sec,
            )
        )
    else:
        out.append(compare("health_interest_coverage", "health", ic.latest, D(str(lim.interest_coverage_min)), good=">=", unit="multiple",
                           missing=ic.reason or "no interest coverage", section=sec))  # fmt: skip
    cash = i.fig.get("cash")
    if i.is_financial:
        out.append(mk("health_cash_vs_debt", "health", "na", reason=FIN, section=sec))
    elif cash is not None and i.debt is not None and cash >= i.debt:
        out.append(mk("health_cash_vs_debt", "health", "pass", value=cash, threshold=i.debt, unit="money",
                      basis=f"Cash ({cash:,.0f}) is at least debt ({i.debt:,.0f})", section=sec))  # fmt: skip
    else:
        nd = i.r("net_debt_to_ebitda")
        lim_nd = D(str(lim.net_debt_to_ebitda_max))
        if nd.latest is None:
            out.append(
                mk(
                    "health_cash_vs_debt",
                    "health",
                    "unknown",
                    reason=nd.reason or "cash, debt or EBITDA not filed",
                    section=sec,
                )
            )
        else:
            ok = nd.latest < lim_nd
            out.append(mk("health_cash_vs_debt", "health", "pass" if ok else "fail", value=nd.latest, threshold=lim_nd, unit="multiple",
                          basis=f"Net debt is {nd.latest:.1f}x EBITDA ({'below' if ok else 'above'} {lim_nd:.1f}x)", section=sec))  # fmt: skip
    return out
