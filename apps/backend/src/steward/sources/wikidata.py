"""Wikidata (SPARQL at `query.wikidata.org`, CC0): aliases in several languages, ownership
and group links, industry, products, index membership, management, website.

Wikidata is community-edited, so it is tier 3: aliases help match news, relationships found
only here are stored as *suggested* until the owner confirms them or GLEIF or a filing
corroborates them, and its financial figures are never used. It has no competitor property;
competitors are inferred (same industry and same stock index) and are suggestions too.
"""

import asyncio
import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from steward.sources.base import rate_limit_wait

_sleep = asyncio.sleep
RATE_LIMIT_ATTEMPTS = 3

SPARQL_URL = "https://query.wikidata.org/sparql"
# Default alias languages (Jobs config: reference.wikidata_languages).
LANGUAGES = ("en", "de", "cs")


class WikidataError(Exception):
    pass


class WikidataRateLimited(WikidataError):
    """HTTP 429 that outlasted the inline retries; `retry_at` is the provider's Retry-After."""

    def __init__(self, message: str, retry_at: dt.datetime | None = None) -> None:
        super().__init__(message)
        self.retry_at = retry_at


@dataclass(frozen=True)
class WikidataLink:
    kind: str  # parent, subsidiary, owned_by, industry, product, ceo, chair, website, part_of
    qid: str | None
    label: str
    lei: str | None = None


def qid_of(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


FIND_QUERY = """SELECT ?item WHERE {{
  {{ ?item wdt:P1278 "{lei}" }} UNION {{ ?item wdt:P946 "{isin}" }}
}} LIMIT 1"""

PROPERTIES_QUERY = """SELECT ?p ?value ?valueLabel ?lei WHERE {{
  VALUES ?item {{ wd:{qid} }}
  {{ ?item wdt:P749 ?value . BIND("parent" AS ?p) }}
  UNION {{ ?item wdt:P355 ?value . BIND("subsidiary" AS ?p) }}
  UNION {{ ?item wdt:P127 ?value . BIND("owned_by" AS ?p) }}
  UNION {{ ?item wdt:P452 ?value . BIND("industry" AS ?p) }}
  UNION {{ ?item wdt:P1056 ?value . BIND("product" AS ?p) }}
  UNION {{ ?item wdt:P169 ?value . BIND("ceo" AS ?p) }}
  UNION {{ ?item wdt:P488 ?value . BIND("chair" AS ?p) }}
  UNION {{ ?item wdt:P856 ?value . BIND("website" AS ?p) }}
  UNION {{ ?item wdt:P361 ?value . BIND("part_of" AS ?p) }}
  OPTIONAL {{ ?value wdt:P1278 ?lei }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,de,cs". }}
}}"""

ALIASES_QUERY = """SELECT ?alias WHERE {{
  wd:{qid} rdfs:label|skos:altLabel ?alias . FILTER(LANG(?alias) IN ({languages}))
}}"""

# Listed companies in the same industry and the same stock index.
COMPETITORS_QUERY = """SELECT DISTINCT ?c ?cLabel WHERE {{
  wd:{qid} wdt:P452 ?industry ; wdt:P361 ?index .
  ?index wdt:P31/wdt:P279* wd:Q223371 .
  ?c wdt:P452 ?industry ; wdt:P361 ?index .
  FILTER(?c != wd:{qid})
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}} LIMIT 15"""


def parse_properties(body: dict[str, Any]) -> list[WikidataLink]:
    links = []
    for b in body.get("results", {}).get("bindings", []):
        value = b["value"]["value"]
        is_item = b["value"].get("type") == "uri" and "/entity/Q" in value
        label = b.get("valueLabel", {}).get("value") or value
        links.append(
            WikidataLink(
                kind=b["p"]["value"],
                qid=qid_of(value) if is_item else None,
                label=label,
                lei=b.get("lei", {}).get("value"),
            )
        )
    return links


def parse_aliases(body: dict[str, Any]) -> list[str]:
    seen: dict[str, None] = {}
    for b in body.get("results", {}).get("bindings", []):
        alias = " ".join(b["alias"]["value"].split())
        if len(alias) >= 3 and "." not in alias.split()[-1][-4:]:  # skip "sap.com"
            seen.setdefault(alias, None)
    return list(seen)


def parse_competitors(body: dict[str, Any]) -> list[WikidataLink]:
    return [
        WikidataLink("competitor", qid_of(b["c"]["value"]), b.get("cLabel", {}).get("value", ""))
        for b in body.get("results", {}).get("bindings", [])
        if b.get("cLabel", {}).get("value") and not b["cLabel"]["value"].startswith("Q")
    ]


class WikidataClient:
    def __init__(self, http: httpx.AsyncClient, user_agent: str) -> None:
        self._http = http
        self._headers = {"Accept": "application/sparql-results+json", "User-Agent": user_agent}

    async def _query(self, query: str) -> dict[str, Any]:
        for attempt in range(RATE_LIMIT_ATTEMPTS):
            response = await self._http.get(
                SPARQL_URL, params={"query": query}, headers=self._headers, timeout=60.0
            )
            if response.status_code != 429:
                break
            wait, retry_at = rate_limit_wait(response.headers.get("Retry-After"), attempt)
            if wait is None or attempt == RATE_LIMIT_ATTEMPTS - 1:
                raise WikidataRateLimited("Wikidata rate limited (HTTP 429)", retry_at)
            await _sleep(wait)
        if response.status_code != 200:
            raise WikidataError(f"Wikidata returned HTTP {response.status_code}")
        try:
            body: dict[str, Any] = response.json()
        except ValueError as exc:
            raise WikidataError("Wikidata sent no JSON") from exc
        return body

    async def find(self, lei: str | None, isin: str | None) -> str | None:
        if not lei and not isin:
            return None
        body = await self._query(FIND_QUERY.format(lei=lei or "-", isin=isin or "-"))
        bindings = body.get("results", {}).get("bindings", [])
        return qid_of(bindings[0]["item"]["value"]) if bindings else None

    async def properties(self, qid: str) -> list[WikidataLink]:
        return parse_properties(await self._query(PROPERTIES_QUERY.format(qid=qid)))

    async def aliases(self, qid: str, languages: Sequence[str] = LANGUAGES) -> list[str]:
        # Codes are validated by the config ([a-z]{2,3}), so they are safe to quote here.
        quoted = ", ".join(f'"{lang}"' for lang in languages if lang.isalpha())
        return parse_aliases(await self._query(ALIASES_QUERY.format(qid=qid, languages=quoted)))

    async def competitors(self, qid: str) -> list[WikidataLink]:
        return parse_competitors(await self._query(COMPETITORS_QUERY.format(qid=qid)))
