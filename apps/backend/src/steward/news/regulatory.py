"""Regulatory announcements (layer 1) and SEC EDGAR full-text search.

- rns: UK regulatory announcements from a holding's Investegate company page
  (`investegate.co.uk/company/<TIDM>`, or a URL in the profile's regulatory_news_urls). Each
  row gives the time (London), the service (RNS, ...), the headline, which is also the
  announcement type, and a link to the full text.
- eqs: German regulatory announcements from the holding's EQS News company page (set in the
  profile's regulatory_news_urls). Each item carries its category (Ad-hoc, Directors'
  Dealings, Voting rights, ...) and ISIN.

Which service applies is decided by the holding's home market (FIRDS/FITRS): RNS for UK, EQS
for German holdings. The announcement type maps to the event taxonomy through the Event rules
config; types it leaves to the classifier (ad-hoc disclosures) are classified by the model.
Results and trading statements map to `earnings_release`, which starts the earnings thesis
check. Neither site documents an API: pages are read politely (one request per holding per
ingest) and both sources are off until the owner has checked the services' terms.

- edgar_fts: EDGAR full-text search (efts.sec.gov). Each holding's watch topics run as
  saved searches over new 8-K, 6-K, 10-Q, 10-K and 20-F filings; every hit becomes a tier-1
  item. Other filers' 10-K and 20-F filings that name the holding next to "customer" or
  "supplier" become suggested relationships linked to the filing.
"""

import datetime as dt
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from html import unescape
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import httpx

from steward.config.event_rules import EventRulesConfig
from steward.documents.fetch import FetchError, default_headers, fetch_document
from steward.news.feeds import CollectResult, HoldingFeedInfo, RawItem
from steward.news.normalize import clean_text

INVESTEGATE_COMPANY = "https://www.investegate.co.uk/company/{tidm}"
LONDON = ZoneInfo("Europe/London")
BERLIN = ZoneInfo("Europe/Berlin")
MAX_PAGE_BYTES = 3_000_000


@dataclass(frozen=True)
class Announcement:
    url: str
    headline: str
    announcement_type: str
    published_at: dt.datetime | None
    service: str
    isin: str | None = None


# ---------------------------------------------------------------------------
# Investegate (RNS)
# ---------------------------------------------------------------------------

_ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_LINK = re.compile(r'<a[^>]*class="announcement-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_TAG = re.compile(r"<[^>]+>")


def _text(html: str) -> str:
    return " ".join(unescape(_TAG.sub(" ", html)).split())


def parse_investegate(html: str, page_url: str) -> list[Announcement]:
    out = []
    for row in _ROW.findall(html):
        cells = _CELL.findall(row)
        link = _LINK.search(row)
        if len(cells) < 4 or link is None:
            continue
        day, clock, service = _text(cells[0]), _text(cells[1]), _text(cells[2])
        try:
            stamp = dt.datetime.strptime(f"{day} {clock}", "%d %b %Y %I:%M %p")
            published: dt.datetime | None = stamp.replace(tzinfo=LONDON).astimezone(dt.UTC)
        except ValueError:
            published = None
        headline = _text(link.group(2))
        out.append(
            Announcement(
                url=urljoin(page_url, link.group(1)),
                headline=headline,
                announcement_type=headline,
                published_at=published,
                service=service or "RNS",
            )
        )
    return out


# ---------------------------------------------------------------------------
# EQS News
# ---------------------------------------------------------------------------

_EQS_DATE = re.compile(r'class="news__date[^"]*"[^>]*>([^<]+)<')
_EQS_ITEM = re.compile(
    r'<a\s+href="(?P<url>https://www\.eqs-news\.com/news/[^"]+)"'
    r'[^>]*?data-news-isin="(?P<isin>[^"]*)"[^>]*?data-news-category=\'(?P<category>[^\']*)\'',
    re.S,
)
_EQS_TIME = re.compile(r'class="news__time">(\d{1,2}:\d{2})<')
_EQS_COMPANY = re.compile(r'class="news__company">([^<]+)<')
_EQS_HEADING = re.compile(r'class="news__heading">(.*?)</p>', re.S)


def _eqs_headline(url: str, company: str, heading: str, category: str) -> str:
    if heading and heading.replace("\xa0", "").strip():
        return f"{company}: {heading}" if company else heading
    # Voting-rights and some notices have no heading: the URL slug is the title.
    slug = url.rstrip("/").split("/")[-2] if url.count("/") > 5 else ""
    words = slug.replace("-", " ").strip()
    title = words[:1].upper() + words[1:] if words else category
    return f"{title} ({category})" if category and category.lower() not in title.lower() else title


def parse_eqs(html: str) -> list[Announcement]:
    """Items in page order; the date comes from the nearest date heading above each."""
    dates = [(m.start(), m.group(1).strip()) for m in _EQS_DATE.finditer(html)]
    items = list(_EQS_ITEM.finditer(html))
    out = []
    for i, m in enumerate(items):
        end = items[i + 1].start() if i + 1 < len(items) else len(html)
        block = html[m.start() : end]
        day = next((d for pos, d in reversed(dates) if pos < m.start()), None)
        clock = _EQS_TIME.search(block)
        published = None
        if day and clock:
            try:
                stamp = dt.datetime.strptime(f"{day} {clock.group(1)}", "%d %B %Y %H:%M")
                published = stamp.replace(tzinfo=BERLIN).astimezone(dt.UTC)
            except ValueError:
                published = None
        company = _EQS_COMPANY.search(block)
        heading = _EQS_HEADING.search(block)
        category = unescape(m.group("category"))
        out.append(
            Announcement(
                url=m.group("url"),
                headline=_eqs_headline(
                    m.group("url"),
                    _text(company.group(1)) if company else "",
                    _text(heading.group(1)) if heading else "",
                    category,
                ),
                announcement_type=category,
                published_at=published,
                service="EQS",
                isin=m.group("isin") or None,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------


def announcement_items(
    announcements: Sequence[Announcement],
    holding: HoldingFeedInfo,
    source_id: str,
    service: str,
    rules: EventRulesConfig,
    outlet_host: str,
    outlet_name: str,
) -> list[RawItem]:
    items = []
    for a in announcements:
        if a.isin and holding.isin and a.isin != holding.isin:
            continue
        items.append(
            RawItem(
                source_id=source_id,
                url=a.url,
                headline=clean_text(a.headline, 500) or a.announcement_type,
                published_at=a.published_at,
                summary=f"{a.service} announcement: {a.announcement_type}",
                language="en",
                outlet_host=outlet_host,
                outlet_name=outlet_name,
                tickers=(holding.ticker,),
                event_type=rules.announcement_event(service, a.announcement_type),
            )
        )
    return items


def _page_for(holding: HoldingFeedInfo, domain: str) -> str | None:
    return next((u for u in holding.regulatory_urls if domain in u), None)


async def collect_regulatory(
    http: httpx.AsyncClient,
    service: str,
    source_id: str,
    holdings: Sequence[HoldingFeedInfo],
    rules: EventRulesConfig,
    country: str,
) -> CollectResult:
    """RNS or EQS for the holdings whose home market is in `country`."""
    result = CollectResult()
    for holding in holdings:
        if holding.home_country != country:
            continue
        if service == "rns":
            url = _page_for(holding, "investegate.co.uk") or (
                INVESTEGATE_COMPANY.format(tidm=holding.tidm) if holding.tidm else None
            )
        else:
            url = _page_for(holding, "eqs-news.com")
        if url is None:
            hint = "a London ticker (OpenFIGI)" if service == "rns" else "an EQS News company page"
            result.errors.append(f"{holding.ticker}: no {service.upper()} page; set {hint} in "
                                 "the holding profile's regulatory_news_urls")  # fmt: skip
            continue
        try:
            document = await fetch_document(
                http, url, max_bytes=MAX_PAGE_BYTES, headers=default_headers()
            )
        except FetchError as exc:
            result.errors.append(f"{holding.ticker}: {exc}")
            continue
        html = document.data.decode("utf-8", errors="replace")
        if service == "rns":
            parsed = parse_investegate(html, url)
            host, name = "investegate.co.uk", "RNS (Investegate)"
        else:
            parsed = parse_eqs(html)
            host, name = "eqs-news.com", "EQS News"
        result.items.extend(
            announcement_items(parsed, holding, source_id, service, rules, host, name)
        )
    return result


# ---------------------------------------------------------------------------
# EDGAR full-text search
# ---------------------------------------------------------------------------

EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/{file}"
TOPIC_FORMS = "8-K,6-K,10-Q,10-K,20-F"
RELATION_FORMS = "10-K,20-F"
MAX_HITS = 20


@dataclass(frozen=True)
class FilingHit:
    accession: str
    file: str
    cik: str | None
    filer_name: str
    form: str
    filed_at: dt.date | None
    url: str


def _filer(display: str) -> str:
    return re.sub(r"\s*\((?:CIK|[A-Z.\-]+)[^)]*\)", "", display).strip() or display


def parse_efts(body: dict[str, Any]) -> list[FilingHit]:
    hits = []
    for h in body.get("hits", {}).get("hits", [])[:MAX_HITS]:
        src = h.get("_source", {})
        accession, _, file = str(h.get("_id", "")).partition(":")
        ciks = src.get("ciks") or []
        cik = ciks[0] if ciks else None
        if not accession or not file or not cik:
            continue
        names = src.get("display_names") or []
        filed = src.get("file_date")
        hits.append(
            FilingHit(
                accession=accession,
                file=file,
                cik=cik,
                filer_name=_filer(names[0]) if names else cik,
                form=str(src.get("form") or src.get("file_type") or ""),
                filed_at=dt.date.fromisoformat(filed) if filed else None,
                url=ARCHIVE.format(cik=int(cik), folder=accession.replace("-", ""), file=file),
            )
        )
    return hits


async def efts_search(
    http: httpx.AsyncClient,
    headers: dict[str, str],
    query: str,
    forms: str,
    start: dt.date,
    end: dt.date,
) -> list[FilingHit]:
    response = await http.get(
        EFTS_URL,
        params={
            "q": query,
            "forms": forms,
            "dateRange": "custom",
            "startdt": start.isoformat(),
            "enddt": end.isoformat(),
        },
        headers=headers,
    )
    if response.status_code != 200:
        raise httpx.HTTPStatusError(
            f"EDGAR full-text search returned HTTP {response.status_code}",
            request=response.request,
            response=response,
        )
    return parse_efts(response.json())


@dataclass
class FtsResult(CollectResult):
    # (ticker, kind customer|supplier, keyword, hit): other filers naming the holding.
    relations: list[tuple[str, str, str, FilingHit]] = field(default_factory=list)
    # (ticker, topic, hit) for every topic hit, to record in filing_hit.
    topic_hits: list[tuple[str, str, FilingHit]] = field(default_factory=list)


async def collect_edgar_fts(
    http: httpx.AsyncClient,
    headers: dict[str, str],
    source_id: str,
    holdings: Sequence[HoldingFeedInfo],
    since: dt.date,
    today: dt.date,
) -> FtsResult:
    result = FtsResult()
    for holding in holdings:
        for topic in holding.topics:
            try:
                hits = await efts_search(http, headers, f'"{topic}"', TOPIC_FORMS, since, today)
            except (httpx.HTTPError, ValueError) as exc:
                result.errors.append(f"{holding.ticker} topic {topic!r}: {exc}")
                continue
            for hit in hits:
                result.topic_hits.append((holding.ticker, topic, hit))
                result.items.append(
                    RawItem(
                        source_id=source_id,
                        url=hit.url,
                        # The topic in the headline is what links the item (via "topic").
                        headline=f"{hit.filer_name} {hit.form} mentions “{topic}”",
                        published_at=dt.datetime.combine(
                            hit.filed_at or today, dt.time(12), tzinfo=dt.UTC
                        ),
                        summary=f"SEC filing {hit.accession} ({hit.form}) mentions the watch "
                        f"topic “{topic}”.",
                        language="en",
                        outlet_host="sec.gov",
                        outlet_name="SEC EDGAR full-text search",
                    )
                )
        name = holding.names[0] if holding.names else None
        if not name or len(name) < 4:
            continue
        # "customer" next to the holding: the filer sells to it (the filer is a supplier);
        # "supplier": the filer buys from it (the filer is a customer).
        for keyword, relation in (("customer", "supplier"), ("supplier", "customer")):
            try:
                hits = await efts_search(
                    http, headers, f'"{name}" {keyword}', RELATION_FORMS, since, today
                )
            except (httpx.HTTPError, ValueError) as exc:
                result.errors.append(f"{holding.ticker} {keyword}: {exc}")
                continue
            for hit in hits:
                if holding.cik and hit.cik and int(hit.cik) == int(holding.cik):
                    continue  # its own filing
                result.relations.append((holding.ticker, relation, keyword, hit))
    return result
