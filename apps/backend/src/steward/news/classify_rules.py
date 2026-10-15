"""Classification settled in code before any model call (cost lever C7).

Two rules, each measured on the stored wire before it was allowed to replace a model call:

- A full-text watch-topic hit ("<filer> 10-Q mentions <topic>") linked to holdings only through
  the topic term is `other`, not relevant, no affected ticker. On 598 such stored items the model
  said exactly that every time. A hit that is also linked by name, competitor, customer or
  supplier carries a possible peer signal, and the model keeps judging those (the stored
  exceptions, such as a competitor's filing, are all among them).
- A headline that repeats an already classified one, with the same candidate holdings, takes
  that item's label. On 87 stored pairs the labels agreed 87 times. A syndicated copy with a
  different headline is not matched here: the stored story-cluster agreement was only 91%.

Neither rule runs for an item with no candidate links, and `rules_first_classification` turns
both off.
"""

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from steward.news.classify import Label

WATCH_TOPIC_SOURCES = frozenset({"edgar-full-text"})
WATCH_TOPIC_MARK = " mentions "
TOPIC_ONLY_BY = "rules:topic_only"
DUPLICATE_BY = "rules:duplicate"

_WORDS = re.compile(r"\W+")


def headline_key(headline: str) -> str:
    return _WORDS.sub(" ", headline.lower()).strip()


def link_tickers(links: Any) -> tuple[str, ...]:
    parsed = json.loads(links) if isinstance(links, str) else links
    return tuple(sorted({str(link["ticker"]) for link in parsed or []}))


def topic_only_label(source_id: str, headline: str, links: Any) -> Label | None:
    """`other` / not relevant for a watch-topic hit linked through the topic term alone."""
    if source_id not in WATCH_TOPIC_SOURCES or WATCH_TOPIC_MARK not in headline:
        return None
    parsed = json.loads(links) if isinstance(links, str) else links
    if not parsed or any(link.get("via") != "topic" for link in parsed):
        return None
    return Label("other", None, False, None, False, None)


@dataclass(frozen=True)
class Known:
    """The labels of recently classified items, found again by headline and candidates."""

    labels: Mapping[tuple[str, tuple[str, ...]], Label]

    def match(self, headline: str, links: Any) -> Label | None:
        tickers = link_tickers(links)
        if not tickers:
            return None
        return self.labels.get((headline_key(headline), tickers))


def known_from_rows(rows: Iterable[Mapping[str, Any]]) -> Known:
    """Oldest first in `rows`; a later item with the same key does not replace the first."""
    labels: dict[tuple[str, tuple[str, ...]], Label] = {}
    for r in rows:
        tickers = link_tickers(r["links"])
        if not tickers:
            continue
        labels.setdefault(
            (headline_key(r["headline"]), tickers),
            Label(
                r["event_type"],
                r["affected_ticker"],
                bool(r["relevant"]),
                r["thesis_link"],
                bool(r["rumour"]),
                r["event_date"],
            ),
        )
    return Known(labels)
