"""Holding profiles: where each company publishes, how its fiscal year runs, and what the
news pipeline (Phase 3) should treat as relevant to it."""

from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, Field, model_validator

from steward.config.common import ConfigModel, CurrencyCode, Ticker, Weight, require_unique

ShortText = Annotated[str, Field(min_length=1, max_length=200)]
Domain = Annotated[
    str,
    Field(
        pattern=r"^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$",
        max_length=253,
        examples=["investor.apple.com"],
    ),
]


class Exposure(ConfigModel):
    kind: Literal["revenue_currency", "revenue_region", "commodity_input", "other"]
    name: ShortText = Field(examples=["USD", "Greater China", "Copper"])
    share: Weight | None = Field(default=None, description="Share of revenue or cost, 0..1.")


class HoldingProfile(ConfigModel):
    ticker: Ticker
    company_name: str = Field(default="", max_length=200)
    aliases: list[ShortText] = Field(
        default_factory=list, description="Other and former names, used to match news."
    )

    # Where results are published.
    ir_url: AnyHttpUrl | None = Field(default=None, description="Investor relations home page.")
    results_urls: list[AnyHttpUrl] = Field(
        default_factory=list,
        description="Pages where results press releases or reports are published.",
    )
    financial_calendar_url: AnyHttpUrl | None = Field(
        default=None, description="IR financial calendar page with upcoming report dates."
    )
    regulatory_news_urls: list[AnyHttpUrl] = Field(default_factory=list)
    extra_domains: list[Domain] = Field(
        default_factory=list,
        description="Further domains the earnings check may search and fetch from "
        "(the IR and results URLs' domains are always allowed).",
    )

    # Filings and fiscal calendar.
    sec_cik: str | None = Field(
        default=None,
        pattern=r"^[0-9]{1,10}$",
        description="SEC Central Index Key. US issuers (8-K) and foreign issuers that furnish "
        "results on 6-K. Looked up from the ticker when empty.",
    )
    sec_ticker: str | None = Field(
        default=None,
        pattern=r"^[A-Z0-9.\-]{1,10}$",
        description="Ticker as EDGAR and Finnhub list it, when it differs from the "
        "Trading212 ticker's prefix.",
    )
    fiscal_year_end_month: int = Field(
        default=12,
        ge=1,
        le=12,
        description="Month the fiscal year ends in (Apple: 9, Microsoft: 6). Maps fiscal "
        "quarters to calendar periods.",
    )
    price_symbol: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9._-]{1,24}$",
        description="Stooq symbol for price history when the one derived from OpenFIGI is "
        "wrong or missing (e.g. 'sap.de').",
    )
    yahoo_symbol: str | None = Field(
        default=None,
        pattern=r"^[A-Z0-9][A-Z0-9.=-]{0,23}$",
        description="Yahoo Finance symbol (intraday prices, and daily prices when Stooq has "
        "none) when the one derived from the listing is wrong or missing (e.g. 'NOVO-B.CO').",
    )
    reporting_currency: CurrencyCode | None = Field(
        default=None, description="Currency the company reports in; figures in another are held."
    )

    # News relevance (used from Phase 3).
    competitors: list[ShortText] = Field(default_factory=list)
    suppliers: list[ShortText] = Field(default_factory=list)
    customers: list[ShortText] = Field(default_factory=list)
    watch_topics: list[ShortText] = Field(default_factory=list)
    exposures: list[Exposure] = Field(default_factory=list)

    def allowed_domains(self) -> list[str]:
        urls = [self.ir_url, self.financial_calendar_url, *self.results_urls]
        hosts = {urlsplit(str(u)).hostname for u in urls if u is not None}
        return sorted({h for h in hosts if h} | set(self.extra_domains))

    def edgar_ticker(self) -> str:
        return self.sec_ticker or self.ticker.split("_")[0].rstrip("abcdefghijklmnopqrstuvwxyz")


class HoldingProfilesConfig(ConfigModel):
    """All holding profiles. Stored as `config_version` rows, domain `holding_profiles`."""

    schema_version: Literal[1] = 1
    profiles: list[HoldingProfile] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_profile_per_ticker(self) -> Self:
        require_unique("profile ticker", [p.ticker for p in self.profiles])
        return self

    def for_ticker(self, ticker: str) -> HoldingProfile | None:
        return next((p for p in self.profiles if p.ticker == ticker), None)
