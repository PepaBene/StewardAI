# ruff: noqa: E501 - check sentences and calls stay on one line each
"""Past axis, six checks from the filed history (insight plan I3.5)."""

from __future__ import annotations

from decimal import Decimal

from steward.analytics.thresholds import THRESHOLDS
from steward.checks.contract import Check
from steward.checks.inputs import CheckInputs, compare, mk
from steward.config.checks import CheckLimits

D = Decimal


def past_checks(i: CheckInputs, lim: CheckLimits) -> list[Check]:
    sec = f"/companies/{i.ticker}/ratios"
    g = i.r("eps_diluted_cagr_5y")
    out = [
        compare("past_earnings_growth", "past", g.latest, D(0), good=">", unit="fraction", missing=g.reason or "no 5-year EPS growth", section=sec),
    ]  # fmt: skip
    if g.latest is None:
        out.append(
            mk(
                "past_earnings_vs_peers",
                "past",
                "unknown",
                reason=g.reason or "no 5-year EPS growth",
                section=sec,
            )
        )
    elif g.peer_median is None or g.peer_count < THRESHOLDS.min_peers_for_median:
        out.append(
            mk(
                "past_earnings_vs_peers",
                "past",
                "unknown",
                reason=g.peer_reason
                or f"fewer than {THRESHOLDS.min_peers_for_median} comparable peers",
                section=sec,
            )
        )
    else:
        out.append(
            compare(
                "past_earnings_vs_peers",
                "past",
                g.latest,
                g.peer_median,
                good=">",
                unit="fraction",
                missing="",
                basis=g.peer_basis(),
                section=sec,
            )
        )
    roe, roce = i.r("roe"), i.r("roce")
    out.append(
        compare(
            "past_roe",
            "past",
            roe.latest,
            D(str(lim.roe_floor)),
            good=">=",
            unit="fraction",
            missing=roe.reason or "no return on equity",
            section=sec,
        )
    )
    if i.is_financial and roce.latest is None:
        out.append(
            mk(
                "past_roce",
                "past",
                "na",
                reason="capital employed is not meaningful for banks and insurers",
                section=sec,
            )
        )
    else:
        out.append(
            compare(
                "past_roce",
                "past",
                roce.latest,
                D(str(lim.roce_floor)),
                good=">=",
                unit="fraction",
                missing=roce.reason or "no return on capital employed",
                section=sec,
            )
        )
    nm = [v for _, v in i.r("net_margin").series if v is not None]
    if len(nm) < 2:
        out.append(
            mk(
                "past_margin_up",
                "past",
                "unknown",
                reason="fewer than two years of net margin",
                section=sec,
            )
        )
    else:
        out.append(
            mk(
                "past_margin_up",
                "past",
                "pass" if nm[-1] > nm[-2] else "fail",
                value=nm[-1],
                threshold=nm[-2],
                unit="fraction",
                section=sec,
            )
        )
    cfo, ni = i.fig.get("cash_from_operations"), i.fig.get("net_income")
    acc = i.r("accruals_ratio").latest
    limit = D(str(lim.accruals_max))
    if cfo is not None and ni is not None:
        ok = cfo >= ni
        basis = f"operating cash flow {'at least' if ok else 'below'} net income"
        if not ok and acc is not None and acc < limit:
            ok, basis = True, f"accruals {acc:.1%} of assets, below {limit:.0%}"
        out.append(
            mk(
                "past_earnings_quality",
                "past",
                "pass" if ok else "fail",
                value=cfo,
                threshold=ni,
                unit="money",
                basis=basis,
                section=sec,
            )
        )
    elif acc is not None:
        ok = acc < limit
        out.append(mk("past_earnings_quality", "past", "pass" if ok else "fail", value=acc, threshold=limit, unit="fraction",
                      basis=f"accruals {acc:.1%} of assets vs {limit:.0%}", section=sec))  # fmt: skip
    else:
        out.append(
            mk(
                "past_earnings_quality",
                "past",
                "unknown",
                reason="no operating cash flow, net income or accruals",
                section=sec,
            )
        )
    return out
