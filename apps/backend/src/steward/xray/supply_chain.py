"""The supply-chain graph (plan M5.5).

Edges come from two sources only, each with its address and date: links the owner entered, and
customer or supplier relationships a held company disclosed in an annual report and that the
relationship review confirmed. Each company is placed against what the portfolio holds in it once
funds are opened up (its look-through weight), so the graph shows where two things you own depend
on each other. It never infers an edge, and a company with no edges does not appear.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from typing import Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.graph.keys import normalize_object_key
from steward.xray.leaves import LeafSet
from steward.xray.owner_input import OwnerInput

# Filings name some counterparties only as a group or a letter ("All Other", "Unidentified Customer
# One", "Accounts Receivable Customer F"). They are not companies, so they are not drawn.
PLACEHOLDER = re.compile(
    r"^(all|any) other|^others?$|^other (customers?|suppliers?)|unidentified|undisclosed|"
    r"accounts? receivable|^(customer|supplier|distributor|partner)s?\s+[a-z0-9]{1,3}$|"
    r"^(a|one|two|three|four) (major |large )?(customer|supplier)",
    re.IGNORECASE,
)


def is_placeholder(name: str) -> bool:
    return bool(PLACEHOLDER.search(name.strip()))


class SupplyNode(BaseModel):
    symbol: str
    name: str | None
    weight: float = Field(
        description="Share of the portfolio held in it, directly or through funds."
    )
    held: bool
    level: int = Field(
        description="0 for a company that only supplies; one more per step downstream."
    )


class SupplyEdge(BaseModel):
    id: int | None = Field(
        description="Set for an entry you made (it can be removed); None for a filing."
    )
    supplier: str
    customer: str
    product: str
    customer_share: float | None
    source_url: str
    as_of: dt.date
    origin: Literal["owner", "filing"] = "owner"
    fiscal_period: str | None = None


class LinkedPair(BaseModel):
    supplier: str
    customer: str
    product: str
    combined_weight: float
    customer_share: float | None


class SupplyChain(BaseModel):
    nodes: list[SupplyNode]
    edges: list[SupplyEdge]
    linked: list[LinkedPair] = Field(
        description="Edges where the portfolio holds both ends, so one event can hit both."
    )
    held_in_graph: float = Field(
        description="Portfolio weight in companies that appear in the graph."
    )
    notes: list[str]


def symbol_of(ticker: str | None) -> str:
    """`AAPL_US_EQ` and `aapl` are both AAPL."""
    return (ticker or "").split("_")[0].strip().upper()


def holdings_by_symbol(leafset: LeafSet) -> tuple[dict[str, float], dict[str, str | None]]:
    weights: dict[str, float] = defaultdict(float)
    names: dict[str, str | None] = {}
    for leaf in leafset.leaves:
        if not leaf.is_equity:
            continue
        sym = symbol_of(leaf.ticker)
        if not sym:
            continue
        weights[sym] += leaf.weight
        names.setdefault(sym, leaf.name)
    return dict(weights), names


def levels(pairs: list[tuple[str, str]]) -> dict[str, int]:
    """Depth of each company from the upstream end: longest path from a pure supplier. A cycle is
    cut where it closes, so a loop of customers still lays out."""
    customers: dict[str, set[str]] = defaultdict(set)
    nodes: set[str] = set()
    for a, b in pairs:
        customers[a].add(b)
        nodes.update((a, b))
    depth = dict.fromkeys(nodes, 0)
    for _ in range(len(nodes)):
        changed = False
        for a in sorted(nodes):
            for b in customers[a]:
                if depth[b] < depth[a] + 1 and depth[a] + 1 < len(nodes):
                    depth[b] = depth[a] + 1
                    changed = True
        if not changed:
            break
    return depth


async def filing_edges(pool: asyncpg.Pool, leafset: LeafSet) -> list[SupplyEdge]:
    """Customer and supplier links the companies you hold disclosed in their annual reports and
    that were confirmed in the relationship review (`relationship_disclosure`, status confirmed).
    An unnamed customer ("Customer A") is left out. A counterparty is matched to a company you
    hold by its normalised name; otherwise it appears under the name as written. The customer's
    share is shown only when the filing states it as a share of the issuer's revenue."""
    weights, names = holdings_by_symbol(leafset)
    if not weights:
        return []
    held_by_name = {normalize_object_key(n): s for s, n in names.items() if n}
    rows = await pool.fetch(
        "select ticker, relationship, direction, counterparty_name, reported_share, share_basis,"
        " fiscal_period, valid_to, source_url, known_at from public.relationship_disclosure"
        " where status = 'confirmed' and not anonymous_counterparty"
        " and counterparty_name is not null and relationship in ('customer', 'supplier')"
        " and upper(split_part(ticker, '_', 1)) = any($1::text[])"
        " order by coalesce(valid_to, known_at::date) desc, id desc",
        list(weights),
    )
    out: dict[tuple[str, str], SupplyEdge] = {}
    for r in rows:
        issuer = symbol_of(r["ticker"])
        other_name = str(r["counterparty_name"]).strip()
        if is_placeholder(other_name):
            continue
        other = held_by_name.get(normalize_object_key(other_name), other_name.upper())
        if other == issuer:
            continue
        sells = r["relationship"] == "customer" and r["direction"] == "issuer_sells_to"
        buys = r["relationship"] == "supplier" and r["direction"] == "issuer_buys_from"
        if not (sells or buys):
            continue
        supplier, customer = (issuer, other) if sells else (other, issuer)
        share = (
            float(r["reported_share"])
            if sells and r["reported_share"] is not None and r["share_basis"] == "issuer_revenue"
            else None
        )
        out.setdefault(
            (supplier, customer),  # the newest period was read first
            SupplyEdge(
                id=None, supplier=supplier, customer=customer,
                product="named in the annual report", customer_share=share,
                source_url=r["source_url"], as_of=r["valid_to"] or r["known_at"].date(),
                origin="filing", fiscal_period=r["fiscal_period"],
            ),
        )  # fmt: skip
    return list(out.values())


def build(
    leafset: LeafSet, inputs: list[OwnerInput], filings: list[SupplyEdge] | None = None
) -> SupplyChain:
    weights, names = holdings_by_symbol(leafset)
    edges = [
        SupplyEdge(
            id=i.id,
            supplier=str(i.payload["supplier"]),
            customer=str(i.payload["customer"]),
            product=str(i.payload["product"]),
            customer_share=i.payload.get("customer_share"),
            source_url=i.source_url,
            as_of=i.as_of,
        )
        for i in inputs
        if i.kind == "supply_edge"
    ]
    entered = {(e.supplier, e.customer) for e in edges}
    edges += [f for f in filings or [] if (f.supplier, f.customer) not in entered]
    depth = levels([(e.supplier, e.customer) for e in edges])
    nodes = [
        SupplyNode(
            symbol=sym,
            name=names.get(sym),
            weight=weights.get(sym, 0.0),
            held=weights.get(sym, 0.0) > 0,
            level=lvl,
        )
        for sym, lvl in sorted(depth.items(), key=lambda kv: (kv[1], kv[0]))
    ]
    linked = [
        LinkedPair(
            supplier=e.supplier,
            customer=e.customer,
            product=e.product,
            combined_weight=weights[e.supplier] + weights[e.customer],
            customer_share=e.customer_share,
        )
        for e in edges
        if weights.get(e.supplier, 0.0) > 0 and weights.get(e.customer, 0.0) > 0
    ]
    linked.sort(key=lambda x: -x.combined_weight)
    notes = [
        "Links come from two places only: ones you entered, and ones a company you hold disclosed "
        "in its annual report and that were confirmed in the relationship review, each with its "
        "source and date. Nothing is inferred. A link you entered replaces a filing's for the "
        "same pair.",
        "Weights are what the portfolio holds in each company directly and through funds, matched "
        "by ticker symbol.",
    ]
    if not edges:
        notes.append("No links yet: none entered, and no confirmed filing disclosure names one.")
    return SupplyChain(
        nodes=nodes,
        edges=edges,
        linked=linked,
        held_in_graph=sum(n.weight for n in nodes),
        notes=notes,
    )
