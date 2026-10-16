# ruff: noqa: E501 - check sentences and calls stay on one line each
"""Value axis, six checks (insight plan I3.3)."""

from __future__ import annotations

from decimal import Decimal

from steward.analytics.thresholds import THRESHOLDS
from steward.checks.contract import Check
from steward.checks.inputs import CheckInputs, compare, mk
from steward.config.checks import CheckLimits

D = Decimal


def _section(i: CheckInputs, tab: str) -> str:
    return f"/companies/{i.ticker}/{tab}"


def multiple_vs_peers(i: CheckInputs) -> Check:
    """P/E when earnings are positive; EV/EBIT for loss-makers; EV/sales when both are negative."""
    sec = _section(i, "ratios")
    for rid, name in (("pe", "P/E"), ("ev_to_ebit", "EV/EBIT"), ("ev_to_sales", "EV/sales")):
        r = i.r(rid)
        if r.latest is None or r.latest <= 0:
            continue
        if r.peer_median is None or r.peer_count < THRESHOLDS.min_peers_for_median:
            reason = (
                r.peer_reason
                or f"fewer than {THRESHOLDS.min_peers_for_median} comparable peers for {name}"
            )
            return mk(
                "value_multiple_vs_peers",
                "value",
                "unknown",
                reason=reason,
                section=sec,
                inputs={"metric": rid},
            )
        return compare(
            "value_multiple_vs_peers", "value", r.latest, r.peer_median, good="<=", unit="multiple",
            missing="", basis=r.peer_basis(), metric=name, inputs={"metric": rid, "peers": r.peer_count}, section=sec,
        )  # fmt: skip
    return mk(
        "value_multiple_vs_peers",
        "value",
        "unknown",
        reason="no positive P/E, EV/EBIT or EV/sales",
        section=sec,
    )


def price_vs_dcf(i: CheckInputs) -> Check:
    sec = _section(i, "valuation")
    if i.price is None:
        return mk(
            "value_price_vs_dcf",
            "value",
            "unknown",
            reason=i.price_note or "no price in the statement currency",
            section=sec,
        )
    return compare(
        "value_price_vs_dcf", "value", i.price, i.dcf_base, good="<=", unit="ratio",
        missing=i.dcf_reason or "no base DCF value", basis="base DCF scenario", section=sec,
        inputs={"price": i.price, "dcf_base": i.dcf_base},
    )  # fmt: skip


def price_vs_owner(i: CheckInputs) -> Check:
    sec = _section(i, "valuation")
    if i.owner_value is None:
        return mk(
            "value_price_vs_owner",
            "value",
            "unknown",
            reason=i.owner_reason or "no saved scenario or thesis fair value",
            section=sec,
        )
    if i.price is None:
        return mk(
            "value_price_vs_owner",
            "value",
            "unknown",
            reason=i.price_note or "no price in the fair value's currency",
            section=sec,
        )
    return compare(
        "value_price_vs_owner", "value", i.price, i.owner_value, good="<=", unit="ratio", missing="",
        basis=i.owner_basis or "your fair value", section=sec, inputs={"price": i.price, "fair_value": i.owner_value},
    )  # fmt: skip


def peg(i: CheckInputs, lim: CheckLimits) -> Check:
    r = i.r("peg")
    sec = _section(i, "ratios")
    if r.latest is None:
        reason = r.reason or "no PEG"
        if "n/m" in reason or "growth" in reason:
            return mk(
                "value_peg",
                "value",
                "na",
                reason=f"no earnings growth to divide by ({reason})",
                section=sec,
            )
        return mk("value_peg", "value", "unknown", reason=reason, section=sec)
    return compare(
        "value_peg",
        "value",
        r.latest,
        D(str(lim.peg_max)),
        good="<=",
        unit="ratio",
        missing="",
        section=sec,
    )


def fcf_yield(i: CheckInputs, lim: CheckLimits) -> Check:
    r = i.r("fcf_yield")
    bar = i.risk_free + D(str(lim.fcf_yield_margin))
    return compare(
        "value_fcf_yield", "value", r.latest, bar, good=">", unit="fraction", missing=r.reason or "no free cash flow yield",
        basis=f"risk-free {i.risk_free:.2%} + margin {lim.fcf_yield_margin:.2%}", section=_section(i, "ratios"),
    )  # fmt: skip


def consensus(i: CheckInputs) -> Check:
    sec = _section(i, "estimates")
    if i.consensus_target is None:
        return mk(
            "value_consensus",
            "value",
            "unknown",
            reason=i.consensus_reason or "no consensus target",
            section=sec,
        )
    if i.price is None:
        return mk(
            "value_consensus",
            "value",
            "unknown",
            reason=i.price_note or "no price in the target's currency",
            section=sec,
        )
    return compare(
        "value_consensus", "value", i.price, i.consensus_target, good="<=", unit="ratio", missing="",
        basis=i.consensus_basis or "consensus", section=sec,
    )  # fmt: skip


def value_checks(i: CheckInputs, lim: CheckLimits) -> list[Check]:
    return [
        multiple_vs_peers(i),
        price_vs_dcf(i),
        price_vs_owner(i),
        peg(i, lim),
        fcf_yield(i, lim),
        consensus(i),
    ]
