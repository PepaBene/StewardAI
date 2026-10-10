"""Pure fund analysis (no I/O): the profile, issuer-normalised holdings, concentration and style
proxies, the bond-fund view and the due-diligence checklist.

The style measures are StewardAI proxies built from the fund's published holdings and the
filed statements of the holdings the app covers. They are not Morningstar Style Box categories
and never named as such. A figure the inputs cannot support is `None` with a reason.
"""

# ruff: noqa: E501
from __future__ import annotations

import csv
import datetime as dt
import io
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from steward.analytics.contract import (
    AnalyticsResult,
    SourceRef,
    input_hash,
    unavailable,
)
from steward.analytics.contract import (
    Status as ResultStatus,
)
from steward.analytics.exposure import (
    YieldInput,
    concentration,
    pe_from_yield,
    portfolio_earnings_yield,
)
from steward.fundresearch.data import FundData, FundListing, lending_answer
from steward.funds.aggregate import IssuerRef, issuer_for

ZERO = Decimal(0)
ONE = Decimal(1)
FUND_METHOD = "1.0.0"  # bump with any change to the rules below; saved checklists keep theirs
TOP_DEFAULT = 25


def D(x: Any) -> Decimal | None:
    if x is None:
        return None
    return x if isinstance(x, Decimal) else Decimal(str(x))


# ----------------------------------------------------------------------------- profile


class ProfileField(BaseModel):
    key: str
    label: str
    value: str | None
    unit: Literal["text", "fraction", "money", "date"] = "text"
    currency: str | None = None
    source: str | None = None
    source_url: str | None = None
    as_of: dt.date | None = None
    reason: str | None = Field(default=None, description="Why there is no value, or a caveat.")


class Sibling(BaseModel):
    isin: str
    name: str | None
    as_of: dt.date | None


class Identity(BaseModel):
    fund_isin: str
    share_class: str | None
    portfolio_id: str | None
    listings: list[FundListing]
    sibling_share_classes: list[Sibling]
    note: str


class FundProfile(BaseModel):
    isin: str
    name: str | None
    provider: str | None
    identity: Identity
    fields: list[ProfileField]
    have: int
    of: int
    charges: dict[str, Any] = Field(default_factory=dict)
    factsheet_as_of: dt.date | None
    holdings_as_of: dt.date | None
    method_version: str = FUND_METHOD
    warnings: list[str] = Field(default_factory=list)


def build_profile(data: FundData) -> FundProfile:
    f = data.factsheet or {}
    meta = data.meta or {}
    facts = data.facts
    sheet_src = (
        f"{f['provider']} factsheet ({f['source']})"
        if f.get("source") and f.get("provider")
        else "issuer factsheet"
    )
    sheet_date = f.get("as_of")
    fields: list[ProfileField] = []

    def add(
        key: str,
        label: str,
        value: Any,
        *,
        unit: Literal["text", "fraction", "money", "date"] = "text",
        currency: str | None = None,
        source: str | None = None,
        as_of: dt.date | None = None,
        url: str | None = None,
        reason: str | None = None,
    ) -> None:
        text = None if value is None or value == "" else str(value)
        fields.append(
            ProfileField(
                key=key,
                label=label,
                value=text,
                unit=unit,
                currency=currency,
                source=source if text else None,
                source_url=url if text else None,
                as_of=as_of if text else None,
                reason=None if text else (reason or "not in the stored issuer data"),
            )
        )

    def owner(key: str) -> tuple[str | None, str | None, dt.date | None]:
        fact = facts.get(key)
        return (fact.value, fact.source_url, fact.as_of) if fact else (None, None, None)

    add(
        "issuer",
        "Issuer",
        f.get("provider") or meta.get("provider"),
        source=sheet_src,
        as_of=sheet_date,
    )
    add(
        "share_class",
        "Share class",
        f.get("share_class"),
        source=sheet_src,
        as_of=sheet_date,
        reason="the issuer snapshot names no share class",
    )
    dom_value, dom_url, dom_date = owner("domicile")
    if dom_value:
        add("domicile", "Domicile", dom_value, source="owner fact", url=dom_url, as_of=dom_date)
    else:
        add(
            "domicile",
            "Domicile",
            None,
            reason=f"not recorded; the ISIN prefix is {data.isin[:2]}, which usually but not always matches the legal domicile",
        )
    add(
        "currency",
        "Fund currency",
        f.get("fund_currency") or meta.get("fund_currency"),
        source=sheet_src,
        as_of=sheet_date,
    )
    add("benchmark", "Benchmark", f.get("benchmark_name"), source=sheet_src, as_of=sheet_date)
    rep, rep_url, rep_date = owner("replication_method")
    add(
        "replication",
        "Replication method",
        rep,
        source="owner fact",
        url=rep_url,
        as_of=rep_date,
        reason="not published in the issuer data; record it with a source on the checklist tab",
    )
    add(
        "distribution",
        "Distribution policy",
        f.get("distribution_policy"),
        source=sheet_src,
        as_of=sheet_date,
    )
    inc, inc_url, inc_date = owner("inception_date")
    add(
        "inception",
        "Inception",
        inc,
        unit="date",
        source="owner fact",
        url=inc_url,
        as_of=inc_date,
        reason="not published in the issuer data; record it with a source on the checklist tab",
    )
    add(
        "total_assets",
        "Total assets",
        f.get("total_assets"),
        unit="money",
        currency=f.get("total_assets_currency"),
        source=sheet_src,
        as_of=f.get("total_assets_as_of") or sheet_date,
    )
    add(
        "expense_ratio",
        "Ongoing charge",
        f.get("expense_ratio"),
        unit="fraction",
        source=sheet_src,
        as_of=sheet_date,
    )
    add("factsheet_date", "Factsheet date", sheet_date, unit="date", source=sheet_src)
    add(
        "holdings_date",
        "Holdings date",
        meta.get("as_of"),
        unit="date",
        source=f"{meta.get('provider', 'issuer')} holdings ({meta.get('source', '')})"
        if meta
        else None,
    )
    have = sum(1 for x in fields if x.value is not None)

    portfolio_id = meta.get("portfolio_id")
    siblings = [
        Sibling(isin=s["fund_isin"], name=s["fund_name"], as_of=s["as_of"]) for s in data.siblings
    ]
    note = (
        "This page is the fund and share class identified by the ISIN above. Exchange listings of it are separate rows with their own currency and price; "
        "other share classes of the same portfolio are listed apart and are not added to it."
    )
    warnings: list[str] = []
    if data.factsheet is None:
        warnings.append("No issuer factsheet is stored; costs and assets are unknown.")
    if data.snapshot is None:
        warnings.append(
            "No holdings snapshot is stored; holdings, concentration and look-through are unknown."
        )
    return FundProfile(
        isin=data.isin,
        name=data.name,
        provider=f.get("provider") or meta.get("provider"),
        identity=Identity(
            fund_isin=data.isin,
            share_class=f.get("share_class"),
            portfolio_id=portfolio_id,
            listings=data.listings,
            sibling_share_classes=siblings,
            note=note,
        ),
        fields=fields,
        have=have,
        of=len(fields),
        charges=dict(f.get("charges") or {}),
        factsheet_as_of=sheet_date,
        holdings_as_of=meta.get("as_of"),
        warnings=warnings,
    )


# ----------------------------------------------------------------------------- holdings


class HoldingRow(BaseModel):
    name: str | None
    isin: str | None
    ticker: str | None
    asset_class: str
    weight: Decimal
    sector: str | None
    country: str | None
    currency: str | None
    currency_derived: bool
    issuer_key: str | None
    issuer_label: str | None


class FundBucket(BaseModel):
    label: str
    weight: Decimal
    kind: Literal["known", "unclassified", "not_accounted"] = "known"


class IssuerLine(BaseModel):
    issuer_key: str
    label: str
    weight: Decimal
    rows: int
    isins: list[str]
    sector: str | None
    country: str | None


class PublisherRow(BaseModel):
    label: str
    code: str | None
    fund_weight: Decimal | None
    benchmark_weight: Decimal | None


class PublisherBreakdown(BaseModel):
    dimension: str
    scheme: str
    as_of: dt.date | None
    rows: list[PublisherRow]


class HoldingsView(BaseModel):
    isin: str
    as_of: dt.date | None
    fetched_at: dt.datetime | None
    snapshot_id: int | None
    source: str | None
    row_count: int
    total_weight: Decimal | None
    residual_weight: Decimal | None = Field(
        description="1 - the published rows; unknown, never spread over the known rows."
    )
    reconciles: bool | None
    reconcile_note: str
    asset_classes: list[FundBucket]
    sectors: list[FundBucket]
    countries: list[FundBucket]
    currencies: list[FundBucket]
    top: list[IssuerLine]
    issuer_count: int
    publisher_breakdowns: list[PublisherBreakdown]
    unidentified_equity_weight: Decimal
    warnings: list[str]
    method_version: str = FUND_METHOD
    input_hash: str | None = None


def holding_rows(
    data: FundData, groups: Mapping[str, IssuerRef], register: Mapping[str, IssuerRef]
) -> list[HoldingRow]:
    snap = data.snapshot
    if snap is None:
        return []
    out: list[HoldingRow] = []
    for r in snap.rows:
        ref = (
            issuer_for(r.isin, r.name, dict(groups), dict(register))
            if r.asset_class == "equity"
            else None
        )
        out.append(
            HoldingRow(
                name=r.name,
                isin=r.isin,
                ticker=r.ticker,
                asset_class=r.asset_class,
                weight=D(r.weight) or ZERO,
                sector=r.sector,
                country=r.country,
                currency=r.currency,
                currency_derived=r.currency_derived,
                issuer_key=ref.key if ref else None,
                issuer_label=ref.label if ref else None,
            )
        )
    return out


def _buckets(
    rows: Sequence[HoldingRow], attr: str, *, equity_only: bool, unclassified: str = "Unclassified"
) -> list[FundBucket]:
    acc: dict[str, Decimal] = defaultdict(lambda: ZERO)
    missing = ZERO
    for r in rows:
        if equity_only and r.asset_class != "equity":
            continue
        key = getattr(r, attr)
        if key:
            acc[key] += r.weight
        else:
            missing += r.weight
    out = [FundBucket(label=k, weight=v) for k, v in sorted(acc.items(), key=lambda kv: -kv[1])]
    if missing > 0:
        out.append(FundBucket(label=unclassified, weight=missing, kind="unclassified"))
    return out


def build_holdings(
    data: FundData, rows: Sequence[HoldingRow], top: int = TOP_DEFAULT
) -> HoldingsView:
    snap, meta, f = data.snapshot, data.meta, data.factsheet or {}
    if snap is None or meta is None:
        return HoldingsView(
            isin=data.isin,
            as_of=None,
            fetched_at=None,
            snapshot_id=None,
            source=None,
            row_count=0,
            total_weight=None,
            residual_weight=None,
            reconciles=None,
            reconcile_note="No holdings snapshot is stored for this fund.",
            asset_classes=[],
            sectors=[],
            countries=[],
            currencies=[],
            top=[],
            issuer_count=0,
            publisher_breakdowns=[],
            unidentified_equity_weight=ZERO,
            warnings=["Holdings are unknown, not empty."],
        )
    total = sum((r.weight for r in rows), ZERO)
    residual = max(ONE - total, ZERO)
    tolerance = D(f.get("reconcile_tolerance")) or Decimal("0.01")
    reconciles = abs(ONE - total) <= tolerance
    warnings = list(snap.warnings)
    if total > ONE + tolerance:
        warnings.append(
            f"Published rows sum to {total:.2%}, more than 100%; derivative or leverage rows may overlap."
        )
    asset: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for r in rows:
        asset[r.asset_class] += r.weight
    asset_classes = [
        FundBucket(label=k, weight=v) for k, v in sorted(asset.items(), key=lambda kv: -kv[1])
    ]
    if residual > 0:
        asset_classes.append(
            FundBucket(
                label="Not accounted for by the published rows",
                weight=residual,
                kind="not_accounted",
            )
        )
    currencies = _buckets(rows, "currency", equity_only=False)
    if any(r.currency_derived for r in rows):
        warnings.append(
            "Some holding currencies are mapped from the listing country, not published by the issuer."
        )
    lines: dict[str, dict[str, Any]] = {}
    unidentified = ZERO
    for r in rows:
        if r.asset_class != "equity" or r.issuer_key is None:
            continue
        if not r.isin:
            unidentified += r.weight
        line = lines.setdefault(
            r.issuer_key,
            {
                "label": r.issuer_label or r.issuer_key,
                "weight": ZERO,
                "rows": 0,
                "isins": set(),
                "sector": r.sector,
                "country": r.country,
            },
        )
        line["weight"] += r.weight
        line["rows"] += 1
        if r.isin:
            line["isins"].add(r.isin)
    issuer_lines = sorted(
        (
            IssuerLine(
                issuer_key=k,
                label=v["label"],
                weight=v["weight"],
                rows=v["rows"],
                isins=sorted(v["isins"]),
                sector=v["sector"],
                country=v["country"],
            )
            for k, v in lines.items()
        ),
        key=lambda x: -x.weight,
    )
    pub: dict[tuple[str, str], list[PublisherRow]] = defaultdict(list)
    pub_date: dict[tuple[str, str], dt.date | None] = {}
    for b in snap.breakdowns:
        key = (b.dimension, b.scheme)
        pub[key].append(
            PublisherRow(
                label=b.label,
                code=b.code,
                fund_weight=D(b.fund_weight),
                benchmark_weight=D(b.benchmark_weight),
            )
        )
        pub_date[key] = b.as_of
    breakdowns = [
        PublisherBreakdown(
            dimension=k[0],
            scheme=k[1],
            as_of=pub_date[k],
            rows=sorted(v, key=lambda x: -(x.fund_weight or ZERO)),
        )
        for k, v in sorted(pub.items())
    ]
    note = (
        f"The published rows account for {total:.2%} of net assets; {residual:.2%} is unknown and is not assigned to any holding."
        if residual > 0
        else f"The published rows account for {total:.2%} of net assets."
    )
    return HoldingsView(
        isin=data.isin,
        as_of=snap.as_of,
        fetched_at=snap.fetched_at,
        snapshot_id=meta["id"],
        source=snap.source,
        row_count=len(rows),
        total_weight=total,
        residual_weight=residual,
        reconciles=reconciles,
        reconcile_note=note,
        asset_classes=asset_classes,
        sectors=_buckets(rows, "sector", equity_only=True),
        countries=_buckets(rows, "country", equity_only=True),
        currencies=currencies,
        top=issuer_lines[:top],
        issuer_count=len(issuer_lines),
        publisher_breakdowns=breakdowns,
        unidentified_equity_weight=unidentified,
        warnings=warnings,
        input_hash=input_hash(
            {"snapshot": meta["id"], "rows": [(r.isin, r.name, str(r.weight)) for r in rows]}
        ),
    )


def issuer_weights(rows: Sequence[HoldingRow]) -> dict[str, Decimal]:
    """Equity weight per canonical issuer (fractions of net assets)."""
    out: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for r in rows:
        if r.asset_class == "equity" and r.issuer_key:
            out[r.issuer_key] += r.weight
    return dict(out)


def exposure_csv(view: HoldingsView, rows: Sequence[HoldingRow]) -> str:
    """A derived exposure table with its provenance in every row; not the issuer's file."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(
        [
            "fund_isin",
            "holdings_as_of",
            "snapshot_id",
            "source",
            "method_version",
            "name",
            "isin",
            "ticker",
            "asset_class",
            "weight",
            "issuer",
            "sector",
            "country",
            "currency",
            "currency_derived",
        ]
    )
    for r in rows:
        w.writerow(
            [
                view.isin,
                view.as_of,
                view.snapshot_id,
                view.source,
                view.method_version,
                r.name,
                r.isin,
                r.ticker,
                r.asset_class,
                r.weight,
                r.issuer_label,
                r.sector,
                r.country,
                r.currency,
                r.currency_derived,
            ]
        )
    if view.residual_weight:
        w.writerow(
            [
                view.isin,
                view.as_of,
                view.snapshot_id,
                view.source,
                view.method_version,
                "Not accounted for by the published rows",
                "",
                "",
                "unknown",
                view.residual_weight,
                "",
                "",
                "",
                "",
                "",
            ]
        )
    return buf.getvalue()


# ----------------------------------------------------------------------------- style proxies

SIZE_BANDS: tuple[tuple[str, Decimal | None], ...] = (
    ("Large (market cap at least EUR 10bn)", Decimal(10_000_000_000)),
    ("Mid (EUR 2bn to 10bn)", Decimal(2_000_000_000)),
    ("Small (below EUR 2bn)", ZERO),
)


@dataclass(frozen=True)
class StyleLine:
    key: str
    label: str
    weight: Decimal
    sector: str | None
    market_cap_eur: Decimal | None = None
    earnings_yield: Decimal | None = None
    price_to_book: Decimal | None = None
    profitability: Decimal | None = None
    covered: bool = False  # the app holds statements and prices for this issuer


class StyleMetric(BaseModel):
    id: str
    label: str
    value: Decimal | None
    unit: str
    covered_weight: Decimal
    equity_weight: Decimal
    excluded: int
    formula: str
    reason: str | None = None


class FundConcentration(BaseModel):
    equity_weight: Decimal
    issuers: int
    top10_weight: Decimal | None = Field(
        description="Share of net assets in the ten largest issuers."
    )
    top10_of_equity: Decimal | None
    hhi: Decimal | None
    effective_issuers: Decimal | None
    unidentified_equity_weight: Decimal
    basis: str


class SizeBand(BaseModel):
    label: str
    weight: Decimal
    kind: Literal["known", "no_market_cap"] = "known"


class StyleBand(BaseModel):
    label: str
    weight: Decimal = Field(description="Share of the fund's identified equity weight.")
    kind: Literal["value", "blend", "growth", "no_earnings", "not_covered"]


VALUE_GROWTH_RULE = (
    "Each covered holding votes: earnings yield of 6% or more and price to book of 1.5 or less each "
    "vote value; earnings yield of 3% or less and price to book of 4 or more each vote growth. More "
    "value votes make it value-leaning, more growth votes growth-leaning, otherwise blend. A holding "
    "with negative earnings is its own band, and one the app has no statements for is not classified. "
    "These thresholds are StewardAI's own and are not Morningstar's Style Box."
)


def classify_style(line: StyleLine) -> str:
    if not line.covered:
        return "not_covered"
    ey, pb = line.earnings_yield, line.price_to_book
    if ey is not None and ey < 0:
        return "no_earnings"
    if ey is None and (pb is None or pb <= 0):
        return "not_covered"
    votes = 0
    if ey is not None:
        votes += 1 if ey >= Decimal("0.06") else -1 if ey <= Decimal("0.03") else 0
    if pb is not None and pb > 0:
        votes += 1 if pb <= Decimal("1.5") else -1 if pb >= Decimal(4) else 0
    return "value" if votes > 0 else "growth" if votes < 0 else "blend"


class StyleView(BaseModel):
    isin: str
    as_of: dt.date | None
    concentration: FundConcentration | None
    size: list[SizeBand]
    size_coverage: Decimal | None
    value_growth: list[StyleBand] = Field(default_factory=list)
    value_growth_rule: str = VALUE_GROWTH_RULE
    metrics: list[StyleMetric]
    note: str
    warnings: list[str]
    method_version: str = FUND_METHOD


STYLE_NOTE = (
    "These are StewardAI proxies from the fund's published holdings and the filed statements of the "
    "holdings the app covers. They are not Morningstar Style Box categories, and a holding the app has no "
    "statements for is left out and counted in the coverage, not guessed."
)


def _weighted_mean(
    lines: Sequence[StyleLine], attr: str, *, skip_financials: bool = False
) -> tuple[Decimal | None, Decimal, int]:
    covered = ZERO
    acc = ZERO
    excluded = 0
    for line in lines:
        v = getattr(line, attr)
        if v is None or (skip_financials and (line.sector or "").lower().startswith("financ")):
            excluded += 1
            continue
        covered += line.weight
        acc += line.weight * v
    return (acc / covered if covered > 0 else None), covered, excluded


def build_style(
    isin: str, as_of: dt.date | None, lines: Sequence[StyleLine], unidentified: Decimal
) -> StyleView:
    equity = sum((x.weight for x in lines), ZERO)
    warnings: list[str] = []
    conc: FundConcentration | None = None
    if equity > 0:
        shares = {x.key: x.weight / equity for x in lines}
        c = concentration(shares, unknown_weight=ZERO)
        ordered = sorted((x.weight for x in lines), reverse=True)
        top10 = sum(ordered[:10], ZERO)
        conc = FundConcentration(
            equity_weight=equity,
            issuers=len(lines),
            top10_weight=top10,
            top10_of_equity=top10 / equity,
            hhi=c.hhi,
            effective_issuers=c.effective_count,
            unidentified_equity_weight=unidentified,
            basis="HHI and effective issuers are computed on each issuer's share of the fund's identified equity weight",
        )
        if unidentified > 0:
            warnings.append(
                f"{unidentified:.2%} of net assets is equity without an ISIN, grouped by name only."
            )
    size: list[SizeBand] = []
    size_cov: Decimal | None = None
    if equity > 0:
        acc = {name: ZERO for name, _ in SIZE_BANDS}
        none = ZERO
        for x in lines:
            if x.market_cap_eur is None:
                none += x.weight
                continue
            for name, floor in SIZE_BANDS:
                if floor is not None and x.market_cap_eur >= floor:
                    acc[name] += x.weight
                    break
        size = [SizeBand(label=n, weight=acc[n]) for n, _ in SIZE_BANDS]
        if none > 0:
            size.append(
                SizeBand(label="No market cap available", weight=none, kind="no_market_cap")
            )
        size_cov = (equity - none) / equity
    value_growth: list[StyleBand] = []
    if equity > 0:
        vg = {k: ZERO for k in ("value", "blend", "growth", "no_earnings", "not_covered")}
        for x in lines:
            vg[classify_style(x)] += x.weight
        names = {
            "value": "Value-leaning",
            "blend": "Blend",
            "growth": "Growth-leaning",
            "no_earnings": "Negative earnings",
            "not_covered": "Not classified (no statements)",
        }
        value_growth = [StyleBand(label=names[k], weight=vg[k] / equity, kind=k) for k in names]
    metrics: list[StyleMetric] = []
    if equity > 0:
        ey = portfolio_earnings_yield(
            [
                YieldInput(key=x.label, weight=x.weight, earnings_yield=x.earnings_yield)
                for x in lines
            ],
            includes_look_through=False,
        )
        ey_cov = ey.coverage.numerator if ey.coverage else ZERO
        metrics.append(
            StyleMetric(
                id="earnings_yield",
                label="Earnings yield (covered holdings with positive earnings)",
                value=ey.value,
                unit="fraction",
                covered_weight=ey_cov,
                equity_weight=equity,
                excluded=len(ey.explain.exclusions) if ey.explain else 0,
                formula="sum(weight x E/P) / sum(weight) over covered positive-earnings holdings",
                reason=None
                if ey.value is not None
                else (
                    ey.warnings[0] if ey.warnings else "no covered holding has positive earnings"
                ),
            )
        )
        pe = pe_from_yield(ey.value)
        metrics.append(
            StyleMetric(
                id="pe_proxy",
                label="Price to earnings (inverse of the yield above)",
                value=pe,
                unit="multiple",
                covered_weight=ey_cov,
                equity_weight=equity,
                excluded=len(ey.explain.exclusions) if ey.explain else 0,
                formula="1 / earnings yield, only when the aggregate yield is positive",
                reason=None if pe is not None else "the aggregate yield is not positive",
            )
        )
        by = ZERO
        covered_pb = ZERO
        ex_pb = 0
        for x in lines:
            if x.price_to_book is None or x.price_to_book <= 0:
                ex_pb += 1
                continue
            covered_pb += x.weight
            by += x.weight / x.price_to_book
        book_yield = by / covered_pb if covered_pb > 0 else None
        pb = ONE / book_yield if book_yield else None
        metrics.append(
            StyleMetric(
                id="pb_proxy",
                label="Price to book (weighted harmonic mean)",
                value=pb,
                unit="multiple",
                covered_weight=covered_pb,
                equity_weight=equity,
                excluded=ex_pb,
                formula="1 / (sum(weight / P/B) / sum(weight)) over covered holdings with positive book value",
                reason=None if pb is not None else "no covered holding has a positive book value",
            )
        )
        prof, prof_cov, prof_ex = _weighted_mean(lines, "profitability", skip_financials=True)
        metrics.append(
            StyleMetric(
                id="profitability",
                label="Gross profit to assets (weighted mean, excludes financials)",
                value=prof,
                unit="fraction",
                covered_weight=prof_cov,
                equity_weight=equity,
                excluded=prof_ex,
                formula="sum(weight x gross profit / total assets) / sum(weight)",
                reason=None
                if prof is not None
                else "no covered non-financial holding has the inputs",
            )
        )
    else:
        warnings.append(
            "No identified equity holdings: concentration and style proxies are unknown."
        )
    return StyleView(
        isin=isin,
        as_of=as_of,
        concentration=conc,
        size=size,
        size_coverage=size_cov,
        value_growth=value_growth,
        metrics=metrics,
        note=STYLE_NOTE,
        warnings=warnings,
    )


# ----------------------------------------------------------------------------- bond funds


class BondMix(BaseModel):
    label: str
    weight: Decimal


class RateBucket(BaseModel):
    """Duration contributed by one key-rate bucket: DV01 x 10,000 / net assets, as reported."""

    label: str
    years: Decimal


class SpreadBucket(BaseModel):
    label: str
    investment_grade: Decimal | None
    non_investment_grade: Decimal | None


class BondView(BaseModel):
    isin: str
    applicable: bool | None
    applicability_reason: str
    weighted_maturity: AnalyticsResult
    duration: AnalyticsResult
    credit_quality: AnalyticsResult
    stated: list[ProfileField]
    asset_mix: list[BondMix]
    asset_mix_source: str | None
    rate_buckets: list[RateBucket] = Field(default_factory=list)
    credit_spread: list[SpreadBucket] = Field(default_factory=list)
    credit_note: str | None = None
    method_version: str = FUND_METHOD
    warnings: list[str]


@dataclass
class NportRisk:
    """The risk metrics a regulatory filing reports, with the net assets they scale against."""

    source: str
    period_end: dt.date
    net_assets: Decimal | None
    metrics: Mapping[str, Any]


RATE_KEYS = (
    ("period3Mon", "3 months"),
    ("period1Yr", "1 year"),
    ("period5Yr", "5 years"),
    ("period10Yr", "10 years"),
    ("period30Yr", "30 years"),
)


def _num(x: Any) -> Decimal | None:
    try:
        return Decimal(str(x)) if x is not None and str(x).strip() != "" else None
    except ArithmeticError:
        return None


def dv01_duration(
    risk: NportRisk | None,
) -> tuple[list[RateBucket], Decimal | None, list[str]]:
    """Duration implied by the filing's USD DV01 at each key rate. Buckets the filing leaves out are not
    guessed; if any is missing the total is withheld rather than understated."""
    if risk is None:
        return [], None, []
    notes: list[str] = []
    table = risk.metrics.get("interest_rate_dv01")
    if not isinstance(table, dict) or not table:
        return [], None, ["The filing reports no interest-rate DV01."]
    if not risk.net_assets or risk.net_assets <= 0:
        return [], None, ["The filing reports no net assets to scale the DV01 against."]
    usd = table.get("USD")
    others = sorted(k for k in table if k != "USD")
    if others:
        notes.append(
            f"DV01 in {', '.join(others)} is not included: converting it needs a dated rate the filing does not give."
        )
    if not isinstance(usd, dict):
        return [], None, [*notes, "The filing reports no USD DV01."]
    buckets: list[RateBucket] = []
    missing: list[str] = []
    for key, label in RATE_KEYS:
        v = _num(usd.get(key))
        if v is None:
            missing.append(label)
        else:
            buckets.append(RateBucket(label=label, years=v * 10000 / risk.net_assets))
    if missing:
        notes.append(f"No DV01 reported for {', '.join(missing)}; the total is withheld.")
        return buckets, None, notes
    return buckets, sum((b.years for b in buckets), ZERO), notes


def credit_spread_split(risk: NportRisk | None) -> list[SpreadBucket]:
    if risk is None:
        return []
    ig = risk.metrics.get("credit_spread_investment_grade")
    nig = risk.metrics.get("credit_spread_non_investment_grade")
    if not isinstance(ig, dict) and not isinstance(nig, dict):
        return []
    out = []
    for key, label in RATE_KEYS:
        a = _num(ig.get(key)) if isinstance(ig, dict) else None
        b = _num(nig.get(key)) if isinstance(nig, dict) else None
        if a is None and b is None:
            continue
        out.append(
            SpreadBucket(
                label=label,
                investment_grade=a * 10000 / risk.net_assets
                if a is not None and risk.net_assets
                else None,
                non_investment_grade=b * 10000 / risk.net_assets
                if b is not None and risk.net_assets
                else None,
            )
        )
    return out


def build_bond(
    data: FundData,
    nport_mix: Sequence[tuple[str, Decimal]],
    nport_source: str | None,
    risk: NportRisk | None = None,
) -> BondView:
    f = data.factsheet or {}
    eq = D(f.get("equity_weight"))
    other = D(f.get("other_weight"))
    if eq is None:
        applicable, why = (
            None,
            "No holdings split is stored, so the fund cannot be classed as bond or equity.",
        )
    elif eq < Decimal("0.2") and (other or ZERO) > Decimal("0.4"):
        applicable, why = (
            True,
            f"Equity is {eq:.1%} and other instruments {other:.1%} of net assets, which is consistent with a bond or multi-asset fund.",
        )
    else:
        applicable, why = (
            False,
            f"Equity is {eq:.1%} of net assets; maturity, duration and credit quality describe bond holdings, which this fund does not mainly hold.",
        )
    method = FUND_METHOD
    reason = "The stored issuer holdings carry no maturity, duration or rating fields, and credit quality is never inferred from an issuer's name."
    stated = [
        ProfileField(
            key="distribution",
            label="Distribution policy",
            value=f.get("distribution_policy"),
            reason=None if f.get("distribution_policy") else "not in the stored issuer data",
            source="issuer factsheet",
            as_of=f.get("as_of"),
        ),
        ProfileField(
            key="benchmark",
            label="Benchmark",
            value=f.get("benchmark_name"),
            reason=None if f.get("benchmark_name") else "not in the stored issuer data",
            source="issuer factsheet",
            as_of=f.get("as_of"),
        ),
    ]
    buckets, dv01_total, dv01_notes = dv01_duration(risk)
    spreads = credit_spread_split(risk)
    warnings = [
        "The instrument category mix and rate sensitivities come from a regulatory N-PORT filing, which only US-registered funds file."
        if nport_mix or risk
        else "No regulatory filing is stored for this fund."
    ]
    warnings.extend(dv01_notes)

    def owner_years(key: str, label: str) -> AnalyticsResult | None:
        fact = data.facts.get(key)
        v = _num(fact.value) if fact else None
        if fact is None or v is None or v < 0:
            return None
        return AnalyticsResult(
            value=v.quantize(Decimal("0.01")),
            unit="years",
            as_of=fact.as_of,
            method_version=method,
            status=ResultStatus.COMPLETE,
            sources=[SourceRef(kind="owner_input", ref=label, url=fact.source_url)],
            warnings=["Recorded by you from the issuer's document, not computed from holdings."],
            input_hash=input_hash({"key": key, "value": str(v), "url": fact.source_url}),
        )

    maturity = owner_years("average_maturity_years", "Average maturity")
    duration = owner_years("effective_duration_years", "Effective duration")
    if duration is None and dv01_total is not None and risk is not None:
        duration = AnalyticsResult(
            value=dv01_total.quantize(Decimal("0.01")),
            unit="years",
            as_of=risk.period_end,
            method_version=method,
            status=ResultStatus.PARTIAL,
            sources=[SourceRef(kind="filing", ref=risk.source)],
            warnings=[
                "DV01-implied: the filing's USD DV01 across key rates times 10,000, over net assets. It is a rate sensitivity as reported, not an issuer-stated duration, and assumes net assets are in US dollars."
            ],
            input_hash=input_hash(
                {
                    "accession": risk.source,
                    "dv01": risk.metrics.get("interest_rate_dv01"),
                    "net_assets": risk.net_assets,
                }
            ),
        )
    credit_note = data.facts.get("credit_quality_note")
    return BondView(
        isin=data.isin,
        applicable=applicable,
        applicability_reason=why,
        weighted_maturity=maturity or unavailable("years", method, reason),
        duration=duration or unavailable("years", method, reason),
        credit_quality=unavailable(
            "ratio",
            method,
            "No rating breakdown is stored, and a rating is never inferred from an issuer's name."
            + (
                " The credit-spread sensitivity below is not a share of holdings."
                if spreads
                else ""
            ),
        ),
        stated=stated,
        asset_mix=[BondMix(label=k, weight=v) for k, v in nport_mix],
        asset_mix_source=nport_source,
        rate_buckets=buckets,
        credit_spread=spreads,
        credit_note=credit_note.value if credit_note else None,
        warnings=warnings,
    )


# ----------------------------------------------------------------------------- checklist

Status = Literal["good", "watch", "concern", "info", "unknown"]


class Criterion(BaseModel):
    id: str
    label: str
    status: Status
    observed: str
    rule: str
    source: str | None = None
    source_url: str | None = None
    critical: bool = False


class Checklist(BaseModel):
    isin: str
    as_of: dt.date
    criteria: list[Criterion]
    summary_available: bool
    summary: str
    abstain_reason: str | None
    method_version: str = FUND_METHOD


def _status_for_cost(er: Decimal | None) -> tuple[Status, str]:
    if er is None:
        return "unknown", "no ongoing charge is stored"
    if er <= Decimal("0.003"):
        return "good", f"{er:.2%} a year"
    if er <= Decimal("0.0075"):
        return "watch", f"{er:.2%} a year"
    return "concern", f"{er:.2%} a year"


def build_checklist(
    data: FundData,
    holdings: HoldingsView,
    style: StyleView,
    *,
    today: dt.date,
    tracking_difference: Decimal | None,
    tracking_note: str | None,
) -> Checklist:
    f = data.factsheet or {}
    facts = data.facts
    sheet_src = "issuer factsheet"
    crit: list[Criterion] = []

    st, obs = _status_for_cost(D(f.get("expense_ratio")))
    crit.append(
        Criterion(
            id="costs",
            label="Costs",
            status=st,
            observed=obs,
            rule="Good at or below 0.30% a year, watch up to 0.75%, concern above",
            source=sheet_src,
            critical=True,
        )
    )

    if tracking_difference is None:
        crit.append(
            Criterion(
                id="tracking",
                label="Tracking evidence",
                status="unknown",
                observed=tracking_note or "no benchmark return series is linked to this fund",
                rule="Annualised tracking difference of the matched-basis return, good within 0.50 percentage points of the charge",
                source="stored price series",
            )
        )
    else:
        er = D(f.get("expense_ratio"))  # a missing charge stays unknown, never 0
        gap = abs(tracking_difference) - er if er is not None else None
        status: Status = (
            "unknown"
            if gap is None
            else "good"
            if gap <= Decimal("0.005")
            else ("watch" if gap <= Decimal("0.015") else "concern")
        )
        crit.append(
            Criterion(
                id="tracking",
                label="Tracking evidence",
                status=status,
                observed=f"{tracking_difference:+.2%} a year against the benchmark"
                + ("" if er is not None else "; the expense ratio is not recorded, so no verdict"),
                rule="Annualised tracking difference within 0.50 percentage points of the charge is good, within 1.5 watch",
                source="stored price series",
            )
        )

    rep = facts.get("replication_method")
    if rep is None:
        crit.append(
            Criterion(
                id="replication",
                label="Replication method",
                status="unknown",
                observed="not recorded",
                rule="Physical full is good, physical sampled is info, synthetic is watch (counterparty risk)",
                critical=True,
            )
        )
    else:
        s: Status = (
            "good"
            if rep.value == "physical_full"
            else ("watch" if rep.value == "synthetic" else "info")
        )
        crit.append(
            Criterion(
                id="replication",
                label="Replication method",
                status=s,
                observed=rep.value.replace("_", " "),
                rule="Physical full is good, physical sampled is info, synthetic is watch (counterparty risk)",
                source="owner fact",
                source_url=rep.source_url,
                critical=True,
            )
        )

    lend = facts.get("securities_lending")
    share = facts.get("lending_revenue_share")
    if lend is None:
        crit.append(
            Criterion(
                id="lending",
                label="Securities lending",
                status="unknown",
                observed="not recorded",
                rule="No lending is good; lending is watch, with the share of revenue returned to the fund noted",
            )
        )
    else:
        answer = lending_answer(lend.value)
        crit.append(
            Criterion(
                id="lending",
                label="Securities lending",
                status="watch" if answer == "yes" else "good" if answer == "no" else "unknown",
                observed=lend.value
                + (f"; revenue share to the fund: {share.value}" if share else ""),
                rule="No lending is good; lending is watch, with the share of revenue returned to the fund noted",
                source="owner fact",
                source_url=lend.source_url,
            )
        )

    dom = facts.get("domicile")
    crit.append(
        Criterion(
            id="domicile",
            label="Domicile and distribution",
            status="info",
            observed=f"domicile {dom.value if dom else 'ISIN prefix ' + data.isin[:2] + ' (legal domicile not recorded)'}; {f.get('distribution_policy') or 'distribution policy unknown'}",
            rule="Shown for your own tax review; the app assesses no tax outcome",
            source="owner fact" if dom else "ISIN prefix",
            source_url=dom.source_url if dom else None,
        )
    )
    tax = facts.get("distribution_tax_note")
    if tax:
        crit.append(
            Criterion(
                id="tax",
                label="Tax note",
                status="info",
                observed=tax.value,
                rule="Owner-recorded, sourced",
                source="owner fact",
                source_url=tax.source_url,
            )
        )

    c = style.concentration
    if c is None or c.effective_issuers is None:
        crit.append(
            Criterion(
                id="diversification",
                label="Diversification",
                status="unknown",
                observed="no identified equity holdings",
                rule="Effective issuers at least 20 is good, 10 to 20 watch, below 10 concern",
                source="holdings snapshot",
                critical=True,
            )
        )
    else:
        n = c.effective_issuers
        s2: Status = "good" if n >= 20 else ("watch" if n >= 10 else "concern")
        crit.append(
            Criterion(
                id="diversification",
                label="Diversification",
                status=s2,
                observed=f"{n:.1f} effective issuers; top 10 hold {c.top10_weight:.1%} of net assets"
                if c.top10_weight is not None
                else f"{n:.1f} effective issuers",
                rule="Effective issuers at least 20 is good, 10 to 20 watch, below 10 concern",
                source="holdings snapshot",
                critical=True,
            )
        )

    assets = D(f.get("total_assets"))
    if assets is None:
        crit.append(
            Criterion(
                id="liquidity",
                label="Fund size and liquidity",
                status="unknown",
                observed="total assets not stored",
                rule="Net assets of at least 100m is good, 20m to 100m watch, below 20m concern (closure risk)",
                source=sheet_src,
            )
        )
    else:
        s3: Status = (
            "good" if assets >= 100_000_000 else ("watch" if assets >= 20_000_000 else "concern")
        )
        liq = facts.get("liquidity_note")
        crit.append(
            Criterion(
                id="liquidity",
                label="Fund size and liquidity",
                status=s3,
                observed=f"{assets:,.0f} {f.get('total_assets_currency') or ''}".strip()
                + (f"; {liq.value}" if liq else ""),
                rule="Net assets of at least 100m is good, 20m to 100m watch, below 20m concern (closure risk)",
                source=sheet_src,
                source_url=liq.source_url if liq else None,
            )
        )

    if holdings.as_of is None:
        crit.append(
            Criterion(
                id="freshness",
                label="Holdings freshness",
                status="unknown",
                observed="no holdings snapshot",
                rule="Holdings at most 45 days old is good, up to 120 watch, older concern",
                source="holdings snapshot",
                critical=True,
            )
        )
    else:
        age = (today - holdings.as_of).days
        s4: Status = "good" if age <= 45 else ("watch" if age <= 120 else "concern")
        crit.append(
            Criterion(
                id="freshness",
                label="Holdings freshness",
                status=s4,
                observed=f"{age} days old (as of {holdings.as_of})",
                rule="Holdings at most 45 days old is good, up to 120 watch, older concern",
                source="holdings snapshot",
                critical=True,
            )
        )

    if holdings.residual_weight is None:
        crit.append(
            Criterion(
                id="completeness",
                label="Look-through completeness",
                status="unknown",
                observed="no holdings snapshot",
                rule="Unaccounted weight at most 1% is good, up to 5% watch, above concern",
                source="holdings snapshot",
            )
        )
    else:
        r = holdings.residual_weight
        s5: Status = (
            "good" if r <= Decimal("0.01") else ("watch" if r <= Decimal("0.05") else "concern")
        )
        crit.append(
            Criterion(
                id="completeness",
                label="Look-through completeness",
                status=s5,
                observed=f"{r:.2%} of net assets not accounted for by the published rows",
                rule="Unaccounted weight at most 1% is good, up to 5% watch, above concern",
                source="holdings snapshot",
            )
        )

    missing = [c.label for c in crit if c.critical and c.status == "unknown"]
    if missing:
        summary_available, summary, why = (
            False,
            "",
            f"No summary: the inputs for {', '.join(missing)} are missing. A summary over incomplete critical inputs would hide the gap.",
        )
    else:
        counts = {k: sum(1 for c in crit if c.status == k) for k in ("good", "watch", "concern")}
        summary_available, summary, why = (
            True,
            f"{counts['good']} good, {counts['watch']} watch, {counts['concern']} concern. Each is shown with its rule; there is no score or rating.",
            None,
        )
    return Checklist(
        isin=data.isin,
        as_of=today,
        criteria=crit,
        summary_available=summary_available,
        summary=summary,
        abstain_reason=why,
    )
