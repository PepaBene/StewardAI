"""A plain web-search API: ranked result links with no model behind them (cost lever C1d).

A provider's built-in search tool bills a search fee and then bills the model to read the pages.
A search API returns only the links, titles and snippets, at a fraction of the fee, and what a
job needs from it (candidate URLs) does not need a model to find. The result list is data from
the provider, never text a model wrote, so a lead's title and URL are exactly what the index
returned, and the caller filters the hosts in code.

Only Brave's news endpoint is implemented. The endpoint, the response shape and the price are
taken from the vendor's public documentation and not verified against a live call or a bill;
read their data terms before enabling it (`ai_search_provider` stays "model" until then).
"""

import datetime as dt
import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

BRAVE_NEWS_URL = "https://api.search.brave.com/res/v1/news/search"
MAX_RESULTS = 20


class SearchAPIError(Exception):
    pass


@dataclass(frozen=True)
class ApiResult:
    url: str
    title: str
    snippet: str
    host: str | None


class BraveNewsSearch:
    """`search` returns up to `count` results published in the window, best first."""

    def __init__(self, http: httpx.AsyncClient, api_key: str) -> None:
        self._http = http
        self._key = api_key

    async def search(
        self, query: str, since: dt.date, until: dt.date, count: int = MAX_RESULTS
    ) -> list[ApiResult]:
        try:
            response = await self._http.get(
                BRAVE_NEWS_URL,
                params={
                    "q": query,
                    "count": min(max(count, 1), MAX_RESULTS),
                    "freshness": f"{since.isoformat()}to{until.isoformat()}",
                },
                headers={"X-Subscription-Token": self._key, "Accept": "application/json"},
                timeout=30.0,
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # The message carries no URL query or header, so no key reaches a log.
            raise SearchAPIError(f"search request failed: {type(exc).__name__}") from exc
        results = body.get("results") if isinstance(body, dict) else None
        out: list[ApiResult] = []
        for row in results if isinstance(results, list) else []:
            if not isinstance(row, dict):
                continue
            url, title = row.get("url"), str(row.get("title") or "").strip()
            if not isinstance(url, str) or not title:
                continue
            meta = row.get("meta_url")
            host = meta.get("hostname") if isinstance(meta, dict) else None
            out.append(
                ApiResult(
                    url=url,
                    title=title,
                    snippet=str(row.get("description") or "").strip()[:400],
                    host=host if isinstance(host, str) else None,
                )
            )
        return out
