"""Story clustering: syndicated copies of one wire story form one cluster and count as one
source.

Two items are copies when they have the same subject (the first content word of one
headline appears in the other) and their headlines, after normalisation (wire prefixes and outlet
suffixes removed, "bln" read as "billion", stopwords dropped), share most of their content
words, or when one headline is a shortened form of the other, or when their summaries open
with the same sentence. Independent reports of the same event are *different* clusters:
corroboration counts them as independent sources.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from steward.news.normalize import headline_tokens

JACCARD = 0.6
CONTAINMENT = 0.85
MIN_CONTAINED_TOKENS = 4
SUMMARY_LEAD_TOKENS = 25
SUMMARY_JACCARD = 0.8
SUMMARY_HEADLINE_JACCARD = 0.3


@dataclass(frozen=True)
class Fingerprint:
    headline: frozenset[str]
    summary_lead: frozenset[str]
    # First content word: a headline's subject ("nvidia raises ..." vs "amd raises ...").
    subject: str = ""

    @classmethod
    def of(
        cls, headline: str, summary: str | None, outlet_names: tuple[str, ...] = ()
    ) -> "Fingerprint":
        lead = headline_tokens(summary or "")[:SUMMARY_LEAD_TOKENS]
        tokens = headline_tokens(headline, outlet_names)
        return cls(frozenset(tokens), frozenset(lead), tokens[0] if tokens else "")


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def similarity(a: Fingerprint, b: Fingerprint) -> float:
    """0..1; at or above `threshold()` the two are copies of one story."""
    if not a.headline or not b.headline:
        return 0.0
    if a.subject != b.subject and a.subject not in b.headline and b.subject not in a.headline:
        # Same wording about a different company is a different story.
        return 0.0
    jaccard = _jaccard(a.headline, b.headline)
    shorter = min(len(a.headline), len(b.headline))
    contained = len(a.headline & b.headline) / shorter
    if contained >= CONTAINMENT and shorter >= MIN_CONTAINED_TOKENS:
        jaccard = max(jaccard, JACCARD)
    if (
        len(a.summary_lead) >= 8
        and len(b.summary_lead) >= 8
        and _jaccard(a.summary_lead, b.summary_lead) >= SUMMARY_JACCARD
        and jaccard >= SUMMARY_HEADLINE_JACCARD
    ):
        jaccard = max(jaccard, JACCARD)
    return jaccard


def same_story(a: Fingerprint, b: Fingerprint) -> bool:
    return similarity(a, b) >= JACCARD


@dataclass(frozen=True)
class ClusterMember:
    cluster_id: int
    fingerprint: Fingerprint


def best_cluster(item: Fingerprint, members: Iterable[ClusterMember]) -> int | None:
    """The cluster of the most similar existing item that is a copy of this one."""
    best: tuple[float, int] | None = None
    for member in members:
        score = similarity(item, member.fingerprint)
        if score >= JACCARD and (best is None or score > best[0]):
            best = (score, member.cluster_id)
    return best[1] if best else None
