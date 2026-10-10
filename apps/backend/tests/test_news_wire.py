"""The wire lane's pure parts: source config, sitemap and GDELT parsing, the circuit breaker, and
the article-text ladder (extraction, paywall-stub detection, rung order)."""

import datetime as dt
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from steward.config.budget import BudgetConfig
from steward.config.news_sources import FetchPolicy, NewsSource, NewsSourcesConfig
from steward.documents.fetch import NotModified, fetch_document
from steward.news.feeds import HoldingFeedInfo, collect_rss_conditional
from steward.news.fulltext import (
    MIN_ARTICLE_CHARS,
    Extracted,
    acquire_fulltext,
    content_hash,
    detect_truncation,
    expiry,
    extract_article,
)
from steward.news.pipeline import breaker_open, next_backoff
from steward.news.wire_sources import (
    collect_gdelt,
    collect_news_sitemap,
    gdelt_query_url,
    parse_gdelt,
    parse_news_sitemap,
    sitemap_page_urls,
)
from steward.research.polite import PoliteFetcher

NOW = dt.datetime(2026, 10, 10, 12, 0, tzinfo=dt.UTC)
FIXTURES = Path(__file__).parent / "fixtures" / "news"

SITEMAP_INDEX = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<sitemap><loc>https://www.reuters.com/arc/outboundfeeds/news-sitemap/?outputType=xml</loc></sitemap>
<sitemap><loc>https://www.reuters.com/arc/outboundfeeds/news-sitemap/?outputType=xml&amp;from=100</loc></sitemap>
<sitemap><loc>https://www.reuters.com/arc/outboundfeeds/news-sitemap/?outputType=xml&amp;from=200</loc></sitemap>
</sitemapindex>"""

SITEMAP_PAGE = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
<url><loc>https://www.reuters.com/markets/toyota-profit-2026-10-10/</loc>
<lastmod>2026-10-10T11:00:00Z</lastmod>
<news:news><news:publication><news:name>Reuters</news:name><news:language>en</news:language>
</news:publication><news:publication_date>2026-10-10T10:55:00Z</news:publication_date>
<news:title>Toyota raises annual profit forecast</news:title></news:news></url>
<url><loc>https://www.reuters.com/no-news-block/</loc></url>
<url><loc>https://www.reuters.com/untitled/</loc>
<news:news><news:publication><news:language>en</news:language></news:publication></news:news></url>
</urlset>"""

LONG_BODY = " ".join(
    f"Sentence number {i} says that the company reported results for the quarter."
    for i in range(30)
)
ARTICLE_HTML = f"""<html><head><title>Toyota raises forecast</title></head><body>
<nav><a href="/">Home</a></nav><article><h1>Toyota raises forecast</h1>
<p>{LONG_BODY}</p></article><footer>Subscribe to our newsletter</footer></body></html>"""


# ---------------------------------------------------------------------------
# Source config
# ---------------------------------------------------------------------------


def test_default_sources_validate_and_are_labelled() -> None:
    config = NewsSourcesConfig()
    by_id = {s.id: s for s in config.sources}
    assert by_id["bloomberg-markets"].paywall == "hard"
    assert by_id["bloomberg-markets"].fetch.rungs == []  # headline + summary only
    assert by_id["bbc-business"].fetch.rungs == [1, 2]
    assert by_id["xinhua-business"].state_media is True
    assert by_id["reuters-sitemap"].type == "news_sitemap"
    # Blocked or gone feeds are kept for the record but off.
    assert by_id["caixin-global"].needs_alt and not by_id["caixin-global"].enabled
    assert by_id["telegraph-business"].needs_alt and not by_id["telegraph-business"].enabled
    # GDELT is off until the probe shows its volume fits the rate limit.
    assert not by_id["gdelt-doc"].enabled


def test_default_outlets_are_tiered() -> None:
    config = NewsSourcesConfig()
    for host in ("www.bbc.co.uk", "asia.nikkei.com", "www.scmp.com", "rss.dw.com"):
        assert config.domain_tier(host) == 2, host
    assert config.domain_tier("www.boj.or.jp") == 1


def test_needs_alt_source_cannot_be_enabled() -> None:
    with pytest.raises(ValidationError, match="needs_alt"):
        NewsSource(id="x", name="X", tier=2, type="rss", url="https://x.test/rss", needs_alt=True)


def test_sitemap_needs_url_and_gdelt_takes_none() -> None:
    with pytest.raises(ValidationError, match="needs a url"):
        NewsSource(id="x", name="X", tier=2, type="news_sitemap")
    with pytest.raises(ValidationError, match="takes no url"):
        NewsSource(id="g", name="G", tier=3, type="gdelt", url="https://x.test/")


def test_fetch_policy_rungs_must_ascend() -> None:
    assert FetchPolicy(rungs=[1, 2, 3]).rungs == [1, 2, 3]
    with pytest.raises(ValidationError, match="ascending"):
        FetchPolicy(rungs=[2, 1])


def test_per_job_monthly_cap_is_validated() -> None:
    cfg = BudgetConfig(per_job_monthly_cap_usd={"news_ingest": 3.0})
    assert cfg.job_monthly_cap("news_ingest") == 3.0
    assert cfg.job_monthly_cap("research") is None
    with pytest.raises(ValidationError):
        BudgetConfig(per_job_monthly_cap_usd={"news_ingest": 0})
    with pytest.raises(ValidationError):
        BudgetConfig(per_job_monthly_cap_usd={"no_such_job": 1})  # type: ignore[dict-item]


# ---------------------------------------------------------------------------
# Sitemap and GDELT
# ---------------------------------------------------------------------------


def test_sitemap_index_lists_pages_newest_first() -> None:
    pages = sitemap_page_urls(SITEMAP_INDEX, "https://www.reuters.com/index")
    assert len(pages) == 3 and pages[0].endswith("outputType=xml")
    assert pages[1].endswith("from=100")
    assert sitemap_page_urls(SITEMAP_PAGE, "https://www.reuters.com/p") == []  # a urlset


def test_news_sitemap_entries_become_items() -> None:
    items = parse_news_sitemap(SITEMAP_PAGE, "reuters-sitemap", region="GB")
    assert len(items) == 1  # no <news:news> block and no title are skipped
    item = items[0]
    assert item.headline == "Toyota raises annual profit forecast"
    assert item.published_at == dt.datetime(2026, 10, 10, 10, 55, tzinfo=dt.UTC)
    assert (item.language, item.region, item.outlet_host) == ("en", "GB", "reuters.com")
    assert item.summary is None


def test_sitemap_rejects_a_dtd() -> None:
    evil = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><urlset></urlset>'
    with pytest.raises(ValueError, match="unsafe or malformed"):
        parse_news_sitemap(evil, "s")


async def test_collect_news_sitemap_reads_only_the_newest_pages() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        body = SITEMAP_INDEX if "index" in request.url.path else SITEMAP_PAGE
        return httpx.Response(200, content=body, headers={"content-type": "application/xml"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        items = await collect_news_sitemap(
            http, "reuters-sitemap", "https://www.reuters.com/index", pages=2
        )
    assert len(requested) == 3  # the index and two pages, not all three
    assert len(items) == 2


GDELT_PAYLOAD = {
    "articles": [
        {
            "url": "https://www.nikkei.com/article/1",
            "title": "トヨタ、通期の利益予想を上方修正",
            "seendate": "20261010T101500Z",
            "domain": "www.nikkei.com",
            "language": "Japanese",
            "sourcecountry": "Japan",
        },
        {"url": "", "title": "no url"},
        {"url": "https://x.test/2", "title": "", "seendate": "20261010T101500Z"},
    ]
}


def test_gdelt_articles_become_tagged_items() -> None:
    items = parse_gdelt(GDELT_PAYLOAD, "gdelt-doc", "7203")
    assert len(items) == 1
    item = items[0]
    assert item.language == "ja" and item.outlet_host == "nikkei.com"
    assert item.tickers == ("7203",)
    assert item.published_at == dt.datetime(2026, 10, 10, 10, 15, tzinfo=dt.UTC)
    assert parse_gdelt("not a dict", "g", "T") == []


def test_gdelt_query_needs_a_real_name() -> None:
    assert gdelt_query_url(["AB"]) is None
    url = gdelt_query_url(["Toyota Motor", "Toyota"], days=2)
    assert url is not None and "timespan=2d" in url and "%22Toyota+Motor%22" in url


def holding(ticker: str, name: str) -> HoldingFeedInfo:
    return HoldingFeedInfo(ticker, (name,), None, None)


async def test_gdelt_rate_limit_stops_the_run_and_spaces_requests() -> None:
    calls: list[str] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if len(calls) == 3:
            return httpx.Response(429, text="Please limit requests")
        return httpx.Response(200, json=GDELT_PAYLOAD)

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    holdings = [holding(f"T{i}", f"Company {i}") for i in range(5)]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await collect_gdelt(http, "gdelt-doc", holdings, 1, sleep=fake_sleep)
    assert len(calls) == 3  # the 429 ends the run: the other holdings would be refused too
    assert len(sleeps) == 2 and all(s >= 5 for s in sleeps)
    assert len(result.items) == 2
    assert any("429" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Source health
# ---------------------------------------------------------------------------


def test_breaker_opens_after_three_failures_and_backs_off() -> None:
    assert next_backoff(1, NOW) is None and next_backoff(2, NOW) is None
    assert next_backoff(3, NOW) == NOW + dt.timedelta(minutes=10)
    assert next_backoff(4, NOW) == NOW + dt.timedelta(minutes=20)
    assert next_backoff(30, NOW) == NOW + dt.timedelta(hours=6)  # capped


def test_breaker_open_only_before_the_backoff_ends() -> None:
    assert not breaker_open(None, NOW)
    assert breaker_open(NOW + dt.timedelta(minutes=1), NOW)
    assert not breaker_open(NOW - dt.timedelta(minutes=1), NOW)


async def test_conditional_get_sends_validators_and_reports_not_modified() -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.headers))
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(
            200,
            content=b'<rss version="2.0"><channel><title>t</title></channel></rss>',
            headers={"etag": '"v1"', "last-modified": "Sat, 10 Oct 2026 10:00:00 GMT"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        _, etag, modified = await collect_rss_conditional(
            http, "s", "https://f.test/rss", (), None, None
        )
        assert etag == '"v1"' and modified == "Sat, 10 Oct 2026 10:00:00 GMT"
        with pytest.raises(NotModified):
            await collect_rss_conditional(http, "s", "https://f.test/rss", (), etag, modified)
        # An unconditional document fetch never sees a 304 and keeps working.
        document = await fetch_document(http, "https://f.test/rss", max_bytes=10_000)
        assert document.etag == '"v1"'
    assert seen[1]["if-none-match"] == '"v1"'


# ---------------------------------------------------------------------------
# Article text: extraction and paywall stubs
# ---------------------------------------------------------------------------


def test_extraction_returns_the_article_not_the_chrome() -> None:
    extracted = extract_article(ARTICLE_HTML, "https://news.test/a")
    assert extracted is not None
    assert "Sentence number 5" in extracted.text
    assert "Subscribe to our newsletter" not in extracted.text or extracted.extractor == "basic"
    assert detect_truncation(extracted.text, ARTICLE_HTML) is None


def test_a_short_page_is_a_stub() -> None:
    assert detect_truncation("Short teaser text.", "<html></html>") == "too_short"
    assert len("x" * (MIN_ARTICLE_CHARS - 1)) < MIN_ARTICLE_CHARS


def test_paywall_notice_at_the_end_is_a_stub_but_one_in_the_middle_is_not() -> None:
    body = LONG_BODY
    assert detect_truncation(body + " Subscribe to continue reading this article.") == (
        "paywall_notice"
    )
    middle = "Readers often ask how to subscribe to continue reading. " + body
    assert detect_truncation(middle) is None


def test_schema_org_paywall_markup_is_a_stub_even_when_the_text_is_long() -> None:
    html = '<script type="application/ld+json">{"isAccessibleForFree": "False"}</script>'
    assert detect_truncation(LONG_BODY, html) == "paywall_markup"


def test_extracted_text_shorter_than_the_summary_is_a_stub() -> None:
    summary = "S" * (len(LONG_BODY) + 50)
    assert detect_truncation(LONG_BODY, None, summary) == "shorter_than_summary"


def test_consent_wall_is_named_as_such() -> None:
    assert detect_truncation("We value your privacy. Accept all cookies.", None) == "consent_wall"


def test_german_and_czech_paywall_notices_are_detected() -> None:
    assert detect_truncation(LONG_BODY + " Weiterlesen mit Handelsblatt Plus") == "paywall_notice"
    assert detect_truncation(LONG_BODY + " Dočtěte článek s předplatným") == "paywall_notice"


def test_content_hash_ignores_whitespace() -> None:
    assert content_hash("a  b\n c") == content_hash("a b c")
    assert content_hash("a b c") != content_hash("a b d")


def test_expiry_follows_retention_unless_the_story_is_kept() -> None:
    policy = FetchPolicy(retention_days=30)
    assert expiry(NOW, policy, keep=False) == NOW + dt.timedelta(days=30)
    assert expiry(NOW, policy, keep=True) is None


# ---------------------------------------------------------------------------
# The ladder
# ---------------------------------------------------------------------------


def page_fetcher(pages: dict[str, tuple[int, str]]) -> tuple[PoliteFetcher, httpx.AsyncClient]:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        status, html = pages.get(str(request.url), (404, ""))
        return httpx.Response(status, content=html.encode(), headers={"content-type": "text/html"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return PoliteFetcher(client, min_interval=0), client


async def test_ladder_takes_feed_text_first_and_never_fetches_the_page() -> None:
    fetcher, client = page_fetcher({})
    async with client:
        result = await acquire_fulltext(
            "https://news.test/a",
            FetchPolicy(rungs=[1, 2]),
            feed_text=LONG_BODY,
            fetcher=fetcher,
        )
    assert (result.state, result.rung) == ("full", 1)


async def test_ladder_falls_through_a_stub_feed_text_to_the_page() -> None:
    fetcher, client = page_fetcher({"https://news.test/a": (200, ARTICLE_HTML)})
    async with client:
        result = await acquire_fulltext(
            "https://news.test/a",
            FetchPolicy(rungs=[1, 2]),
            feed_text="A teaser.",
            fetcher=fetcher,
        )
    assert (result.state, result.rung) == ("full", 2)
    assert result.extracted is not None and "Sentence number 5" in result.extracted.text


async def test_paywall_page_is_stored_as_a_stub_not_as_the_article() -> None:
    paywalled = (
        "<html><body><article><p>Markets fell on Friday.</p>"
        "<p>Subscribe to continue reading this article.</p></article></body></html>"
    )
    fetcher, client = page_fetcher({"https://news.test/a": (200, paywalled)})
    async with client:
        result = await acquire_fulltext(
            "https://news.test/a", FetchPolicy(rungs=[2]), fetcher=fetcher
        )
    assert result.state == "stub" and result.truncated
    assert result.truncation_reason in {"too_short", "paywall_notice"}


async def test_policy_without_rungs_skips_the_fetch_entirely() -> None:
    fetcher, client = page_fetcher({"https://news.test/a": (200, ARTICLE_HTML)})
    async with client:
        result = await acquire_fulltext(
            "https://news.test/a", FetchPolicy(rungs=[]), fetcher=fetcher
        )
    assert result.state == "skipped"


async def test_refused_page_fails_and_is_not_worked_around() -> None:
    fetcher, client = page_fetcher({"https://news.test/a": (403, "")})
    async with client:
        result = await acquire_fulltext(
            "https://news.test/a", FetchPolicy(rungs=[2]), fetcher=fetcher
        )
    assert result.state == "failed" and result.note


async def test_browser_rung_runs_only_when_allowed_and_earlier_rungs_failed() -> None:
    rendered: list[str] = []

    async def render(url: str) -> str | None:
        rendered.append(url)
        return ARTICLE_HTML

    fetcher, client = page_fetcher({"https://news.test/a": (403, "")})
    async with client:
        denied = await acquire_fulltext(
            "https://news.test/a", FetchPolicy(rungs=[2]), fetcher=fetcher, render=render
        )
        allowed = await acquire_fulltext(
            "https://news.test/a", FetchPolicy(rungs=[2, 3]), fetcher=fetcher, render=render
        )
    assert denied.state == "failed" and not rendered[:0]
    assert (allowed.state, allowed.rung) == ("full", 3) and rendered == ["https://news.test/a"]


async def test_api_rung_is_a_hook() -> None:
    async def api(url: str) -> Extracted | None:
        return Extracted(LONG_BODY, "guardian-api", byline="A. Writer")

    result = await acquire_fulltext("https://news.test/a", FetchPolicy(rungs=[4]), api=api)
    assert (result.state, result.rung) == ("full", 4)
    assert result.extracted is not None and result.extracted.byline == "A. Writer"
