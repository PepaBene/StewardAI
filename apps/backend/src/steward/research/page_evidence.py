"""Stored text for the web pages a report cites.

A web citation arrives as a URL and a page title. The claim checker can only test a sentence
against stored text, so every sentence that rested on a page was rejected ("figure not in the
cited snippets") however true it was. Before the claims are checked, each cited page is
fetched once and the lines of it that best match the sentences citing it are stored on the
source as its snippet. The lines are copied from the page unchanged, never summarised, so
the checker still compares a sentence with what the page says. A page that cannot be fetched
keeps its title and the sentences resting on it stay unverifiable.
"""

import logging
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence

from steward.research.sources import Registry
from steward.verify.claims import numbers_in, strip_links

logger = logging.getLogger(__name__)

MAX_PAGES = 12
SNIPPET_CHARS = 3000
WINDOW_LINES = 3
_WORD = re.compile(r"[a-z]{5,}")

FetchPage = Callable[[str], Awaitable[str | None]]


def _needs_text(snippet: str | None, title: str) -> bool:
    return not snippet or snippet == title or len(snippet) < 200


def best_passages(text: str, sentences: Sequence[str], limit: int = SNIPPET_CHARS) -> str:
    """The windows of consecutive lines of `text` that share the most figures and words with
    the sentences, in page order, within `limit` characters."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ""
    wanted_numbers = {n.text for s in sentences for n in numbers_in(strip_links(s))}
    wanted_words = {w for s in sentences for w in _WORD.findall(strip_links(s).lower())}
    scored: list[tuple[int, int]] = []
    for start in range(len(lines)):
        window = " ".join(lines[start : start + WINDOW_LINES])
        have = {n.text for n in numbers_in(window)}
        score = 3 * len(wanted_numbers & have) + len(
            wanted_words & set(_WORD.findall(window.lower()))
        )
        if score:
            scored.append((score, start))
    chosen: list[int] = []
    used = 0
    for _score, start in sorted(scored, reverse=True):
        if any(abs(start - c) < WINDOW_LINES for c in chosen):
            continue
        size = len(" ".join(lines[start : start + WINDOW_LINES]))
        if used + size > limit:
            continue
        chosen.append(start)
        used += size
    return "\n".join(" ".join(lines[s : s + WINDOW_LINES]) for s in sorted(chosen))[:limit]


async def attach_page_text(
    reg: Registry,
    cited_by: Mapping[str, Sequence[str]],
    fetch: FetchPage,
    max_pages: int = MAX_PAGES,
) -> int:
    """Store passages on the cited search sources of `cited_by` (source id -> the sentences
    that cite it). Returns how many pages gave text. A failed fetch is ignored."""
    done = 0
    for sid, sentences in cited_by.items():
        if done >= max_pages:
            break
        source = reg.get(sid)
        if source is None or source.kind != "search" or not source.url:
            continue
        if not _needs_text(source.snippet, source.title):
            continue
        try:
            text = await fetch(source.url)
        except Exception:
            logger.info("could not fetch %s for claim evidence", source.url, exc_info=True)
            continue
        if not text:
            continue
        passages = best_passages(text, sentences)
        if passages:
            source.snippet = passages
            done += 1
    return done
