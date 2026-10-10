"""N6.9: does a story strengthen or weaken a thesis assumption.

The owner's thesis for the story's holding lists KPIs, exit conditions and why it is owned. The
model says, per assumption the story touches, whether it supports or weakens it and cites the
extracted claim that is the reason.

Rules kept in code (plan 6.9, free-data plan D5.6):
- the assumption must exist in the thesis, the cited claim must exist in the extraction;
- a story may mark a thesis *breach* only when it is primary-confirmed; any other weakening is a
  *review* prompt for the owner. Nothing here changes the thesis.
"""

from typing import Literal

from pydantic import BaseModel, Field

from steward.ai import Usage
from steward.config.thesis import ThesisConfig
from steward.llm.cache import ResultCache
from steward.llm.client import LLM
from steward.news.analysis import StoryMaterial, structured
from steward.news.extract import StoryExtraction

SYSTEM_PROMPT = """\
You relate one news story to an investor's written thesis for a holding. You are given the \
story's verified claims and the thesis: why it is owned, its KPIs (id, metric, threshold) and \
its exit conditions.

Rules:
- List only assumptions the story actually bears on. For each give `kind` (kpi, exit_condition \
or why_owned), `ref` (the KPI id, the exit condition text copied exactly, or "why_owned"), \
`effect` (strengthens, weakens or neutral), one-sentence `reason`, and `claim_index`, the number \
of the claim that is the reason.
- Do not judge whether the thesis is right, do not predict the price, and do not say a thesis \
is broken: that is the owner's decision.
"""

Effect = Literal["strengthens", "weakens", "neutral"]
Kind = Literal["kpi", "exit_condition", "why_owned"]


class ThesisLinkOut(BaseModel):
    kind: Kind
    ref: str
    effect: Effect
    reason: str
    claim_index: int


class ThesisLinks(BaseModel):
    links: list[ThesisLinkOut] = Field(default_factory=list)


class LinkedAssumption(BaseModel):
    kind: Kind
    ref: str
    effect: Effect
    reason: str
    claim: str
    status: Literal["supports", "review", "breach"]


def valid_refs(thesis: ThesisConfig) -> dict[Kind, set[str]]:
    return {
        "kpi": {k.id for k in thesis.kpis},
        "exit_condition": set(thesis.exit_conditions),
        "why_owned": {"why_owned"},
    }


def checked(
    story: StoryMaterial,
    extraction: StoryExtraction,
    thesis: ThesisConfig,
    raw: ThesisLinks,
) -> list[LinkedAssumption]:
    refs = valid_refs(thesis)
    out: list[LinkedAssumption] = []
    for link in raw.links:
        if link.effect == "neutral" or link.ref not in refs[link.kind]:
            continue
        if not 0 <= link.claim_index < len(extraction.key_claims) or not link.reason.strip():
            continue
        if link.effect == "strengthens":
            status: Literal["supports", "review", "breach"] = "supports"
        elif link.kind != "why_owned" and story.lifecycle == "primary_confirmed":
            status = "breach"
        else:
            status = "review"
        out.append(
            LinkedAssumption(
                kind=link.kind,
                ref=link.ref,
                effect=link.effect,
                reason=link.reason.strip(),
                claim=extraction.key_claims[link.claim_index].text,
                status=status,
            )
        )
    return out


def thesis_text(thesis: ThesisConfig) -> str:
    kpis = "\n".join(
        f"- {k.id}: {k.metric} {k.operator} {k.threshold:g} {k.unit}" for k in thesis.kpis
    )
    exits = "\n".join(f"- {e}" for e in thesis.exit_conditions)
    return (
        f"Why owned: {thesis.why_owned}\nKPIs:\n{kpis or '- none'}\n"
        f"Exit conditions:\n{exits or '- none'}"
    )


async def link_thesis(
    llm: LLM,
    model: str,
    story: StoryMaterial,
    extraction: StoryExtraction,
    thesis: ThesisConfig,
    usage: Usage,
    *,
    system: str = SYSTEM_PROMPT,
    effort: str | None = None,
    cache: ResultCache | None = None,
) -> list[LinkedAssumption]:
    claims = "\n".join(f"{i}: {c.text}" for i, c in enumerate(extraction.key_claims))
    raw = await structured(
        llm,
        model,
        system,
        f"Story: {story.headline}\nClaims:\n{claims}\n\nThesis for {story.ticker}:\n"
        f"{thesis_text(thesis)}",
        ThesisLinks,
        usage,
        effort=effort,
        cache=cache,
        cache_key="news_thesis",
        max_tokens=2000,
    )
    return checked(story, extraction, thesis, raw)
