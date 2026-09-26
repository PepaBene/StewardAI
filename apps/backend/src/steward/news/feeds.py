"""News collectors: each source type turns its feed into `RawItem`s.

- rss: any RSS or Atom feed (company IR feeds, newswires, outlets), parsed with feedparser.
- page: a news page without a feed, read daily; links that were not on it last time become
  items. The first read only records what is there, so a page's back catalogue never floods
  the feed.
- edgar: every holding's recent SEC filings (8-K and its items, 6-K, 10-Q, 10-K, Form 4,
  Schedule 13D/G). These are labelled by rule from the form and item numbers, not by a model.
- finnhub: Finnhub's company news per US holding (the outlet behind each headline sets its tier).
- google_news: a Google News RSS query per holding built from its names (off by default).

Collectors only fetch and parse. Freshness, blocking, tiers, linking and dedupe happen after.
"""

import calendar
import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote_plus, urljoin

import feedparser
import httpx

from steward.config.news_sources import GoogleNewsLocale
from steward.documents.fetch import FetchError, default_headers, fetch_document
from steward.news.normalize import clean_text, host_of
from steward.sources.edgar import EdgarClient, EdgarError, Filing

MAX_FEED_BYTES = 5_000_000


@dataclass(frozen=True)
class RawItem:
    source_id: str
    url: str
    headline: str
    published_at: dt.datetime | None
    summary: str | None = None
    language: str | None = None
    outlet_host: str | None = None
    outlet_name: str | None = None
    # Holdings the source itself ties the item to (EDGAR filer, Finnhub symbol, scoped source).
    tickers: tuple[str, ...] = ()
    # Event type labelled by rule (EDGAR forms); skips the classification call.
    event_type: str | None = None


@dataclass(frozen=True)
class HoldingFeedInfo:
    ticker: str
    names: tuple[str, ...]
    cik: str | None
    symbol: str | None
    isin: str | None = None
    # Home market country (FIRDS/FITRS): decides RNS (GB) or EQS (DE).
    home_country: str | None = None
    # London ticker (TIDM) for the Investegate company page.
    tidm: str | None = None
    regulatory_urls: tuple[str, ...] = ()
    topics: tuple[str, ...] = ()


@dataclass
class CollectResult:
    items: list[RawItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# RSS / Atom
# ---------------------------------------------------------------------------


def _entry_time(entry: Any) -> dt.datetime | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        value = entry.get(key)
        if value:
            # feedparser normalises every date to a UTC struct_time.
            return dt.datetime.fromtimestamp(calendar.timegm(value), tz=dt.UTC)
    return None


def parse_feed(
    data: bytes, source_id: str, *, tickers: tuple[str, ...] = (), feed_url: str = ""
) -> list[RawItem]:
    parsed = feedparser.parse(data)
    language = (parsed.feed.get("language") or "")[:2].lower() or None
    items = []
    for entry in parsed.entries:
        link = entry.get("link") or ""
        title = clean_text(entry.get("title"), 500)
        if not link or not title:
            continue
        link = urljoin(feed_url, link)
        source = entry.get("source") or {}
        outlet_url = source.get("href") if isinstance(source, dict) else None
        outlet_name = source.get("title") if isinstance(source, dict) else None
        items.append(
            RawItem(
                source_id=source_id,
                url=link,
                headline=title,
                published_at=_entry_time(entry),
                summary=clean_text(entry.get("summary") or entry.get("description")),
                language=language,
                outlet_host=host_of(outlet_url) or host_of(link),
                outlet_name=outlet_name,
                tickers=tickers,
            )
        )
    return items


async def collect_rss(
    http: httpx.AsyncClient, source_id: str, url: str, tickers: tuple[str, ...]
) -> list[RawItem]:
    document = await fetch_document(http, url, max_bytes=MAX_FEED_BYTES, headers=default_headers())
    return parse_feed(document.data, source_id, tickers=tickers, feed_url=url)


# ---------------------------------------------------------------------------
# Pages without a feed
# ---------------------------------------------------------------------------


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "nav", "footer", "header"):
            self._skip += 1
        elif tag == "a" and not self._skip:
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "nav", "footer", "header"):
            self._skip = max(0, self._skip - 1)
        elif tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._text).split())))
            self._href = None

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)


def page_links(html: str, page_url: str, min_text: int = 20) -> list[tuple[str, str]]:
    """(absolute URL, link text) of article-like links on the page's own site."""
    parser = _LinkParser()
    parser.feed(html)
    site = host_of(page_url) or ""
    seen: set[str] = set()
    links = []
    for href, text in parser.links:
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        url = urljoin(page_url, href).split("#")[0]
        host = host_of(url) or ""
        if not (host == site or host.endswith(f".{site}") or site.endswith(f".{host}")):
            continue
        if len(text) < min_text or url in seen or url.rstrip("/") == page_url.rstrip("/"):
            continue
        seen.add(url)
        links.append((url, text))
    return links


async def collect_page(
    http: httpx.AsyncClient,
    source_id: str,
    url: str,
    tickers: tuple[str, ...],
    previous: set[str] | None,
    now: dt.datetime,
) -> tuple[list[RawItem], list[str]]:
    """New links since the last read, and the page's current links (the next baseline).
    With no baseline yet (`previous` is None), nothing is new."""
    document = await fetch_document(http, url, max_bytes=MAX_FEED_BYTES, headers=default_headers())
    html = document.data.decode("utf-8", errors="replace")
    links = page_links(html, url)
    current = [u for u, _ in links]
    if previous is None:
        return [], current
    items = [
        RawItem(
            source_id=source_id,
            url=link,
            headline=text[:500],
            published_at=now,
            outlet_host=host_of(link),
            tickers=tickers,
        )
        for link, text in links
        if link not in previous
    ]
    return items, current


# ---------------------------------------------------------------------------
# SEC EDGAR filings
# ---------------------------------------------------------------------------

_8K_ITEMS: dict[str, tuple[str, str]] = {
    "1.01": ("product_business", "Entry into a Material Definitive Agreement"),
    "1.02": ("product_business", "Termination of a Material Definitive Agreement"),
    "1.03": ("regulatory_legal", "Bankruptcy or Receivership"),
    "2.01": ("m_and_a", "Completion of Acquisition or Disposition of Assets"),
    "2.02": ("earnings_release", "Results of Operations and Financial Condition"),
    "2.03": ("capital_allocation", "Creation of a Direct Financial Obligation"),
    "2.05": ("guidance_change", "Costs Associated with Exit or Disposal Activities"),
    "2.06": ("guidance_change", "Material Impairments"),
    "3.01": ("regulatory_legal", "Notice of Delisting or Failure to Satisfy a Listing Rule"),
    "3.02": ("capital_allocation", "Unregistered Sales of Equity Securities"),
    "4.01": ("management_change", "Changes in Registrant's Certifying Accountant"),
    "4.02": ("regulatory_legal", "Non-Reliance on Previously Issued Financial Statements"),
    "5.02": ("management_change", "Departure or Appointment of Directors or Officers"),
    "5.07": ("other", "Submission of Matters to a Vote of Security Holders"),
    "7.01": ("other", "Regulation FD Disclosure"),
    "8.01": ("other", "Other Events"),
    "9.01": ("other", "Financial Statements and Exhibits"),
}
_FORMS: dict[str, tuple[str, str]] = {
    "10-Q": ("other", "quarterly report (10-Q)"),
    "10-K": ("other", "annual report (10-K)"),
    "20-F": ("other", "annual report (20-F)"),
    "6-K": ("other", "report of a foreign issuer (6-K)"),
    "4": ("insider_transaction", "insider transaction report (Form 4)"),
    "SC 13D": ("capital_allocation", "beneficial ownership report (Schedule 13D)"),
    "SC 13D/A": ("capital_allocation", "amended beneficial ownership report (Schedule 13D/A)"),
    "SC 13G": ("other", "passive ownership report (Schedule 13G)"),
    "SC 13G/A": ("other", "amended passive ownership report (Schedule 13G/A)"),
}
_SEVERITY_ORDER = (
    "guidance_change", "m_and_a", "earnings_release", "regulatory_legal", "management_change",
    "capital_allocation", "product_business", "insider_transaction", "other",
)  # fmt: skip


def label_filing(filing: Filing) -> tuple[str, str] | None:
    """(event type, description) of a filing worth an item, or None to skip it."""
    form = filing.form
    if form in ("8-K", "8-K/A"):
        items = [i.strip() for i in filing.items.split(",") if i.strip()]
        labelled = [_8K_ITEMS[i] + (i,) for i in items if i in _8K_ITEMS]
        if not labelled:
            return ("other", f"current report ({form})")
        labelled.sort(key=lambda x: _SEVERITY_ORDER.index(x[0]))
        event, _, _ = labelled[0]
        described = "; ".join(f"Item {i}: {text}" for _, text, i in labelled if i != "9.01")
        return event, f"{form} \u2014 {described or 'Financial Statements and Exhibits'}"
    return _FORMS.get(form)


def filing_items(
    filings: Sequence[Filing], holding: HoldingFeedInfo, source_id: str, since: dt.datetime
) -> list[RawItem]:
    name = holding.names[0] if holding.names else holding.ticker
    items = []
    for filing in filings:
        published = filing.accepted_at or dt.datetime.combine(
            filing.filing_date, dt.time(12), tzinfo=dt.UTC
        )
        if published < since:
            continue
        label = label_filing(filing)
        if label is None:
            continue
        event, description = label
        items.append(
            RawItem(
                source_id=source_id,
                url=filing.index_url,
                headline=f"{name} files {description}",
                published_at=published,
                summary=f"SEC filing {filing.accession}, filed {filing.filing_date.isoformat()}.",
                language="en",
                outlet_host="sec.gov",
                outlet_name="SEC EDGAR",
                tickers=(holding.ticker,),
                event_type=event,
            )
        )
    return items


async def collect_edgar(
    edgar: EdgarClient, source_id: str, holdings: Sequence[HoldingFeedInfo], since: dt.datetime
) -> CollectResult:
    result = CollectResult()
    for holding in holdings:
        cik = holding.cik
        try:
            if cik is None and holding.symbol:
                cik = await edgar.cik_for_ticker(holding.symbol)
            if cik is None:
                continue
            filings = await edgar.filings(cik)
        except (EdgarError, httpx.HTTPError, ValueError, KeyError) as exc:
            result.errors.append(f"{holding.ticker}: {exc}")
            continue
        result.items.extend(filing_items(filings, holding, source_id, since))
    return result


# ---------------------------------------------------------------------------
# Finnhub company news
# ---------------------------------------------------------------------------

COMPANY_NEWS_URL = "https://finnhub.io/api/v1/company-news"

# Finnhub's `source` names, mapped to the outlet domain that decides their tier.
FINNHUB_OUTLETS: dict[str, str] = {
    "reuters": "reuters.com", "cnbc": "cnbc.com", "marketwatch": "marketwatch.com",
    "seekingalpha": "seekingalpha.com", "yahoo": "finance.yahoo.com", "benzinga": "benzinga.com",
    "bloomberg": "bloomberg.com", "businesswire": "businesswire.com",
    "globenewswire": "globenewswire.com", "prnewswire": "prnewswire.com", "dowjones": "wsj.com",
    "wsj": "wsj.com", "motleyfool": "fool.com", "fool": "fool.com", "investing.com":
    "investing.com", "zacks": "zacks.com", "tipranks": "tipranks.com", "thefly": "thefly.com",
    "apnews": "apnews.com", "associatedpress": "apnews.com", "ft": "ft.com",
}  # fmt: skip


def finnhub_outlet(name: str, url: str) -> str | None:
    host = host_of(url)
    if host and not host.endswith("finnhub.io"):
        return host
    key = "".join((name or "").lower().split())
    return FINNHUB_OUTLETS.get(key)


def parse_company_news(rows: list[dict[str, Any]], source_id: str, ticker: str) -> list[RawItem]:
    items = []
    for row in rows:
        url, headline = row.get("url"), clean_text(row.get("headline"), 500)
        if not url or not headline:
            continue
        stamp = row.get("datetime")
        published = dt.datetime.fromtimestamp(stamp, tz=dt.UTC) if stamp else None
        name = str(row.get("source") or "")
        items.append(
            RawItem(
                source_id=source_id,
                url=str(url),
                headline=headline,
                published_at=published,
                summary=clean_text(row.get("summary")),
                language="en",
                outlet_host=finnhub_outlet(name, str(url)),
                outlet_name=name or None,
                tickers=(ticker,),
            )
        )
    return items


async def collect_finnhub(
    http: httpx.AsyncClient,
    api_key: str,
    source_id: str,
    holdings: Sequence[HoldingFeedInfo],
    since: dt.datetime,
    today: dt.date,
) -> CollectResult:
    result = CollectResult()
    for holding in holdings:
        if not holding.symbol:
            continue
        try:
            response = await http.get(
                COMPANY_NEWS_URL,
                params={
                    "symbol": holding.symbol,
                    "from": since.date().isoformat(),
                    "to": today.isoformat(),
                },
                headers={"X-Finnhub-Token": api_key},
            )
        except httpx.HTTPError as exc:
            result.errors.append(f"{holding.ticker}: {type(exc).__name__}")
            continue
        if response.status_code != 200:
            result.errors.append(f"{holding.ticker}: HTTP {response.status_code}")
            continue
        rows = response.json()
        if isinstance(rows, list):
            result.items.extend(parse_company_news(rows, source_id, holding.ticker))
    return result


# ---------------------------------------------------------------------------
# Google News query per holding
# ---------------------------------------------------------------------------

GOOGLE_NEWS_URL = "https://news.google.com/rss/search?q={query}&{locale}"
# A Google News RSS search returns at most 100 items (unofficial endpoint, observed cap).
GOOGLE_NEWS_MAX_ITEMS = 100


def google_news_url(
    names: Sequence[str], days: int, locale: GoogleNewsLocale | None = None
) -> str | None:
    terms = [f'"{n}"' for n in dict.fromkeys(n for n in names if len(n) >= 3)][:5]
    if not terms:
        return None
    query = f"({' OR '.join(terms)}) when:{max(1, days)}d"
    return GOOGLE_NEWS_URL.format(
        query=quote_plus(query), locale=(locale or GoogleNewsLocale()).query_params()
    )


async def collect_google_news(
    http: httpx.AsyncClient,
    source_id: str,
    holdings: Sequence[HoldingFeedInfo],
    days: int,
    locale: GoogleNewsLocale | None = None,
) -> CollectResult:
    result = CollectResult()
    for holding in holdings:
        url = google_news_url(holding.names, days, locale)
        if url is None:
            continue
        try:
            items = await collect_rss(http, source_id, url, (holding.ticker,))
        except FetchError as exc:
            result.errors.append(f"{holding.ticker}: {exc}")
            continue
        result.items.extend(items)
        if len(items) >= GOOGLE_NEWS_MAX_ITEMS:
            result.errors.append(
                f"{holding.ticker}: Google News returned {len(items)} items, its cap; older "
                "items in the window may be missing"
            )
    return result
