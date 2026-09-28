"""Holding profiles: where each company publishes, how its fiscal year runs, and what the
news pipeline (Phase 3) should treat as relevant to it."""

import datetime as dt
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


class ManualFundamentals(ConfigModel):
    """Per-share figures entered by hand, for a holding no automatic source covers (e.g.
    Prague listings, whose filings are not machine-readable). Shown as "entered manually"."""

    eps_ttm: float | None = Field(
        default=None,
        description="Diluted earnings per share over the trailing twelve months (or the "
        "latest full year), per ordinary share.",
    )
    book_value_per_share: float | None = Field(
        default=None, description="Equity attributable to shareholders per ordinary share."
    )
    dividend_per_share: float | None = Field(
        default=None, ge=0, description="Dividends per ordinary share paid over the last year."
    )
    currency: CurrencyCode = Field(description="Currency of the per-share figures.")
    as_of: dt.date = Field(description="Period end (or date) the figures are for.")
    source_note: str = Field(
        default="",
        max_length=300,
        description="Where the figures come from, e.g. 'Annual report 2025, p. 112'.",
    )

    @model_validator(mode="after")
    def _some_figure(self) -> Self:
        if (
            self.eps_ttm is None
            and self.book_value_per_share is None
            and (self.dividend_per_share is None)
        ):
            raise ValueError("enter at least one of EPS, book value or dividend per share")
        return self


ValuationSource = Literal["auto", "sec", "esef", "manual"]


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
    reporting_currency: CurrencyCode | None = Field(
        default=None, description="Currency the company reports in; figures in another are held."
    )

    # Valuation (P/E, P/B, dividend yield).
    valuation_source: ValuationSource = Field(
        default="auto",
        description="Which fundamentals the valuation uses: 'auto' takes the freshest "
        "automatic source (SEC XBRL, then ESEF) and the manual figures only when there is "
        "none; 'sec', 'esef' or 'manual' force one.",
    )
    shares_per_listing: float | None = Field(
        default=None,
        gt=0,
        le=1000,
        description="Ordinary shares one priced unit represents, for depositary receipts "
        "(TSMC's ADS = 5 shares). Empty: 1.",
    )
    manual_fundamentals: ManualFundamentals | None = Field(
        default=None,
        description="Hand-entered per-share figures for holdings without an automatic source.",
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
