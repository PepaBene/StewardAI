"""Which sections a report has, in what order and with what titles (Phase 3).

A research profile says how to investigate; a report template says how to present. This module
joins them for one run: the sections to investigate and write, in order. A template can
rearrange, rename, shorten and hide sections and add owner-defined ones. It cannot touch a
code-written section's figures (those come from stored records whatever the template says) and
cannot reach the strategy: it never produces text, it only lays out what the run produced.
"""

from dataclasses import dataclass
from typing import Literal

from steward.config.research import (
    MODEL_SECTIONS,
    ReportTemplate,
    ResearchProfile,
)
from steward.research.writer import SECTION_TITLES

# Every section a run may write; the order a template-less profile gets is its own list.
BUILTIN_CODE = frozenset(
    {"financial_trend", "valuation", "ownership", "relationships", "risk", "liquidity"}
)


@dataclass(frozen=True)
class SectionSpec:
    id: str
    title: str
    written_by: Literal["code", "model"]
    task: str | None = None  # a custom section's question; None uses the built-in task
    max_sentences: int | None = None
    detail_only: bool = False
    custom: bool = False


def resolve_layout(profile: ResearchProfile, template: ReportTemplate | None) -> list[SectionSpec]:
    """The sections of this run. Without a template, the profile's own list, unchanged."""
    if template is None:
        return [
            SectionSpec(
                id=s,
                title=SECTION_TITLES[s],
                written_by="model" if s in MODEL_SECTIONS else "code",
            )
            for s in profile.sections
        ]
    custom = {c.id: c for c in template.custom_sections}
    out: list[SectionSpec] = []
    for entry in template.sections:
        if not entry.enabled:
            continue
        if entry.id in custom:
            c = custom[entry.id]
            out.append(
                SectionSpec(
                    id=c.id,
                    title=entry.title or c.title,
                    written_by="model",
                    task=c.task,
                    max_sentences=entry.max_sentences,
                    detail_only=entry.detail_only,
                    custom=True,
                )
            )
        else:
            out.append(
                SectionSpec(
                    id=entry.id,
                    title=entry.title or SECTION_TITLES[entry.id],
                    written_by="model" if entry.id in MODEL_SECTIONS else "code",
                    max_sentences=entry.max_sentences,
                    detail_only=entry.detail_only,
                )
            )
    return out


def investigation_profile(profile: ResearchProfile, layout: list[SectionSpec]) -> ResearchProfile:
    """The profile `gather` reads: its code sections are the layout's built-in ones. Custom
    sections are model-written and need nothing gathered."""
    built_in = [s.id for s in layout if not s.custom and s.id in BUILTIN_CODE | MODEL_SECTIONS]
    return profile.model_copy(update={"sections": built_in or profile.sections})
