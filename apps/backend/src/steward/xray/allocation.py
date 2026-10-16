"""Allocation views over the X-Ray leaf set: the Lorenz curve of the equity holdings (with the HHI,
the effective number of holdings and the Gini coefficient) and the full sector -> industry ->
holding tree with weights. Holdings are issuers, looked through funds, as everywhere in the X-Ray.
Nothing here is stored; weights are shares of the whole portfolio, shares of equity are given
beside them."""

from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel, Field

from steward.xray.diversification import INDUSTRY_UNKNOWN, SECTOR_UNKNOWN
from steward.xray.leaves import Leaf, LeafSet


class LorenzPoint(BaseModel):
    holdings_share: float = Field(description="Cumulative share of holdings, smallest first.")
    weight_share: float = Field(description="Cumulative share of equity value they make up.")


class LorenzCurve(BaseModel):
    points: list[LorenzPoint] = Field(description="Starts at (0, 0), ends at (1, 1).")
    holdings: int
    hhi: float | None = Field(description="Sum of squared shares of the equity, 0 to 1.")
    effective_n: float | None = Field(description="1 / HHI: equally sized holdings that match it.")
    gini: float | None = Field(description="0 equal weights, towards 1 one holding dominates.")
    top_weight_share: float | None = Field(description="Share of equity in the largest holding.")
    reason: str | None = None


class AllocationNode(BaseModel):
    id: str
    label: str
    level: str = Field(description="sector, industry or holding.")
    weight: float = Field(description="Share of the whole portfolio.")
    share_of_equity: float | None
    unclassified: bool = False
    children: list[AllocationNode] = Field(default_factory=list)


class Allocation(BaseModel):
    as_of: str
    equity_weight: float = Field(description="Share of the portfolio that is equity.")
    lorenz: LorenzCurve
    tree: list[AllocationNode] = Field(description="Sectors, largest first.")
    notes: list[str]
    reasons: list[str] = Field(default_factory=list)


AllocationNode.model_rebuild()


def _key(x: Leaf) -> str:
    return x.issuer_key or x.isin or x.ticker or x.name or "unnamed"


def _label(x: Leaf) -> str:
    return x.issuer_label or x.name or x.ticker or x.isin or "Unnamed holding"


def lorenz_curve(weights: list[float]) -> LorenzCurve:
    """Lorenz curve, HHI, effective count and Gini of positive weights."""
    w = sorted(v for v in weights if v > 0)
    total = sum(w)
    if not w or total <= 0:
        return LorenzCurve(
            points=[], holdings=0, hhi=None, effective_n=None, gini=None, top_weight_share=None,
            reason="no equity holdings with a weight",
        )  # fmt: skip
    n = len(w)
    shares = [v / total for v in w]
    points = [LorenzPoint(holdings_share=0.0, weight_share=0.0)]
    cum = 0.0
    for i, s in enumerate(shares, start=1):
        cum += s
        points.append(LorenzPoint(holdings_share=i / n, weight_share=min(cum, 1.0)))
    points[-1] = LorenzPoint(holdings_share=1.0, weight_share=1.0)
    hhi = sum(s * s for s in shares)
    area = sum(
        (b.holdings_share - a.holdings_share) * (a.weight_share + b.weight_share) / 2
        for a, b in zip(points, points[1:], strict=False)
    )
    return LorenzCurve(
        points=points, holdings=n, hhi=hhi, effective_n=1 / hhi, gini=max(0.0, 1 - 2 * area),
        top_weight_share=shares[-1],
    )  # fmt: skip


def build_tree(leaves: list[Leaf], equity_total: float) -> list[AllocationNode]:
    """Equity leaves as sector -> industry -> holding (one node per issuer), largest first."""
    by_sector: dict[str, dict[str, dict[str, tuple[str, float]]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for x in leaves:
        if x.weight <= 0 or not x.is_equity:
            continue
        s = x.sector or SECTOR_UNKNOWN
        i = x.industry or INDUSTRY_UNKNOWN
        k = _key(x)
        label, w = by_sector[s][i].get(k, (_label(x), 0.0))
        by_sector[s][i][k] = (label, w + x.weight)

    def share(w: float) -> float | None:
        return w / equity_total if equity_total > 0 else None

    tree: list[AllocationNode] = []
    for s, industries in by_sector.items():
        ind_nodes: list[AllocationNode] = []
        for i, holdings in industries.items():
            hold_nodes = [
                AllocationNode(id=f"h:{s}|{i}|{k}", label=lab, level="holding", weight=w,
                               share_of_equity=share(w))
                for k, (lab, w) in sorted(holdings.items(), key=lambda kv: -kv[1][1])
            ]  # fmt: skip
            iw = sum(h.weight for h in hold_nodes)
            ind_nodes.append(AllocationNode(
                id=f"i:{s}|{i}", label=i, level="industry", weight=iw, share_of_equity=share(iw),
                unclassified=i == INDUSTRY_UNKNOWN, children=hold_nodes,
            ))  # fmt: skip
        ind_nodes.sort(key=lambda n: -n.weight)
        sw = sum(n.weight for n in ind_nodes)
        tree.append(AllocationNode(
            id=f"s:{s}", label=s, level="sector", weight=sw, share_of_equity=share(sw),
            unclassified=s == SECTOR_UNKNOWN, children=ind_nodes,
        ))  # fmt: skip
    tree.sort(key=lambda n: -n.weight)
    return tree


def build(as_of: str, leafset: LeafSet, notes: list[str]) -> Allocation:
    equity = [x for x in leafset.leaves if x.is_equity and x.weight > 0]
    equity_total = sum(x.weight for x in equity)
    issuers: dict[str, float] = defaultdict(float)
    for x in equity:
        issuers[_key(x)] += x.weight
    reasons = [] if equity else ["no equity holdings in the portfolio on this date"]
    return Allocation(
        as_of=as_of, equity_weight=equity_total, lorenz=lorenz_curve(list(issuers.values())),
        tree=build_tree(equity, equity_total), notes=notes, reasons=reasons,
    )  # fmt: skip
