"""News sources config: the source registry, outlet trust tiers and freshness.

Every ingest run reads the active version, so a source added here is fetched on the next
run with no deploy.
"""

from collections.abc import Collection
from typing import Annotated, Literal, Self, cast

from pydantic import AnyHttpUrl, Field, model_validator

from steward.config.common import ConfigModel, Slug, Ticker, require_unique

Tier = Literal[1, 2, 3]
SourceType = Literal[
    "rss",
    "page",
    "edgar",
    "finnhub",
    "google_news",
    "rns",
    "eqs",
    "edgar_fts",
    "alpha_vantage",
    "news_sitemap",
    "gdelt",
    "ai_search",
    "tdnet",
    "hkex",
    "cninfo",
    "dart",
    "gazette",
    "fca_nsm",
    "filings",
]
Paywall = Literal["open", "metered", "hard"]
Domain = Annotated[
    str,
    Field(
        pattern=r"^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$",
        max_length=253,
        examples=["reuters.com"],
    ),
]

# Types whose URL comes from each holding (EDGAR CIK, Finnhub symbol, aliases, the regulatory
# news page), not the config.
PER_HOLDING_TYPES: frozenset[str] = frozenset(
    {
        "edgar",
        "finnhub",
        "google_news",
        "rns",
        "eqs",
        "edgar_fts",
        "alpha_vantage",
        "gdelt",
        "ai_search",
        "tdnet",
        "hkex",
        "cninfo",
        "dart",
        "gazette",
        "fca_nsm",
        "filings",
    }
)
# Regulatory news services and the home-market country they cover (from FIRDS/FITRS).
REGULATORY_COUNTRY: dict[str, str] = {"rns": "GB", "eqs": "DE"}
# Official disclosure sources (news/primary_sources.py), keyed to the holding's home country.
PRIMARY_COUNTRY: dict[str, str] = {
    "tdnet": "JP",
    "hkex": "HK",
    "cninfo": "CN",
    "dart": "KR",
    "gazette": "GB",
    "fca_nsm": "GB",
}
# Types that relay other outlets' items: an item's tier is its outlet's, not the source's.
AGGREGATOR_TYPES: frozenset[str] = frozenset(
    {"finnhub", "google_news", "alpha_vantage", "gdelt", "ai_search"}
)


Display = Literal["full", "excerpt", "link_only"]
Rung = Literal[1, 2, 3, 4]


def _default_rungs() -> list[Rung]:
    return [1, 2]


class FetchPolicy(ConfigModel):
    """How far the article-text ladder (news/fulltext.py) may go for a source's items."""

    rungs: list[Rung] = Field(
        default_factory=_default_rungs,
        description="Ladder rungs allowed, tried in order: 1 feed text, 2 polite fetch + "
        "extractor, 3 browser render, 4 official API. Empty: headline and summary only.",
    )
    max_requests_per_hour: int = Field(
        default=30, ge=0, le=3600, description="Article page fetches per hour for this source."
    )
    retention_days: int = Field(
        default=90, ge=1, le=3650, description="Days the extracted text is kept."
    )
    display: Display = Field(
        default="excerpt",
        description="What the app shows of the text: full, a short excerpt, or only a link. "
        "Analysis may use more than is displayed.",
    )

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.rungs != sorted(set(self.rungs)):
            raise ValueError("rungs must be unique and in ascending order")
        return self


class NewsSource(ConfigModel):
    id: Slug
    name: str = Field(min_length=1, max_length=100)
    tier: Tier = Field(
        description="1: the company, regulators, central banks, official newswires. "
        "2: curated outlets. 3: blogs and social (labelled sentiment, never alert alone)."
    )
    type: SourceType = Field(
        description="rss: an RSS or Atom feed. page: a news page read daily; new links on it "
        "become items. edgar: every holding's SEC filings. finnhub: US company news. "
        "google_news: a Google News query per holding (unofficial endpoint). "
        "rns: UK regulatory announcements per holding (Investegate company page). "
        "eqs: German regulatory announcements (EQS News company page from the holding "
        "profile's regulatory_news_urls). edgar_fts: SEC full-text search for each holding's "
        "watch topics and for other filers naming it."
    )
    url: AnyHttpUrl | None = Field(default=None, description="Feed or page URL (rss, page).")
    enabled: bool = True
    tickers: list[Ticker] = Field(
        default_factory=list,
        description="Holdings this source is about (e.g. a company's own IR feed): its items "
        "link to them without a name match. Empty: items are matched by name.",
    )
    macro: bool = Field(
        default=False,
        description="A macro or central-bank source: its items are kept without matching a "
        "holding and appear under Markets and macro.",
    )
    region: str | None = Field(
        default=None,
        pattern=r"^[A-Z]{2}$",
        description="ISO country of the outlet or authority (US, GB, JP, ...). Null: global.",
    )
    language: str | None = Field(
        default=None,
        pattern=r"^[a-z]{2,3}$",
        description="Language of the source's items when the feed does not say (ISO 639).",
    )
    paywall: Paywall = Field(
        default="open",
        description="open: the article page is readable. metered: some free reads. hard: only "
        "headline and summary are public. Never bypassed.",
    )
    state_media: bool = Field(
        default=False,
        description="State-run media: labelled, and never the only corroboration of a "
        "political story.",
    )
    bias_note: str | None = Field(
        default=None,
        max_length=200,
        description="Short label shown with the outlet (tabloid, trade press, state media).",
    )
    needs_alt: bool = Field(
        default=False,
        description="The feed is blocked or gone (HTTP 402/403/404); kept off until another "
        "route (Google News query, browser rung) is configured.",
    )
    fetch: FetchPolicy = Field(default_factory=FetchPolicy)

    @model_validator(mode="after")
    def _url_matches_type(self) -> Self:
        if self.type in PER_HOLDING_TYPES and self.url is not None:
            raise ValueError(f"a {self.type} source takes no url; it is built per holding")
        if self.type not in PER_HOLDING_TYPES and self.url is None:
            raise ValueError(f"a {self.type} source needs a url")
        if self.needs_alt and self.enabled:
            raise ValueError("a source marked needs_alt cannot be enabled")
        return self


class DomainTier(ConfigModel):
    domain: Domain
    tier: Tier


class GoogleNewsLocale(ConfigModel):
    """The edition a Google News query searches (its hl, gl and ceid parameters)."""

    language: str = Field(
        default="en-US",
        pattern=r"^[a-z]{2,3}(-[A-Z]{2})?$",
        description="Interface language, e.g. en-US, en-GB, de, cs.",
        examples=["en-US"],
    )
    country: str = Field(
        default="US", pattern=r"^[A-Z]{2}$", description="Edition country, e.g. US, GB, DE, CZ."
    )

    @property
    def ceid(self) -> str:
        return f"{self.country}:{self.language.split('-')[0]}"

    def query_params(self) -> str:
        return f"hl={self.language}&gl={self.country}&ceid={self.ceid}"


def _default_sources() -> list[NewsSource]:
    def central_bank(source_id: str, name: str, url: str, region: str | None = None) -> NewsSource:
        return NewsSource.model_validate(
            {
                "id": source_id,
                "name": name,
                "tier": 1,
                "type": "rss",
                "url": url,
                "macro": True,
                "region": region,
            }
        )

    def outlet(
        source_id: str,
        name: str,
        url: str,
        region: str,
        language: str = "en",
        *,
        paywall: Paywall = "open",
        state_media: bool = False,
        bias_note: str | None = None,
        needs_alt: bool = False,
        disabled: bool = False,
        rungs: tuple[int, ...] = (1, 2),
    ) -> NewsSource:
        return NewsSource.model_validate(
            {
                "id": source_id,
                "name": name,
                "tier": 2,
                "type": "rss",
                "url": url,
                "region": region,
                "language": language,
                "paywall": paywall,
                "state_media": state_media,
                "bias_note": bias_note,
                "needs_alt": needs_alt,
                "enabled": not (needs_alt or disabled),
                # A hard-paywalled outlet stays headline + summary: no article fetch at all.
                "fetch": {
                    "rungs": [] if paywall == "hard" else list(rungs),
                    "display": "link_only" if paywall == "hard" else "excerpt",
                },
            }
        )

    # Outlet feeds probed live on 2026-10-10 (docs/plans/news_coverage_analytics_improvement_
    # plan.md section 2): HTTP 200 with parsable items. Reuse terms are the owner's to check.
    wire = [
        outlet(
            "bloomberg-markets",
            "Bloomberg Markets",
            "https://feeds.bloomberg.com/markets/news.rss",
            "US",
            paywall="hard",
        ),
        outlet(
            "bloomberg-politics",
            "Bloomberg Politics",
            "https://feeds.bloomberg.com/politics/news.rss",
            "US",
            paywall="hard",
        ),
        outlet(
            "bloomberg-technology",
            "Bloomberg Technology",
            "https://feeds.bloomberg.com/technology/news.rss",
            "US",
            paywall="hard",
        ),
        outlet("ft-home", "Financial Times", "https://www.ft.com/rss/home", "GB", paywall="hard"),
        outlet(
            "ft-markets",
            "Financial Times Markets",
            "https://www.ft.com/markets?format=rss",
            "GB",
            paywall="hard",
        ),
        outlet(
            "ft-companies",
            "Financial Times Companies",
            "https://www.ft.com/companies?format=rss",
            "GB",
            paywall="hard",
        ),
        outlet(
            "bbc-business", "BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml", "GB"
        ),
        outlet("bbc-world", "BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml", "GB"),
        outlet("bbc-asia", "BBC Asia", "https://feeds.bbci.co.uk/news/world/asia/rss.xml", "GB"),
        outlet(
            "guardian-business",
            "The Guardian Business",
            "https://www.theguardian.com/uk/business/rss",
            "GB",
        ),
        outlet(
            "sky-business",
            "Sky News Business",
            "https://feeds.skynews.com/feeds/rss/business.xml",
            "GB",
        ),
        outlet(
            "telegraph-business",
            "The Telegraph Business",
            "https://www.telegraph.co.uk/business/rss.xml",
            "GB",
            paywall="hard",
            needs_alt=True,
        ),
        outlet(
            "nikkei-asia",
            "Nikkei Asia",
            "https://asia.nikkei.com/rss/feed/nar",
            "JP",
            paywall="metered",
        ),
        outlet(
            "japan-times",
            "The Japan Times",
            "https://www.japantimes.co.jp/feed/",
            "JP",
            paywall="metered",
        ),
        outlet(
            "scmp-business",
            "SCMP Business",
            "https://www.scmp.com/rss/92/feed",
            "HK",
            paywall="metered",
        ),
        outlet(
            "scmp-china", "SCMP China", "https://www.scmp.com/rss/4/feed", "HK", paywall="metered"
        ),
        outlet(
            "caixin-global",
            "Caixin Global",
            "https://www.caixinglobal.com/rss/",
            "CN",
            paywall="metered",
            needs_alt=True,
        ),
        outlet(
            "xinhua-business",
            "Xinhua Business",
            "https://www.xinhuanet.com/english/rss/businessrss.xml",
            "CN",
            state_media=True,
            bias_note="State media",
        ),
        outlet(
            "straits-times",
            "The Straits Times Business",
            "https://www.straitstimes.com/news/business/rss.xml",
            "SG",
            paywall="metered",
        ),
        outlet("yonhap", "Yonhap News", "https://en.yna.co.kr/RSS/news.xml", "KR"),
        outlet(
            "economic-times",
            "The Economic Times Markets",
            "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
            "IN",
        ),
        outlet(
            "livemint",
            "Mint Markets",
            "https://www.livemint.com/rss/markets",
            "IN",
            paywall="metered",
        ),
        outlet("dw-business", "DW Business", "https://rss.dw.com/rdf/rss-en-bus", "DE"),
        outlet(
            "handelsblatt",
            "Handelsblatt Finanzen",
            "https://www.handelsblatt.com/contentexport/feed/finanzen",
            "DE",
            "de",
            paywall="metered",
        ),
        outlet(
            "faz-finanzen",
            "FAZ Finanzen",
            "https://www.faz.net/rss/aktuell/finanzen/",
            "DE",
            "de",
            paywall="metered",
        ),
        outlet(
            "tagesschau-wirtschaft",
            "Tagesschau Wirtschaft",
            "https://www.tagesschau.de/wirtschaft/index~rss2.xml",
            "DE",
            "de",
        ),
        outlet(
            "globe-and-mail-business",
            "The Globe and Mail Business",
            "https://www.theglobeandmail.com/arc/outboundfeeds/rss/category/business/",
            "CA",
            paywall="metered",
        ),
        outlet(
            "financial-post",
            "Financial Post",
            "https://financialpost.com/feed",
            "CA",
            paywall="metered",
        ),
        outlet("cbc-business", "CBC Business", "https://www.cbc.ca/webfeed/rss/rss-business", "CA"),
        outlet(
            "aljazeera",
            "Al Jazeera",
            "https://www.aljazeera.com/xml/rss/all.xml",
            "QA",
            state_media=True,
            bias_note="State-funded",
        ),
        outlet(
            "cnbc-markets",
            "CNBC",
            "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
            "US",
            # search.cnbc.com's robots.txt says `Disallow: /` for generic agents
            # (docs/plans/news_source_catalog.md): off until the owner decides.
            disabled=True,
        ),
        outlet(
            "marketwatch-top",
            "MarketWatch",
            "https://feeds.content.dowjones.io/public/rss/mw_topstories",
            "US",
            paywall="metered",
        ),
        outlet(
            "wsj-markets",
            "WSJ Markets",
            "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain",
            "US",
            paywall="hard",
        ),
        outlet(
            "hn-cz", "Hospodářské noviny", "https://hn.cz/?m=rss", "CZ", "cs", paywall="metered"
        ),
        outlet("e15", "E15", "https://www.e15.cz/rss", "CZ", "cs", paywall="metered"),
        outlet("seznam-zpravy", "Seznam Zprávy", "https://www.seznamzpravy.cz/rss", "CZ", "cs"),
        NewsSource.model_validate(
            {
                "id": "reuters-sitemap",
                "name": "Reuters (news sitemap)",
                "tier": 2,
                "type": "news_sitemap",
                "url": "https://www.reuters.com/arc/outboundfeeds/news-sitemap-index/?outputType=xml",
                "region": "GB",
                "language": "en",
                "paywall": "hard",
                "fetch": {"rungs": [], "display": "link_only"},
            }
        ),
        # GDELT's free API rate-limits hard (HTTP 429 on a cold probe): off until the N0
        # probe shows the per-holding query volume fits.
        NewsSource(
            id="gdelt-doc",
            name="GDELT DOC (discovery)",
            tier=3,
            type="gdelt",
            enabled=False,
            fetch=FetchPolicy(rungs=[], display="link_only"),
        ),
    ]
    return [
        *wire,
        # Leads from the AI web search job (news/ai_search.py): it runs as its own job, so the
        # news pipeline never collects it. Always tier 3: a lead never alerts on its own.
        NewsSource(
            id="ai-search",
            name="AI web search (leads)",
            tier=3,
            type="ai_search",
            fetch=FetchPolicy(rungs=[], display="link_only"),
        ),
        NewsSource(id="edgar-filings", name="SEC EDGAR filings", tier=1, type="edgar"),
        NewsSource(
            id="edgar-full-text", name="SEC EDGAR full-text search", tier=1, type="edgar_fts"
        ),
        NewsSource(id="finnhub-company-news", name="Finnhub company news", tier=2, type="finnhub"),
        NewsSource(
            id="alpha-vantage-sentiment",
            name="Alpha Vantage ticker sentiment",
            tier=3,
            type="alpha_vantage",
            enabled=False,
        ),
        NewsSource(id="google-news", name="Google News", tier=2, type="google_news", enabled=False),
        # Off until the owner has checked the services' terms for automated personal use.
        NewsSource(
            id="rns-investegate", name="RNS (Investegate)", tier=1, type="rns", enabled=False
        ),
        NewsSource(id="eqs-news", name="EQS News", tier=1, type="eqs", enabled=False),
        central_bank(
            "fed-press",
            "Federal Reserve press releases",
            "https://www.federalreserve.gov/feeds/press_monetary.xml",
        ),
        central_bank("ecb-press", "ECB press releases", "https://www.ecb.europa.eu/rss/press.html"),
        central_bank(
            "cnb-press",
            "ČNB press releases",
            "https://www.cnb.cz/en/.content/rss-feed/rss-feed_tz.xml",
        ),
        # Feeds probed 2026-10-10 (HTTP 200, RSS/Atom) from this machine.
        central_bank(
            "boj-whatsnew",
            "Bank of Japan what's new",
            "https://www.boj.or.jp/en/rss/whatsnew.xml",
            "JP",
        ),
        central_bank(
            "boe-news", "Bank of England news", "https://www.bankofengland.co.uk/rss/news", "GB"
        ),
        central_bank(
            "bundesbank-press",
            "Deutsche Bundesbank",
            "https://www.bundesbank.de/service/rss/en/633306/feed.rss",
            "DE",
        ),
        central_bank(
            "boc-press",
            "Bank of Canada press releases",
            "https://www.bankofcanada.ca/content_type/press-releases/feed/",
            "CA",
        ),
        # Official disclosure sources by market. TDnet, HKEXnews and CNINFO need no key; DART
        # needs DART_API_KEY. `filings` reads the EDINET and Companies House filings the
        # disclosure jobs already store. All but DART are on by default (a saved config gets any
        # that it lacks, see `with_default_sources`); DART stays off until a key is set.
        NewsSource(
            id="tdnet",
            name="TDnet (Tokyo)",
            tier=1,
            type="tdnet",
            enabled=True,
            region="JP",
            language="ja",
        ),
        NewsSource(
            id="hkexnews",
            name="HKEXnews",
            tier=1,
            type="hkex",
            enabled=True,
            region="HK",
            language="en",
        ),
        NewsSource(
            id="cninfo",
            name="CNINFO (Shanghai, Shenzhen)",
            tier=1,
            type="cninfo",
            enabled=True,
            region="CN",
            language="zh",
        ),
        NewsSource(
            id="dart",
            name="DART (Korea)",
            tier=1,
            type="dart",
            enabled=False,
            region="KR",
            language="ko",
        ),
        NewsSource(
            id="gazette",
            name="The Gazette (UK)",
            tier=1,
            type="gazette",
            enabled=True,
            region="GB",
            language="en",
        ),
        NewsSource(
            id="fca-nsm",
            name="FCA National Storage Mechanism (UK)",
            tier=1,
            type="fca_nsm",
            enabled=True,
            region="GB",
            language="en",
        ),
        NewsSource(
            id="filings-edinet-ch",
            name="EDINET and Companies House filings",
            tier=1,
            type="filings",
            enabled=True,
        ),
    ]


def _default_domain_tiers() -> list[DomainTier]:
    tiers: dict[int, list[str]] = {
        1: [
            "sec.gov", "globenewswire.com", "prnewswire.com", "businesswire.com",
            "federalreserve.gov", "ecb.europa.eu", "cnb.cz", "boj.or.jp",
            "bankofengland.co.uk", "bankofcanada.ca", "bundesbank.de", "release.tdnet.info",
            "hkexnews.hk", "cninfo.com.cn", "dart.fss.or.kr", "thegazette.co.uk",
            "data.fca.org.uk",
        ],
        2: [
            "reuters.com", "apnews.com", "cnbc.com", "marketwatch.com", "ft.com",
            "bloomberg.com", "wsj.com", "hn.cz", "e15.cz", "seznamzpravy.cz",
            "bbc.co.uk", "bbc.com", "theguardian.com", "skynews.com", "sky.com",
            "telegraph.co.uk", "nikkei.com", "japantimes.co.jp", "scmp.com", "caixinglobal.com",
            "xinhuanet.com", "straitstimes.com", "yna.co.kr", "economictimes.indiatimes.com",
            "livemint.com", "dw.com", "handelsblatt.com", "faz.net", "tagesschau.de",
            "theglobeandmail.com", "financialpost.com", "cbc.ca", "aljazeera.com",
        ],
        3: ["seekingalpha.com", "reddit.com", "x.com", "twitter.com", "medium.com"],
    }  # fmt: skip
    return [
        DomainTier(domain=d, tier=cast(Tier, tier))
        for tier, domains in tiers.items()
        for d in domains
    ]


class SyndicationGroup(ConfigModel):
    """Outlets that republish one wire's copy: one independence group, counted once as a
    source however many of them carry a story. The first domain is the group's key."""

    name: Slug
    domains: list[Domain] = Field(min_length=2)


def _default_syndication_groups() -> list[SyndicationGroup]:
    return [
        SyndicationGroup(
            name="reuters",
            domains=[
                "reuters.com",
                "finance.yahoo.com",
                "nasdaq.com",
                "investing.com",
                "marketscreener.com",
            ],
        ),
        SyndicationGroup(name="dow-jones", domains=["wsj.com", "marketwatch.com", "barrons.com"]),
    ]


TopicKind = Literal["macro", "regulation", "rates", "currency", "sector", "supply_chain", "other"]


class PortfolioTopic(ConfigModel):
    """A portfolio-level watch topic: wire items that mention one of its keywords are tagged
    with it, so a relevant story that names no holding is still found by topic. A tag never
    links an item to a holding and never alerts."""

    id: Slug
    label: str = Field(min_length=1, max_length=80)
    kind: TopicKind = "other"
    keywords: list[Annotated[str, Field(min_length=2, max_length=80)]] = Field(
        min_length=1, max_length=40
    )
    enabled: bool = True


def _default_portfolio_topics() -> list[PortfolioTopic]:
    def topic(id_: str, label: str, kind: TopicKind, *keywords: str) -> PortfolioTopic:
        return PortfolioTopic(id=id_, label=label, kind=kind, keywords=list(keywords))

    return [
        topic("rates", "Interest rates", "rates", "interest rate", "rate cut", "rate hike",
              "central bank", "yield curve", "inflation"),
        topic("trade-policy", "Tariffs and trade policy", "regulation", "tariff", "tariffs",
              "export controls", "sanctions", "trade war"),
        topic("antitrust", "Antitrust and regulation", "regulation", "antitrust",
              "competition authority", "SEC investigation", "fined"),
        topic("fx", "Currencies", "currency", "dollar", "euro", "yen", "yuan", "sterling",
              "currency"),
        topic("semiconductors", "Semiconductor supply", "supply_chain", "semiconductor",
              "chip shortage", "foundry", "TSMC"),
        topic("ai-compute", "AI and data centres", "sector", "artificial intelligence",
              "data center", "data centre", "GPU", "large language model"),
        topic("energy-oil", "Oil and gas", "sector", "crude oil", "Brent", "OPEC", "natural gas",
              "LNG"),
        topic("energy-transition", "Renewables and electrification", "sector", "solar",
              "wind power", "electric vehicle", "battery", "hydrogen"),
        topic("metals-mining", "Metals and mining", "sector", "copper", "lithium", "iron ore",
              "gold price", "rare earths"),
        topic("agriculture", "Food and agriculture", "sector", "wheat", "fertilizer",
              "crop yields", "grain exports"),
        topic("banks-credit", "Banks and credit", "sector", "bank run", "credit spreads",
              "loan losses", "capital requirements", "private credit"),
        topic("real-estate", "Property markets", "sector", "commercial real estate",
              "mortgage rates", "housing starts", "property developer"),
        topic("healthcare-pharma", "Pharma and healthcare", "sector", "FDA approval",
              "clinical trial", "drug pricing", "GLP-1"),
        topic("cybersecurity", "Cyber incidents", "other", "cyberattack", "ransomware",
              "data breach", "outage"),
        topic("geopolitics-asia", "Asia geopolitics", "macro", "Taiwan Strait", "South China Sea",
              "North Korea", "Kashmir"),
        topic("geopolitics-europe", "Europe and Russia", "macro", "Ukraine", "NATO",
              "gas supplies", "Russian sanctions"),
        topic("middle-east", "Middle East", "macro", "Red Sea", "Strait of Hormuz", "Gaza",
              "Iran"),
        topic("china-economy", "China economy", "macro", "China property", "PBOC",
              "yuan fixing", "stimulus package"),
        topic("japan-policy", "Japan policy", "macro", "Bank of Japan", "yen intervention",
              "Nikkei", "Tokyo CPI"),
        topic("europe-policy", "Euro area policy", "macro", "ECB", "Bundesbank", "eurozone",
              "energy prices"),
        topic("us-fiscal", "US fiscal and politics", "macro", "debt ceiling", "Treasury",
              "government shutdown", "federal budget"),
        topic("emerging-markets", "Emerging markets", "macro", "emerging markets", "capital flows",
              "sovereign default", "IMF programme"),
        topic("commodities-shipping", "Shipping and freight", "supply_chain", "container rates",
              "port strike", "Suez Canal", "Panama Canal", "freight"),
        topic("labour", "Labour and strikes", "macro", "strike action", "union", "layoffs",
              "unemployment"),
        topic("climate-weather", "Climate and weather shocks", "macro", "hurricane", "drought",
              "heatwave", "flooding", "wildfire"),
        topic("accounting-fraud", "Accounting and governance", "regulation", "restatement",
              "short seller", "auditor resigns", "whistleblower"),
        topic("m-and-a", "Deals", "other", "takeover bid", "merger", "acquisition", "buyout",
              "spin-off"),
        topic("ipo-markets", "Equity issuance", "other", "IPO", "share placement",
              "secondary offering", "rights issue"),
        topic("crypto", "Crypto assets", "other", "bitcoin", "stablecoin", "crypto exchange",
              "ETF inflows"),
        topic("tech-regulation", "Tech regulation", "regulation", "Digital Markets Act",
              "app store", "data protection", "content moderation"),
        topic("defence", "Defence spending", "sector", "defence budget", "arms", "missile",
              "military contract"),
    ]  # fmt: skip


class NewsSourcesConfig(ConfigModel):
    """Stored as `config_version` rows, domain `news_sources`."""

    schema_version: Literal[1] = 1
    portfolio_topics: list[PortfolioTopic] = Field(
        default_factory=_default_portfolio_topics,
        description="Watch topics for the whole portfolio (macro, regulation, rates, currencies, "
        "sectors, supply chain). Matching wire items are tagged with the topic id.",
    )
    syndication_groups: list[SyndicationGroup] = Field(
        default_factory=_default_syndication_groups,
        description="Outlets that republish one wire's copy. A story carried by several of "
        "them counts as one independent source, not several.",
    )
    sources: list[NewsSource] = Field(default_factory=_default_sources)
    domain_tiers: list[DomainTier] = Field(
        default_factory=_default_domain_tiers,
        description="Trust tier per outlet domain (subdomains included). Every holding's IR "
        "and results domains count as tier 1.",
    )
    blocked_domains: list[Domain] = Field(
        default_factory=list, description="Items from these outlets are dropped."
    )
    allowed_domains: list[Domain] = Field(
        default_factory=list,
        description="When set, aggregator items (Finnhub, Google News) are kept only from "
        "these outlets (and the tiered ones above).",
    )
    unknown_outlet_tier: Tier = Field(
        default=3,
        description="Tier for an aggregator's item from an outlet not listed above.",
    )
    freshness_hours: int = Field(
        default=72, ge=1, le=720, description="Items published earlier are dropped."
    )
    include_tier3: bool = Field(
        default=False,
        description="Keep tier-3 items (labelled sentiment; they never alert on their own).",
    )
    google_news_locale: GoogleNewsLocale = Field(
        default_factory=GoogleNewsLocale,
        description="The Google News edition searched by google_news sources.",
    )

    @model_validator(mode="after")
    def _unique(self) -> Self:
        require_unique("source id", [s.id for s in self.sources])
        require_unique("domain", [d.domain for d in self.domain_tiers])
        require_unique("portfolio topic id", [t.id for t in self.portfolio_topics])
        return self

    def domain_tier(self, host: str | None) -> int | None:
        """The configured tier of an outlet host, matching the longest listed domain."""
        if not host:
            return None
        host = host.lower().removeprefix("www.")
        best: tuple[int, int] | None = None
        for entry in self.domain_tiers:
            if host == entry.domain or host.endswith(f".{entry.domain}"):
                if best is None or len(entry.domain) > best[0]:
                    best = (len(entry.domain), entry.tier)
        return best[1] if best else None

    def independence_key(self, host: str | None) -> str:
        """The outlet's independence group key (its group's first domain), else the host."""
        if not host:
            return ""
        clean = host.lower().removeprefix("www.")
        for group in self.syndication_groups:
            if any(clean == d or clean.endswith(f".{d}") for d in group.domains):
                return str(group.domains[0])
        return clean

    def group_members(self, hosts: Collection[str]) -> frozenset[str]:
        """`hosts` plus every other domain of the groups they belong to (so a search does not
        count another outlet of the same wire as independent confirmation)."""
        out = set(hosts)
        for group in self.syndication_groups:
            if any(self.independence_key(h) == group.domains[0] for h in hosts):
                out.update(group.domains)
        return frozenset(out)

    def allowed_aggregated(self, host: str | None) -> bool:
        """Whether an aggregator's item from `host` passes the allow list."""
        if not self.allowed_domains:
            return True
        if not host:
            return False
        host = host.lower().removeprefix("www.")
        listed = [*self.allowed_domains, *(d.domain for d in self.domain_tiers if d.tier <= 2)]
        return any(host == d or host.endswith(f".{d}") for d in listed)

    def blocked(self, host: str | None) -> bool:
        if not host:
            return False
        host = host.lower().removeprefix("www.")
        return any(host == d or host.endswith(f".{d}") for d in self.blocked_domains)


def with_default_sources(config: NewsSourcesConfig) -> NewsSourcesConfig:
    """The config with the default official-disclosure source of any type the saved list has
    none of. A config saved before a source type existed (TDnet, the FCA NSM...) would never
    run it, and the owner would have to add it by hand; the stored version is not changed."""
    have = {s.type for s in config.sources} | {s.id for s in config.sources}
    missing = [
        s
        for s in _default_sources()
        if (s.type in PRIMARY_COUNTRY or s.type == "filings")
        and s.type not in have
        and s.id not in have
    ]
    if not missing:
        return config
    return config.model_copy(update={"sources": [*config.sources, *missing]})
