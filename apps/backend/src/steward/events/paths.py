"""Event-to-portfolio impact paths (plan M7.4).

An event starts at one company and follows the supply-chain edges the app already holds: a supplier
disruption flows downstream to the customers it feeds; a customer loss flows upstream to the
suppliers that sell to it. Each step is one edge with its source and date. The path ends at a held
company, where the portfolio weight (direct and through funds) and the owner's thesis indicators
are shown.

Confidence is set by the weakest edge on the path and never exceeds it. Only a path whose every
edge is high confidence, and whose event is confirmed, may be called high severity; anything else
is capped at medium, so a single doubtful link cannot raise a high-severity alert. Nothing here
estimates a loss: a revenue share is multiplied along the path only when every edge states one."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

from steward.xray.supply_chain import SupplyChain, SupplyEdge

Direction = Literal["downstream", "upstream"]
Confidence = Literal["high", "medium", "low"]
Severity = Literal["high", "medium", "low", "none"]
EventState = Literal["confirmed", "reported", "rumoured"]

RANK: dict[Confidence, int] = {"low": 0, "medium": 1, "high": 2}
MAX_HOPS = 3
# An edge older than this is treated as possibly stale.
STALE_AFTER_DAYS = 3 * 365
# A path must reach at least this share of the portfolio to be called high severity.
HIGH_WEIGHT = 0.02

METHOD = (
    "A path follows confirmed supply-chain links from the company in the event. The confidence of "
    "a path is its weakest link: a link is high when it is a stated fact (owner-entered or a "
    "confirmed filing disclosure) with a stated revenue share and a date within three years, "
    "medium when it has no stated share, and low when it is older than three years. Severity is "
    "high only for a confirmed event on a path of high links that reaches at least 2% of the "
    "portfolio. A revenue share is shown only for a single customer link that states one."
)


class PathEdge(BaseModel):
    supplier: str
    customer: str
    product: str
    customer_share: float | None
    source_url: str
    as_of: dt.date
    origin: Literal["owner", "filing"]
    confidence: Confidence
    why: str


class EventPath(BaseModel):
    start: str
    end: str
    edges: list[PathEdge]
    confidence: Confidence = Field(description="The weakest link on the path.")
    weight: float = Field(description="Portfolio weight in the company at the end of the path.")
    revenue_share: float | None = Field(
        description="Share of the end supplier's revenue that the lost customer represents, "
        "only for a single customer link that states one."
    )
    severity: Severity
    indicators: list[str] = Field(description="Thesis indicators of the end company to watch.")


class EventImpact(BaseModel):
    symbol: str
    direction: Direction
    state: EventState
    paths: list[EventPath]
    reached_weight: float = Field(
        description="Portfolio weight in the companies reached, once each."
    )
    own_weight: float
    notes: list[str]
    method: str = METHOD


def edge_confidence(edge: SupplyEdge, today: dt.date) -> tuple[Confidence, str]:
    if (today - edge.as_of).days > STALE_AFTER_DAYS:
        return "low", f"dated {edge.as_of}, more than three years old"
    if edge.customer_share is None:
        return "medium", "the link is stated but no revenue share is given"
    return "high", "a stated link with a revenue share"


def weakest(levels: list[Confidence]) -> Confidence:
    return min(levels, key=lambda c: RANK[c]) if levels else "low"


def severity_of(confidence: Confidence, state: EventState, weight: float) -> Severity:
    if weight <= 0:
        return "none"
    if confidence == "high" and state == "confirmed" and weight >= HIGH_WEIGHT:
        return "high"
    if confidence == "low" or state == "rumoured":
        return "low"
    return "medium"


def event_paths(
    chain: SupplyChain,
    symbol: str,
    direction: Direction,
    state: EventState = "reported",
    indicators: dict[str, list[str]] | None = None,
    today: dt.date | None = None,
) -> EventImpact:
    today = today or dt.date.today()
    symbol = symbol.strip().upper()
    weights = {n.symbol: n.weight for n in chain.nodes}
    nxt: dict[str, list[SupplyEdge]] = {}
    for e in chain.edges:
        key = e.supplier if direction == "downstream" else e.customer
        nxt.setdefault(key, []).append(e)

    paths: list[EventPath] = []

    def walk(at: str, trail: list[SupplyEdge], seen: set[str]) -> None:
        if len(trail) >= MAX_HOPS:
            return
        for e in sorted(nxt.get(at, []), key=lambda e: (e.supplier, e.customer)):
            other = e.customer if direction == "downstream" else e.supplier
            if other in seen:
                continue
            trail2 = [*trail, e]
            paths.append(_path(symbol, other, trail2, direction, state, weights, indicators, today))
            walk(other, trail2, seen | {other})

    walk(symbol, [], {symbol})
    held = [p for p in paths if p.weight > 0]
    reached: dict[str, float] = {}
    for p in held:
        reached[p.end] = p.weight
    held.sort(key=lambda p: (-RANK[p.confidence], -p.weight, len(p.edges), p.end))
    notes = []
    if symbol not in weights:
        notes.append(f"{symbol} has no links in the supply-chain graph, so no path starts there.")
    elif not paths:
        notes.append(f"{symbol} has no {direction} links on record.")
    elif not held:
        notes.append("The links lead only to companies the portfolio does not hold.")
    if state != "confirmed":
        notes.append("The event is not confirmed, so no path is called high severity.")
    return EventImpact(
        symbol=symbol,
        direction=direction,
        state=state,
        paths=held,
        reached_weight=sum(reached.values()),
        own_weight=weights.get(symbol, 0.0),
        notes=notes,
    )


def _path(
    start: str,
    end: str,
    trail: list[SupplyEdge],
    direction: Direction,
    state: EventState,
    weights: dict[str, float],
    indicators: dict[str, list[str]] | None,
    today: dt.date,
) -> EventPath:
    edges: list[PathEdge] = []
    for e in trail:
        conf, why = edge_confidence(e, today)
        edges.append(
            PathEdge(
                supplier=e.supplier, customer=e.customer, product=e.product,
                customer_share=e.customer_share, source_url=e.source_url, as_of=e.as_of,
                origin=e.origin, confidence=conf, why=why,
            )
        )  # fmt: skip
    confidence = weakest([e.confidence for e in edges])
    weight = weights.get(end, 0.0)
    share: float | None = None
    # Only a direct link states how much of the supplier's revenue the customer is; compounding
    # that over more steps would claim a precision the filings do not give.
    if direction == "upstream" and len(trail) == 1:
        share = trail[0].customer_share
    return EventPath(
        start=start, end=end, edges=edges, confidence=confidence, weight=weight,
        revenue_share=share, severity=severity_of(confidence, state, weight),
        indicators=(indicators or {}).get(end, []),
    )  # fmt: skip
