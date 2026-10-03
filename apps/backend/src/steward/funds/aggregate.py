"""Issuer-level aggregation of direct shares and fund holdings (plan D6.4, D6.8).

The portfolio's direct stocks and the holdings of every held fund are merged by canonical
issuer, so an ADR and the ordinary share of one company are one issuer exposure that stays
traceable to both securities (`IssuerExposure.securities`). The report gives

- the top issuers, with which channel (direct, each fund) the exposure arrives through, and
  the overlap: the part of an issuer's exposure that more than one channel supplies;
- region / country / sector / currency exposure, as shares of total portfolio value;
- fee drag from the funds' expense ratios (funds without a known ratio are listed, not
  assumed to be free);
- "unknown fund weight": per fund, the part of its value the published rows do not account for
  (a fund with no snapshot is wholly unknown; a looked-through fund contributes the residual
  of its rows to 100 %). Unknown weight is its own bucket in every dimension and is never
  redistributed over the known holdings;
- every as-of date: the portfolio snapshot, each fund's holdings date, its lag and whether it
  is stale, and whether the rows reconcile to 100 % of net assets within tolerance.

`aggregate_exposure` is pure (testable without a database); `aggregate_portfolio` loads the
inputs, optionally as of a past date (only records known by then, see lookthrough.pit).
"""

import datetime as dt
import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.config.jobs import JobsConfig, LookThroughSettings
from steward.config.store import load_active
from steward.lookthrough.classify import COUNTRIES, country_name, region_of
from steward.lookthrough.factsheet import DEFAULT_TOLERANCE, latest_expense_ratios
from steward.lookthrough.pit import load_portfolio_as_of, load_snapshots_as_of
from steward.lookthrough.read import (
    CASH,
    FUND_CASH,
    NOT_LOOKED,
    UNCLASSIFIED,
    UNKNOWN_FUND,
    PortfolioIn,
    PositionIn,
    RowIn,
    SnapshotIn,
    _currency,
    is_fund_type,
    load_portfolio,
    load_snapshots,
)

Dimension = Literal["country", "region", "sector", "currency"]
DIMENSIONS: tuple[Dimension, ...] = ("country", "region", "sector", "currency")
SPECIAL_LABELS = {
    CASH: "Cash",
    FUND_CASH: "Fund cash, derivatives & other",
    NOT_LOOKED: "Fund not looked through (unknown)",
    UNKNOWN_FUND: "Unknown fund weight (rows do not account for it)",
    UNCLASSIFIED: "Unclassified",
}
SPECIAL_KEYS = set(SPECIAL_LABELS)

METHOD = (
    "Direct stocks plus each fund's weight x its published rows, merged by canonical issuer "
    "(config issuer groups, then the confirmed issuer register, then the ISIN). Weights are "
    "shares of total portfolio value. Cash, the funds' own cash/derivatives, funds without a "
    "snapshot and the part of a fund its rows do not account for are separate buckets; "
    "unknown weight is never redistributed over known holdings. Fund snapshots are as of "
    "their own dates, not the portfolio date; 13F, N-PORT and issuer holdings are never added "
    "together."
)


# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------


class ChannelWeight(BaseModel):
    kind: Literal["direct", "fund"]
    ticker: str | None
    isin: str | None = Field(description="The fund's ISIN for a fund channel.")
    label: str
    weight: float = Field(description="Share of total portfolio value.")
    value_base: float


class IssuerSecurity(BaseModel):
    isin: str | None
    name: str
    ticker: str | None
    weight: float
    via: list[ChannelWeight]


class IssuerExposure(BaseModel):
    key: str
    label: str
    merged_securities: int = Field(description="Distinct securities merged into this issuer.")
    securities: list[IssuerSecurity] = Field(description="Traceable to each security.")
    weight: float
    value_base: float
    direct_weight: float
    fund_weight: float
    overlap_weight: float = Field(
        description="Exposure supplied by more than one channel: weight minus the largest "
        "single channel."
    )
    channels: list[ChannelWeight]
    sector: str | None
    country: str | None
    region: str | None
    currency: str | None


class Bucket(BaseModel):
    key: str
    label: str
    kind: Literal[
        "classified", "cash", "fund_cash", "not_looked_through", "unknown_fund", "unclassified"
    ]
    weight: float
    value_base: float
    direct_weight: float
    fund_weight: float


class ExposureView(BaseModel):
    dimension: Dimension
    buckets: list[Bucket]
    reconciles: bool = Field(description="Buckets add up to the total within tolerance.")
    issuer_buckets: dict[str, dict[str, float]] = Field(
        default_factory=dict,
        description="Per returned issuer (`issuers[].key`): bucket key -> that issuer's weight "
        "in this bucket, as a fraction of total portfolio value. Additive per row, so an "
        "issuer split across buckets (e.g. 'Technology' and 'Information Technology', an "
        "ADR + ordinary in two currencies) appears in each; summed over ALL issuers a bucket's "
        "value equals `buckets[].weight` minus its non-issuer part (cash, fund cash, unknown "
        "fund weight are not issuers). Only the top `top` issuers are listed, so the rest of a "
        "bucket is `bucket.weight - sum(listed)`.",
    )


class FundAsOf(BaseModel):
    isin: str
    label: str
    ticker: str | None
    weight: float
    value_base: float
    status: Literal["ok", "stale", "missing", "not_configured"]
    holdings_as_of: dt.date | None
    lag_days: int | None
    stale: bool
    source: str | None
    rows_total_weight: float | None
    residual_weight: float | None = Field(description="1 - sum of published rows; unknown.")
    unknown_weight: float = Field(description="Share of total portfolio value left unknown.")
    reconciles: bool | None
    expense_ratio: float | None
    fee_drag: float | None = Field(description="weight x expense ratio, per year.")
    warnings: list[str]


class FeeDrag(BaseModel):
    annual_drag: float = Field(description="Share of total portfolio value per year.")
    annual_cost_base: float
    funds_with_ratio: int
    funds_without_ratio: list[str]
    unpriced_weight: float = Field(
        description="Portfolio share in funds whose expense ratio is unknown (not assumed 0)."
    )
    weighted_expense_ratio: float | None


class Unknown(BaseModel):
    unknown_fund_weight: float = Field(
        description="Share of total portfolio value in fund weight that is not accounted for "
        "by the published rows or whose fund is not looked through. Never redistributed."
    )
    unknown_fund_value: float
    not_looked_through_weight: float
    residual_weight: float
    over_reported_weight: float
    by_fund: dict[str, float]


class AggregateReport(BaseModel):
    as_of: dt.date = Field(description="The date this report answers for.")
    portfolio_snapshot_date: dt.date | None
    base_currency: str | None
    total_value: float
    available: bool
    method: str
    funds: list[FundAsOf]
    stale_funds: list[str]
    issuers: list[IssuerExposure]
    overlap: list[IssuerExposure] = Field(description="Issuers reached through 2+ channels.")
    exposures: list[ExposureView]
    fees: FeeDrag | None
    unknown: Unknown | None
    notes: list[str]


# ---------------------------------------------------------------------------
# Issuer identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IssuerRef:
    key: str
    label: str


UNIDENTIFIED_ISSUER = IssuerRef("unidentified", "Unidentified rows (no ISIN and no name)")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def issuer_for(
    isin: str | None,
    name: str | None,
    groups: dict[str, IssuerRef],
    register: dict[str, IssuerRef],
) -> IssuerRef | None:
    """Canonical issuer of a security: an explicit issuer group, else the issuer register
    (confirmed `security.issuer_id`), else the ISIN itself; a name only when there is no ISIN.
    A ticker or name alone never merges two ISINs."""
    if isin:
        return groups.get(isin) or register.get(isin) or IssuerRef(f"isin:{isin}", name or isin)
    if name:
        return IssuerRef("name:" + re.sub(r"[^a-z0-9]+", " ", name.lower()).strip(), name)
    return None


def config_groups(settings: LookThroughSettings) -> dict[str, IssuerRef]:
    out: dict[str, IssuerRef] = {}
    for group in settings.issuer_groups:
        ref = IssuerRef("group:" + _slug(group.label), group.label)
        for isin in group.isins:
            out.setdefault(isin, ref)
    return out


async def load_issuer_register(pool: asyncpg.Pool, isins: list[str]) -> dict[str, IssuerRef]:
    """ISIN -> issuer from the identity register (security.issuer_id), where known."""
    if not isins:
        return {}
    rows = await pool.fetch(
        "select s.isin, i.id, i.legal_name from public.security s"
        " join public.issuer i on i.id = s.issuer_id where s.isin = any($1::text[])",
        isins,
    )
    return {r["isin"]: IssuerRef(f"issuer:{r['id']}", r["legal_name"]) for r in rows}


# ---------------------------------------------------------------------------
# Computation
# ---------------------------------------------------------------------------


@dataclass
class _SecAcc:
    isin: str | None
    name: str
    ticker: str | None
    via: dict[tuple[str, str | None, str | None, str], float] = field(
        default_factory=lambda: defaultdict(float)
    )


@dataclass
class _IssuerAcc:
    ref: IssuerRef
    securities: dict[str, _SecAcc] = field(default_factory=dict)
    sector: str | None = None
    country: str | None = None
    currency: str | None = None
    names: list[str] = field(default_factory=list)
    by_dim: dict[str, dict[str, float]] = field(
        default_factory=lambda: {d: defaultdict(float) for d in DIMENSIONS}
    )


class _Buckets:
    def __init__(self) -> None:
        self.direct: dict[str, float] = defaultdict(float)
        self.fund: dict[str, float] = defaultdict(float)

    def add(self, key: str | None, weight: float, *, direct: bool) -> None:
        (self.direct if direct else self.fund)[key or UNCLASSIFIED] += weight


def _bucket_kind(key: str) -> str:
    return {
        CASH: "cash",
        FUND_CASH: "fund_cash",
        NOT_LOOKED: "not_looked_through",
        UNKNOWN_FUND: "unknown_fund",
        UNCLASSIFIED: "unclassified",
    }.get(key, "classified")


def _label(dimension: Dimension, key: str) -> str:
    if key in SPECIAL_LABELS:
        return SPECIAL_LABELS[key]
    return (country_name(key) or key) if dimension == "country" else key


def aggregate_exposure(
    portfolio: PortfolioIn | None,
    settings: LookThroughSettings,
    snapshots: dict[str, SnapshotIn],
    *,
    as_of: dt.date,
    register: dict[str, IssuerRef] | None = None,
    expense_ratios: dict[str, Decimal] | None = None,
    tolerance: Decimal = DEFAULT_TOLERANCE,
    top: int = 25,
) -> AggregateReport:
    ratios = expense_ratios or {}
    tol = float(tolerance)
    groups = config_groups(settings)
    reg = register or {}
    notes: list[str] = []
    if portfolio is None:
        return AggregateReport(
            as_of=as_of,
            portfolio_snapshot_date=None,
            base_currency=None,
            total_value=0.0,
            available=False,
            method=METHOD,
            funds=[],
            stale_funds=[],
            issuers=[],
            overlap=[],
            exposures=[],
            fees=None,
            unknown=None,
            notes=["No portfolio snapshot known at this date."],
        )

    total = sum(p.value_base for p in portfolio.positions) + portfolio.cash_base
    by_isin_row: dict[str, RowIn] = {}
    for other in snapshots.values():
        for r in other.rows:
            if r.isin and r.asset_class == "equity":
                by_isin_row.setdefault(r.isin, r)

    buckets = {d: _Buckets() for d in DIMENSIONS}
    issuers: dict[str, _IssuerAcc] = {}
    funds_out: list[FundAsOf] = []
    unknown_by_fund: dict[str, float] = {}
    residual_total = over_total = not_looked_total = 0.0
    direct_value = fund_value = 0.0
    unidentified_weight = 0.0

    def add_security(
        *,
        isin: str | None,
        name: str | None,
        ticker: str | None,
        sector: str | None,
        country: str | None,
        currency: str | None,
        via: tuple[str, str | None, str | None, str],
        weight: float,
    ) -> None:
        nonlocal unidentified_weight
        if weight == 0:
            return
        ref = issuer_for(isin, name, groups, reg)
        if ref is None:
            # A row with neither ISIN nor name cannot be tied to any issuer; its weight is
            # kept in one labelled bucket (never merged into a real issuer) and counted.
            unidentified_weight += weight
            ref = UNIDENTIFIED_ISSUER
        acc = issuers.setdefault(ref.key, _IssuerAcc(ref))
        sec_key = isin or ("name:" + (name or "") if name else f"unidentified:{ticker or ''}")
        sec = acc.securities.setdefault(sec_key, _SecAcc(isin, name or isin or "—", ticker))
        if via[0] == "direct" and ticker:
            sec.ticker = ticker
        sec.ticker = sec.ticker or ticker
        sec.via[via] += weight
        for dim, bucket_key in (
            ("country", country),
            ("region", region_of(country)),
            ("sector", sector),
            ("currency", currency),
        ):
            acc.by_dim[dim][bucket_key or UNCLASSIFIED] += weight
        if name:
            acc.names.append(name)
        acc.sector = acc.sector or sector
        acc.country = acc.country or country
        acc.currency = acc.currency or currency

    for p in portfolio.positions:
        w = p.value_base / total if total > 0 else 0.0
        fund_cfg = settings.fund(p.isin)
        if fund_cfg is None and not is_fund_type(p.security_type):
            direct_value += p.value_base
            match = by_isin_row.get(p.isin or "")
            country = (
                settings.direct_countries.get(p.ticker)
                or p.home_country
                or (match.country if match else None)
            )
            country = country if country in COUNTRIES else None
            sector = settings.direct_sectors.get(p.ticker) or (match.sector if match else None)
            currency = _currency(p.currency)
            buckets["country"].add(country, w, direct=True)
            buckets["region"].add(region_of(country), w, direct=True)
            buckets["sector"].add(sector, w, direct=True)
            buckets["currency"].add(currency, w, direct=True)
            add_security(
                isin=p.isin,
                name=p.name or p.ticker,
                ticker=p.ticker,
                sector=sector,
                country=country,
                currency=currency,
                via=("direct", p.ticker, None, "Direct"),
                weight=w,
            )
            continue

        fund_value += p.value_base
        label = fund_cfg.label if fund_cfg else (p.name or p.ticker)
        snap = snapshots.get(p.isin or "") if fund_cfg else None
        ratio = ratios.get(p.isin or "")
        fee = float(ratio) * w if ratio is not None else None
        if snap is None:
            not_looked_total += w
            unknown_by_fund[p.isin or p.ticker] = unknown_by_fund.get(p.isin or p.ticker, 0.0) + w
            for d in DIMENSIONS:
                buckets[d].add(NOT_LOOKED, w, direct=False)
            funds_out.append(
                FundAsOf(
                    isin=p.isin or p.ticker,
                    label=label,
                    ticker=p.ticker,
                    weight=w,
                    value_base=p.value_base,
                    status="not_configured" if fund_cfg is None else "missing",
                    holdings_as_of=None,
                    lag_days=None,
                    stale=False,
                    source=None,
                    rows_total_weight=None,
                    residual_weight=None,
                    unknown_weight=w,
                    reconciles=None,
                    expense_ratio=float(ratio) if ratio is not None else None,
                    fee_drag=fee,
                    warnings=[
                        "no issuer mapping configured (look_through.funds)"
                        if fund_cfg is None
                        else "no holdings snapshot stored yet"
                    ],
                )
            )
            continue

        equity = [r for r in snap.rows if r.asset_class == "equity"]
        rows_total = sum(r.weight for r in snap.rows)
        equity_total = sum(r.weight for r in equity)
        remainder = rows_total - equity_total  # cash + derivatives + other, signed
        residual = 1.0 - rows_total
        unknown_w = w * max(residual, 0.0)
        over = w * max(-residual, 0.0)
        residual_total += unknown_w
        over_total += over
        unknown_by_fund[p.isin or p.ticker] = (
            unknown_by_fund.get(p.isin or p.ticker, 0.0) + unknown_w
        )
        lag = (as_of - snap.as_of).days
        stale = lag > settings.stale_after_days
        reconciles = abs(residual) <= tol
        for r in equity:
            c = w * r.weight
            buckets["country"].add(r.country, c, direct=False)
            buckets["region"].add(region_of(r.country), c, direct=False)
            buckets["sector"].add(r.sector, c, direct=False)
            buckets["currency"].add(r.currency, c, direct=False)
            add_security(
                isin=r.isin,
                name=r.name,
                ticker=r.ticker,
                sector=r.sector,
                country=r.country,
                currency=r.currency,
                via=("fund", p.ticker, p.isin, label),
                weight=c,
            )
        for d in DIMENSIONS:
            buckets[d].add(FUND_CASH, w * remainder, direct=False)
            buckets[d].add(UNKNOWN_FUND, unknown_w, direct=False)
        fund_warnings = list(snap.warnings)
        if stale:
            fund_warnings.append(
                f"holdings are {lag} days old (stale after {settings.stale_after_days})"
            )
        if not reconciles:
            fund_warnings.append(
                f"published rows add up to {rows_total:.4f} of net assets; the "
                f"{abs(residual):.4f} difference is "
                f"{'unknown' if residual > 0 else 'over-reported'}"
            )
        funds_out.append(
            FundAsOf(
                isin=p.isin or p.ticker,
                label=label,
                ticker=p.ticker,
                weight=w,
                value_base=p.value_base,
                status="stale" if stale else "ok",
                holdings_as_of=snap.as_of,
                lag_days=lag,
                stale=stale,
                source=snap.source,
                rows_total_weight=rows_total,
                residual_weight=residual,
                unknown_weight=unknown_w,
                reconciles=reconciles,
                expense_ratio=float(ratio) if ratio is not None else None,
                fee_drag=fee,
                warnings=fund_warnings,
            )
        )

    cash_w = portfolio.cash_base / total if total > 0 else 0.0
    for d in DIMENSIONS:
        buckets[d].add(CASH, cash_w, direct=True)

    # ---- issuers ----
    def channel_list(acc: _IssuerAcc) -> list[ChannelWeight]:
        merged: dict[tuple[str, str | None, str | None, str], float] = defaultdict(float)
        for sec in acc.securities.values():
            for key, weight in sec.via.items():
                merged[key] += weight
        return [
            ChannelWeight(
                kind="direct" if k[0] == "direct" else "fund",
                ticker=k[1],
                isin=k[2],
                label=k[3],
                weight=v,
                value_base=v * total,
            )
            for k, v in sorted(merged.items(), key=lambda kv: -kv[1])
        ]

    exposures_out: list[IssuerExposure] = []
    for acc in issuers.values():
        channels = channel_list(acc)
        weight = sum(c.weight for c in channels)
        biggest = max((c.weight for c in channels), default=0.0)
        label = acc.ref.label
        if len(acc.securities) > 1 or acc.ref.key.startswith(("isin:", "name:")):
            label = (
                acc.ref.label
                if not acc.ref.key.startswith(("isin:", "name:"))
                else max(
                    acc.names, key=lambda n: (sum(ch.islower() for ch in n), len(n)), default=label
                )
            )
        exposures_out.append(
            IssuerExposure(
                key=acc.ref.key,
                label=label,
                merged_securities=len(acc.securities),
                securities=[
                    IssuerSecurity(
                        isin=s.isin,
                        name=s.name,
                        ticker=s.ticker,
                        weight=sum(s.via.values()),
                        via=[
                            ChannelWeight(
                                kind="direct" if k[0] == "direct" else "fund",
                                ticker=k[1],
                                isin=k[2],
                                label=k[3],
                                weight=v,
                                value_base=v * total,
                            )
                            for k, v in sorted(s.via.items(), key=lambda kv: -kv[1])
                        ],
                    )
                    for s in sorted(acc.securities.values(), key=lambda s: -sum(s.via.values()))
                ],
                weight=weight,
                value_base=weight * total,
                direct_weight=sum(c.weight for c in channels if c.kind == "direct"),
                fund_weight=sum(c.weight for c in channels if c.kind == "fund"),
                overlap_weight=weight - biggest if len(channels) > 1 else 0.0,
                channels=channels,
                sector=acc.sector,
                country=acc.country,
                region=region_of(acc.country),
                currency=acc.currency,
            )
        )
    exposures_out.sort(key=lambda e: -e.weight)
    overlap = sorted(
        (e for e in exposures_out if len(e.channels) > 1), key=lambda e: -e.overlap_weight
    )

    # ---- dimensions ----
    views: list[ExposureView] = []
    shown_issuers = exposures_out[:top]
    for d in DIMENSIONS:
        keys = set(buckets[d].direct) | set(buckets[d].fund)
        items: list[Bucket] = []
        for k in keys:
            dw, fw = buckets[d].direct.get(k, 0.0), buckets[d].fund.get(k, 0.0)
            weight = dw + fw
            if abs(weight) < 1e-12:
                continue
            items.append(
                Bucket(
                    key=k,
                    label=_label(d, k),
                    kind=_bucket_kind(k),
                    weight=weight,
                    value_base=weight * total,
                    direct_weight=dw,
                    fund_weight=fw,
                )
            )
        items.sort(key=lambda b: (b.kind != "classified", -b.weight))
        views.append(
            ExposureView(
                dimension=d,
                buckets=items,
                reconciles=abs(sum(b.weight for b in items) - (1.0 + over_total)) <= tol,
                issuer_buckets={
                    e.key: {
                        k: v for k, v in sorted(issuers[e.key].by_dim[d].items()) if abs(v) > 1e-12
                    }
                    for e in shown_issuers
                },
            )
        )

    # ---- fees ----
    held_funds = [f for f in funds_out if f.weight > 0]
    priced = [f for f in held_funds if f.fee_drag is not None]
    unpriced = [f for f in held_funds if f.fee_drag is None]
    fees: FeeDrag | None = None
    if held_funds:
        drag = sum(f.fee_drag or 0.0 for f in priced)
        priced_weight = sum(f.weight for f in priced)
        fees = FeeDrag(
            annual_drag=drag,
            annual_cost_base=drag * total,
            funds_with_ratio=len(priced),
            funds_without_ratio=[f.label for f in unpriced],
            unpriced_weight=sum(f.weight for f in unpriced),
            weighted_expense_ratio=drag / priced_weight if priced_weight > 0 else None,
        )
        if unpriced:
            notes.append(
                "Fee drag excludes funds without a known expense ratio: "
                + ", ".join(f.label for f in unpriced)
                + "."
            )

    unknown_total = residual_total + not_looked_total
    stale_funds = [f.label for f in funds_out if f.stale]
    if stale_funds:
        notes.append("Stale fund holdings: " + ", ".join(stale_funds) + ".")
    if over_total > tol:
        notes.append("Some funds' published rows exceed 100% of net assets.")
    if unknown_total > 0:
        notes.append(
            f"{unknown_total:.1%} of portfolio value is unknown fund weight; it is shown as its "
            "own bucket and not spread over known holdings."
        )
    if unidentified_weight > 0:
        notes.append(
            f"{unidentified_weight:.2%} of portfolio value sits in published rows with neither "
            "an ISIN nor a name: kept as one 'unidentified' line, not merged into any issuer."
        )
    if any(e.merged_securities > 1 for e in exposures_out):
        notes.append("Issuers with several securities (ADR/ordinary, share classes) are merged.")
    return AggregateReport(
        as_of=as_of,
        portfolio_snapshot_date=portfolio.snapshot_date,
        base_currency=portfolio.base_currency,
        total_value=total,
        available=any(f.holdings_as_of for f in funds_out) or direct_value > 0,
        method=METHOD,
        funds=funds_out,
        stale_funds=stale_funds,
        issuers=shown_issuers,
        overlap=overlap[:top],
        exposures=views,
        fees=fees,
        unknown=Unknown(
            unknown_fund_weight=unknown_total,
            unknown_fund_value=unknown_total * total,
            not_looked_through_weight=not_looked_total,
            residual_weight=residual_total,
            over_reported_weight=over_total,
            by_fund=unknown_by_fund,
        ),
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Fund-to-fund overlap, concentration and coverage (D6.8)
# ---------------------------------------------------------------------------


class FundPairOverlap(BaseModel):
    fund_a: str
    fund_b: str
    overlap: float = Field(description="Sum over shared issuers of the smaller fund weight.")
    shared_issuers: int
    as_of_a: dt.date
    as_of_b: dt.date
    same_date: bool


class FundCoverage(BaseModel):
    isin: str
    label: str
    as_of: dt.date
    lag_days: int
    stale: bool
    equity_weight: float
    cash_derivative_other_weight: float
    unknown_weight: float = Field(description="1 - sum of rows, when positive.")
    unidentified_equity_weight: float = Field(
        description="Equity rows without an ISIN (matched by name only)."
    )
    hhi: float | None
    effective_n: float | None
    top10_share: float | None
    issuers: int


class FundOverlapReport(BaseModel):
    as_of: dt.date
    pairs: list[FundPairOverlap]
    funds: list[FundCoverage]
    notes: list[str]


def compute_fund_overlap(
    settings: LookThroughSettings,
    snapshots: dict[str, SnapshotIn],
    *,
    as_of: dt.date,
    register: dict[str, IssuerRef] | None = None,
) -> FundOverlapReport:
    groups = config_groups(settings)
    reg = register or {}
    per_fund: dict[str, dict[str, float]] = {}
    coverage: list[FundCoverage] = []
    for isin, snap in sorted(snapshots.items()):
        weights: dict[str, float] = defaultdict(float)
        unidentified = 0.0
        for r in snap.rows:
            if r.asset_class != "equity":
                continue
            if not r.isin:
                unidentified += r.weight
            ref = issuer_for(r.isin, r.name, groups, reg)
            if ref is not None:
                weights[ref.key] += r.weight
        per_fund[isin] = dict(weights)
        equity = sum(weights.values())
        rows_total = sum(r.weight for r in snap.rows)
        total_eq = sum(w for w in weights.values() if w > 0)
        shares = sorted((w / total_eq for w in weights.values() if w > 0), reverse=True)
        hhi = sum(s * s for s in shares) if shares else None
        cfg = settings.fund(isin)
        lag = (as_of - snap.as_of).days
        coverage.append(
            FundCoverage(
                isin=isin,
                label=cfg.label if cfg else isin,
                as_of=snap.as_of,
                lag_days=lag,
                stale=lag > settings.stale_after_days,
                equity_weight=equity,
                cash_derivative_other_weight=rows_total - equity,
                unknown_weight=max(1.0 - rows_total, 0.0),
                unidentified_equity_weight=unidentified,
                hhi=hhi,
                effective_n=1 / hhi if hhi else None,
                top10_share=sum(shares[:10]) if shares else None,
                issuers=len(shares),
            )
        )
    pairs: list[FundPairOverlap] = []
    isins = sorted(per_fund)
    for i, a in enumerate(isins):
        for b in isins[i + 1 :]:
            shared = set(per_fund[a]) & set(per_fund[b])
            pairs.append(
                FundPairOverlap(
                    fund_a=a,
                    fund_b=b,
                    overlap=sum(min(per_fund[a][k], per_fund[b][k]) for k in shared),
                    shared_issuers=len(shared),
                    as_of_a=snapshots[a].as_of,
                    as_of_b=snapshots[b].as_of,
                    same_date=snapshots[a].as_of == snapshots[b].as_of,
                )
            )
    pairs.sort(key=lambda p: -p.overlap)
    notes = [
        "Overlap is the sum of the smaller weight per shared issuer, each fund as published "
        "on its own date; it is not an index overlap."
    ]
    if any(not p.same_date for p in pairs):
        notes.append("Some fund pairs are compared across different holdings dates.")
    return FundOverlapReport(as_of=as_of, pairs=pairs, funds=coverage, notes=notes)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


async def _inputs(
    pool: asyncpg.Pool, as_of: dt.date | None
) -> tuple[JobsConfig, PortfolioIn | None, dict[str, SnapshotIn], dt.date]:
    stored = await load_active(pool, "jobs", JobsConfig)
    config = stored.body if stored else JobsConfig()
    isins = [f.isin for f in config.look_through.funds]
    if as_of is None:
        day = dt.datetime.now(dt.UTC).date()
        return config, await load_portfolio(pool), await load_snapshots(pool, isins), day
    return (
        config,
        await load_portfolio_as_of(pool, as_of),
        await load_snapshots_as_of(pool, isins, as_of),
        as_of,
    )


def _position_isins(portfolio: PortfolioIn | None, snapshots: dict[str, SnapshotIn]) -> list[str]:
    isins = {p.isin for p in (portfolio.positions if portfolio else []) if p.isin}
    for snap in snapshots.values():
        isins.update(r.isin for r in snap.rows if r.isin)
    return sorted(isins)


async def aggregate_portfolio(
    pool: asyncpg.Pool, *, as_of: dt.date | None = None, top: int = 25
) -> AggregateReport:
    """The aggregate report from stored data; `as_of` answers as of a past date using only
    what was known by then."""
    config, portfolio, snapshots, day = await _inputs(pool, as_of)
    settings = config.look_through
    register = await load_issuer_register(pool, _position_isins(portfolio, snapshots))
    ratios = await latest_expense_ratios(pool, [f.isin for f in settings.funds], as_of)
    return aggregate_exposure(
        portfolio,
        settings,
        snapshots,
        as_of=day,
        register=register,
        expense_ratios=ratios,
        top=top,
    )


async def fund_overlap(pool: asyncpg.Pool, *, as_of: dt.date | None = None) -> FundOverlapReport:
    config, portfolio, snapshots, day = await _inputs(pool, as_of)
    register = await load_issuer_register(pool, _position_isins(portfolio, snapshots))
    return compute_fund_overlap(config.look_through, snapshots, as_of=day, register=register)


__all__ = [
    "UNKNOWN_FUND",
    "AggregateReport",
    "FundOverlapReport",
    "PositionIn",
    "aggregate_exposure",
    "aggregate_portfolio",
    "compute_fund_overlap",
    "fund_overlap",
]
