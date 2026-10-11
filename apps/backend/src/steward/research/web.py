"""The web-search adapter: the one place that knows what the model provider's search returns.

The routing client turns `web_search_tool` into the provider's own search and hands back its
response blocks. `normalise_results` reads those blocks into `SearchHit`s, the only shape the
rest of research (and any saved report) sees. A change of provider changes this file; stored
reports keep the same `DiscoveredUrl` records.

The provider runs the search and opens pages itself, so we see what it returned (url, title,
age) and what the model chose to cite, not which pages it read in full. A URL that was cited
rests on text the model read; one that only appeared in results is recorded as found.
"""

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from steward.research.source_policy import SourcePolicy, normalise_host, paywall_limit


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str
    page_age: str | None = None


@dataclass
class SearchOutcome:
    hits: list[SearchHit]
    errors: list[str]
    queries: list[str] = field(default_factory=list)


def web_search_tool(model: str, max_uses: int, domains: tuple[str, ...]) -> dict[str, Any]:
    """The web search tool a call site passes to `client.messages.parse`. `model` is accepted
    so a call site need not know which models search differently."""
    del model
    return {
        "type": "web_search",
        "name": "web_search",
        "max_uses": max_uses,
        "allowed_domains": list(domains),
    }


def normalise_results(content: list[Any]) -> SearchOutcome:
    hits: list[SearchHit] = []
    errors: list[str] = []
    queries: list[str] = []
    for block in content:
        if getattr(block, "type", None) == "server_tool_use":
            # the provider's own record of a search the model asked for
            asked = getattr(block, "input", None)
            query = asked.get("query") if isinstance(asked, dict) else None
            if query:
                queries.append(str(query)[:200])
            continue
        if getattr(block, "type", None) != "web_search_tool_result":
            continue
        results = getattr(block, "content", None)
        if isinstance(results, list):
            for r in results:
                url = getattr(r, "url", None)
                if url:
                    age = getattr(r, "page_age", None)
                    hits.append(
                        SearchHit(
                            url=str(url),
                            title=str(getattr(r, "title", "") or url)[:300],
                            page_age=str(age) if age else None,
                        )
                    )
        else:
            code = getattr(results, "error_code", None) or getattr(results, "type", None)
            errors.append(str(code or "search failed")[:80])
    return SearchOutcome(hits, errors, queries)


UrlStatus = Literal["cited", "found_not_cited", "excluded"]


class DiscoveredUrl(BaseModel):
    """One URL the search returned, kept whether or not a sentence cites it."""

    url: str
    title: str
    host: str
    status: UrlStatus
    section: str
    round: int = 0
    tier: int | None = None
    role: str | None = None
    page_age: str | None = Field(default=None, description="The provider's age, if it gave one.")
    retrieved_at: str
    limitation: str | None = Field(
        default=None, description="Undated, likely paywalled, or why it was excluded."
    )


def discovered(
    hit: SearchHit,
    policy: SourcePolicy,
    section: str,
    round_no: int,
    cited: set[str],
    now: dt.datetime,
) -> DiscoveredUrl:
    host = normalise_host(urlparse(hit.url).hostname)
    decision = policy.decide(host)
    limits = [m for m in (None if decision.allowed else decision.reason, paywall_limit(host)) if m]
    if hit.page_age is None:
        limits.append("undated: the provider gave no publication age")
    if decision.allowed and decision.reason:
        limits.append(decision.reason)
    status: UrlStatus = (
        "excluded" if not decision.allowed else "cited" if hit.url in cited else "found_not_cited"
    )
    return DiscoveredUrl(
        url=hit.url,
        title=hit.title,
        host=host,
        status=status,
        section=section,
        round=round_no,
        tier=decision.tier,
        role=decision.role,
        page_age=hit.page_age,
        retrieved_at=now.isoformat(timespec="seconds"),
        limitation="; ".join(limits) or None,
    )


def merge_discovered(
    existing: list[DiscoveredUrl], new: list[DiscoveredUrl]
) -> list[DiscoveredUrl]:
    """One record per URL. A later citation upgrades `found_not_cited` to `cited`; the first
    section and time it appeared in are kept."""
    by_url = {d.url: d for d in existing}
    for d in new:
        old = by_url.get(d.url)
        if old is None:
            by_url[d.url] = d
        elif d.status == "cited" and old.status != "cited":
            by_url[d.url] = old.model_copy(update={"status": "cited"})
    return list(by_url.values())
