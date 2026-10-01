"""The look-through portfolio: direct stocks plus each fund's weight times its holdings'
weights, from the latest stored issuer snapshots (never fetched on a request).

Definitions (shown to the owner as the method note):
- Weights are fractions of the portfolio's total value in the base currency (positions plus
  cash). Portfolio cash is its own bucket; a fund's own cash, derivatives and anything else
  that is not an equity line is "fund cash & derivatives"; a fund with no stored snapshot
  (or no look-through config) is "not looked through". Nothing is spread over buckets it
  was not published for.
- Country: a direct stock's home market (or the configured override); a fund's per-holding
  country as its issuer publishes it (iShares: country of risk), except where the issuer
  publishes an official country table (Vanguard: FTSE country classification), which is
  used for that fund instead of its per-holding listing country. Region is derived from the
  country with one static map for every source.
- Sector: GICS-style. A direct stock takes the configured override, else the sector a
  looked-through fund gives the same ISIN, else its strategy sector bucket when that names a
  GICS sector; otherwise it is unclassified.
- Currency: the trading currency (iShares' `marketCurrencyCode`, a direct stock's trading
  currency); for Vanguard it is derived from the listing country (flagged as derived).
- Concentration: HHI (sum of squared weights), effective number of holdings (1/HHI) and
  the top-10 share over the underlying securities, merged by ISIN and by the configured
  issuer groups, renormalised over what is looked through (cash, fund cash and funds not
  looked through are left out; coverage says how much that is).
- Benchmark: the first configured benchmark symbol (`prices.benchmark_symbols`, default
  ^spx) is proxied by the holdings of the fund `look_through.benchmark_proxies` names for it
  (default: CSPX for the S&P 500), equity lines renormalised to 100 %. The FTSE All-World
  weights come from Vanguard's benchmark column (`benchmarkMktPercent`) next to VWCE's own
  country, region and ICB sector tables; the index's constituents are not published there,
  so it has no concentration figures.
"""

import datetime as dt
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.config.jobs import JobsConfig, LookThroughFund, LookThroughSettings
from steward.config.store import load_active
from steward.config.strategy import StrategyConfig
from steward.lookthrough.classify import (
    COUNTRIES,
    country_name,
    currency_of,
    normalize_sector,
    region_of,
)
from steward.lookthrough.collect import STATUS_KEY

Dimension = Literal["country", "region", "sector", "currency"]
BucketKind = Literal["classified", "unclassified", "cash", "fund_cash", "not_looked_through"]
DIMENSIONS: tuple[Dimension, ...] = ("country", "region", "sector", "currency")

CASH = "__cash__"
FUND_CASH = "__fund_cash__"
NOT_LOOKED = "__not_looked_through__"
UNCLASSIFIED = "__unclassified__"
SPECIAL_LABELS = {
    CASH: "Cash",
    FUND_CASH: "Fund cash & derivatives",
    NOT_LOOKED: "Not looked through",
    UNCLASSIFIED: "Unclassified",
}
SPECIAL_KINDS: dict[str, BucketKind] = {
    CASH: "cash",
    FUND_CASH: "fund_cash",
    NOT_LOOKED: "not_looked_through",
    UNCLASSIFIED: "unclassified",
}

SOURCE_LABELS = {
    "ishares_json": "iShares product-data API (undocumented)",
    "ishares_spreadsheet": "iShares full-fund download (SpreadsheetML fallback)",
    "vanguard_graphql": "Vanguard GraphQL (undocumented)",
}
PROVIDER_LABELS = {"ishares": "iShares", "vanguard": "Vanguard"}
TIER = 1
TIER_LABEL = "Tier 1 · the fund issuer's own data, through an undocumented endpoint"
COUNTRY_BASIS_LABELS = {
    "country_of_risk": "country of risk, per holding",
    "listing_country": "listing country, per holding",
    "ftse_country": "FTSE country classification (the issuer's fund table)",
}

METHOD = (
    "Direct stocks plus each fund's weight x its holdings' weights, from the latest stored "
    "issuer snapshots. Weights are shares of total portfolio value; cash, the funds' own "
    "cash & derivatives and funds without a snapshot are shown separately, never spread. "
    "Region is derived from country; Vanguard currencies are derived from the listing "
    "country. Concentration: HHI, 1/HHI and top-10 share over the looked-through "
    "securities (merged by ISIN and issuer groups)."
)


# ---------------------------------------------------------------------------
# API models
# ---------------------------------------------------------------------------


class ExposureBucket(BaseModel):
    key: str = Field(
        description="ISO country code, region, GICS sector or currency code; or one of "
        "__cash__, __fund_cash__, __not_looked_through__, __unclassified__."
    )
    label: str
    kind: BucketKind
    weight: float = Field(description="Share of total portfolio value.")
    value_base: float
    direct_weight: float = Field(description="Part of `weight` held directly.")
    fund_weight: float = Field(description="Part of `weight` held through funds.")
    benchmark_weight: float | None = Field(
        description="The primary benchmark's weight in this bucket (its looked-through part "
        "renormalised to 100%); null for buckets it has no counterpart for."
    )


class ExposureDimension(BaseModel):
    dimension: Dimension
    basis: str = Field(description="How the buckets were classified.")
    buckets: list[ExposureBucket] = Field(description="By weight, special buckets last.")


class Via(BaseModel):
    kind: Literal["direct", "fund"]
    ticker: str | None
    label: str
    weight: float
    value_base: float


class UnderlyingHolding(BaseModel):
    key: str
    name: str
    isin: str | None
    isins: list[str]
    ticker: str | None
    sector: str | None
    country: str | None
    currency: str | None
    weight: float = Field(description="Share of total portfolio value, direct plus via funds.")
    value_base: float
    via: list[Via]
    benchmark_weight: float | None


class Concentration(BaseModel):
    hhi: float = Field(description="Sum of squared weights (weights renormalised to 1).")
    effective_n: float = Field(description="1 / HHI: as concentrated as this many equal names.")
    top10_share: float
    holdings: int
    basis: str


class FundHolding(BaseModel):
    name: str
    isin: str | None
    ticker: str | None
    sector: str | None
    country: str | None
    currency: str | None
    weight_in_fund: float
    portfolio_weight: float
    value_base: float


class LastAttempt(BaseModel):
    checked_at: dt.datetime
    ok: bool
    outcome: str | None
    error: str | None
    fallback: str | None


class FundLookThrough(BaseModel):
    isin: str
    label: str
    ticker: str | None = Field(description="The Trading212 ticker when the fund is held.")
    name: str | None
    provider: Literal["ishares", "vanguard"] | None
    portfolio_id: str | None
    held: bool
    benchmark_proxy_for: list[str]
    value_base: float
    weight: float | None
    status: Literal["ok", "stale", "missing", "not_configured"]
    as_of: dt.date | None
    fetched_at: dt.datetime | None
    source: str | None
    source_label: str | None
    tier: int | None
    tier_label: str | None
    fallback: Literal["cached", "spreadsheet"] | None = Field(
        description="cached: the latest fetch failed and this stored snapshot is shown; "
        "spreadsheet: read from the iShares full-fund download (no country, no ISIN)."
    )
    last_attempt: LastAttempt | None
    country_basis: str | None
    currency_basis: str | None
    equity_share: float | None
    holdings_count: int | None
    warnings: list[str]
    top_holdings: list[FundHolding]


class BenchmarkBucket(BaseModel):
    key: str
    label: str
    weight: float


class BenchmarkLookThrough(BaseModel):
    key: str
    label: str
    symbol: str | None
    primary: bool
    source_label: str
    as_of: dt.date | None
    definition: str
    sector_scheme: Literal["gics", "icb_industry"]
    currency_derived: bool
    exposures: dict[str, list[BenchmarkBucket]]
    concentration: Concentration | None
    foreign_share: float | None = Field(
        description="Share in currencies other than the base currency."
    )


class FxRow(BaseModel):
    currency: str
    weight: float = Field(description="Share of the portfolio's known-currency value.")
    benchmark_weight: float | None


class FxExposure(BaseModel):
    base_currency: str
    foreign_share: float | None = Field(
        description="Share of known-currency value (securities looked through plus cash) in "
        "currencies other than the base currency."
    )
    known_share: float = Field(description="Share of total value whose currency is known.")
    benchmark_foreign_share: float | None
    benchmark_label: str | None
    rows: list[FxRow]


class Coverage(BaseModel):
    total_value: float
    cash_value: float
    direct_value: float
    fund_value: float
    looked_through_value: float
    not_looked_through_value: float
    fund_share_looked_through: float | None = Field(
        description="Share of the funds' value whose holdings are looked through."
    )
    portfolio_share_looked_through: float | None = Field(
        description="Share of total portfolio value that is direct stocks or looked-through "
        "funds (the rest is cash and funds not looked through)."
    )


class LookThroughReport(BaseModel):
    snapshot_date: dt.date | None
    base_currency: str | None
    available: bool = Field(description="At least one held fund is looked through.")
    method: str
    coverage: Coverage | None
    exposures: list[ExposureDimension]
    top_holdings: list[UnderlyingHolding]
    concentration: Concentration | None
    benchmark: BenchmarkLookThrough | None = Field(description="The primary benchmark.")
    benchmarks: list[BenchmarkLookThrough]
    fx: FxExposure | None
    funds: list[FundLookThrough]
    notes: list[str]


# ---------------------------------------------------------------------------
# Inputs (plain data, so the computation is testable without a database)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PositionIn:
    ticker: str
    name: str | None
    isin: str | None
    security_type: str | None
    home_country: str | None
    currency: str | None
    value_base: float
    strategy_sector: str | None = None


@dataclass(frozen=True)
class RowIn:
    name: str | None
    isin: str | None
    ticker: str | None
    asset_class: str
    weight: float
    sector: str | None
    country: str | None
    currency: str | None
    currency_derived: bool


@dataclass(frozen=True)
class BreakdownIn:
    dimension: str
    scheme: str
    code: str | None
    label: str
    fund_weight: float | None
    benchmark_weight: float | None
    as_of: dt.date | None


@dataclass
class SnapshotIn:
    isin: str
    source: str
    as_of: dt.date
    fetched_at: dt.datetime
    fund_name: str | None
    country_basis: str | None
    currency_basis: str | None
    warnings: list[str]
    rows: list[RowIn]
    breakdowns: list[BreakdownIn] = field(default_factory=list)


@dataclass(frozen=True)
class PortfolioIn:
    snapshot_date: dt.date
    base_currency: str
    account_currency: str
    cash_base: float
    positions: list[PositionIn]


_ETF_TYPE = re.compile(r"\b(etp|etf|etn|fund|unit)\b")


def is_fund_type(security_type: str | None) -> bool:
    t = (security_type or "").lower()
    return bool(_ETF_TYPE.search(t)) or "exchange traded" in t


def _currency(code: str | None) -> str | None:
    if not code:
        return None
    code = code.upper()
    # London quotes in pence: the currency is sterling.
    return {"GBX": "GBP", "GBP": "GBP", "ZAC": "ZAR", "ILA": "ILS"}.get(code, code)


# ---------------------------------------------------------------------------
# Computation
# ---------------------------------------------------------------------------


class _Acc:
    """Weight per bucket, split by how it is held."""

    def __init__(self) -> None:
        self.direct: dict[str, float] = defaultdict(float)
        self.fund: dict[str, float] = defaultdict(float)

    def add(self, key: str | None, weight: float, *, direct: bool) -> None:
        (self.direct if direct else self.fund)[key or UNCLASSIFIED] += weight


def _label(dimension: Dimension, key: str) -> str:
    if key in SPECIAL_LABELS:
        return SPECIAL_LABELS[key]
    if dimension == "country":
        return country_name(key) or key
    return key


def _normalised(weights: dict[str, float]) -> dict[str, float]:
    total = sum(w for w in weights.values() if w > 0)
    return {k: w / total for k, w in weights.items() if w > 0} if total > 0 else {}


@dataclass
class _Security:
    key: str
    names: list[str] = field(default_factory=list)
    isins: set[str] = field(default_factory=set)
    ticker: str | None = None
    sector: str | None = None
    country: str | None = None
    currency: str | None = None
    via: dict[tuple[str, str | None, str], float] = field(
        default_factory=lambda: defaultdict(float)
    )

    @property
    def weight(self) -> float:
        return sum(self.via.values())

    group_label: str | None = None

    def name(self) -> str:
        if self.group_label:
            return self.group_label
        # Prefer a mixed-case spelling ("NVIDIA Corp") over iShares' upper case ("NVIDIA").
        return max(
            self.names, key=lambda n: (sum(c.islower() for c in n), len(n)), default=self.key
        )


class _Securities:
    def __init__(self, groups: dict[str, tuple[str, str]]) -> None:
        # ISIN -> (group key, group label)
        self.groups = groups
        self.items: dict[str, _Security] = {}

    def key_for(self, isin: str | None, name: str | None) -> str | None:
        if isin:
            group = self.groups.get(isin)
            return group[0] if group else isin
        if name:
            return "name:" + re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
        return None

    def add(
        self,
        *,
        isin: str | None,
        name: str | None,
        ticker: str | None,
        sector: str | None,
        country: str | None,
        currency: str | None,
        via: tuple[str, str | None, str],
        weight: float,
    ) -> None:
        key = self.key_for(isin, name)
        if key is None or weight == 0:
            return
        sec = self.items.get(key)
        if sec is None:
            sec = self.items[key] = _Security(key=key)
            group = self.groups.get(isin or "")
            if group:
                sec.group_label = group[1]
        if name:
            sec.names.append(name)
        if isin:
            sec.isins.add(isin)
        # A direct position's own (Trading212) ticker wins over a fund line's local one.
        sec.ticker = ticker if via[0] == "direct" and ticker else (sec.ticker or ticker)
        sec.sector = sec.sector or sector
        sec.country = sec.country or country
        sec.currency = sec.currency or currency
        sec.via[via] += weight


def _concentration(weights: Iterable[float], basis: str) -> Concentration | None:
    positive = sorted((w for w in weights if w > 0), reverse=True)
    total = sum(positive)
    if total <= 0:
        return None
    shares = [w / total for w in positive]
    hhi = sum(s * s for s in shares)
    return Concentration(
        hhi=hhi,
        effective_n=1 / hhi,
        top10_share=sum(shares[:10]),
        holdings=len(shares),
        basis=basis,
    )


def _issuer_groups(settings: LookThroughSettings) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for group in settings.issuer_groups:
        key = "group:" + re.sub(r"[^a-z0-9]+", "-", group.label.lower()).strip("-")
        for isin in group.isins:
            out.setdefault(isin, (key, group.label))
    return out


def _fund_country_table(snap: SnapshotIn) -> dict[str, float] | None:
    """The fund's official country table (FTSE scheme), normalised, when published."""
    table = {
        b.code: b.fund_weight
        for b in snap.breakdowns
        if b.scheme == "ftse_country" and b.code and b.fund_weight is not None
    }
    normalised = _normalised({k: v for k, v in table.items() if k})
    return normalised or None


def _benchmark_from_rows(
    key: str,
    label: str,
    symbol: str | None,
    primary: bool,
    snap: SnapshotIn,
    fund: LookThroughFund,
    base_currency: str,
    groups: dict[str, tuple[str, str]],
) -> BenchmarkLookThrough:
    equity = [r for r in snap.rows if r.asset_class == "equity" and r.weight > 0]
    total = sum(r.weight for r in equity)
    dims: dict[str, dict[str, float]] = {d: defaultdict(float) for d in DIMENSIONS}
    securities = _Securities(groups)
    for r in equity:
        w = r.weight / total if total > 0 else 0.0
        dims["country"][r.country or UNCLASSIFIED] += w
        dims["region"][region_of(r.country) or UNCLASSIFIED] += w
        dims["sector"][r.sector or UNCLASSIFIED] += w
        dims["currency"][r.currency or UNCLASSIFIED] += w
        securities.add(
            isin=r.isin,
            name=r.name,
            ticker=r.ticker,
            sector=r.sector,
            country=r.country,
            currency=r.currency,
            via=("fund", None, fund.label),
            weight=w,
        )
    currency = dims["currency"]
    known = sum(w for k, w in currency.items() if k != UNCLASSIFIED)
    foreign = (
        sum(w for k, w in currency.items() if k not in (UNCLASSIFIED, base_currency)) / known
        if known > 0
        else None
    )
    provider = PROVIDER_LABELS.get(snap.source.split("_")[0], snap.source)
    return BenchmarkLookThrough(
        key=key,
        label=label,
        symbol=symbol,
        primary=primary,
        source_label=f"{provider} · {fund.label} holdings ({SOURCE_LABELS.get(snap.source)})",
        as_of=snap.as_of,
        definition=f"{label} proxied by {fund.label}'s equity holdings as of {snap.as_of}, "
        "renormalised to 100% (the fund's cash and derivatives left out).",
        sector_scheme="gics",
        currency_derived=snap.currency_basis == "derived",
        exposures={
            d: sorted(
                (
                    BenchmarkBucket(key=k, label=_label(d, k), weight=w)
                    for k, w in dims[d].items()
                    if w > 0
                ),
                key=lambda b: -b.weight,
            )
            for d in DIMENSIONS
        },
        concentration=_concentration(
            (s.weight for s in securities.items.values()),
            f"{len(securities.items)} securities of {fund.label} (issuer groups merged)",
        ),
        foreign_share=foreign,
    )


def _benchmark_from_table(snap: SnapshotIn, base_currency: str) -> BenchmarkLookThrough | None:
    """FTSE All-World weights from Vanguard's benchmark column."""
    country = _normalised(
        {
            b.code: b.benchmark_weight
            for b in snap.breakdowns
            if b.scheme == "ftse_country" and b.code and b.benchmark_weight is not None
        }
    )
    if not country:
        return None
    region: dict[str, float] = defaultdict(float)
    currency: dict[str, float] = defaultdict(float)
    for code, w in country.items():
        region[region_of(code) or UNCLASSIFIED] += w
        currency[currency_of(code) or UNCLASSIFIED] += w
    sector = _normalised(
        {
            b.label: b.benchmark_weight
            for b in snap.breakdowns
            if b.scheme == "icb_industry" and b.benchmark_weight is not None
        }
    )
    known = sum(w for k, w in currency.items() if k != UNCLASSIFIED)
    as_of = max((b.as_of for b in snap.breakdowns if b.as_of), default=snap.as_of)

    def buckets(d: Dimension, weights: dict[str, float]) -> list[BenchmarkBucket]:
        return sorted(
            (BenchmarkBucket(key=k, label=_label(d, k), weight=w) for k, w in weights.items()),
            key=lambda b: -b.weight,
        )

    return BenchmarkLookThrough(
        key="ftse_all_world",
        label="FTSE All-World",
        symbol=None,
        primary=False,
        source_label="Vanguard GraphQL (undocumented) · benchmark column of VWCE's tables",
        as_of=as_of,
        definition="The FTSE All-World index weights Vanguard publishes next to VWCE's own "
        "(benchmarkMktPercent): FTSE country classification, region derived from country, "
        "ICB industries (not GICS), currency derived from country. The index's "
        "constituents are not published there, so it has no concentration figures.",
        sector_scheme="icb_industry",
        currency_derived=True,
        exposures={
            "country": buckets("country", country),
            "region": buckets("region", dict(region)),
            "sector": buckets("sector", sector),
            "currency": buckets("currency", dict(currency)),
        },
        concentration=None,
        foreign_share=(
            sum(w for k, w in currency.items() if k not in (UNCLASSIFIED, base_currency)) / known
            if known > 0
            else None
        ),
    )


def compute_look_through(
    portfolio: PortfolioIn | None,
    settings: LookThroughSettings,
    snapshots: dict[str, SnapshotIn],
    status: dict[str, dict[str, object]],
    *,
    benchmark_symbols: list[str],
    today: dt.date,
    top: int = 25,
    fund_top: int = 10,
) -> LookThroughReport:
    notes: list[str] = []
    groups = _issuer_groups(settings)
    base_currency = portfolio.base_currency if portfolio else None

    # ---- benchmarks (independent of the portfolio) ----
    benchmarks: list[BenchmarkLookThrough] = []
    primary_symbol = benchmark_symbols[0] if benchmark_symbols else None
    for proxy in settings.benchmark_proxies:
        fund = settings.fund(proxy.fund_isin)
        snap = snapshots.get(proxy.fund_isin)
        if fund is None or snap is None:
            continue
        benchmarks.append(
            _benchmark_from_rows(
                key=f"proxy:{proxy.symbol}",
                label=proxy.label,
                symbol=proxy.symbol,
                primary=proxy.symbol == primary_symbol,
                snap=snap,
                fund=fund,
                base_currency=base_currency or "",
                groups=groups,
            )
        )
    for snap in snapshots.values():
        if snap.source == "vanguard_graphql":
            ftse = _benchmark_from_table(snap, base_currency or "")
            if ftse is not None and not any(b.key == ftse.key for b in benchmarks):
                benchmarks.append(ftse)
    primary = next((b for b in benchmarks if b.primary), None)
    if primary_symbol and primary is None:
        proxy_for = next(
            (p for p in settings.benchmark_proxies if p.symbol == primary_symbol), None
        )
        notes.append(
            f"No benchmark look-through for {primary_symbol}: "
            + (
                f"no snapshot of its proxy fund {proxy_for.fund_isin} yet."
                if proxy_for
                else "no proxy fund is configured for it (look_through.benchmark_proxies)."
            )
        )

    # ---- funds ----
    held_funds: dict[str, PositionIn] = {}
    if portfolio:
        for p in portfolio.positions:
            if p.isin and settings.fund(p.isin):
                held_funds[p.isin] = p
    total = (
        sum(p.value_base for p in portfolio.positions) + portfolio.cash_base if portfolio else 0.0
    )

    funds_out: list[FundLookThrough] = []

    def fund_entry(
        isin: str, label: str, position: PositionIn | None, cfg: LookThroughFund | None
    ) -> FundLookThrough:
        snap = snapshots.get(isin)
        st = status.get(isin) or {}
        last: LastAttempt | None = None
        if st.get("checked_at"):
            last = LastAttempt(
                checked_at=dt.datetime.fromisoformat(str(st["checked_at"])),
                ok=bool(st.get("ok")),
                outcome=str(st["outcome"]) if st.get("outcome") else None,
                error=str(st["error"]) if st.get("error") else None,
                fallback=str(st["fallback"]) if st.get("fallback") else None,
            )
        value = position.value_base if position else 0.0
        weight = value / total if position and total > 0 else None
        proxies = [p.symbol for p in settings.benchmark_proxies if p.fund_isin == isin]
        if cfg is None or snap is None:
            return FundLookThrough(
                isin=isin,
                label=label,
                ticker=position.ticker if position else None,
                name=position.name if position else None,
                provider=cfg.provider if cfg else None,
                portfolio_id=cfg.portfolio_id if cfg else None,
                held=position is not None,
                benchmark_proxy_for=proxies,
                value_base=value,
                weight=weight,
                status="not_configured" if cfg is None else "missing",
                as_of=None,
                fetched_at=None,
                source=None,
                source_label=None,
                tier=None,
                tier_label=None,
                fallback=None,
                last_attempt=last,
                country_basis=None,
                currency_basis=None,
                equity_share=None,
                holdings_count=None,
                warnings=[],
                top_holdings=[],
            )
        failed_since = last is not None and not last.ok and last.checked_at > snap.fetched_at
        fallback: Literal["cached", "spreadsheet"] | None = (
            "spreadsheet"
            if snap.source == "ishares_spreadsheet"
            else ("cached" if failed_since else None)
        )
        equity = [r for r in snap.rows if r.asset_class == "equity"]
        equity_share = sum(r.weight for r in equity)
        table = _fund_country_table(snap)
        top_rows = sorted(equity, key=lambda r: -r.weight)[:fund_top]
        return FundLookThrough(
            isin=isin,
            label=label,
            ticker=position.ticker if position else None,
            name=snap.fund_name or (position.name if position else None),
            provider=cfg.provider,
            portfolio_id=cfg.portfolio_id,
            held=position is not None,
            benchmark_proxy_for=proxies,
            value_base=value,
            weight=weight,
            status="stale" if (today - snap.as_of).days > settings.stale_after_days else "ok",
            as_of=snap.as_of,
            fetched_at=snap.fetched_at,
            source=snap.source,
            source_label=SOURCE_LABELS.get(snap.source, snap.source),
            tier=TIER,
            tier_label=TIER_LABEL,
            fallback=fallback,
            last_attempt=last,
            country_basis=COUNTRY_BASIS_LABELS["ftse_country"]
            if table
            else COUNTRY_BASIS_LABELS.get(snap.country_basis or "", "not published"),
            currency_basis="derived from the listing country"
            if snap.currency_basis == "derived"
            else "the issuer's market currency per holding",
            equity_share=equity_share,
            holdings_count=len(equity),
            warnings=snap.warnings,
            top_holdings=[
                FundHolding(
                    name=r.name or r.ticker or "—",
                    isin=r.isin,
                    ticker=r.ticker,
                    sector=r.sector,
                    country=r.country,
                    currency=r.currency,
                    weight_in_fund=r.weight,
                    portfolio_weight=(weight or 0.0) * r.weight,
                    value_base=value * r.weight,
                )
                for r in top_rows
            ],
        )

    for cfg in settings.funds:
        position = held_funds.get(cfg.isin)
        proxies = [p for p in settings.benchmark_proxies if p.fund_isin == cfg.isin]
        if position is None and not proxies:
            continue
        funds_out.append(fund_entry(cfg.isin, cfg.label, position, cfg))

    if portfolio is None:
        return LookThroughReport(
            snapshot_date=None,
            base_currency=None,
            available=False,
            method=METHOD,
            coverage=None,
            exposures=[],
            top_holdings=[],
            concentration=None,
            benchmark=primary,
            benchmarks=benchmarks,
            fx=None,
            funds=funds_out,
            notes=[*notes, "No portfolio snapshot yet."],
        )

    # ---- the look-through portfolio ----
    by_isin_row: dict[str, RowIn] = {}
    for snap in snapshots.values():
        for r in snap.rows:
            if r.isin and r.asset_class == "equity":
                by_isin_row.setdefault(r.isin, r)

    acc = {d: _Acc() for d in DIMENSIONS}
    securities = _Securities(groups)
    direct_value = fund_value = looked_value = not_looked_value = 0.0

    for p in portfolio.positions:
        w = p.value_base / total if total > 0 else 0.0
        fund_cfg = settings.fund(p.isin)
        if fund_cfg is not None or is_fund_type(p.security_type):
            fund_value += p.value_base
            snap = snapshots.get(p.isin or "") if fund_cfg else None
            if snap is None:
                not_looked_value += p.value_base
                for d in DIMENSIONS:
                    acc[d].add(NOT_LOOKED, w, direct=False)
                if fund_cfg is None:
                    funds_out.append(fund_entry(p.isin or p.ticker, p.ticker, p, None))
                continue
            looked_value += p.value_base
            equity_rows = [r for r in snap.rows if r.asset_class == "equity"]
            equity_share = sum(r.weight for r in equity_rows)
            other_share = max(0.0, 1.0 - equity_share)
            table = _fund_country_table(snap)
            for r in equity_rows:
                contribution = w * r.weight
                if table is None:
                    acc["country"].add(r.country, contribution, direct=False)
                    acc["region"].add(region_of(r.country), contribution, direct=False)
                acc["sector"].add(r.sector, contribution, direct=False)
                acc["currency"].add(r.currency, contribution, direct=False)
                securities.add(
                    isin=r.isin,
                    name=r.name,
                    ticker=r.ticker,
                    sector=r.sector,
                    country=r.country,
                    currency=r.currency,
                    via=("fund", p.ticker, fund_cfg.label if fund_cfg else p.ticker),
                    weight=contribution,
                )
            if table is not None:
                for code, share in table.items():
                    contribution = w * equity_share * share
                    acc["country"].add(code, contribution, direct=False)
                    acc["region"].add(region_of(code), contribution, direct=False)
            for d in DIMENSIONS:
                acc[d].add(FUND_CASH, w * other_share, direct=False)
            continue

        direct_value += p.value_base
        match = by_isin_row.get(p.isin or "")
        country = (
            settings.direct_countries.get(p.ticker)
            or p.home_country
            or (match.country if match else None)
        )
        sector = (
            settings.direct_sectors.get(p.ticker)
            or (match.sector if match else None)
            or normalize_sector(p.strategy_sector)
        )
        currency = _currency(p.currency)
        acc["country"].add(country if country in COUNTRIES else None, w, direct=True)
        acc["region"].add(region_of(country), w, direct=True)
        acc["sector"].add(sector, w, direct=True)
        acc["currency"].add(currency, w, direct=True)
        securities.add(
            isin=p.isin,
            name=p.name or p.ticker,
            ticker=p.ticker,
            sector=sector,
            country=country,
            currency=currency,
            via=("direct", p.ticker, "Direct"),
            weight=w,
        )

    cash_w = portfolio.cash_base / total if total > 0 else 0.0
    for d in DIMENSIONS:
        acc[d].add(CASH, cash_w, direct=True)

    primary_weights: dict[str, dict[str, float]] = (
        {d: {b.key: b.weight for b in primary.exposures.get(d, [])} for d in DIMENSIONS}
        if primary
        else {}
    )
    basis = {
        "country": "Direct stocks: home market (or override). Funds: "
        + "; ".join(
            f"{f.label} {f.country_basis}" for f in funds_out if f.held and f.as_of is not None
        ),
        "region": "Derived from country (one static map for every source).",
        "sector": "GICS sectors: the issuers' per-holding sectors; direct stocks as the funds "
        "classify the same ISIN, else the override or the strategy's sector bucket.",
        "currency": "Trading currency: iShares' market currency, direct stocks' trading "
        "currency; Vanguard's derived from the listing country.",
    }
    exposures: list[ExposureDimension] = []
    for d in DIMENSIONS:
        keys = set(acc[d].direct) | set(acc[d].fund)
        buckets = []
        for k in keys:
            dw, fw = acc[d].direct.get(k, 0.0), acc[d].fund.get(k, 0.0)
            weight = dw + fw
            if abs(weight) < 1e-12:
                continue
            kind: BucketKind = SPECIAL_KINDS.get(k, "classified")
            buckets.append(
                ExposureBucket(
                    key=k,
                    label=_label(d, k),
                    kind=kind,
                    weight=weight,
                    value_base=weight * total,
                    direct_weight=dw,
                    fund_weight=fw,
                    benchmark_weight=primary_weights[d].get(k, 0.0)
                    if primary_weights and kind == "classified"
                    else None,
                )
            )
        buckets.sort(key=lambda b: (b.kind != "classified", -b.weight))
        exposures.append(ExposureDimension(dimension=d, basis=basis[d], buckets=buckets))

    # ---- top underlying holdings and concentration ----
    bench_security: dict[str, float] = {}
    if primary is not None:
        proxy = next(p for p in settings.benchmark_proxies if f"proxy:{p.symbol}" == primary.key)
        snap = snapshots[proxy.fund_isin]
        equity = [r for r in snap.rows if r.asset_class == "equity" and r.weight > 0]
        eq_total = sum(r.weight for r in equity)
        bench_secs = _Securities(groups)
        for r in equity:
            bench_secs.add(
                isin=r.isin,
                name=r.name,
                ticker=r.ticker,
                sector=None,
                country=None,
                currency=None,
                via=("fund", None, "bench"),
                weight=r.weight / eq_total,
            )
        bench_security = {k: s.weight for k, s in bench_secs.items.items()}

    ranked = sorted(securities.items.values(), key=lambda s: -s.weight)
    top_holdings = [
        UnderlyingHolding(
            key=s.key,
            name=s.name(),
            isin=sorted(s.isins)[0] if len(s.isins) == 1 else None,
            isins=sorted(s.isins),
            ticker=s.ticker,
            sector=s.sector,
            country=s.country,
            currency=s.currency,
            weight=s.weight,
            value_base=s.weight * total,
            via=[
                Via(kind=kind, ticker=ticker, label=label, weight=w, value_base=w * total)
                for (kind, ticker, label), w in sorted(s.via.items(), key=lambda kv: -kv[1])
            ],
            benchmark_weight=bench_security.get(s.key) if primary else None,
        )
        for s in ranked[:top]
    ]
    covered = direct_value + looked_value
    concentration = _concentration(
        (s.weight for s in ranked),
        f"{len(ranked)} securities (direct and through looked-through funds, issuer groups "
        f"merged), renormalised over {covered / total:.0%} of portfolio value"
        if total > 0
        else "",
    )

    # ---- currency / FX ----
    currency_buckets = {
        k: acc["currency"].direct.get(k, 0.0) + acc["currency"].fund.get(k, 0.0)
        for k in set(acc["currency"].direct) | set(acc["currency"].fund)
    }
    known: dict[str, float] = {
        k: w for k, w in currency_buckets.items() if k not in SPECIAL_KINDS and w > 0
    }
    cash_ccy = _currency(portfolio.account_currency) or portfolio.account_currency
    if cash_w > 0:
        known[cash_ccy] = known.get(cash_ccy, 0.0) + cash_w
    known_total = sum(known.values())
    bench_ccy = primary_weights.get("currency", {})
    fx = FxExposure(
        base_currency=portfolio.base_currency,
        foreign_share=(
            sum(w for k, w in known.items() if k != portfolio.base_currency) / known_total
            if known_total > 0
            else None
        ),
        known_share=known_total,
        benchmark_foreign_share=primary.foreign_share if primary else None,
        benchmark_label=primary.label if primary else None,
        rows=sorted(
            (
                FxRow(
                    currency=k,
                    weight=w / known_total,
                    benchmark_weight=bench_ccy.get(k, 0.0) if primary else None,
                )
                for k, w in known.items()
            ),
            key=lambda r: -r.weight,
        ),
    )
    if not_looked_value > 0:
        missing = [
            f.label for f in funds_out if f.held and f.status in ("missing", "not_configured")
        ]
        notes.append(
            f"{not_looked_value / total:.0%} of the portfolio is in funds not looked through "
            f"({', '.join(missing)}): shown as its own bucket."
        )

    return LookThroughReport(
        snapshot_date=portfolio.snapshot_date,
        base_currency=portfolio.base_currency,
        available=looked_value > 0,
        method=METHOD,
        coverage=Coverage(
            total_value=total,
            cash_value=portfolio.cash_base,
            direct_value=direct_value,
            fund_value=fund_value,
            looked_through_value=looked_value,
            not_looked_through_value=not_looked_value,
            fund_share_looked_through=looked_value / fund_value if fund_value > 0 else None,
            portfolio_share_looked_through=covered / total if total > 0 else None,
        ),
        exposures=exposures,
        top_holdings=top_holdings,
        concentration=concentration,
        benchmark=primary,
        benchmarks=benchmarks,
        fx=fx,
        funds=funds_out,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


async def load_snapshots(pool: asyncpg.Pool, isins: list[str]) -> dict[str, SnapshotIn]:
    """The latest stored snapshot of each fund, with its rows and breakdowns."""
    metas = await pool.fetch(
        "select distinct on (fund_isin) id, fund_isin, source, as_of, fetched_at, fund_name,"
        " country_basis, currency_basis, warnings from public.etf_snapshot"
        " where fund_isin = any($1::text[]) order by fund_isin, as_of desc, fetched_at desc",
        isins,
    )
    return await hydrate_snapshots(pool, metas)


async def hydrate_snapshots(
    pool: asyncpg.Pool, metas: Sequence[asyncpg.Record]
) -> dict[str, SnapshotIn]:
    """Snapshot rows and breakdowns for the given `etf_snapshot` metadata records."""
    if not metas:
        return {}
    ids = [m["id"] for m in metas]
    rows: dict[int, list[RowIn]] = defaultdict(list)
    for r in await pool.fetch(
        "select snapshot_id, name, isin, ticker, asset_class, weight, sector, country, currency,"
        " currency_derived from public.etf_holding where snapshot_id = any($1::bigint[])"
        " order by snapshot_id, row_index",
        ids,
    ):
        rows[r["snapshot_id"]].append(
            RowIn(
                name=r["name"],
                isin=r["isin"],
                ticker=r["ticker"],
                asset_class=r["asset_class"],
                weight=float(r["weight"]),
                sector=r["sector"],
                country=r["country"],
                currency=r["currency"],
                currency_derived=r["currency_derived"],
            )
        )
    breakdowns: dict[int, list[BreakdownIn]] = defaultdict(list)
    for b in await pool.fetch(
        "select snapshot_id, dimension, scheme, code, label, fund_weight, benchmark_weight,"
        " as_of from public.etf_breakdown where snapshot_id = any($1::bigint[])",
        ids,
    ):
        breakdowns[b["snapshot_id"]].append(
            BreakdownIn(
                dimension=b["dimension"],
                scheme=b["scheme"],
                code=b["code"],
                label=b["label"],
                fund_weight=float(b["fund_weight"]) if b["fund_weight"] is not None else None,
                benchmark_weight=float(b["benchmark_weight"])
                if b["benchmark_weight"] is not None
                else None,
                as_of=b["as_of"],
            )
        )
    return {
        m["fund_isin"]: SnapshotIn(
            isin=m["fund_isin"],
            source=m["source"],
            as_of=m["as_of"],
            fetched_at=m["fetched_at"],
            fund_name=m["fund_name"],
            country_basis=m["country_basis"],
            currency_basis=m["currency_basis"],
            warnings=list(m["warnings"] or []),
            rows=rows.get(m["id"], []),
            breakdowns=breakdowns.get(m["id"], []),
        )
        for m in metas
    }


async def load_portfolio(pool: asyncpg.Pool) -> PortfolioIn | None:
    account = await pool.fetchrow(
        "select snapshot_date, base_currency, account_currency, cash_base"
        " from public.account_snapshot order by snapshot_date desc limit 1"
    )
    if account is None:
        return None
    strategy = await load_active(pool, "strategy", StrategyConfig)
    classifications = strategy.body.classifications if strategy else {}
    positions = await pool.fetch(
        "select p.ticker, p.value_base, p.instrument_currency, i.name, i.isin, i.security_type,"
        " i.home_country from public.position_snapshot p"
        " left join public.instrument i on i.t212_ticker = p.ticker"
        " where p.snapshot_date = $1 order by p.value_base desc",
        account["snapshot_date"],
    )
    return PortfolioIn(
        snapshot_date=account["snapshot_date"],
        base_currency=account["base_currency"],
        account_currency=account["account_currency"],
        cash_base=float(account["cash_base"]),
        positions=[
            PositionIn(
                ticker=p["ticker"],
                name=p["name"],
                isin=p["isin"],
                security_type=p["security_type"],
                home_country=p["home_country"],
                currency=p["instrument_currency"],
                value_base=float(p["value_base"]),
                strategy_sector=(classifications.get(p["ticker"]) or {}).get("sector"),
            )
            for p in positions
        ],
    )


async def portfolio_look_through(
    pool: asyncpg.Pool, *, top: int = 25, today: dt.date | None = None
) -> LookThroughReport:
    stored = await load_active(pool, "jobs", JobsConfig)
    config = stored.body if stored else JobsConfig()
    settings = config.look_through
    portfolio = await load_portfolio(pool)
    snapshots = await load_snapshots(pool, [f.isin for f in settings.funds])
    raw = await pool.fetchval("select value from public.sync_state where key = $1", STATUS_KEY)
    status = dict(json.loads(raw)) if raw else {}
    return compute_look_through(
        portfolio,
        settings,
        snapshots,
        status,
        benchmark_symbols=config.prices.benchmark_symbols,
        today=today or dt.datetime.now(dt.UTC).date(),
        top=top,
    )
