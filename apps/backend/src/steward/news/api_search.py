"""Cost lever: the news jobs' web searches through a plain search API instead of the model's.

The provider's built-in search bills a fee per search and then bills the model to read the
pages. Here the code runs the searches (the same Brave client and few-day result cache the chat
uses, restricted to the outlet tiers the job may cite) and hands the model only the titles,
snippets and links that came back. The model never searches and never writes a URL it was not
given, so the caller's check of cited URLs against `urls` stays exactly as strict as before.

These searches are not billed by the model provider, so they are counted here (`searches`) and
never in the usage the provider's search tool reports.
"""

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

import httpx

from steward.chat.search_tool import ChatWebSearch
from steward.llm.cache import SEARCH_CACHE_DAYS_FRESH, ResultCache
from steward.search_api import BraveNewsSearch
from steward.settings import Settings

SNIPPET_CHARS = 300


@dataclass
class ApiSearchRound:
    """What a set of searches found: allowed results (best first, no repeats) and the cost."""

    results: list[dict[str, str]] = field(default_factory=list)
    searches: int = 0  # API calls made; an answer from the cache is not a call
    errors: list[str] = field(default_factory=list)

    @property
    def urls(self) -> set[str]:
        return {r["url"] for r in self.results}

    @property
    def titles(self) -> dict[str, str]:
        return {r["url"]: r["title"][:300] for r in self.results if r.get("title")}

    def block(self) -> str:
        """The results as the model reads them: one numbered entry each."""
        if not self.results:
            return "(no results)"
        return "\n".join(
            f"[{i}] {r['title']}\n    {r['url']}\n    {r.get('snippet', '')[:SNIPPET_CHARS]}"
            for i, r in enumerate(self.results, 1)
        )


class ApiSearcher:
    def __init__(
        self,
        api: BraveNewsSearch,
        cache: ResultCache | None,
        cache_days: int = SEARCH_CACHE_DAYS_FRESH,
    ) -> None:
        self._api = api
        self._cache = cache
        self._cache_days = cache_days

    async def run(
        self,
        queries: list[str],
        domains: tuple[str, ...],
        *,
        days: int,
        max_searches: int,
        today: dt.date,
    ) -> ApiSearchRound:
        """Run up to `max_searches` of the distinct `queries` over the last `days` days."""
        web = ChatWebSearch(self._api, domains, self._cache, self._cache_days, max_searches)
        found: dict[str, dict[str, str]] = {}
        errors: list[str] = []
        for query in dict.fromkeys(q.strip() for q in queries if q.strip()):
            out: dict[str, Any] = await web.call({"query": query, "days": days}, today)
            if "error" in out:
                errors.append(str(out["error"]))
                if web.left <= 0:
                    break
                continue
            for r in out.get("results", []):
                found.setdefault(r["url"], r)
        return ApiSearchRound(list(found.values()), max_searches - web.left, errors)


def api_searcher(
    settings: Settings, http: httpx.AsyncClient, cache: ResultCache | None
) -> ApiSearcher | None:
    """The searcher, or None when no BRAVE_API_KEY is set (the caller falls back)."""
    if settings.brave_api_key is None:
        return None
    return ApiSearcher(BraveNewsSearch(http, settings.brave_api_key.get_secret_value()), cache)
