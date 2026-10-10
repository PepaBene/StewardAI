"""Selectable X-Ray views (plan M5.1, M5.5).

Every view is the same leaves grouped by a different question, so every view adds up to the
whole portfolio, cash and unknown included (`total_weight`, `reconciles`). Each bucket names its
largest contributors and how its classification was obtained (reported, inferred, owner-assigned).
Unknown weight is its own bucket and is never spread over the known ones.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from steward.fundresearch.analysis import SIZE_BANDS, StyleLine, classify_style
from steward.funds.aggregate import SPECIAL_LABELS
from steward.lookthrough.classify import country_name
from steward.lookthrough.read import CASH, FUND_CASH, NOT_LOOKED, UNCLASSIFIED, UNKNOWN_FUND
from steward.xray.leaves import (
    Basis,
    IssuerFacts,
    Leaf,
    LeafSet,
    XrayData,
    lookup_key,
    region_for,
)

Dimension = Literal[
    "asset_class",
    "sector",
    "industry",
    "country",
    "domicile",
    "listing",
    "region",
    "currency",
    "issuer",
    "fund",
    "style",
    "size",
    "fees",
    "revenue_country",
    "commodity",
    "trade_product",
]
DIMENSIONS: tuple[Dimension, ...] = (
    "asset_class",
    "sector",
    "industry",
    "country",
    "domicile",
    "listing",
    "region",
    "currency",
    "issuer",
    "fund",
    "style",
    "size",
    "fees",
    "revenue_country",
    "commodity",
    "trade_product",
)

OTHER_ISSUERS = "__other_issuers__"
NO_MARKET_CAP = "__no_market_cap__"
ISSUER_LIMIT = 40

DIMENSION_INFO: dict[str, tuple[str, str, str]] = {
    "asset_class": (
        "Asset class",
        "classification",
        "Equities, cash, derivatives and other, as the fund issuers publish them.",
    ),
    "sector": (
        "Sector",
        "classification",
        "GICS sector. A fund's own rows give the sector; a direct stock takes yours, then a fund "
        "row for the same ISIN, then a provider label mapped to GICS.",
    ),
    "industry": (
        "Industry",
        "classification",
        "The data provider's industry label. Fund rows carry none, so only holdings with a stored "
        "provider profile are classified.",
    ),
    "country": (
        "Country",
        "classification",
        "One country per holding, but not one meaning: iShares publishes country of risk, "
        "Vanguard the listing country and a direct stock the instrument list's home market. The "
        "mix is shown, and the view says so when more than one is present.",
    ),
    "domicile": (
        "Domicile",
        "classification",
        "Where the company is based: the data provider's country when stored, otherwise the "
        "country prefix of the ISIN (where the security was registered). Always the same meaning "
        "for every holding, unlike the country view.",
    ),
    "listing": (
        "Listing country",
        "classification",
        "The country of the exchange the data provider names for the security. A stock that "
        "trades on an exchange the app does not map, or has no stored profile, is unclassified.",
    ),
    "region": (
        "Region",
        "classification",
        "Derived from the country above, so it inherits its basis.",
    ),
    "currency": (
        "Trading currency",
        "classification",
        "The currency a holding trades in, not its economic exposure.",
    ),
    "issuer": (
        "Issuer",
        "structure",
        "Securities merged by canonical issuer (an ADR and its ordinary share are one issuer). "
        "The largest issuers are listed; the rest are one bucket.",
    ),
    "fund": (
        "Fund or direct",
        "structure",
        "Where the exposure arrives: direct holdings, each fund, cash. A fund's own unknown "
        "weight stays inside that fund.",
    ),
    "style": (
        "Style proxy",
        "derived",
        "StewardAI's value / blend / growth proxy from earnings yield and price to book. It is not "
        "a Morningstar Style Box.",
    ),
    "size": (
        "Market-cap band",
        "derived",
        "Large, mid and small from fixed EUR thresholds on stored market caps.",
    ),
    "fees": (
        "Fees",
        "derived",
        "Weight by the ongoing charge of the fund it sits in; direct holdings carry no fund fee "
        "and an unknown charge is not assumed to be zero.",
    ),
    "revenue_country": (
        "Country of revenue",
        "owner data",
        "Where a company sells, only for companies with a reported geography whose members are "
        "country names, or whose geography you mapped to countries. Everything else is shown as "
        "having no revenue data.",
    ),
    "commodity": (
        "Commodity exposure",
        "owner data",
        "Only for companies whose segments you mapped to a commodity.",
    ),
    "trade_product": (
        "Trade product",
        "owner data",
        "Only for companies whose segments you mapped to a trade product (HS code).",
    ),
}

ASSET_CLASS_LABELS = {
    "equity": "Equities",
    "cash": "Cash",
    "derivative": "Derivatives",
    "other": "Other assets (not equity, cash or derivative)",
    "unknown": "Unknown",
}
COUNTRY_KIND_LABELS = {
    "country_of_risk": "country of risk",
    "listing_country": "listing country",
    "home_market": "home market (instrument list)",
    "owner": "your own entry",
    "unstated": "basis not stated",
}
FEE_BANDS: tuple[tuple[str, str, Decimal | None], ...] = (
    ("fee:lt0.10", "Under 0.10% a year", Decimal("0.001")),
    ("fee:0.10-0.25", "0.10% to 0.25%", Decimal("0.0025")),
    ("fee:0.25-0.50", "0.25% to 0.50%", Decimal("0.005")),
    ("fee:gte0.50", "0.50% or more", None),
)
FEE_DIRECT = "fee:direct"
FEE_UNKNOWN = "fee:unknown"
FEE_LABELS = {
    FEE_DIRECT: "No fund fee (direct holdings)",
    FEE_UNKNOWN: "Ongoing charge unknown (not assumed zero)",
    **{k: label for k, label, _ in FEE_BANDS},
}
STYLE_LABELS = {
    "value": "Value-leaning",
    "blend": "Blend",
    "growth": "Growth-leaning",
    "no_earnings": "Negative earnings",
    "not_covered": "Not classified (no statements)",
}


class XrayContributor(BaseModel):
    key: str
    label: str
    weight: float = Field(description="Share of total portfolio value.")
    share_of_bucket: float | None


class XrayBucket(BaseModel):
    key: str
    label: str
    kind: Literal[
        "classified",
        "cash",
        "fund_cash",
        "not_looked_through",
        "unknown_fund",
        "unclassified",
        "other",
    ]
    weight: float
    value_base: float
    direct_weight: float
    fund_weight: float
    contributors: list[XrayContributor]
    basis: dict[str, float] = Field(
        description="Weight in this bucket by how it was classified: reported, inferred, owner."
    )


class XrayView(BaseModel):
    dimension: Dimension
    label: str
    group: str
    description: str
    buckets: list[XrayBucket]
    total_weight: float
    reconciles: bool = Field(description="Buckets add up to the whole portfolio within tolerance.")
    classified_weight: float = Field(description="Weight in buckets that carry a real label.")
    equity_weight: float
    bases: dict[str, float] = Field(description="Classified weight by basis across the view.")
    country_kinds: dict[str, float] | None = Field(
        default=None, description="Country view only: weight by what kind of country it is."
    )
    mixed_basis: bool = False
    warnings: list[str]


@dataclass(frozen=True)
class Piece:
    key: str
    share: float
    basis: Basis
    detail: str | None = None


@dataclass
class ViewContext:
    data: XrayData
    leafset: LeafSet
    facts: dict[str, IssuerFacts] = field(default_factory=dict)  # by issuer key


_SPECIAL_KINDS = {
    CASH: "cash",
    "cash": "cash",  # the asset-class and fund views key cash by its plain name
    FUND_CASH: "fund_cash",
    NOT_LOOKED: "not_looked_through",
    UNKNOWN_FUND: "unknown_fund",
    UNCLASSIFIED: "unclassified",
    OTHER_ISSUERS: "other",
}


def bucket_kind(key: str) -> str:
    return _SPECIAL_KINDS.get(key, "classified")


def label_for(dimension: str, key: str) -> str:
    if key in SPECIAL_LABELS:
        return SPECIAL_LABELS[key]
    if key == OTHER_ISSUERS:
        return "All other issuers"
    if key == NO_MARKET_CAP:
        return "No market cap available"
    if dimension in ("country", "domicile", "listing", "revenue_country"):
        return country_name(key) or key
    if dimension == "asset_class":
        return ASSET_CLASS_LABELS.get(key, key)
    if dimension == "fees":
        return FEE_LABELS.get(key, key)
    if dimension == "style":
        return STYLE_LABELS.get(key, key)
    return key


def _fee_key(ratio: Decimal | None, kind: str) -> str:
    if kind == "direct":
        return FEE_DIRECT
    if ratio is None:
        return FEE_UNKNOWN
    for key, _label, ceiling in FEE_BANDS:
        if ceiling is None or ratio < ceiling:
            return key
    return FEE_BANDS[-1][0]


def _size_key(facts: IssuerFacts | None) -> str:
    if facts is None or facts.market_cap_eur is None:
        return NO_MARKET_CAP
    for name, floor in SIZE_BANDS:
        if floor is not None and facts.market_cap_eur >= floor:
            return name
    return SIZE_BANDS[-1][0]


def _style_key(leaf: Leaf, facts: IssuerFacts | None) -> str:
    if facts is None or not facts.covered:
        return "not_covered"
    return classify_style(
        StyleLine(
            key=leaf.issuer_key or "",
            label=leaf.issuer_label or "",
            weight=Decimal(0),
            sector=leaf.sector,
            earnings_yield=facts.earnings_yield,
            price_to_book=facts.price_to_book,
            covered=True,
        )
    )


def _special(leaf: Leaf) -> list[Piece]:
    return [Piece(leaf.special or UNCLASSIFIED, 1.0, "none")]


def pieces(leaf: Leaf, dimension: Dimension, ctx: ViewContext) -> list[Piece]:
    """How one leaf spreads over the buckets of a dimension; the shares add up to 1."""
    if dimension == "asset_class":
        if leaf.special in (NOT_LOOKED, UNKNOWN_FUND):
            return _special(leaf)
        return [Piece(leaf.asset_class, 1.0, "reported")]
    if dimension == "fund":
        return [Piece(leaf.channel_key, 1.0, "reported")]
    if dimension == "fees":
        if leaf.channel_kind == "cash":
            return _special(leaf)
        channel = ctx.leafset.channels.get(leaf.channel_key)
        ratio = channel.expense_ratio if channel else None
        return [Piece(_fee_key(ratio, leaf.channel_kind), 1.0, "reported")]
    if not leaf.is_equity:
        return _special(leaf)
    if dimension == "issuer":
        return [Piece(leaf.issuer_key or UNCLASSIFIED, 1.0, "reported")]
    facts = ctx.facts.get(leaf.issuer_key or "")
    if dimension == "sector":
        return [Piece(leaf.sector or UNCLASSIFIED, 1.0, leaf.sector_basis)]
    if dimension == "industry":
        return [Piece(leaf.industry or UNCLASSIFIED, 1.0, leaf.industry_basis)]
    if dimension == "country":
        return [
            Piece(
                leaf.country or UNCLASSIFIED,
                1.0,
                leaf.country_basis,
                leaf.country_kind if leaf.country else None,
            )
        ]
    if dimension == "domicile":
        return [Piece(leaf.domicile or UNCLASSIFIED, 1.0, leaf.domicile_basis)]
    if dimension == "listing":
        return [Piece(leaf.listing or UNCLASSIFIED, 1.0, leaf.listing_basis)]
    if dimension == "region":
        region = region_for(leaf.country)
        return [Piece(region or UNCLASSIFIED, 1.0, "inferred" if region else "none")]
    if dimension == "currency":
        return [Piece(leaf.currency or UNCLASSIFIED, 1.0, leaf.currency_basis)]
    if dimension == "style":
        return [Piece(_style_key(leaf, facts), 1.0, "inferred")]
    if dimension == "size":
        key = _size_key(facts)
        return [Piece(key, 1.0, "inferred" if key != NO_MARKET_CAP else "none")]
    # revenue and trade dimensions: only where the company has data
    slices = (lookup_key(ctx.data.revenue, leaf.isin, leaf.ticker) or {}).get(dimension, [])
    if not slices:
        return [Piece(UNCLASSIFIED, 1.0, "none")]
    out = [Piece(s.value, s.share, s.basis, "owner mapping" if s.basis == "owner" else "reported")
           for s in slices if s.share > 0]  # fmt: skip
    reported = sum(p.share for p in out)
    if reported > 1.0 + 1e-9:
        # Slices that add to more than the whole would count a leaf above its weight: scale them
        # back to a whole rather than let the view over-count.
        out = [replace(p, share=p.share / reported) for p in out]
        reported = 1.0
    rest = 1.0 - reported
    if rest > 1e-9:
        out.append(Piece(UNCLASSIFIED, rest, "none"))
    return out


@dataclass
class _Acc:
    label: str | None = None
    weight: float = 0.0
    direct: float = 0.0
    fund: float = 0.0
    basis: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    contrib: dict[tuple[str, str], float] = field(default_factory=lambda: defaultdict(float))


def _contributor(leaf: Leaf, dimension: Dimension) -> tuple[str, str]:
    if dimension == "issuer":
        return leaf.channel_key, leaf.channel_label
    if leaf.special:
        return leaf.channel_key, SPECIAL_LABELS.get(leaf.special, leaf.channel_label)
    return leaf.issuer_key or leaf.channel_key, leaf.issuer_label or leaf.channel_label


def build_view(dimension: Dimension, ctx: ViewContext, *, contributors: int = 6) -> XrayView:
    leafset = ctx.leafset
    accs: dict[str, _Acc] = defaultdict(_Acc)
    country_kinds: dict[str, float] = defaultdict(float)
    equity_weight = 0.0
    for leaf in leafset.leaves:
        if leaf.is_equity:
            equity_weight += leaf.weight
        for piece in pieces(leaf, dimension, ctx):
            w = leaf.weight * piece.share
            if w == 0:
                continue
            acc = accs[piece.key]
            if acc.label is None and dimension in ("issuer", "fund"):
                acc.label = leaf.issuer_label if dimension == "issuer" else leaf.channel_label
            acc.weight += w
            if leaf.channel_kind == "fund":
                acc.fund += w
            else:
                acc.direct += w
            if bucket_kind(piece.key) == "classified" and piece.basis != "none":
                acc.basis[piece.basis] += w
            if dimension == "country" and piece.detail:
                country_kinds[piece.detail] += w
            acc.contrib[_contributor(leaf, dimension)] += w

    if dimension == "issuer" and len(accs) > ISSUER_LIMIT + 1:
        ranked = sorted(
            (k for k in accs if bucket_kind(k) == "classified"), key=lambda k: -accs[k].weight
        )
        rest = _Acc()
        for key in ranked[ISSUER_LIMIT:]:
            gone = accs.pop(key)
            rest.weight += gone.weight
            rest.direct += gone.direct
            rest.fund += gone.fund
            for basis_name, v in gone.basis.items():
                rest.basis[basis_name] += v
            for c, v in gone.contrib.items():
                rest.contrib[c] += v
        accs[OTHER_ISSUERS] = rest

    total = leafset.total
    buckets: list[XrayBucket] = []
    for key, acc in accs.items():
        if abs(acc.weight) < 1e-12:
            continue
        top = sorted(acc.contrib.items(), key=lambda kv: -abs(kv[1]))[:contributors]
        buckets.append(
            XrayBucket(
                key=key,
                label=label_for(dimension, key)
                if key in SPECIAL_LABELS or key == OTHER_ISSUERS
                else (acc.label or label_for(dimension, key)),
                kind=bucket_kind(key),
                weight=acc.weight,
                value_base=acc.weight * total,
                direct_weight=acc.direct,
                fund_weight=acc.fund,
                contributors=[
                    XrayContributor(
                        key=ck,
                        label=cl,
                        weight=cw,
                        share_of_bucket=cw / acc.weight if acc.weight else None,
                    )
                    for (ck, cl), cw in top
                ],
                basis=dict(acc.basis),
            )
        )
    order = {"classified": 0, "other": 1, "unclassified": 2}
    buckets.sort(key=lambda b: (order.get(b.kind, 3), -b.weight))
    sum_w = sum(b.weight for b in buckets)
    classified = sum(b.weight for b in buckets if b.kind in ("classified", "other"))
    bases: dict[str, float] = defaultdict(float)
    for bucket in buckets:
        for basis, w in bucket.basis.items():
            bases[basis] += w
    warnings: list[str] = []
    mixed = False
    kinds: dict[str, float] | None = None
    if dimension == "country":
        kinds = {k: v for k, v in country_kinds.items() if v > 1e-9}
        material = {k: v for k, v in kinds.items() if v >= 0.001}
        if len(material) > 1:
            mixed = True
            parts = ", ".join(
                f"{COUNTRY_KIND_LABELS.get(k, k)} {v:.1%}"
                for k, v in sorted(material.items(), key=lambda kv: -kv[1])
            )
            warnings.append(
                f"These country labels are not one thing: {parts}. A fund's country of risk and "
                "a listing country can differ for the same company, so read each bucket with its "
                "contributors rather than as a single country measure."
            )
    if dimension == "region":
        warnings.append("Region is derived from the country view and shares its basis.")
    if dimension in ("revenue_country", "commodity", "trade_product"):
        covered = classified
        if equity_weight > 0 and covered < equity_weight - 1e-9:
            warnings.append(
                f"Only {covered / equity_weight:.1%} of equity weight has this data; the rest is "
                "shown as having none, not estimated."
            )
    if dimension == "style":
        warnings.append("A StewardAI proxy from stated thresholds, not a Morningstar Style Box.")
    if dimension in ("industry", "sector"):
        unclassified = sum(b.weight for b in buckets if b.kind == "unclassified")
        if unclassified > 0:
            warnings.append(f"{unclassified:.1%} of the portfolio has no {dimension} label.")
    tol = ctx.data.tolerance
    info = DIMENSION_INFO[dimension]
    return XrayView(
        dimension=dimension,
        label=info[0],
        group=info[1],
        description=info[2],
        buckets=buckets,
        total_weight=sum_w,
        reconciles=abs(sum_w - (1.0 + leafset.over_reported)) <= tol,
        classified_weight=classified,
        equity_weight=equity_weight,
        bases=dict(bases),
        country_kinds=kinds,
        mixed_basis=mixed,
        warnings=warnings,
    )


def build_views(ctx: ViewContext) -> list[XrayView]:
    return [build_view(d, ctx) for d in DIMENSIONS]
