"""Stock Intersection (plan M5.2): the same company reached through several channels.

Built on the issuer-level aggregation (`funds.aggregate`), which merges securities by canonical
issuer and keeps the trail from each issuer to its securities (ADR, ordinary, share classes, by
listing) and from each security to the channel it arrives through (direct, or which fund). This
module adds the question "what if a fund were not there": removing a fund's contribution and
recomputing the issuer concentration. The proceeds are assumed to sit in cash (nothing else
changes); that is a stated assumption, not a recommendation, and it keeps unknown fund weight in
its own bucket.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from steward.funds.aggregate import AggregateReport, FundAsOf, IssuerExposure, Unknown, is_shared


class XrayDrop(BaseModel):
    key: str
    label: str
    before: float
    after: float


class XrayRemoval(BaseModel):
    isin: str
    label: str
    weight: float = Field(description="The fund's share of total portfolio value.")
    issuers_touched: int
    unknown_removed: float = Field(description="Unknown weight that leaves with the fund.")
    hhi_before: float | None
    hhi_after: float | None
    effective_before: float | None
    effective_after: float | None
    top10_before: float | None
    top10_after: float | None
    largest_before: XrayDrop | None
    largest_after: XrayDrop | None
    biggest_drops: list[XrayDrop]
    shared_after: int = Field(description="Issuers still reached through two or more channels.")


class XrayIntersection(BaseModel):
    as_of: str
    total_value: float
    issuer_count: int
    shared_count: int = Field(description="Issuers reached through two or more channels.")
    shared_weight: float = Field(description="Combined weight of those issuers.")
    redundant_weight: float = Field(
        description="Exposure to shared issuers beyond the largest single channel."
    )
    issuers: list[IssuerExposure]
    funds: list[FundAsOf]
    unknown: Unknown | None
    removal: list[XrayRemoval]
    notes: list[str]


def _stats(weights: list[float]) -> tuple[float | None, float | None, float | None]:
    """HHI and effective count on shares of the identified weight, and the top-10 weight."""
    known = sum(w for w in weights if w > 0)
    if known <= 0:
        return None, None, None
    shares = [w / known for w in weights if w > 0]
    hhi = sum(s * s for s in shares)
    return hhi, 1.0 / hhi, sum(sorted((w for w in weights if w > 0), reverse=True)[:10])


def _channel_weight(issuer: IssuerExposure, fund_isin: str) -> float:
    return sum(c.weight for c in issuer.channels if c.kind == "fund" and c.isin == fund_isin)


def intersection(report: AggregateReport, *, limit: int = 100) -> XrayIntersection:
    issuers = report.issuers
    weights = [x.weight for x in issuers]
    hhi0, eff0, top0 = _stats(weights)
    shared = [x for x in issuers if is_shared(x.channels)]
    largest0 = max(issuers, key=lambda x: x.weight, default=None)
    removals: list[XrayRemoval] = []
    for fund in sorted(report.funds, key=lambda f: -f.weight):
        if fund.weight <= 0:
            continue
        after: list[tuple[IssuerExposure, float]] = []
        touched = 0
        for x in issuers:
            cut = _channel_weight(x, fund.isin)
            if cut:
                touched += 1
            after.append((x, x.weight - cut))
        ws = [w for _, w in after]
        hhi1, eff1, top1 = _stats(ws)
        top_after = max(after, key=lambda t: t[1], default=None)
        drops = sorted(
            ((x, x.weight, w) for x, w in after if x.weight - w > 1e-12), key=lambda t: t[2] - t[1]
        )[:8]
        removals.append(
            XrayRemoval(
                isin=fund.isin,
                label=fund.label,
                weight=fund.weight,
                issuers_touched=touched,
                unknown_removed=fund.unknown_weight,
                hhi_before=hhi0,
                hhi_after=hhi1,
                effective_before=eff0,
                effective_after=eff1,
                top10_before=top0,
                top10_after=top1,
                largest_before=XrayDrop(
                    key=largest0.key,
                    label=largest0.label,
                    before=largest0.weight,
                    after=largest0.weight,
                )
                if largest0
                else None,
                largest_after=XrayDrop(
                    key=top_after[0].key,
                    label=top_after[0].label,
                    before=top_after[0].weight,
                    after=top_after[1],
                )
                if top_after
                else None,
                biggest_drops=[
                    XrayDrop(key=x.key, label=x.label, before=b, after=a) for x, b, a in drops
                ],
                shared_after=sum(
                    1
                    for x, _ in after
                    if is_shared(
                        [c for c in x.channels if not (c.kind == "fund" and c.isin == fund.isin)]
                    )
                ),
            )
        )
    ordered = sorted(issuers, key=lambda x: (-is_shared(x.channels), -x.weight))
    notes = [
        "Issuers are merged across securities (an ADR and its ordinary share, share classes); "
        "open an issuer to see each security and the channel it comes through.",
        "Removing a fund assumes its proceeds sit in cash and nothing else changes. Concentration "
        "is measured on the identified issuer weight; unknown fund weight stays unknown.",
        *report.notes,
    ]
    return XrayIntersection(
        as_of=report.as_of.isoformat(),
        total_value=report.total_value,
        issuer_count=len(issuers),
        shared_count=len(shared),
        shared_weight=sum(x.weight for x in shared),
        redundant_weight=sum(x.overlap_weight for x in shared),
        issuers=ordered[:limit],
        funds=report.funds,
        unknown=report.unknown,
        removal=removals,
        notes=notes,
    )
