"""Exposure to a theme or event through every path (plan M6.3).

A theme is a set of selectors: an issuer, a sector or industry, a country (of risk, domicile,
listing or revenue), a currency, a commodity or a trade product. Each equity leaf of the X-Ray is
measured once, whatever number of selectors it matches and whether it is held directly or inside
funds:

- the leaf's exposed share is the largest share any single selector gives it (shares are never
  added across selectors, so a Swiss bank in a European-financials theme is not counted for its
  sector and again for its country);
- every selector that matched is kept as a path, with how the label was obtained;
- where a selector's dimension has no label for the leaf, the leaf may be exposed through it: up
  to that share is reported as unknown, never as zero and never as a match;
- a fund that is not looked through, or the part of a fund its rows do not account for, is
  unknown in full.

Confidence is a stated rule over the unknown weight and the share of the match that rests on an
inferred label, not a probability.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from steward.analytics.versions import version
from steward.lookthrough.read import NOT_LOOKED, UNCLASSIFIED, UNKNOWN_FUND
from steward.xray.views import Dimension, ViewContext, label_for, pieces

SelectorKind = Literal[
    "sector",
    "industry",
    "country",
    "domicile",
    "listing",
    "currency",
    "revenue_country",
    "commodity",
    "trade_product",
    "issuer",
]
Confidence = Literal["high", "medium", "low"]

# Largest share of the portfolio that may be unknown (or rest on inferred labels) for each level.
HIGH_UNKNOWN = 0.05
MEDIUM_UNKNOWN = 0.20
HIGH_INFERRED_SHARE = 0.20


class ThemeSelector(BaseModel):
    kind: SelectorKind
    value: str = Field(
        min_length=1, max_length=120, description="Sector name, ISO code, issuer key…"
    )


class ThemeDefinition(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    selectors: list[ThemeSelector] = Field(min_length=1, max_length=30)
    note: str | None = Field(default=None, max_length=500)


class ThemePath(BaseModel):
    channel: str = Field(description="'Direct holdings' or the fund the exposure comes through.")
    channel_kind: str
    issuer: str
    weight: float = Field(description="Leaf weight, share of total portfolio value.")
    exposed: float = Field(description="Weight counted as exposed (leaf weight x exposed share).")
    selectors: list[str] = Field(description="Every selector that matched, with its label basis.")
    basis: str = Field(description="Weakest label basis among the matching selectors.")


class ThemeIssuer(BaseModel):
    issuer: str
    exposed: float
    direct: float
    via_funds: float
    paths: list[ThemePath]


class ThemeUnknown(BaseModel):
    weight: float
    reasons: dict[str, float] = Field(description="Reason -> unknown weight.")


class ThemeExposure(BaseModel):
    method_version: str
    theme: ThemeDefinition
    exposed_weight: float = Field(description="Known exposure, share of total portfolio value.")
    direct_weight: float
    fund_weight: float
    unknown: ThemeUnknown
    upper_bound: float = Field(description="Known plus unknown: the most the theme could reach.")
    inferred_share: float = Field(
        description="Share of the known exposure resting on inferred labels."
    )
    confidence: Confidence
    confidence_reason: str
    issuers: list[ThemeIssuer]
    by_selector: dict[str, float] = Field(
        description="Exposed weight per selector on its own (these overlap and do not add up)."
    )
    equity_weight: float
    warnings: list[str]
    method: str


METHOD = (
    "Each equity leaf is counted once: its exposed share is the largest any one selector gives "
    "it, so selectors never double count. Direct holdings and fund rows are merged by issuer and "
    "keep every path. A selector whose dimension has no label for a leaf adds that leaf's "
    "possible exposure to the unknown weight; a fund that is not looked through is unknown in "
    "full. Revenue, commodity and trade selectors use reported or owner-mapped revenue shares "
    "only. Confidence follows the unknown weight and the inferred share of the match."
)

_WEAKNESS = {"owner": 0, "reported": 0, "inferred": 1, "none": 2}


@dataclass
class _LeafMatch:
    share: float
    paths: list[str]
    basis: str
    unknown: float


def _selector_label(selector: ThemeSelector) -> str:
    return f"{selector.kind}: {label_for(selector.kind, selector.value)}"


def _match(leaf, selectors: list[ThemeSelector], ctx: ViewContext) -> _LeafMatch:  # type: ignore[no-untyped-def]
    best = 0.0
    paths: list[str] = []
    basis = "reported"
    unknown = 0.0
    for sel in selectors:
        dimension: Dimension = sel.kind
        got = pieces(leaf, dimension, ctx)
        hit = [p for p in got if p.key == sel.value]
        missing = sum(p.share for p in got if p.key == UNCLASSIFIED)
        unknown = max(unknown, missing)
        if not hit:
            continue
        share = sum(p.share for p in hit)
        best = max(best, share)
        weakest = max((p.basis for p in hit), key=lambda b: _WEAKNESS.get(b, 2))
        paths.append(f"{_selector_label(sel)} ({weakest})")
        if _WEAKNESS.get(weakest, 2) > _WEAKNESS.get(basis, 0):
            basis = weakest
    return _LeafMatch(best, paths, basis, min(unknown, 1.0 - best))


def theme_exposure(theme: ThemeDefinition, ctx: ViewContext) -> ThemeExposure:
    """Exposure of the whole portfolio to a theme, merged by issuer, with its unknown."""
    leafset = ctx.leafset
    issuers: dict[str, ThemeIssuer] = {}
    by_selector: dict[str, float] = defaultdict(float)
    reasons: dict[str, float] = defaultdict(float)
    direct = fund = unknown_total = inferred = equity_weight = 0.0

    for leaf in leafset.leaves:
        if leaf.special in (NOT_LOOKED, UNKNOWN_FUND):
            what = (
                f"{leaf.channel_label}: holdings are not stored"
                if leaf.special == NOT_LOOKED
                else f"{leaf.channel_label}: weight its published rows do not account for"
            )
            reasons[what] += leaf.weight
            unknown_total += leaf.weight
            continue
        if not leaf.is_equity:
            continue
        equity_weight += leaf.weight
        m = _match(leaf, theme.selectors, ctx)
        for sel in theme.selectors:
            dimension: Dimension = sel.kind
            if any(p.key == sel.value for p in pieces(leaf, dimension, ctx)):
                share = sum(p.share for p in pieces(leaf, dimension, ctx) if p.key == sel.value)
                by_selector[_selector_label(sel)] += leaf.weight * share
        if m.unknown > 0:
            gap = leaf.weight * m.unknown
            unknown_total += gap
            reasons[f"{leaf.issuer_label or leaf.name or 'holding'}: label missing"] += gap
        if m.share <= 0:
            continue
        exposed = leaf.weight * m.share
        is_direct = leaf.channel_kind == "direct"
        direct += exposed if is_direct else 0.0
        fund += 0.0 if is_direct else exposed
        if m.basis == "inferred":
            inferred += exposed
        name = leaf.issuer_label or leaf.name or leaf.issuer_key or "Unidentified"
        key = leaf.issuer_key or name
        entry = issuers.setdefault(
            key, ThemeIssuer(issuer=name, exposed=0.0, direct=0.0, via_funds=0.0, paths=[])
        )
        entry.exposed += exposed
        if is_direct:
            entry.direct += exposed
        else:
            entry.via_funds += exposed
        entry.paths.append(
            ThemePath(
                channel=leaf.channel_label,
                channel_kind=leaf.channel_kind,
                issuer=name,
                weight=leaf.weight,
                exposed=exposed,
                selectors=m.paths,
                basis=m.basis,
            )
        )

    exposed_total = direct + fund
    inferred_share = inferred / exposed_total if exposed_total > 0 else 0.0
    confidence, why = _confidence(exposed_total, unknown_total, inferred_share)
    warnings: list[str] = []
    if len(theme.selectors) > 1:
        warnings.append(
            "Selectors overlap; the exposure counts each holding once, at the largest share "
            "any one selector gives it."
        )
    if any(s.kind in ("revenue_country", "commodity", "trade_product") for s in theme.selectors):
        warnings.append(
            "Revenue, commodity and trade shares exist only for companies that report them or "
            "that you mapped; the rest are in the unknown weight."
        )
    if any(s.kind == "currency" for s in theme.selectors):
        warnings.append("Currency is the trading currency, not the company's economic exposure.")
    return ThemeExposure(
        method_version=version("theme_exposure"),
        theme=theme,
        exposed_weight=exposed_total,
        direct_weight=direct,
        fund_weight=fund,
        unknown=ThemeUnknown(weight=unknown_total, reasons=dict(reasons)),
        upper_bound=min(1.0, exposed_total + unknown_total),
        inferred_share=inferred_share,
        confidence=confidence,
        confidence_reason=why,
        issuers=sorted(issuers.values(), key=lambda i: -i.exposed),
        by_selector=dict(by_selector),
        equity_weight=equity_weight,
        warnings=warnings,
        method=METHOD,
    )


def _confidence(exposed: float, unknown: float, inferred_share: float) -> tuple[Confidence, str]:
    stated = f"unknown {unknown:.1%} of the portfolio, {inferred_share:.0%} of the match inferred"
    if unknown <= HIGH_UNKNOWN and inferred_share <= HIGH_INFERRED_SHARE:
        return "high", stated
    if unknown <= MEDIUM_UNKNOWN:
        return "medium", stated
    return "low", stated
