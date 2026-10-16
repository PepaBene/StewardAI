# ruff: noqa: E501, RUF001 - check sentences and calls stay on one line each
"""Future axis, six checks; all need published estimates (insight plan I3.4)."""

from __future__ import annotations

from decimal import Decimal

from steward.analytics.thresholds import THRESHOLDS
from steward.checks.contract import Check
from steward.checks.inputs import CheckInputs, compare, mk
from steward.config.checks import CheckLimits

D = Decimal


def _growth(nxt: Decimal | None, last: Decimal | None) -> Decimal | None:
    if nxt is None or last is None or last <= 0:
        return None
    return nxt / last - 1


def future_checks(i: CheckInputs, lim: CheckLimits) -> list[Check]:
    sec = f"/companies/{i.ticker}/estimates"
    reason = i.estimates_reason or "no stored analyst estimates"
    eps_g = _growth(i.eps_next, i.eps_trailing)
    rev_g = _growth(i.revenue_next, i.revenue_trailing)
    eps_missing = (
        reason
        if i.eps_next is None
        else (
            "n/m: trailing EPS is not positive"
            if i.eps_trailing is not None and i.eps_trailing <= 0
            else "no trailing EPS"
        )
    )
    rev_missing = reason if i.revenue_next is None else "no trailing revenue"
    peers_ok = i.peer_forecast_count >= THRESHOLDS.min_peers_for_median
    peer_reason = (
        f"{i.peer_forecast_count} peers with estimates (needs {THRESHOLDS.min_peers_for_median})"
    )
    peer_basis = f"{i.peer_forecast_count} peers with estimates"
    out = [
        compare("future_earnings_vs_risk_free", "future", eps_g, i.risk_free, good=">", unit="fraction", missing=eps_missing, section=sec),
        compare("future_earnings_vs_peers", "future", eps_g, i.peer_eps_growth if peers_ok else None, good=">", unit="fraction",
                missing=eps_missing if eps_g is None else peer_reason, basis=peer_basis, section=sec),
        compare("future_revenue_vs_peers", "future", rev_g, i.peer_revenue_growth if peers_ok else None, good=">", unit="fraction",
                missing=rev_missing if rev_g is None else peer_reason, basis=peer_basis, section=sec),
    ]  # fmt: skip
    if i.eps_next is None or i.eps_trailing is None:
        out.append(mk("future_eps_positive", "future", "unknown", reason=eps_missing, section=sec))
    else:
        out.append(
            compare(
                "future_eps_positive",
                "future",
                i.eps_next,
                i.eps_trailing,
                good=">",
                unit="ratio",
                missing="",
                section=sec,
            )
        )
    equity = i.fig.get("equity")
    shares = i.fig.get("weighted_shares_diluted") or i.fig.get("shares_outstanding")
    roe = None
    roe_missing = reason
    if i.eps_next is not None:
        if equity is None or shares is None:
            roe_missing = "no equity or share count filed"
        elif equity <= 0:
            roe_missing = "n/m: equity is not positive"
        else:
            roe = i.eps_next * shares / equity
    out.append(compare("future_roe", "future", roe, D(str(lim.forecast_roe_floor)), good=">=", unit="fraction", missing=roe_missing,
                       basis="next year's EPS × diluted shares / latest equity", section=sec))  # fmt: skip
    if i.analyst_count is None:
        out.append(mk("future_analysts", "future", "unknown", reason=reason, section=sec))
    else:
        out.append(
            compare(
                "future_analysts",
                "future",
                D(i.analyst_count),
                D(lim.analyst_count_min),
                good=">=",
                unit="count",
                missing="",
                section=sec,
            )
        )
    return out
