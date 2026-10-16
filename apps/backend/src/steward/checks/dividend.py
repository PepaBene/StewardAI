# ruff: noqa: E501 - check sentences and calls stay on one line each
"""Dividend axis, six checks (insight plan I1.7 / I3.7). All `na` for a non-payer; yield versus
peers is `na` below the configured yield floor, with the sentence saying why."""

from __future__ import annotations

from decimal import Decimal

from steward.analytics.thresholds import THRESHOLDS
from steward.checks.contract import Check
from steward.checks.inputs import CheckInputs, cagr, compare, mk
from steward.config.checks import CheckLimits

D = Decimal
IDS = (
    "dividend_payout_earnings",
    "dividend_payout_cash",
    "dividend_no_cut",
    "dividend_growth",
    "dividend_yield_vs_peers",
    "dividend_shareholder_yield_covered",
)


def dividend_checks(i: CheckInputs, lim: CheckLimits) -> list[Check]:
    sec = f"/companies/{i.ticker}/dividends"
    if i.payer is False:
        return [mk(c, "dividend", "na", reason="pays no dividend", section=sec) for c in IDS]
    if i.payer is None:
        return [
            mk(c, "dividend", "unknown", reason="dividend history not stored", section=sec)
            for c in IDS
        ]
    out: list[Check] = []
    paid = i.fig.get("dividends_paid")
    paid = abs(paid) if paid is not None else None
    ni = i.fig.get("net_income")
    payout = i.r("payout_ratio").latest
    if payout is None and paid is not None and ni is not None and ni > 0:
        payout = paid / ni
    if payout is None and ni is not None and ni <= 0:
        out.append(mk("dividend_payout_earnings", "dividend", "fail", value=None, threshold=D(str(lim.payout_limit_earnings)), unit="fraction",
                      v="all (earnings not positive)", section=sec))  # fmt: skip
    else:
        out.append(compare("dividend_payout_earnings", "dividend", payout, D(str(lim.payout_limit_earnings)), good="<=", unit="fraction",
                           missing="dividends paid or net income not filed", section=sec))  # fmt: skip
    cfo, capex = i.fig.get("cash_from_operations"), i.fig.get("capex")
    fcf = cfo - abs(capex) if cfo is not None and capex is not None else None
    if paid is not None and fcf is not None and fcf <= 0:
        out.append(mk("dividend_payout_cash", "dividend", "fail", threshold=D(str(lim.payout_limit_cash)), unit="fraction",
                      v="all (free cash flow not positive)", section=sec))  # fmt: skip
    else:
        out.append(compare("dividend_payout_cash", "dividend", paid / fcf if paid is not None and fcf else None, D(str(lim.payout_limit_cash)),
                           good="<=", unit="fraction", missing="dividends paid, operating cash flow or capex not filed", section=sec))  # fmt: skip
    if i.years_without_cut is None:
        out.append(
            mk(
                "dividend_no_cut",
                "dividend",
                "unknown",
                reason="fewer than two years of dividend history",
                section=sec,
            )
        )
    else:
        out.append(
            compare(
                "dividend_no_cut",
                "dividend",
                D(i.years_without_cut),
                D(lim.no_cut_years_min),
                good=">=",
                unit="count",
                missing="",
                section=sec,
            )
        )
    years = lim.dividend_growth_years
    g, span = cagr([(y, v) for y, v in i.dps_by_year], years)
    if g is None:
        out.append(
            mk(
                "dividend_growth",
                "dividend",
                "unknown",
                reason=f"{years}-year dividend growth: {span}",
                section=sec,
            )
        )
    else:
        out.append(
            mk(
                "dividend_growth",
                "dividend",
                "pass" if g > 0 else "fail",
                value=g,
                threshold=D(0),
                unit="fraction",
                basis=span,
                section=sec,
            )
        )
    y = i.r("dividend_yield")
    floor = D(str(lim.dividend_yield_floor))
    if y.latest is None:
        out.append(
            mk(
                "dividend_yield_vs_peers",
                "dividend",
                "unknown",
                reason=y.reason or "no dividend yield",
                section=sec,
            )
        )
    elif y.latest < floor:
        out.append(
            mk(
                "dividend_yield_vs_peers",
                "dividend",
                "na",
                reason=f"yield {y.latest:.2%} is below the {floor:.0%} floor where a peer comparison means something",
                section=sec,
            )
        )
    elif y.peer_median is None or y.peer_count < THRESHOLDS.min_peers_for_median:
        out.append(
            mk(
                "dividend_yield_vs_peers",
                "dividend",
                "unknown",
                reason=y.peer_reason
                or f"fewer than {THRESHOLDS.min_peers_for_median} comparable peers",
                section=sec,
            )
        )
    else:
        out.append(
            compare(
                "dividend_yield_vs_peers",
                "dividend",
                y.latest,
                y.peer_median,
                good=">=",
                unit="fraction",
                missing="",
                basis=y.peer_basis(),
                section=sec,
            )
        )
    tsy, fy = i.r("total_shareholder_yield").latest, i.r("fcf_yield").latest
    out.append(compare("dividend_shareholder_yield_covered", "dividend", tsy, fy, good="<=", unit="fraction",
                       missing="no shareholder yield or free cash flow yield", section=sec))  # fmt: skip
    return out
