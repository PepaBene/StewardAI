# ruff: noqa: E501
"""Diversification pictures (insight I11.4-I11.6): one sankey from sector to industry to holding,
revenue by country of sale with its coverage, and the concentration verdict in one sentence.

Pure over the loaded X-Ray leaves, so the figures agree with the X-Ray views: every weight is a
share of total portfolio value, funds count through their looked-through rows, and what cannot
be classified (cash, funds not looked through, rows with no label) is a named node, never spread
over the known ones.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, Field

from steward.analytics.versions import version
from steward.funds.aggregate import SPECIAL_LABELS
from steward.xray.leaves import Leaf, LeafSet
from steward.xray.metrics import XrayConcentration
from steward.xray.views import XrayView

SECTOR_UNKNOWN = "Sector not stated"
INDUSTRY_UNKNOWN = "Industry not stated"
OTHER_HOLDINGS = "Smaller holdings"
HOLDINGS_PER_INDUSTRY = 6
MIN_HOLDING_WEIGHT = 0.005  # below 0.5 % of the portfolio a holding joins "Smaller holdings"
MAP_MIN_COUNTRIES = 6  # plan I11.5: no map unless six or more countries

# Effective number of holdings (1 / HHI on identified equity) bands for the verdict.
CONCENTRATED_BELOW = 10.0
BROAD_FROM = 25.0


class SankeyNode(BaseModel):
    id: str
    label: str
    column: int = Field(description="0 sector, 1 industry, 2 holding.")
    weight: float
    kind: Literal["classified", "unclassified", "residual", "other"]


class SankeyLink(BaseModel):
    source: str
    target: str
    weight: float


class GeographyRow(BaseModel):
    country: str
    label: str
    weight: float = Field(description="Share of total portfolio value earned in this country.")
    share_of_known: float
    basis: dict[str, float]


class Geography(BaseModel):
    rows: list[GeographyRow]
    known_weight: float = Field(description="Equity weight whose revenue geography is known.")
    equity_weight: float
    coverage: float | None = Field(description="known_weight / equity_weight.")
    coverage_text: str
    countries: int
    map_allowed: bool = Field(description=f"At least {MAP_MIN_COUNTRIES} countries are known.")


class ConcentrationVerdict(BaseModel):
    sentence: str
    band: Literal["concentrated", "moderate", "broad", "unknown"]
    top3_share: float | None = Field(description="Three largest issuers, share of total value.")
    effective_n: float | None
    hhi: float | None
    largest: str | None
    largest_weight: float | None
    holdings: int
    basis: str


class Diversification(BaseModel):
    as_of: str
    nodes: list[SankeyNode]
    links: list[SankeyLink]
    residual_weight: float = Field(
        description="Cash, funds not looked through and unknown fund weight: outside the sankey."
    )
    residual: list[SankeyNode]
    geography: Geography
    concentration: ConcentrationVerdict
    method_version: str
    notes: list[str]


def _holding_label(x: Leaf) -> str:
    return x.issuer_label or x.name or x.ticker or x.isin or "Unnamed holding"


def sankey(leafset: LeafSet) -> tuple[list[SankeyNode], list[SankeyLink], list[SankeyNode]]:
    """Equity leaves grouped sector -> industry -> holding (issuer). Within each industry the
    largest holdings get their own node; the rest are one "Smaller holdings" node per industry, so
    the picture stays readable and the weights still add up."""
    sector_w: dict[str, float] = defaultdict(float)
    industry_w: dict[tuple[str, str], float] = defaultdict(float)
    holding_w: dict[tuple[str, str, str], float] = defaultdict(float)
    residual: dict[str, float] = defaultdict(float)
    for x in leafset.leaves:
        if x.weight <= 0:
            continue
        if not x.is_equity:
            residual[x.special or x.asset_class] += x.weight
            continue
        s = x.sector or SECTOR_UNKNOWN
        i = x.industry or INDUSTRY_UNKNOWN
        sector_w[s] += x.weight
        industry_w[(s, i)] += x.weight
        holding_w[(s, i, x.issuer_key or x.isin or _holding_label(x))] += x.weight
    labels = {
        (x.sector or SECTOR_UNKNOWN, x.industry or INDUSTRY_UNKNOWN, x.issuer_key or x.isin or _holding_label(x)): _holding_label(x)
        for x in leafset.leaves if x.is_equity
    }  # fmt: skip
    nodes: list[SankeyNode] = []
    links: list[SankeyLink] = []
    for s, w in sorted(sector_w.items(), key=lambda kv: -kv[1]):
        nodes.append(SankeyNode(id=f"s:{s}", label=s, column=0, weight=w,
                                kind="unclassified" if s == SECTOR_UNKNOWN else "classified"))  # fmt: skip
    for (s, i), w in sorted(industry_w.items(), key=lambda kv: -kv[1]):
        iid = f"i:{s}|{i}"
        nodes.append(SankeyNode(id=iid, label=i, column=1, weight=w,
                                kind="unclassified" if i == INDUSTRY_UNKNOWN else "classified"))  # fmt: skip
        links.append(SankeyLink(source=f"s:{s}", target=iid, weight=w))
        members = sorted(
            ((k[2], hw) for k, hw in holding_w.items() if k[0] == s and k[1] == i),
            key=lambda kv: -kv[1],
        )
        shown = [m for m in members[:HOLDINGS_PER_INDUSTRY] if m[1] >= MIN_HOLDING_WEIGHT]
        rest = sum(hw for k, hw in members if (k, hw) not in shown)
        for key, hw in shown:
            hid = f"h:{s}|{i}|{key}"
            nodes.append(SankeyNode(id=hid, label=labels.get((s, i, key), key), column=2,
                                    weight=hw, kind="classified"))  # fmt: skip
            links.append(SankeyLink(source=iid, target=hid, weight=hw))
        if rest > 0:
            count = len(members) - len(shown)
            oid = f"h:{s}|{i}|__other__"
            nodes.append(SankeyNode(id=oid, label=f"{OTHER_HOLDINGS} ({count})", column=2,
                                    weight=rest, kind="other"))  # fmt: skip
            links.append(SankeyLink(source=iid, target=oid, weight=rest))
    residual_nodes = [
        SankeyNode(id=f"r:{k}", label=SPECIAL_LABELS.get(k, k.capitalize()), column=0, weight=w,
                   kind="residual")
        for k, w in sorted(residual.items(), key=lambda kv: -kv[1])
    ]  # fmt: skip
    return nodes, links, residual_nodes


def geography(view: XrayView | None) -> Geography:
    if view is None:
        return Geography(rows=[], known_weight=0.0, equity_weight=0.0, coverage=None,
                         coverage_text="No revenue geography is stored for any holding.",
                         countries=0, map_allowed=False)  # fmt: skip
    known = [b for b in view.buckets if b.kind == "classified"]
    known_w = sum(b.weight for b in known)
    rows = [
        GeographyRow(country=b.key, label=b.label, weight=b.weight,
                     share_of_known=b.weight / known_w if known_w else 0.0, basis=b.basis)
        for b in sorted(known, key=lambda b: -b.weight)
    ]  # fmt: skip
    eq = view.equity_weight
    cov = known_w / eq if eq > 0 else None
    text = (
        f"Revenue geography known for {round(cov * 100)}% of the equity you hold"
        f" ({round(known_w * 100, 1)}% of the portfolio)."
        if cov is not None
        else "No equity holdings to place."
    )
    return Geography(rows=rows, known_weight=known_w, equity_weight=eq, coverage=cov,
                     coverage_text=text, countries=len(rows),
                     map_allowed=len(rows) >= MAP_MIN_COUNTRIES)  # fmt: skip


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%" if x >= 0.095 else f"{x * 100:.1f}%"


def verdict(issuer: XrayConcentration | None) -> ConcentrationVerdict:
    """One sentence from the issuer level: the three largest issuers' share of the whole
    portfolio, the effective number of holdings and the largest name."""
    if issuer is None or issuer.effective_n is None or not issuer.top_items:
        return ConcentrationVerdict(
            sentence="Concentration cannot be measured: no identified equity holdings.",
            band="unknown", top3_share=None, effective_n=None, hhi=None, largest=None,
            largest_weight=None, holdings=0, basis="issuer",
        )  # fmt: skip
    top3 = sum(i.weight for i in issuer.top_items[:3])
    n = issuer.effective_n
    band: Literal["concentrated", "moderate", "broad"] = (
        "concentrated" if n < CONCENTRATED_BELOW else "broad" if n >= BROAD_FROM else "moderate"
    )
    largest = issuer.largest or issuer.top_items[0]
    words = {
        "concentrated": "concentrated",
        "moderate": "moderately spread",
        "broad": "broadly spread",
    }
    sentence = (
        f"Your three largest issuers are {_pct(top3)} of the portfolio and it is as {words[band]} as "
        f"{n:.0f} equal holdings; the largest is {largest.label} at {_pct(largest.weight)}."
    )
    if issuer.unknown_weight > 0.005:
        sentence += f" {_pct(issuer.unknown_weight)} sits in funds whose holdings are not known."
    return ConcentrationVerdict(
        sentence=sentence, band=band, top3_share=top3, effective_n=n, hhi=issuer.hhi,
        largest=largest.label, largest_weight=largest.weight, holdings=issuer.items,
        basis=issuer.basis,
    )  # fmt: skip


def build(
    as_of: str,
    leafset: LeafSet,
    revenue_view: XrayView | None,
    levels: list[XrayConcentration],
    notes: list[str],
) -> Diversification:
    nodes, links, residual = sankey(leafset)
    issuer = next((c for c in levels if c.level == "issuer"), None)
    return Diversification(
        as_of=as_of, nodes=nodes, links=links,
        residual_weight=sum(r.weight for r in residual), residual=residual,
        geography=geography(revenue_view), concentration=verdict(issuer),
        method_version=version("diversification"),
        notes=[
            "Sector is GICS-style; industry is the data provider's label, which fund rows do not "
            "carry, so a fund's holdings show as industry not stated.",
            *notes,
        ],
    )  # fmt: skip
