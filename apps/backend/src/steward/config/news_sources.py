"""News sources config: the source registry, outlet trust tiers and freshness.

Every ingest run reads the active version, so a source added here is fetched on the next
run with no deploy.
"""

from typing import Annotated, Literal, Self, cast

from pydantic import AnyHttpUrl, Field, model_validator

from steward.config.common import ConfigModel, Slug, Ticker, require_unique

Tier = Literal[1, 2, 3]
SourceType = Literal["rss", "page", "edgar", "finnhub", "google_news"]
Domain = Annotated[
    str,
    Field(
        pattern=r"^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$",
        max_length=253,
        examples=["reuters.com"],
    ),
]

# Types whose URL comes from each holding (EDGAR CIK, Finnhub symbol, aliases), not the config.
PER_HOLDING_TYPES: frozenset[str] = frozenset({"edgar", "finnhub", "google_news"})
# Types that relay other outlets' items: an item's tier is its outlet's, not the source's.
AGGREGATOR_TYPES: frozenset[str] = frozenset({"finnhub", "google_news"})


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
        "google_news: a Google News query per holding (unofficial endpoint)."
    )
    url: AnyHttpUrl | None = Field(default=None, description="Feed or page URL (rss, page).")
    enabled: bool = True
    tickers: list[Ticker] = Field(
        default_factory=list,
        description="Holdings this source is about (e.g. a company's own IR feed): its items "
        "link to them without a name match. Empty: items are matched by name.",
    )

    @model_validator(mode="after")
    def _url_matches_type(self) -> Self:
        if self.type in PER_HOLDING_TYPES and self.url is not None:
            raise ValueError(f"a {self.type} source takes no url; it is built per holding")
        if self.type not in PER_HOLDING_TYPES and self.url is None:
            raise ValueError(f"a {self.type} source needs a url")
        return self


class DomainTier(ConfigModel):
    domain: Domain
    tier: Tier


def _default_sources() -> list[NewsSource]:
    return [
        NewsSource(id="edgar-filings", name="SEC EDGAR filings", tier=1, type="edgar"),
        NewsSource(id="finnhub-company-news", name="Finnhub company news", tier=2, type="finnhub"),
        NewsSource(id="google-news", name="Google News", tier=2, type="google_news", enabled=False),
    ]


def _default_domain_tiers() -> list[DomainTier]:
    tiers: dict[int, list[str]] = {
        1: [
            "sec.gov", "globenewswire.com", "prnewswire.com", "businesswire.com",
            "federalreserve.gov", "ecb.europa.eu", "cnb.cz",
        ],
        2: [
            "reuters.com", "apnews.com", "cnbc.com", "marketwatch.com", "ft.com",
            "bloomberg.com", "wsj.com", "hn.cz", "e15.cz", "seznamzpravy.cz",
        ],
        3: ["seekingalpha.com", "reddit.com", "x.com", "twitter.com", "medium.com"],
    }  # fmt: skip
    return [
        DomainTier(domain=d, tier=cast(Tier, tier))
        for tier, domains in tiers.items()
        for d in domains
    ]


class NewsSourcesConfig(ConfigModel):
    """Stored as `config_version` rows, domain `news_sources`."""

    schema_version: Literal[1] = 1
    sources: list[NewsSource] = Field(default_factory=_default_sources)
    domain_tiers: list[DomainTier] = Field(
        default_factory=_default_domain_tiers,
        description="Trust tier per outlet domain (subdomains included). Every holding's IR "
        "and results domains count as tier 1.",
    )
    blocked_domains: list[Domain] = Field(
        default_factory=list, description="Items from these outlets are dropped."
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

    @model_validator(mode="after")
    def _unique(self) -> Self:
        require_unique("source id", [s.id for s in self.sources])
        require_unique("domain", [d.domain for d in self.domain_tiers])
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

    def blocked(self, host: str | None) -> bool:
        if not host:
            return False
        host = host.lower().removeprefix("www.")
        return any(host == d or host.endswith(f".{d}") for d in self.blocked_domains)
