"""Active share against index weights the owner pasted (plan M5.6).

No provider supplies an index's constituents here, so the weights are the owner's, with the source
and date they came from. The comparison is the portfolio's equity (funds opened up, renormalised to
100%) against the index: active share is half the sum of the absolute weight differences. A
constituent list that covers only part of the index (a top-50 paste) cannot give a true active
share, so the figure is then a lower bound and says so.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

from pydantic import BaseModel, Field

from steward.xray.leaves import LeafSet
from steward.xray.owner_input import OwnerInput
from steward.xray.supply_chain import symbol_of


class IndexDifference(BaseModel):
    name: str
    portfolio_weight: float
    index_weight: float
    difference: float = Field(description="Portfolio minus index, in weight.")


class IndexComparison(BaseModel):
    label: str
    source_url: str
    as_of: dt.date
    constituents: int
    index_weight_listed: float = Field(description="Sum of the pasted index weights.")
    active_share: float | None
    partial: bool = Field(description="The pasted weights cover less than the whole index.")
    index_weight_held: float = Field(description="Index weight in names the portfolio also holds.")
    unmatched: int = Field(description="Pasted constituents that no holding matched.")
    overweights: list[IndexDifference]
    underweights: list[IndexDifference]
    notes: list[str]


class IndexComparisons(BaseModel):
    comparisons: list[IndexComparison]
    notes: list[str]


COVERAGE_FULL = 0.97  # pasted weights adding up to this much count as the whole index


def portfolio_names(leafset: LeafSet) -> tuple[dict[str, float], dict[str, str], dict[str, str]]:
    """Equity weight by issuer key (renormalised to 1), names, and symbol/ISIN -> issuer key."""
    equity = [x for x in leafset.leaves if x.is_equity]
    total = sum(x.weight for x in equity)
    weights: dict[str, float] = defaultdict(float)
    names: dict[str, str] = {}
    lookup: dict[str, str] = {}
    for x in equity:
        key = x.issuer_key or x.isin or symbol_of(x.ticker) or x.name or "?"
        weights[key] += x.weight / total if total > 0 else 0.0
        names.setdefault(key, x.issuer_label or x.name or key)
        if x.isin:
            lookup[x.isin.upper()] = key
        if x.ticker:
            lookup[symbol_of(x.ticker)] = key
    return dict(weights), names, lookup


def compare(leafset: LeafSet, item: OwnerInput) -> IndexComparison:
    weights, names, lookup = portfolio_names(leafset)
    constituents = item.payload["constituents"]
    index_w: dict[str, float] = defaultdict(float)
    index_names: dict[str, str] = {}
    unmatched = 0
    for c in constituents:
        key = None
        if c.get("isin"):
            key = lookup.get(str(c["isin"]).upper())
        if key is None and c.get("ticker"):
            key = lookup.get(symbol_of(str(c["ticker"])))
        if key is None:
            unmatched += 1
            key = f"index:{c.get('isin') or c.get('ticker') or c['name']}"
        index_w[key] += float(c["weight"])
        index_names.setdefault(key, str(c["name"]))
    listed = sum(index_w.values())
    partial = listed < COVERAGE_FULL
    keys = set(weights) | set(index_w)
    diffs = [
        IndexDifference(
            name=names.get(k) or index_names.get(k) or k,
            portfolio_weight=weights.get(k, 0.0),
            index_weight=index_w.get(k, 0.0),
            difference=weights.get(k, 0.0) - index_w.get(k, 0.0),
        )
        for k in keys
    ]
    has_equity = bool(weights)
    active = 0.5 * sum(abs(d.difference) for d in diffs) if has_equity else None
    held = sum(w for k, w in index_w.items() if k in weights)
    notes = [
        "Index weights are the ones you entered, not an official feed. The portfolio side is "
        "equity only, funds opened up and renormalised to 100%."
    ]
    if partial:
        notes.append(
            f"The pasted weights add up to {listed:.1%} of the index, so the active share is "
            "approximate: names you did not paste would move it."
        )
    if unmatched:
        notes.append(
            f"{unmatched} pasted names matched no holding (matched by ISIN, then ticker symbol)."
        )
    if not has_equity:
        notes.append("The portfolio holds no equity once funds are opened up.")
    return IndexComparison(
        label=item.key,
        source_url=item.source_url,
        as_of=item.as_of,
        constituents=len(constituents),
        index_weight_listed=listed,
        active_share=active,
        partial=partial,
        index_weight_held=held,
        unmatched=unmatched,
        overweights=sorted((d for d in diffs if d.difference > 0), key=lambda d: -d.difference)[
            :10
        ],
        underweights=sorted((d for d in diffs if d.difference < 0), key=lambda d: d.difference)[
            :10
        ],
        notes=notes,
    )


def build(leafset: LeafSet, inputs: list[OwnerInput]) -> IndexComparisons:
    items = [i for i in inputs if i.kind == "index_weights"]
    notes = [] if items else ["No index weights entered yet."]
    return IndexComparisons(comparisons=[compare(leafset, i) for i in items], notes=notes)
