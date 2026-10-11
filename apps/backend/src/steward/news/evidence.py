"""Evidence model (plan N5): what kind of evidence an item is, how a story's lifecycle follows
from its evidence, and how a link to a holding is graded.

- **Provenance kind** of an item, from its source and outlet: a regulator's or exchange's own
  filing, the company's release, a central bank, a wire agency, a curated outlet, or an
  aggregator / AI lead. Primary kinds can confirm an event by themselves; the rest corroborate.
- **Lifecycle** of a story, derived from the corroboration status, the evidence kinds, any
  correction notice among its items and the owner's decision: reported, corroborated,
  primary_confirmed, disputed, corrected, resolved. Every change is written to
  `story_lifecycle_event` with its reason and the evidence behind it; nothing is overwritten.
- **Link confidence**: a holding found by identifier (source, ISIN, ticker symbol) is stronger
  than one found by name, which is stronger than a competitor, supplier, customer or topic hit.
  A story whose links to its holding are all indirect needs a higher severity to alert.
- **Portfolio topics**: the watch topics (rates, tariffs, ...) a wire item mentions.
"""

import re
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from steward.config.news_sources import NewsSource, NewsSourcesConfig, PortfolioTopic
from steward.news.link import INDIRECT_VIA, LINK_CONFIDENCE, topic_pattern

EvidenceKind = Literal[
    "primary_filing",
    "regulator",
    "company_release",
    "central_bank",
    "wire",
    "outlet",
    "aggregator",
    "lead",
]
Lifecycle = Literal[
    "reported", "corroborated", "primary_confirmed", "disputed", "corrected", "resolved"
]
LIFECYCLES: tuple[str, ...] = (
    "reported",
    "corroborated",
    "primary_confirmed",
    "disputed",
    "corrected",
    "resolved",
)
PRIMARY_KINDS: frozenset[str] = frozenset(
    {"primary_filing", "regulator", "company_release", "central_bank"}
)

# Source types that read an exchange's or regulator's own filing list.
_FILING_TYPES = frozenset(
    {"edgar", "edgar_fts", "rns", "eqs", "tdnet", "hkex", "cninfo", "dart", "fca_nsm", "filings"}
)


def evidence_kind(
    source: NewsSource,
    tier: int,
    host: str | None,
    ir_domains: Collection[str],
    sources: NewsSourcesConfig,
) -> EvidenceKind:
    """The kind of evidence an item from `source` (outlet host `host`, final `tier`) is."""
    if source.type == "ai_search":
        return "lead"
    clean = (host or "").lower().removeprefix("www.")
    if source.tickers and tier == 1 and source.type == "rss":
        return "company_release"  # a holding's own investor-relations feed
    if clean and any(clean == d or clean.endswith(f".{d}") for d in ir_domains):
        return "company_release"
    if source.type in _FILING_TYPES:
        return (
            "regulator"
            if source.type in ("rns", "eqs", "dart", "tdnet", "hkex", "cninfo", "fca_nsm")
            else ("primary_filing")
        )
    if source.macro and tier == 1:
        return "central_bank"
    if source.type == "gazette":
        return "regulator"
    if source.type in ("finnhub", "google_news", "alpha_vantage", "gdelt") or tier == 3:
        return "aggregator"
    key = sources.independence_key(host)
    if any(key == g.domains[0] for g in sources.syndication_groups):
        return "wire"
    return "outlet"


_CORRECTION = re.compile(
    # "Correction" / "Corrected" only as a label (at the start, after a bracket, or before a
    # colon or dash): "market correction" and "price correction" are ordinary news words.
    r"[(\[]\s*(?:correction|corrected)\b|"
    r"(?<!market )(?<!stock )(?<!price )(?<!bond )(?<!equity )"
    r"\b(?:correction|corrected)\s*[:–—-]|"
    r"\b(retract(?:s|ed|ion)?|amended|restated|berichtigung|rectificatif)\b|訂正|更正|정정",
    re.IGNORECASE,
)


def is_correction(headline: str) -> bool:
    """A headline that says it corrects an earlier report or filing. Revisions such as
    guidance changes (業績予想の修正) and "market correction" are news, not corrections, and do
    not match."""
    return _CORRECTION.search(headline) is not None


@dataclass(frozen=True)
class Basis:
    """What the lifecycle is derived from."""

    status: str  # story_cluster.status
    confidence: str  # story_cluster.confidence
    kinds: frozenset[str]  # evidence kinds of the story's items
    tier1_support: bool = False  # a tier-1 URL found by the corroboration search
    corrected: bool = False  # some item is a correction notice
    closed: bool = False  # the owner closed the story


def lifecycle_for(basis: Basis) -> tuple[Lifecycle, str]:
    """The lifecycle state and the reason for it. Order: closed, disputed, corrected, then the
    strength of confirmation."""
    if basis.closed:
        return "resolved", "closed by the owner"
    if basis.status == "disputed" or basis.confidence == "disputed":
        return "disputed", "sources contradict it or the owner marked it false"
    if basis.corrected:
        return "corrected", "an item in the story is a correction notice"
    if basis.status == "confirmed":
        primary = sorted(basis.kinds & PRIMARY_KINDS)
        if primary:
            return "primary_confirmed", f"stated by primary evidence ({', '.join(primary)})"
        if basis.confidence == "verified":
            return "primary_confirmed", "stated by a tier-1 source"
        if basis.tier1_support:
            return "primary_confirmed", "a tier-1 source found by search confirms it"
        return "corroborated", "independent sources report it"
    return "reported", "reported; not yet confirmed"


def link_confidence(via: str) -> float:
    return LINK_CONFIDENCE.get(via, 0.5)


def indirect_only(vias: Iterable[str]) -> bool:
    """True when every way an item reaches the holding is an indirect one."""
    kinds = list(vias)
    return bool(kinds) and all(v in INDIRECT_VIA for v in kinds)


def matching_topics(text: str, topics: Sequence[PortfolioTopic]) -> list[str]:
    """Ids of the enabled portfolio topics whose keywords occur in `text`."""
    found = []
    for topic in topics:
        if topic.enabled and any(topic_pattern(k).search(text) for k in topic.keywords):
            found.append(topic.id)
    return found
