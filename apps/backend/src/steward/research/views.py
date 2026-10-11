"""Concise and detailed views over one stored report (Phase 3).

The canonical object is the typed report body. A view only chooses what to show: it never
rewrites a sentence or a figure, and every sentence it keeps still cites its sources. Markdown
is a rendering of a view.
"""

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from steward.research.run import ResearchBody

View = Literal["concise", "detailed"]
CONCISE_MODEL_SENTENCES = 3
CONCISE_CODE_SENTENCES = 4


def apply_view(body: "ResearchBody", view: View) -> "ResearchBody":
    """The body as the view shows it. `detailed` is the stored body unchanged."""
    if view == "detailed":
        return body.model_copy(update={"view": "detailed"})
    sections = []
    for section in body.sections:
        if section.detail_only:
            continue
        cap = CONCISE_MODEL_SENTENCES if section.written_by == "model" else CONCISE_CODE_SENTENCES
        sections.append(section.model_copy(update={"sentences": section.sentences[:cap]}))
    cited = {i for s in sections for sentence in s.sentences for i in sentence.source_ids}
    kept = {(s.id, i) for s in sections for i in range(len(s.sentences))}
    return body.model_copy(
        update={
            "view": "concise",
            "sections": sections,
            "sources": [s for s in body.sources if s.id in cited],
            "discovered_urls": [],
            "ledger": body.ledger.restricted(kept) if body.ledger else None,
        }
    )
