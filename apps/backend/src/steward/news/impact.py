"""N6.3: how a story reaches the portfolio, along edges that exist.

Code does the graph work with the existing supply-chain paths (`steward.events.paths`): from the
company in the story, every downstream and upstream path to a held company, with the weakest-link
confidence and the exposure-weighted portfolio share at its end. These are the `second_order`
chains. A holding the story names directly is `direct`.

The model adds only two things, both checked in code:
- a sentence explaining each verified path (matched by path index; an index that does not exist
  is dropped);
- hypotheses: links the graph does not hold. A hypothesis is kept only when it ends at a held
  company, is labelled `hypothesis` and carries no weight, and is never called an exposure.

The weight is exposure, the share of the portfolio in the companies reached. It is not a
forecast of return.
"""

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

from steward.ai import Usage
from steward.events.paths import EventPath, EventState, event_paths
from steward.llm.cache import ResultCache
from steward.llm.client import LLM
from steward.news.analysis import StoryMaterial, structured
from steward.news.extract import StoryExtraction
from steward.xray.supply_chain import SupplyChain, symbol_of

SYSTEM_PROMPT = """\
You explain how one news story reaches a personal portfolio. You are given the story's \
extracted event, the supply-chain paths the portfolio's graph already holds from the company in \
the story, and the held companies.

Rules:
- For each numbered path write one plain sentence saying why the event travels along it \
(what is supplied, what is lost or gained). Use only the path's own facts. Do not add numbers.
- You may propose hypotheses: a held company the event could reach by a link the graph does not \
hold, with the entities between and the relation. Say it as a possibility. Never give a weight.
- Never state a price move or a return.
"""

Relation = Literal["direct", "second_order", "hypothesis"]
Confidence = Literal["high", "medium", "low"]


class PathNote(BaseModel):
    path_index: int
    text: str


class Hypothesis(BaseModel):
    holding: str = Field(description="A held company symbol.")
    via: list[str] = Field(description="Entities between the event and the holding.")
    relation: str
    text: str
    confidence: Confidence = "low"


class ImpactNotes(BaseModel):
    paths: list[PathNote] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)


class ImpactChain(BaseModel):
    relation: Relation
    holding: str
    chain: list[str] = Field(description="Company symbols from the event to the holding.")
    links: list[str] = Field(default_factory=list)
    confidence: Confidence
    weight: float | None = Field(
        description="Portfolio share in the holding (exposure, not a return forecast); null for "
        "a hypothesis."
    )
    severity: str | None = None
    note: str | None = None


class ImpactResult(BaseModel):
    symbol: str | None
    chains: list[ImpactChain]
    direct_weight: float
    reached_weight: float
    notes: list[str] = Field(default_factory=list)


def event_state(extraction: StoryExtraction | None, lifecycle: str) -> EventState:
    if lifecycle == "primary_confirmed" or (
        extraction is not None and extraction.status in ("confirmed", "official")
    ):
        return "confirmed"
    if extraction is not None and extraction.status == "rumour":
        return "rumoured"
    return "reported"


def _chain(path: EventPath) -> ImpactChain:
    members = [path.start]
    for e in path.edges:
        members.append(e.customer if e.supplier == members[-1] else e.supplier)
    return ImpactChain(
        relation="second_order",
        holding=path.end,
        chain=members,
        links=[f"{e.supplier} supplies {e.customer} ({e.product})" for e in path.edges],
        confidence=path.confidence,
        weight=path.weight,
        severity=path.severity,
    )


def graph_chains(
    graph: SupplyChain,
    story: StoryMaterial,
    extraction: StoryExtraction | None,
    indicators: dict[str, list[str]] | None = None,
    today: dt.date | None = None,
) -> tuple[ImpactResult, list[EventPath]]:
    """The verified chains of a story from the graph, and the raw paths they were built from."""
    symbol = symbol_of(story.ticker) if story.ticker else None
    weights = {n.symbol: n for n in graph.nodes}
    state = event_state(extraction, story.lifecycle)
    chains: list[ImpactChain] = []
    paths: list[EventPath] = []
    notes: list[str] = []
    direct = 0.0
    reached = 0.0
    if symbol:
        node = weights.get(symbol)
        if node is not None and node.weight > 0:
            direct = node.weight
            chains.append(
                ImpactChain(
                    relation="direct",
                    holding=symbol,
                    chain=[symbol],
                    confidence="high",
                    weight=node.weight,
                )
            )
        for direction in ("downstream", "upstream"):
            impact = event_paths(graph, symbol, direction, state, indicators, today)
            for p in impact.paths:
                paths.append(p)
                chains.append(_chain(p))
            reached += impact.reached_weight
            notes.extend(impact.notes)
    else:
        notes.append("The story is not tied to one company, so no graph path starts from it.")
    return (
        ImpactResult(
            symbol=symbol,
            chains=chains,
            direct_weight=direct,
            reached_weight=reached,
            notes=list(dict.fromkeys(notes)),
        ),
        paths,
    )


def apply_notes(
    result: ImpactResult, paths: list[EventPath], notes: ImpactNotes, graph: SupplyChain
) -> ImpactResult:
    """Attach the model's sentences to the verified chains and keep valid hypotheses."""
    chains = list(result.chains)
    by_index = {n.path_index: n.text.strip() for n in notes.paths if n.text.strip()}
    second = [i for i, c in enumerate(chains) if c.relation == "second_order"]
    for idx, chain_pos in enumerate(second):
        text = by_index.get(idx)
        if text:
            chains[chain_pos] = chains[chain_pos].model_copy(update={"note": text})
    held = {n.symbol for n in graph.nodes if n.weight > 0}
    for h in notes.hypotheses:
        holding = symbol_of(h.holding)
        if holding not in held or not h.text.strip():
            continue
        if any(c.holding == holding and c.relation != "hypothesis" for c in chains):
            continue
        chains.append(
            ImpactChain(
                relation="hypothesis",
                holding=holding,
                chain=[result.symbol or "", *[symbol_of(v) or v for v in h.via], holding],
                links=[h.relation],
                confidence="low",
                weight=None,
                note=h.text.strip(),
            )
        )
    return result.model_copy(update={"chains": chains})


def notes_prompt(
    story: StoryMaterial,
    extraction: StoryExtraction | None,
    paths: list[EventPath],
    held: list[str],
) -> str:
    lines = [f"Story: {story.headline}", f"Company in the story: {story.ticker or 'none'}"]
    if extraction is not None:
        lines.append(f"Event: {extraction.event_type}: {extraction.action}")
        lines.append("Claims: " + " | ".join(c.text for c in extraction.key_claims))
    lines.append("Paths from the graph:")
    for i, p in enumerate(paths):
        links = "; ".join(f"{e.supplier} supplies {e.customer} ({e.product})" for e in p.edges)
        lines.append(f"{i}: {p.start} -> {p.end}: {links}")
    lines.append("Held companies: " + ", ".join(held))
    return "\n".join(lines)


async def explain_chains(
    llm: LLM,
    model: str,
    story: StoryMaterial,
    extraction: StoryExtraction | None,
    result: ImpactResult,
    paths: list[EventPath],
    graph: SupplyChain,
    usage: Usage,
    *,
    system: str = SYSTEM_PROMPT,
    effort: str | None = None,
    cache: ResultCache | None = None,
) -> ImpactResult:
    held = sorted(n.symbol for n in graph.nodes if n.weight > 0)
    notes = await structured(
        llm,
        model,
        system,
        notes_prompt(story, extraction, paths, held),
        ImpactNotes,
        usage,
        effort=effort,
        cache=cache,
        cache_key="news_impact",
        max_tokens=2500,
    )
    return apply_notes(result, paths, notes, graph)
