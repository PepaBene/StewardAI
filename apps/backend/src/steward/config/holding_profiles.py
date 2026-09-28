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


class ShareBasis(ConfigModel):
    """How a holding's 13F figures (shares of its US line) compare with shares outstanding.

    Defaults when a field is empty (steward.fundamentals.shares.DEFAULT_BASIS_BY_CIK): one US
    share represents one home-market share, except TSM (1 ADS = 5 common shares, as its
    20-F states); where a filing tags shares outstanding per class, every class is counted
    (Alphabet A+B+C, Novo Nordisk A+B). Narrow it to the class the US line is, e.g. GOOGL
    us-gaap:CommonClassAMember or NVO nvo:BShareCapitalMember.
    """

    ads_ratio: float | None = Field(
        default=None,
        gt=0,
        le=1000,
        description="Home-market shares represented by one share of the US-listed line "
        "(ADS ratio). Empty: the default (TSM 5, others 1).",
    )
    share_classes: list[Annotated[str, Field(pattern=r"^[A-Za-z0-9-]+:[A-Za-z0-9_]+$")]] = Field(
        default_factory=list,
        description="Share-class members counted in the denominator when the filing "
        "tags shares outstanding per class, e.g. us-gaap:CommonClassAMember. Empty (the "
        "default): all classes.",
    )
    manual_shares_outstanding: int | None = Field(
        default=None,
        gt=0,
        description="Shares outstanding entered by hand, for issuers that do not file XBRL "
        "with the SEC (e.g. Prague-listed companies). Used only when no SEC figure exists.",
    )
    manual_as_of: dt.date | None = Field(default=None, description="Date the manual figure is for.")
    manual_source: str = Field(
        default="",
        max_length=300,
        description="Where the manual figure comes from, e.g. 'CEZ shareholder structure page'.",
    )

    @model_validator(mode="after")
    def _manual_is_dated(self) -> Self:
        if self.manual_shares_outstanding is not None and self.manual_as_of is None:
            raise ValueError("manual_as_of is required with manual_shares_outstanding")
        return self


class ManualSegmentRow(ConfigModel):
    """Revenue for one region, segment or product, entered by hand from an annual report
    (for issuers whose filings carry no machine-readable breakdown, e.g. CEZ and KB)."""

    label: ShortText = Field(examples=["Czech Republic"])
    axis: Literal["geographic", "segment", "product"]
    value: float = Field(ge=0, description="Revenue in `currency` for the period.")
    currency: CurrencyCode
    period_start: dt.date
    period_end: dt.date
    source: str = Field(
        min_length=1,
        max_length=300,
        description="Where the figure comes from, e.g. 'Annual Report 2025, segment note, p. 212'.",
    )

    @model_validator(mode="after")
    def _period_order(self) -> Self:
        if self.period_end < self.period_start:
            raise ValueError("period_end is before period_start")
        return self


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

    # SEC XBRL fundamentals: the 13F share basis and revenue breakdowns entered by hand.
    share_basis: ShareBasis = Field(default_factory=ShareBasis)
    manual_segments: list[ManualSegmentRow] = Field(
        default_factory=list,
        max_length=60,
        description="Revenue by region / segment / product entered by hand; shown only when "
        "no SEC XBRL breakdown exists for the holding.",
    )

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
