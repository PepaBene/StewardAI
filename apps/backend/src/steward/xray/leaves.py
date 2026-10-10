"""Leaves: the atoms every X-Ray view is built from (plan M5.1, M5.2).

A leaf is one weighted piece of the portfolio: a directly held stock, one published row of a held
fund (scaled by the fund's weight), or a labelled non-security remainder (cash, a fund's own
cash and derivatives, a fund that is not looked through, the part of a fund its rows do not
account for). Leaf weights are shares of total portfolio value and add up to
`1 + over_reported`, exactly as `funds.aggregate` does, so the two agree.

Each classification carries the way it was obtained:

- `reported`: published by the fund issuer, the instrument list or a data provider;
- `inferred`: StewardAI derived it (a region from a country, a provider sector label mapped to
  GICS, a currency the issuer did not publish);
- `owner`: you entered it (strategy config, `look_through.direct_*`, a segment mapping);
- `none`: not known (the bucket is `unclassified`).

A country label also says what kind of country it is, because the issuers disagree:
`country_of_risk`, `listing_country`, `home_market` (the instrument list's home market of a
directly held stock), `owner`, or `unstated`. Views never merge those without saying so.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from steward.config.jobs import LookThroughSettings
from steward.funds.aggregate import (
    IssuerRef,
    config_groups,
    issuer_for,
)
from steward.lookthrough.classify import COUNTRIES, country_code, region_of
from steward.lookthrough.read import (
    CASH,
    FUND_CASH,
    NOT_LOOKED,
    UNKNOWN_FUND,
    PortfolioIn,
    PositionIn,
    RowIn,
    SnapshotIn,
    _currency,
    is_fund_type,
)

Basis = Literal["reported", "inferred", "owner", "none"]
ChannelKind = Literal["direct", "fund", "cash"]

DIRECT_CHANNEL = "direct"
CASH_CHANNEL = "cash"

# The data provider that fills sector and industry for a directly held stock labels sectors its own
# way; this is the stated, explicit mapping to the GICS names the funds use. Anything else stays
# unmapped rather than guessed.
PROVIDER_SECTOR_TO_GICS: dict[str, str] = {
    "technology": "Information Technology",
    "information technology": "Information Technology",
    "financial services": "Financials",
    "financials": "Financials",
    "financial": "Financials",
    "healthcare": "Health Care",
    "health care": "Health Care",
    "consumer cyclical": "Consumer Discretionary",
    "consumer discretionary": "Consumer Discretionary",
    "consumer defensive": "Consumer Staples",
    "consumer staples": "Consumer Staples",
    "basic materials": "Materials",
    "materials": "Materials",
    "communication services": "Communication Services",
    "industrials": "Industrials",
    "energy": "Energy",
    "utilities": "Utilities",
    "real estate": "Real Estate",
}


def gics_from_provider(label: str | None) -> str | None:
    return PROVIDER_SECTOR_TO_GICS.get((label or "").strip().lower())


@dataclass(frozen=True)
class Profile:
    """A data provider's sector, industry, country and exchange for one ticker."""

    sector: str | None = None
    industry: str | None = None
    country: str | None = None
    exchange: str | None = None


# Where a provider's exchange name trades: matched as a lower-case fragment, first hit wins. An
# exchange not listed here leaves the listing country unknown rather than guessed.
EXCHANGE_COUNTRY: tuple[tuple[str, str], ...] = (
    ("nasdaq", "US"), ("nyse", "US"), ("amex", "US"), ("cboe", "US"), ("bats", "US"),
    ("london", "GB"), ("lse", "GB"), ("xetra", "DE"), ("frankfurt", "DE"), ("stuttgart", "DE"),
    ("berlin", "DE"), ("hamburg", "DE"), ("munich", "DE"), ("dusseldorf", "DE"),
    ("paris", "FR"), ("euronext paris", "FR"), ("amsterdam", "NL"), ("brussels", "BE"),
    ("lisbon", "PT"), ("dublin", "IE"), ("milan", "IT"), ("borsa italiana", "IT"),
    ("madrid", "ES"), ("swiss", "CH"), ("six ", "CH"), ("vienna", "AT"), ("copenhagen", "DK"),
    ("stockholm", "SE"), ("helsinki", "FI"), ("oslo", "NO"), ("warsaw", "PL"),
    ("prague", "CZ"), ("toronto", "CA"), ("tsx", "CA"), ("tokyo", "JP"), ("japan", "JP"),
    ("hong kong", "HK"), ("shanghai", "CN"), ("shenzhen", "CN"), ("korea", "KR"),
    ("taiwan", "TW"), ("australia", "AU"), ("asx", "AU"), ("nse", "IN"), ("bombay", "IN"),
    ("singapore", "SG"), ("johannesburg", "ZA"), ("sao paulo", "BR"), ("mexic", "MX"),
)  # fmt: skip


def listing_country(exchange: str | None) -> str | None:
    text = (exchange or "").strip().lower()
    if not text:
        return None
    for fragment, code in EXCHANGE_COUNTRY:
        if fragment in text:
            return code
    return None


def domicile_of(profile: Profile, isin: str | None) -> tuple[str | None, str]:
    """Where the company is based, with the basis: the provider's country when stored (reported),
    else the ISIN's country prefix, which is where the security was registered (inferred)."""
    code = country_code(profile.country)
    if code:
        return code, "reported"
    prefix = (isin or "")[:2].upper()
    if prefix in COUNTRIES:
        return prefix, "inferred"
    return None, "none"


@dataclass(frozen=True)
class IssuerFacts:
    """Size and valuation inputs for one issuer, from the stored statements and prices."""

    market_cap_eur: Decimal | None = None
    earnings_yield: Decimal | None = None
    price_to_book: Decimal | None = None
    profitability: Decimal | None = None
    dividend_yield: Decimal | None = None
    net_debt_to_equity: Decimal | None = None
    net_debt_to_ebitda: Decimal | None = None
    covered: bool = False  # the app holds statements and prices for this issuer


@dataclass(frozen=True)
class RevenueSlice:
    """Part of one company's revenue in one value of a revenue or trade dimension."""

    value: str
    share: float  # of the company's operating revenue
    basis: Basis  # reported label, or the owner's mapping


@dataclass(frozen=True)
class Leaf:
    channel_kind: ChannelKind
    channel_key: str  # "direct", the fund's ISIN or ticker, "cash"
    channel_label: str
    weight: float
    asset_class: str  # equity, cash, derivative, other, unknown
    special: str | None = None  # CASH, FUND_CASH, NOT_LOOKED or UNKNOWN_FUND
    issuer_key: str | None = None
    issuer_label: str | None = None
    isin: str | None = None
    ticker: str | None = None
    name: str | None = None
    sector: str | None = None
    sector_basis: Basis = "none"
    industry: str | None = None
    industry_basis: Basis = "none"
    country: str | None = None
    country_basis: Basis = "none"
    country_kind: str = "unstated"
    currency: str | None = None
    currency_basis: Basis = "none"
    domicile: str | None = None  # where the company is based, never mixed with `country`
    domicile_basis: Basis = "none"
    listing: str | None = None  # country of the exchange the provider names
    listing_basis: Basis = "none"

    @property
    def is_equity(self) -> bool:
        return self.asset_class == "equity" and self.special is None


@dataclass
class ChannelInfo:
    """A held position that supplies leaves: direct stocks share one channel, each fund and the cash
    are their own."""

    key: str
    label: str
    kind: ChannelKind
    weight: float = 0.0
    expense_ratio: Decimal | None = None
    ticker: str | None = None


@dataclass
class XrayData:
    portfolio: PortfolioIn | None
    settings: LookThroughSettings
    snapshots: dict[str, SnapshotIn]
    register: dict[str, IssuerRef] = field(default_factory=dict)
    expense_ratios: dict[str, Decimal] = field(default_factory=dict)
    profiles: dict[str, Profile] = field(default_factory=dict)  # by ticker
    revenue: dict[str, dict[str, list[RevenueSlice]]] = field(default_factory=dict)
    # Float rounding only: a dropped leaf of even 0.1 % of the portfolio must not read "reconciles".
    tolerance: float = 1e-6


@dataclass
class LeafSet:
    leaves: list[Leaf]
    channels: dict[str, ChannelInfo]
    total: float
    over_reported: float
    base_currency: str | None
    direct_value: float = 0.0
    fund_value: float = 0.0
    unidentified_weight: float = 0.0


def lookup_key(data_map: dict, isin: str | None, ticker: str | None):  # type: ignore[no-untyped-def,type-arg]
    """An entry stored by ISIN, else by ticker. Fund rows carry the issuer's ticker, which is
    not always the Trading212 one, so the ISIN is the reliable key."""
    if isin and isin.upper() in data_map:
        return data_map[isin.upper()]
    if ticker and ticker.upper() in data_map:
        return data_map[ticker.upper()]
    return None


def _profile(data: XrayData, isin: str | None, ticker: str | None) -> Profile:
    return lookup_key(data.profiles, isin, ticker) or Profile()


def _country(code: str | None) -> str | None:
    return code if code in COUNTRIES else None


def _direct_leaf(
    p: PositionIn,
    w: float,
    data: XrayData,
    groups: dict[str, IssuerRef],
    match: tuple[RowIn, SnapshotIn] | None,
) -> Leaf:
    settings = data.settings
    ref = issuer_for(p.isin, p.name or p.ticker, groups, data.register)
    prof = _profile(data, p.isin, p.ticker)

    country = _country(settings.direct_countries.get(p.ticker))
    country_basis: Basis = "owner" if country else "none"
    country_kind = "owner"
    if country is None:
        country = _country(p.home_country)
        if country:
            country_basis, country_kind = "reported", "home_market"
    if country is None and match is not None:
        country = _country(match[0].country)
        if country:
            country_basis, country_kind = "reported", match[1].country_basis or "unstated"

    sector: str | None = settings.direct_sectors.get(p.ticker)
    sector_basis: Basis = "owner" if sector else "none"
    if sector is None and match is not None and match[0].sector:
        sector, sector_basis = match[0].sector, "reported"
    if sector is None:
        mapped = gics_from_provider(prof.sector)
        if mapped:
            sector, sector_basis = mapped, "inferred"

    currency = _currency(p.currency)
    domicile, domicile_basis = domicile_of(prof, p.isin)
    listed = listing_country(prof.exchange)
    return Leaf(
        channel_kind="direct",
        channel_key=DIRECT_CHANNEL,
        channel_label="Direct holdings",
        weight=w,
        asset_class="equity",
        issuer_key=ref.key if ref else None,
        issuer_label=ref.label if ref else (p.name or p.ticker),
        isin=p.isin,
        ticker=p.ticker,
        name=p.name or p.ticker,
        sector=sector,
        sector_basis=sector_basis,
        industry=prof.industry,
        industry_basis="reported" if prof.industry else "none",
        country=country,
        country_basis=country_basis,
        country_kind=country_kind if country else "unstated",
        currency=currency,
        currency_basis="reported" if currency else "none",
        domicile=domicile,
        domicile_basis=domicile_basis,  # type: ignore[arg-type]
        listing=listed,
        listing_basis="inferred" if listed else "none",
    )


def _fund_row_leaf(
    r: RowIn,
    w: float,
    snap: SnapshotIn,
    fund: ChannelInfo,
    data: XrayData,
    groups: dict[str, IssuerRef],
) -> Leaf:
    ref = issuer_for(r.isin, r.name, groups, data.register)
    prof = _profile(data, r.isin, r.ticker)
    sector, sector_basis = r.sector, ("reported" if r.sector else "none")
    if sector is None:
        mapped = gics_from_provider(prof.sector)
        if mapped:
            sector, sector_basis = mapped, "inferred"
    country = _country(r.country)
    currency = r.currency
    domicile, domicile_basis = domicile_of(prof, r.isin)
    listed = listing_country(prof.exchange)
    return Leaf(
        channel_kind="fund",
        channel_key=fund.key,
        channel_label=fund.label,
        weight=w,
        asset_class="equity",
        issuer_key=ref.key if ref else None,
        issuer_label=ref.label if ref else r.name,
        isin=r.isin,
        ticker=r.ticker,
        name=r.name,
        sector=sector,
        sector_basis=sector_basis,  # type: ignore[arg-type]
        industry=prof.industry,
        industry_basis="reported" if prof.industry else "none",
        country=country,
        country_basis="reported" if country else "none",
        country_kind=(snap.country_basis or "unstated") if country else "unstated",
        currency=currency,
        currency_basis=("inferred" if r.currency_derived else "reported") if currency else "none",
        domicile=domicile,
        domicile_basis=domicile_basis,  # type: ignore[arg-type]
        listing=listed,
        listing_basis="inferred" if listed else "none",
    )


def build_leaves(data: XrayData) -> LeafSet:
    portfolio = data.portfolio
    if portfolio is None:
        return LeafSet([], {}, 0.0, 0.0, None)
    settings = data.settings
    groups = config_groups(settings)
    total = sum(p.value_base for p in portfolio.positions) + portfolio.cash_base
    channels: dict[str, ChannelInfo] = {}
    leaves: list[Leaf] = []
    over = 0.0
    direct_value = fund_value = 0.0
    unidentified = 0.0

    matches: dict[str, tuple[RowIn, SnapshotIn]] = {}
    for fund_snap in data.snapshots.values():
        for r in fund_snap.rows:
            if r.isin and r.asset_class == "equity":
                matches.setdefault(r.isin, (r, fund_snap))

    direct = ChannelInfo(DIRECT_CHANNEL, "Direct holdings", "direct")

    for p in portfolio.positions:
        w = p.value_base / total if total > 0 else 0.0
        fund_cfg = settings.fund(p.isin)
        if fund_cfg is None and not is_fund_type(p.security_type):
            direct_value += p.value_base
            direct.weight += w
            match = matches.get(p.isin or "")
            leaf = _direct_leaf(p, w, data, groups, match)
            if leaf.issuer_key is None:
                unidentified += w
            leaves.append(leaf)
            continue

        fund_value += p.value_base
        key = p.isin or p.ticker
        label = fund_cfg.label if fund_cfg else (p.name or p.ticker)
        ratio = data.expense_ratios.get(p.isin or "")
        fund = channels.setdefault(
            key, ChannelInfo(key, label, "fund", expense_ratio=ratio, ticker=p.ticker)
        )
        fund.weight += w
        snap = data.snapshots.get(p.isin or "") if fund_cfg else None
        if snap is None:
            leaves.append(
                Leaf(
                    channel_kind="fund",
                    channel_key=key,
                    channel_label=label,
                    weight=w,
                    asset_class="unknown",
                    special=NOT_LOOKED,
                )
            )
            continue
        rows_total = sum(r.weight for r in snap.rows)
        residual = 1.0 - rows_total
        over += w * max(-residual, 0.0)
        by_class: dict[str, float] = defaultdict(float)
        for r in snap.rows:
            if r.asset_class == "equity":
                leaves.append(_fund_row_leaf(r, w * r.weight, snap, fund, data, groups))
            else:
                by_class[r.asset_class] += w * r.weight
        for asset_class, weight in by_class.items():
            leaves.append(
                Leaf(
                    channel_kind="fund",
                    channel_key=key,
                    channel_label=label,
                    weight=weight,
                    asset_class=asset_class,
                    special=FUND_CASH,
                )
            )
        if residual > 0:
            leaves.append(
                Leaf(
                    channel_kind="fund",
                    channel_key=key,
                    channel_label=label,
                    weight=w * residual,
                    asset_class="unknown",
                    special=UNKNOWN_FUND,
                )
            )

    cash_w = portfolio.cash_base / total if total > 0 else 0.0
    cash = ChannelInfo(CASH_CHANNEL, "Cash", "cash", weight=cash_w)
    leaves.append(
        Leaf(
            channel_kind="cash",
            channel_key=CASH_CHANNEL,
            channel_label="Cash",
            weight=cash_w,
            asset_class="cash",
            special=CASH,
        )
    )
    channels[DIRECT_CHANNEL] = direct
    channels[CASH_CHANNEL] = cash
    return LeafSet(
        leaves=[x for x in leaves if x.weight != 0],
        channels=channels,
        total=total,
        over_reported=over,
        base_currency=portfolio.base_currency,
        direct_value=direct_value,
        fund_value=fund_value,
        unidentified_weight=unidentified,
    )


def region_for(code: str | None) -> str | None:
    return region_of(code)
