"""Evidence for the investment case that the app computes itself (plan M2.6).

Concentration (largest segment, geography and customer) and capital allocation (cash returned,
share count, debt, free cash flow per share) are observations from filed statements and
confirmed disclosures. They are shown beside the owner's cited items and never replace them;
an unknown input leaves the observation empty with its reason, never a zero.
"""

# ruff: noqa: E501
from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from steward.analytics.contract import rounded
from steward.company.evidence import AllocationYear, CaseView
from steward.company.segments import SegmentAnalysis

ONE = Decimal(1)
ZERO = Decimal(0)
WINDOW = 3  # fiscal years


class Observed(BaseModel):
    kind: str = Field(description="concentration or capital_allocation")
    id: str
    label: str
    value: Decimal | None
    unit: str
    period: str | None = None
    detail: str = Field(description="How it was computed and from what.")
    reason: str | None = None
    source: str


class CaseWithObserved(CaseView):
    observed: list[Observed] = Field(
        default_factory=list,
        description="Figures the app computed from filings and disclosures, kept apart from your items.",
    )
    observed_note: str = (
        "Observed figures are computed by the app from filed statements and confirmed disclosures. "
        "They are evidence to cite, not a verdict; an empty one says what is missing."
    )


def _share_row(analysis: SegmentAnalysis, kind: str, label: str, obs_id: str) -> Observed:
    b = next((x for x in analysis.breakdowns if x.kind == kind), None)
    if b is None:
        return Observed(
            kind="concentration",
            id=obs_id,
            label=label,
            value=None,
            unit="fraction",
            detail="Largest operating member's share of operating revenue before eliminations.",
            reason=f"no {kind} revenue breakdown is stored",
            source="filed segment facts",
        )
    members = [m for m in b.members if m.role == "operating" and m.latest_share is not None]
    if not members:
        return Observed(
            kind="concentration",
            id=obs_id,
            label=label,
            value=None,
            unit="fraction",
            period=b.latest_period,
            detail="Largest operating member's share of operating revenue before eliminations.",
            reason="no member has a reported latest value",
            source="filed segment facts",
        )
    top = max(members, key=lambda m: m.latest_share or ZERO)
    return Observed(
        kind="concentration",
        id=obs_id,
        label=label,
        value=top.latest_share,
        unit="fraction",
        period=b.latest_period,
        detail=f"{top.label}: its share of operating revenue before eliminations.",
        source="filed segment facts",
    )


def _effective_members(analysis: SegmentAnalysis) -> Observed:
    b = next((x for x in analysis.breakdowns if x.kind == "segment"), None)
    shares = (
        [m.latest_share for m in b.members if m.role == "operating" and m.latest_share] if b else []
    )
    hhi = sum((s * s for s in shares), ZERO)
    return Observed(
        kind="concentration",
        id="effective_segments",
        label="Effective number of segments",
        value=rounded(ONE / hhi, "multiple") if hhi > 0 else None,
        unit="multiple",
        period=b.latest_period if b else None,
        detail="One over the sum of squared segment shares: 3.0 means revenue is as spread as three equal segments.",
        reason=None if hhi > 0 else "no segment shares are stored",
        source="filed segment facts",
    )


def _customer(concentration: Sequence[dict[str, Any]]) -> Observed:
    row = next((r for r in concentration if r.get("relationship") == "customer"), None)
    if row is None:
        return Observed(
            kind="concentration",
            id="top_customer",
            label="Largest disclosed customer",
            value=None,
            unit="fraction",
            detail="Share of revenue the company itself discloses for one customer.",
            reason="no confirmed customer disclosure with a reported share",
            source="relationship disclosures",
        )
    name = row.get("counterparty_name") or "an unnamed customer"
    basis = f" ({row['share_basis']})" if row.get("share_basis") else ""
    return Observed(
        kind="concentration",
        id="top_customer",
        label="Largest disclosed customer",
        value=row["reported_share"],
        unit="fraction",
        period=row.get("fiscal_period"),
        detail=f"{name}{basis}, as the company discloses it.",
        source=row.get("source_url") or "relationship disclosures",
    )


def _window(years: Sequence[AllocationYear]) -> list[AllocationYear]:
    return list(years)[-WINDOW:]


def capital_observations(years: Sequence[AllocationYear]) -> list[Observed]:
    win = _window(years)
    span = f"{win[0].label} to {win[-1].label}" if win else None
    out: list[Observed] = []

    full = [
        y for y in win if y.fcf is not None and (y.dividends is not None or y.buybacks is not None)
    ]
    fcf_total = sum((y.fcf or ZERO for y in full), ZERO)
    returned = sum(((y.dividends or ZERO) + (y.buybacks or ZERO) for y in full), ZERO)
    out.append(
        Observed(
            kind="capital_allocation",
            id="cash_returned",
            label="Free cash flow returned to shareholders",
            value=rounded(returned / fcf_total, "fraction") if full and fcf_total > 0 else None,
            unit="fraction",
            period=span,
            detail=f"Dividends and repurchases paid over free cash flow, summed over {len(full)} of {len(win)} years with both figures.",
            reason=None
            if full and fcf_total > 0
            else "needs free cash flow above zero and a dividend or repurchase figure",
            source="filed cash-flow statements",
        )
    )

    shares = [y for y in win if y.share_count]
    change = (
        rounded(shares[-1].share_count / shares[0].share_count - ONE, "fraction")  # type: ignore[operator]
        if len(shares) >= 2 and shares[0].share_count
        else None
    )
    out.append(
        Observed(
            kind="capital_allocation",
            id="share_count_change",
            label="Change in weighted share count",
            value=change,
            unit="fraction",
            period=f"{shares[0].label} to {shares[-1].label}" if len(shares) >= 2 else span,
            detail="Last against first weighted diluted (or basic) share count in the window; a fall is net buying back.",
            reason=None if change is not None else "needs share counts in at least two years",
            source="filed income statements",
        )
    )

    debt = [y for y in win if y.net_debt_change is not None]
    out.append(
        Observed(
            kind="capital_allocation",
            id="net_debt_issued",
            label="Net debt issued",
            value=sum((y.net_debt_change or ZERO for y in debt), ZERO) if debt else None,
            unit="money",
            period=span,
            detail=f"Debt issued less repaid, summed over {len(debt)} of {len(win)} years; positive means more borrowing.",
            reason=None if debt else "no debt issuance and repayment figures are filed",
            source="filed cash-flow statements",
        )
    )

    deals = [y for y in win if y.acquisitions is not None or y.divestitures is not None]
    net_deals = sum(((y.acquisitions or ZERO) - (y.divestitures or ZERO) for y in deals), ZERO)
    out.append(
        Observed(
            kind="capital_allocation",
            id="net_acquisitions",
            label="Net spent on acquisitions",
            value=net_deals if deals else None,
            unit="money",
            period=span,
            detail=f"Cash paid for businesses less cash received from divestitures, summed over {len(deals)} of {len(win)} years that file either line; positive means net buying.",
            reason=None
            if deals
            else "no acquisition or divestiture cash flow is filed for these years",
            source="filed cash-flow statements",
        )
    )

    fps = [y for y in win if y.fcf_per_share is not None and y.fcf_per_share > 0]
    growth = None
    if len(fps) >= 2:
        n = len(fps) - 1
        ratio = float(fps[-1].fcf_per_share / fps[0].fcf_per_share)  # type: ignore[operator]
        growth = rounded(Decimal(str(ratio ** (1 / n) - 1)), "fraction")
    out.append(
        Observed(
            kind="capital_allocation",
            id="fcf_per_share_growth",
            label="Free cash flow per share, annual growth",
            value=growth,
            unit="fraction",
            period=f"{fps[0].label} to {fps[-1].label}" if len(fps) >= 2 else span,
            detail="Compound annual growth between the first and last year in the window that has positive free cash flow per share.",
            reason=None
            if growth is not None
            else "needs positive free cash flow per share in at least two years",
            source="filed statements",
        )
    )
    return out


def concentration_observations(
    analysis: SegmentAnalysis, concentration: Sequence[dict[str, Any]]
) -> list[Observed]:
    return [
        _share_row(analysis, "segment", "Largest segment", "top_segment"),
        _share_row(analysis, "geographic", "Largest geography", "top_geography"),
        _share_row(analysis, "product", "Largest product line", "top_product"),
        _effective_members(analysis),
        _customer(concentration),
    ]
