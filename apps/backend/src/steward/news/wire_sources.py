"""Wire-lane collectors for sources that are not RSS feeds.

- news_sitemap: a Google News sitemap index (Reuters publishes one in place of the RSS it ended
  in 2020). The newest page lists title, publication time and language of each new article, in
  near real time and with no summary.
- gdelt: GDELT DOC 2.0 article search per holding. It indexes news in 65 languages every 15
  minutes and is a discovery lead: the items are tier 3 unless their outlet is tiered.

Like every collector, these only fetch and parse.
"""

import asyncio
import datetime as dt
from collections.abc import Awaitable, Callable, Sequence
from urllib.parse import quote_plus, urljoin
from xml.etree import ElementTree as ET

import httpx

from steward.documents.fetch import default_headers, fetch_document
from steward.news.feeds import MAX_FEED_BYTES, CollectResult, HoldingFeedInfo, RawItem
from steward.news.normalize import clean_text, host_of
from steward.sources.xml_guard import UnsafeXml, reject_dtd

_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
_NEWS_NS = "{http://www.google.com/schemas/sitemap-news/0.9}"
# Sitemap pages read per run, newest first. A page holds up to 100 articles; a run every few
# minutes sees the new ones on the first page, a longer gap needs more.
SITEMAP_PAGES = 2

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
# GDELT asks for at most one request every five seconds.
GDELT_MIN_INTERVAL = 5.5
GDELT_MAX_RECORDS = 75
_GDELT_LANGUAGES = {
    "english": "en", "german": "de", "japanese": "ja", "chinese": "zh", "korean": "ko",
    "french": "fr", "spanish": "es", "czech": "cs", "italian": "it", "russian": "ru",
    "portuguese": "pt", "dutch": "nl", "polish": "pl", "arabic": "ar", "turkish": "tr",
    "hindi": "hi", "indonesian": "id", "swedish": "sv", "danish": "da", "norwegian": "no",
}  # fmt: skip


def _parse_xml(data: bytes) -> ET.Element:
    try:
        reject_dtd(data)
        return ET.fromstring(data)  # noqa: S314 - DTDs refused above
    except (UnsafeXml, ET.ParseError) as exc:
        raise ValueError(f"unsafe or malformed sitemap: {exc}") from exc


def _iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def sitemap_page_urls(data: bytes, base_url: str) -> list[str]:
    """The sitemap pages a sitemap index lists, in the order given (newest first for news
    sitemaps). A document that is itself a urlset has no pages."""
    root = _parse_xml(data)
    return [
        urljoin(base_url, loc.text.strip())
        for loc in root.iter(f"{_SITEMAP_NS}loc")
        if loc.text and root.tag == f"{_SITEMAP_NS}sitemapindex"
    ]


def parse_news_sitemap(
    data: bytes, source_id: str, *, region: str | None = None, default_language: str | None = None
) -> list[RawItem]:
    """The `<news:news>` entries of a Google News sitemap page as items (no summary)."""
    root = _parse_xml(data)
    items: list[RawItem] = []
    for url in root.iter(f"{_SITEMAP_NS}url"):
        loc = url.find(f"{_SITEMAP_NS}loc")
        news = url.find(f"{_NEWS_NS}news")
        if loc is None or not loc.text or news is None:
            continue
        title = clean_text(news.findtext(f"{_NEWS_NS}title"), 500)
        if not title:
            continue
        publication = news.find(f"{_NEWS_NS}publication")
        language = (
            publication.findtext(f"{_NEWS_NS}language") if publication is not None else None
        ) or default_language
        name = publication.findtext(f"{_NEWS_NS}name") if publication is not None else None
        link = loc.text.strip()
        items.append(
            RawItem(
                source_id=source_id,
                url=link,
                headline=title,
                published_at=_iso(news.findtext(f"{_NEWS_NS}publication_date"))
                or _iso(url.findtext(f"{_SITEMAP_NS}lastmod")),
                language=(language or "")[:2].lower() or None,
                outlet_host=host_of(link),
                outlet_name=name,
                region=region,
            )
        )
    return items


async def collect_news_sitemap(
    http: httpx.AsyncClient,
    source_id: str,
    url: str,
    *,
    region: str | None = None,
    default_language: str | None = None,
    pages: int = SITEMAP_PAGES,
) -> list[RawItem]:
    """New articles from a news sitemap (or sitemap index): the newest `pages` pages."""
    document = await fetch_document(http, url, max_bytes=MAX_FEED_BYTES, headers=default_headers())
    page_urls = sitemap_page_urls(document.data, url)
    if not page_urls:
        return parse_news_sitemap(
            document.data, source_id, region=region, default_language=default_language
        )
    items: list[RawItem] = []
    for page in page_urls[: max(1, pages)]:
        body = await fetch_document(http, page, max_bytes=MAX_FEED_BYTES, headers=default_headers())
        items.extend(
            parse_news_sitemap(
                body.data, source_id, region=region, default_language=default_language
            )
        )
    return items


def gdelt_query_url(names: Sequence[str], days: int = 1) -> str | None:
    terms = [f'"{n}"' for n in dict.fromkeys(n for n in names if len(n) >= 4)][:4]
    if not terms:
        return None
    query = f"({' OR '.join(terms)})" if len(terms) > 1 else terms[0]
    return (
        f"{GDELT_URL}?query={quote_plus(query)}&mode=artlist&format=json"
        f"&maxrecords={GDELT_MAX_RECORDS}&sort=datedesc&timespan={max(1, days)}d"
    )


def _gdelt_time(value: object) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return dt.datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.UTC)
    except ValueError:
        return None


def parse_gdelt(payload: object, source_id: str, ticker: str) -> list[RawItem]:
    articles = payload.get("articles") if isinstance(payload, dict) else None
    items: list[RawItem] = []
    for row in articles if isinstance(articles, list) else []:
        if not isinstance(row, dict):
            continue
        url, title = row.get("url"), clean_text(row.get("title"), 500)
        if not url or not title:
            continue
        language = _GDELT_LANGUAGES.get(str(row.get("language") or "").lower())
        domain = str(row.get("domain") or "") or host_of(str(url)) or ""
        items.append(
            RawItem(
                source_id=source_id,
                url=str(url),
                headline=title,
                published_at=_gdelt_time(row.get("seendate")),
                language=language,
                outlet_host=domain.lower().removeprefix("www.") or None,
                outlet_name=domain or None,
                tickers=(ticker,),
            )
        )
    return items


async def collect_gdelt(
    http: httpx.AsyncClient,
    source_id: str,
    holdings: Sequence[HoldingFeedInfo],
    days: int,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    min_interval: float = GDELT_MIN_INTERVAL,
) -> CollectResult:
    """One GDELT DOC query per holding, spaced to GDELT's rate limit. An HTTP 429 stops the
    run (the rest would be refused too) and is reported, never retried in a loop."""
    result = CollectResult()
    first = True
    for holding in holdings:
        url = gdelt_query_url(holding.names, days)
        if url is None:
            continue
        if not first:
            await sleep(min_interval)
        first = False
        try:
            response = await http.get(url, headers=default_headers())
        except httpx.HTTPError as exc:
            result.errors.append(f"{holding.ticker}: {type(exc).__name__}")
            continue
        if response.status_code == 429:
            result.errors.append("GDELT rate limit (HTTP 429); remaining holdings skipped")
            break
        if response.status_code != 200:
            result.errors.append(f"{holding.ticker}: HTTP {response.status_code}")
            continue
        try:
            payload = response.json()
        except ValueError:
            # GDELT answers a query it cannot parse with plain text, not JSON.
            result.errors.append(f"{holding.ticker}: GDELT returned no JSON")
            continue
        result.items.extend(parse_gdelt(payload, source_id, holding.ticker))
    return result
