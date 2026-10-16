# ruff: noqa: E501 - check sentences and calls stay on one line each
"""Risk checks, a separate list (insight plan I3.8). `pass` means the risk is absent.

The red-flag rules of Discover (`discovery.composite.RED_FLAG_RULES`) are reused for asset
growth, a young listing and a short cash runway, so both screens apply the same limits; the
dilution and accruals limits are the owner's configured ones.
"""

from __future__ import annotations

from decimal import Decimal

from steward.checks.contract import Check
from steward.checks.inputs import CheckInputs, compare, mk
from steward.config.checks import CheckLimits
from steward.discovery.composite import RED_FLAG_RULES

D = Decimal


def risk_checks(i: CheckInputs, lim: CheckLimits) -> list[Check]:
    t = i.ticker
    ratios_sec, fin_sec = f"/companies/{t}/ratios", f"/companies/{t}/financials"
    out: list[Check] = []
    window = f"the last {lim.insider_window_days} days"
    if not i.insider_has_filings:
        out.append(
            mk(
                "risk_insider_selling",
                "risk",
                "unknown",
                reason="no insider filings stored (US Form 4 only)",
                section=f"/companies/{t}/governance",
            )
        )
    else:
        sold = i.insider_net_sold_value or D(0)
        out.append(mk("risk_insider_selling", "risk", "fail" if sold > 0 else "pass", value=sold, unit="money", basis=window,
                      inputs={"sellers": i.insider_sellers}, section=f"/companies/{t}/governance"))  # fmt: skip
    dil = i.r("dilution_1y")
    out.append(
        compare(
            "risk_dilution",
            "risk",
            dil.latest,
            D(str(lim.dilution_max)),
            good="<=",
            unit="fraction",
            missing=dil.reason or "no share count history",
            section=ratios_sec,
        )
    )
    eq = i.fig.get("equity")
    if eq is None:
        out.append(
            mk(
                "risk_negative_equity",
                "risk",
                "unknown",
                reason="equity not filed",
                section=fin_sec,
            )
        )
    else:
        out.append(
            mk(
                "risk_negative_equity",
                "risk",
                "pass" if eq > 0 else "fail",
                value=eq,
                unit="money",
                section=fin_sec,
            )
        )
    from steward.checks.health import net_interest_earner

    ic = i.r("interest_coverage")
    if net_interest_earner(i):
        out.append(
            mk(
                "risk_interest_coverage",
                "risk",
                "na",
                reason="earns more interest than it pays",
                section=ratios_sec,
            )
        )
    else:
        out.append(compare("risk_interest_coverage", "risk", ic.latest, D(str(lim.interest_coverage_min)), good=">=", unit="multiple",
                           missing=ic.reason or "no interest coverage", section=ratios_sec))  # fmt: skip
    acc = i.r("accruals_ratio")
    out.append(
        compare(
            "risk_accruals",
            "risk",
            acc.latest,
            D(str(lim.accruals_max)),
            good="<",
            unit="fraction",
            missing=acc.reason or "no accruals ratio",
            section=ratios_sec,
        )
    )
    om = [v for _, v in i.r("operating_margin").series if v is not None]
    if len(om) < 4:
        out.append(
            mk(
                "risk_margin_decline",
                "risk",
                "unknown",
                reason="fewer than four years of operating margin",
                section=ratios_sec,
            )
        )
    else:
        out.append(
            mk(
                "risk_margin_decline",
                "risk",
                "pass" if om[-1] >= om[-4] else "fail",
                value=om[-1],
                threshold=om[-4],
                unit="fraction",
                section=ratios_sec,
            )
        )
    out.append(compare("risk_liquidity", "risk", i.avg_turnover, D(str(lim.liquidity_floor)), good=">=", unit="money",
                       missing="no turnover data for the listing", basis=i.turnover_basis or "", section=f"/companies/{t}"))  # fmt: skip
    bench = D(str(lim.benchmark_volatility))
    cap = bench * D(str(lim.volatility_vs_benchmark_max))
    out.append(compare("risk_volatility", "risk", i.volatility_1y, cap, good="<=", unit="fraction", missing="fewer than a year of daily prices",
                       basis=f"{lim.volatility_vs_benchmark_max}x benchmark {bench:.0%}", section=f"/companies/{t}"))  # fmt: skip
    out.append(
        compare(
            "risk_small_cap",
            "risk",
            i.market_cap,
            D(str(lim.small_cap_max)),
            good=">=",
            unit="money",
            missing="no market value",
            section=f"/companies/{t}",
        )
    )
    fcf = i.series.get("fcf", [])
    vals = [v for _, v in fcf[-3:] if v is not None]
    if len(vals) < 3:
        out.append(
            mk(
                "risk_negative_fcf",
                "risk",
                "unknown",
                reason="fewer than three years of free cash flow",
                section=fin_sec,
            )
        )
    else:
        out.append(
            mk(
                "risk_negative_fcf",
                "risk",
                "fail" if all(v < 0 for v in vals) else "pass",
                value=vals[-1],
                unit="money",
                section=fin_sec,
            )
        )
    if i.debt is not None and i.debt == 0:
        out.append(mk("risk_debt_wall", "risk", "na", reason="no financial debt", section=fin_sec))
    elif i.debt_due_1y is None or not i.debt:
        out.append(
            mk(
                "risk_debt_wall",
                "risk",
                "unknown",
                reason="debt maturities not filed",
                section=fin_sec,
            )
        )
    else:
        out.append(
            compare(
                "risk_debt_wall",
                "risk",
                i.debt_due_1y / i.debt,
                D(str(lim.debt_maturity_share_max)),
                good="<=",
                unit="fraction",
                missing="",
                section=fin_sec,
            )
        )
    conc_limit = D("0.10")
    if not i.customer_known:
        out.append(
            mk(
                "risk_customer_concentration",
                "risk",
                "unknown",
                reason="no confirmed customer disclosures",
                section=f"/companies/{t}/network",
            )
        )
    else:
        share = i.customer_max_share or D(0)
        out.append(mk("risk_customer_concentration", "risk", "fail" if share > conc_limit else "pass", value=share, threshold=conc_limit, unit="fraction",
                      section=f"/companies/{t}/network"))  # fmt: skip
    if i.amendments_12m is None:
        out.append(
            mk(
                "risk_restatement",
                "risk",
                "unknown",
                reason="no filing history stored",
                section=fin_sec,
            )
        )
    else:
        out.append(mk("risk_restatement", "risk", "fail" if i.amendments_12m else "pass", value=D(i.amendments_12m), unit="count",
                      basis=i.amendment_basis or "", section=fin_sec))  # fmt: skip
    days = lim.earnings_window_days
    if i.next_report is None:
        out.append(
            mk(
                "risk_earnings_soon",
                "risk",
                "unknown",
                reason="no next report date stored",
                informational=True,
                section=f"/companies/{t}",
            )
        )
    else:
        soon = 0 <= (i.next_report - i.today).days <= days
        out.append(mk("risk_earnings_soon", "risk", "fail" if soon else "pass", t=str(days), basis=i.next_report.isoformat(),
                      informational=True, section=f"/companies/{t}"))  # fmt: skip
    _, limit, _, _ = RED_FLAG_RULES["asset_growth"]
    ta = [v for _, v in i.series.get("total_assets", []) if v is not None]
    growth = ta[-1] / ta[-2] - 1 if len(ta) >= 2 and ta[-2] > 0 else None
    out.append(
        compare(
            "risk_asset_growth",
            "risk",
            growth,
            limit,
            good="<=",
            unit="fraction",
            missing="fewer than two years of total assets",
            section=fin_sec,
        )
    )
    _, limit, _, _ = RED_FLAG_RULES["young_listing"]
    out.append(
        compare(
            "risk_young_listing",
            "risk",
            i.years_listed,
            limit,
            good=">=",
            unit="years",
            missing="no first trading date stored",
            section=f"/companies/{t}",
        )
    )
    _, limit, _, _ = RED_FLAG_RULES["short_runway"]
    if i.cash_runway_years is None:
        reason = i.cash_runway_reason or "no cash runway"
        if reason.startswith("not burning"):
            out.append(
                mk(
                    "risk_short_runway",
                    "risk",
                    "pass",
                    basis="Not burning cash: no runway limit applies",
                    section=ratios_sec,
                )
            )
        else:
            out.append(
                mk("risk_short_runway", "risk", "unknown", reason=reason, section=ratios_sec)
            )
    else:
        ok = i.cash_runway_years >= limit
        out.append(mk("risk_short_runway", "risk", "pass" if ok else "fail", value=i.cash_runway_years, threshold=limit, unit="years",
                      basis=f"Cash covers {i.cash_runway_years:.1f} years of cash burn", section=ratios_sec))  # fmt: skip
    return out
