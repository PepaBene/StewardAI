"""The company ratios page (plan M2.15): every ratio from the shared library, per fiscal year and
trailing twelve months, with the company's own medians, the peer median and percentile, and the
inputs behind the latest value.

Inputs are the cells of the standardized statements (`steward.company.statements`), so a ratio
reproduces by hand from the cells the financials page links to.
"""

# ruff: noqa: E501, RUF001
from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from statistics import median
from typing import Any

from pydantic import BaseModel, Field

from steward.analytics.contract import SourceRef, input_hash, rounded
from steward.analytics.ratio_set import (
    COMPANY_RATIOS,
    GROUP_LABEL,
    GROUP_ORDER,
    CompanyRatio,
    Period,
)
from steward.analytics.ratios import RatioValue
from steward.analytics.returns import to_major_unit
from steward.analytics.thresholds import THRESHOLDS, peer_median_allowed
from steward.analytics.versions import version
from steward.company.statements import Column, StatementsView

PLACES = {
    "fraction": "fraction",
    "multiple": "multiple",
    "days": "days",
    "years": "ratio",
    "money_per_share": "per_share",
}
FINANCIAL_WORDS = (
    "financial",
    "bank",
    "insurance",
    "insurer",
    "capital markets",
    "asset management",
    "credit services",
)


class RatioPoint(BaseModel):
    column: str
    label: str
    value: Decimal | None
    reason: str | None = None


class RatioInput(BaseModel):
    key: str
    label: str
    value: Decimal | None
    column: str
    concept: str | None = None
    accession: str | None = None
    filing_url: str | None = None
    derived: str | None = None


class PeerStat(BaseModel):
    scope: str
    count: int = Field(description="Comparable peers with a value.")
    members: int = Field(description="Peers considered.")
    median: Decimal | None
    low: Decimal | None
    high: Decimal | None
    percentile: Decimal | None = Field(description="Share of peers at or below the company, 0–1.")
    withheld_reason: str | None = None
    basis: str | None = Field(
        default=None, description="The period the company's figure is compared on."
    )


class RatioRow(BaseModel):
    id: str
    label: str
    group: str
    unit: str
    formula: str
    period_rule: str
    exclusions: str
    higher_is_better: bool | None
    points: list[RatioPoint]
    latest: RatioPoint | None
    own_median_5y: Decimal | None
    own_median_10y: Decimal | None
    own_median_note: str | None = None
    peer: PeerStat | None = None
    inputs: list[RatioInput] = Field(default_factory=list)


class RatioGroup(BaseModel):
    id: str
    label: str
    ratios: list[RatioRow]


class PeerExcluded(BaseModel):
    ticker: str
    reason: str


class RatiosView(BaseModel):
    ticker: str
    as_of: dt.date | None
    price: Decimal | None
    price_date: dt.date | None
    price_currency: str | None
    statement_currency: str | None
    sector: str | None
    industry: str | None
    is_financial: bool
    columns: list[Column]
    groups: list[RatioGroup]
    peer_scope: str | None
    peer_excluded: list[PeerExcluded]
    warnings: list[str]
    method_version: str
    input_hash: str
    sources: list[SourceRef]


@dataclass
class Market:
    """Price history in major units, plus the share count to use for a market cap."""

    closes: list[tuple[dt.date, Decimal]] = field(default_factory=list)
    currency: str | None = None
    shares: Decimal | None = None

    def price_on(self, day: dt.date) -> tuple[Decimal, dt.date] | None:
        best: tuple[dt.date, Decimal] | None = None
        for d, c in self.closes:
            if d <= day:
                best = (d, c)
            else:
                break
        if best is None or (day - best[0]).days > 10:
            return None
        return best[1], best[0]


def market_from(
    closes: Sequence[tuple[dt.date, Decimal]], currency: str | None, shares: Decimal | None
) -> Market:
    if not closes or not currency:
        return Market(shares=shares)
    major: list[tuple[dt.date, Decimal]] = []
    code = currency
    for d, c in closes:
        value, code = to_major_unit(c, currency)
        major.append((d, value))
    return Market(major, code, shares)


def is_financial_issuer(sector: str | None, industry: str | None) -> bool:
    text = f"{sector or ''} {industry or ''}".lower()
    return any(w in text for w in FINANCIAL_WORDS)


def figures_by_column(view: StatementsView) -> dict[str, dict[str, Decimal | None]]:
    out: dict[str, dict[str, Decimal | None]] = {c.id: {} for c in view.columns}
    for table in view.statements:
        for line in table.lines:
            for cid, cell in line.cells.items():
                if cell.unit in ("money", "per_share", "shares"):
                    out[cid][line.key] = cell.value
    for f in out.values():
        f["gross_profit_resolved"] = f.get("gross_profit")
        f["total_liabilities_resolved"] = f.get("total_liabilities")
    return out


def _near(columns: Sequence[Column], day: dt.date, days: int = 21) -> Column | None:
    hits = [c for c in columns if abs((c.end - day).days) <= days]
    return min(hits, key=lambda c: abs((c.end - day).days)) if hits else None


def build_periods(
    annual: StatementsView,
    quarterly: StatementsView | None,
    market: Market,
    *,
    financial: bool,
    forward_eps: Decimal | None = None,
    implied_growth: RatioValue | None = None,
) -> list[tuple[Column, Period]]:
    """One Period per fiscal year (newest last) and a TTM period when it is newer."""
    fig = figures_by_column(annual)
    cols = [c for c in annual.columns if c.kind == "annual"]
    out: list[tuple[Column, Period]] = []

    def period(
        col: Column,
        f: Mapping[str, Decimal | None],
        prior: Mapping[str, Decimal | None],
        back: Mapping[int, Mapping[str, Decimal | None]],
        *,
        current: bool,
    ) -> Period:
        shares = f.get("weighted_shares_diluted") or f.get("weighted_shares_basic")
        if current and market.shares:
            shares = market.shares
        hit = market.price_on(dt.date.today() if current else col.end)
        price, price_day = hit if hit else (None, None)
        cap: Decimal | None = None
        compatible = not (
            market.currency and annual.currency and market.currency != annual.currency
        )
        if price is not None and shares and compatible:
            cap = price * shares
        return Period(
            end=col.end,
            f=f,
            prior=prior,
            back=back,
            market_cap=cap,
            price_date=price_day,
            price=price if compatible else None,
            financial=financial,
            forward_eps=forward_eps if current else None,
            implied_growth=implied_growth if current else None,
        )

    for i, col in enumerate(cols):
        prior_col = _near(cols[:i], col.end - dt.timedelta(days=365))
        back = {}
        for years in (1, 3, 5):
            hit_col = _near(cols[:i], col.end - dt.timedelta(days=round(365.25 * years)))
            if hit_col is not None:
                back[years] = fig[hit_col.id]
        newest = i == len(cols) - 1
        out.append(
            (
                col,
                period(
                    col, fig[col.id], fig[prior_col.id] if prior_col else {}, back, current=newest
                ),
            )
        )
    if quarterly is not None:
        qfig = figures_by_column(quarterly)
        ttm = next((c for c in quarterly.columns if c.kind == "ttm"), None)
        last_fy = cols[-1].end if cols else None
        if ttm is not None and (last_fy is None or (ttm.end - last_fy).days > 21):
            qcols = [c for c in quarterly.columns if c.kind == "quarter"]
            prior_q = _near(qcols, ttm.end - dt.timedelta(days=365))
            back = {}
            for years in (1, 3, 5):
                hit_col = _near(cols, ttm.end - dt.timedelta(days=round(365.25 * years)), days=60)
                if hit_col is not None:
                    back[years] = fig[hit_col.id]
            out.append(
                (
                    ttm,
                    period(
                        ttm, qfig[ttm.id], qfig[prior_q.id] if prior_q else {}, back, current=True
                    ),
                )
            )
    return out


def evaluate(p: Period, ratio: CompanyRatio) -> RatioValue:
    try:
        return ratio.fn(p)
    except (
        ArithmeticError,
        ValueError,
    ) as exc:  # a malformed filed value must not take the page down
        return RatioValue(None, f"n/m: {type(exc).__name__}")


def _unit_of(ratio: CompanyRatio) -> str:
    return PLACES.get(ratio.unit, "ratio")


def _median(values: Sequence[Decimal]) -> Decimal | None:
    return Decimal(str(median(values))) if values else None


def percentile_of(value: Decimal, peers: Sequence[Decimal]) -> Decimal | None:
    if not peers:
        return None
    return Decimal(sum(1 for v in peers if v <= value)) / Decimal(len(peers))


def peer_stat(company: Decimal | None, peers: Mapping[str, Decimal | None], scope: str) -> PeerStat:
    known = [v for v in peers.values() if v is not None]
    ok, why = peer_median_allowed(len(known))
    ordered = sorted(known)
    return PeerStat(
        scope=scope,
        count=len(known),
        members=len(peers),
        median=rounded(_median(known), "ratio") if ok else None,
        low=ordered[0] if ordered and ok else None,
        high=ordered[-1] if ordered and ok else None,
        percentile=rounded(percentile_of(company, known), "ratio")
        if ok and company is not None
        else None,
        withheld_reason=why,
    )


def build_ratios(
    ticker: str,
    annual: StatementsView,
    quarterly: StatementsView | None,
    market: Market,
    *,
    sector: str | None = None,
    industry: str | None = None,
    peers: Mapping[str, Mapping[str, RatioValue]] | None = None,
    peer_scope: str | None = None,
    peer_excluded: Sequence[PeerExcluded] = (),
    forward_eps: Decimal | None = None,
    implied_growth: RatioValue | None = None,
) -> RatiosView:
    financial = is_financial_issuer(sector, industry)
    pairs = build_periods(
        annual,
        quarterly,
        market,
        financial=financial,
        forward_eps=forward_eps,
        implied_growth=implied_growth,
    )
    columns = [c for c, _ in pairs]
    warnings: list[str] = []
    if sector is None:
        warnings.append(
            "No provider sector is stored, so bank and insurer rules are not applied; check the issuer type before reading margins and leverage."
        )
    if financial:
        warnings.append(
            "Bank or insurer: margins, working-capital, EV-based and leverage ratios are n/m for this issuer type."
        )
    price_hit = market.price_on(dt.date.today())
    if price_hit is None:
        warnings.append(
            "No recent stored close: valuation ratios for the latest period are unavailable."
        )
    fig_latest = pairs[-1][1].f if pairs else {}
    latest_col = pairs[-1][0] if pairs else None
    cells = _cell_index(
        annual if latest_col is None or latest_col.kind == "annual" else (quarterly or annual)
    )
    rows_by_group: dict[str, list[RatioRow]] = {g: [] for g in GROUP_ORDER}
    for ratio in COMPANY_RATIOS.values():
        points: list[RatioPoint] = []
        for col, p in pairs:
            v = evaluate(p, ratio)
            points.append(
                RatioPoint(
                    column=col.id,
                    label=col.label,
                    value=rounded(v.value, _unit_of(ratio)),
                    reason=v.reason,
                )
            )
        annual_known = [
            pt.value
            for pt, (c, _) in zip(points, pairs, strict=True)
            if c.kind == "annual" and pt.value is not None
        ]
        m5 = _median(annual_known[-5:]) if len(annual_known) >= 3 else None
        m10 = _median(annual_known[-10:]) if len(annual_known) >= 5 else None
        latest = points[-1] if points else None
        peer = None
        if peers is not None and peer_scope is not None:
            # Peers are loaded on their last fiscal year, so a TTM company point is not compared
            # with them: the company's latest fiscal-year point is.
            ref, basis = latest, "latest fiscal year (company and peers)"
            if pairs and pairs[-1][0].kind != "annual":
                ref = next(
                    (
                        pt
                        for pt, (c, _) in zip(reversed(points), reversed(pairs), strict=True)
                        if c.kind == "annual"
                    ),
                    None,
                )
                basis = "company's latest fiscal year, not its trailing twelve months, to match the peers' fiscal year"
            peer = peer_stat(
                ref.value if ref else None,
                {t: (rv.value if (rv := r.get(ratio.id)) else None) for t, r in peers.items()},
                peer_scope,
            )
            peer.basis = basis
        inputs: list[RatioInput] = []
        if latest_col is not None:
            for key in ratio.inputs:
                cell = cells.get((key, latest_col.id))
                label = key.replace("_", " ").replace(" resolved", "")
                inputs.append(
                    RatioInput(
                        key=key,
                        label=label,
                        value=fig_latest.get(key),
                        column=latest_col.id,
                        concept=cell.get("concept") if cell else None,
                        accession=cell.get("accession") if cell else None,
                        filing_url=cell.get("filing_url") if cell else None,
                        derived=cell.get("derived") if cell else None,
                    )
                )
        rows_by_group[ratio.group].append(
            RatioRow(
                id=ratio.id,
                label=ratio.label,
                group=ratio.group,
                unit=ratio.unit,
                formula=ratio.formula,
                period_rule=ratio.period_rule,
                exclusions=ratio.exclusions,
                higher_is_better=ratio.higher_is_better,
                points=points,
                latest=latest,
                own_median_5y=rounded(m5, _unit_of(ratio)),
                own_median_10y=rounded(m10, _unit_of(ratio)),
                own_median_note=None
                if m5 is not None
                else "fewer than three fiscal years with a value",
                peer=peer,
                inputs=inputs,
            )
        )
    groups = [RatioGroup(id=g, label=GROUP_LABEL[g], ratios=rows_by_group[g]) for g in GROUP_ORDER]
    return RatiosView(
        ticker=ticker,
        as_of=latest_col.end if latest_col else None,
        price=price_hit[0] if price_hit else None,
        price_date=price_hit[1] if price_hit else None,
        price_currency=market.currency,
        statement_currency=annual.currency,
        sector=sector,
        industry=industry,
        is_financial=financial,
        columns=columns,
        groups=groups,
        peer_scope=peer_scope,
        peer_excluded=list(peer_excluded),
        warnings=warnings + [w for w in annual.warnings if "failed" in w],
        method_version=version("company_ratios"),
        input_hash=input_hash(
            {
                "statements": annual.input_hash,
                "ttm": quarterly.input_hash if quarterly else None,
                "price": price_hit,
            }
        ),
        sources=annual.sources,
    )


def _cell_index(view: StatementsView) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for table in view.statements:
        for line in table.lines:
            for cid, cell in line.cells.items():
                if cell.value is not None:
                    out[(line.key, cid)] = {
                        "concept": cell.concept,
                        "accession": cell.accession,
                        "filing_url": cell.filing_url,
                        "derived": cell.derived,
                    }
    return out


def peer_period_ok(
    company_end: dt.date | None, peer_end: dt.date | None
) -> tuple[bool, str | None]:
    """A peer whose latest fiscal year ended well before the company's is stale: comparing it
    would mix years. A peer that ended later is kept (its distance is still shown)."""
    if company_end is None or peer_end is None:
        return False, "no filed period"
    behind = (company_end - peer_end).days
    if behind > THRESHOLDS.peer_period_distance_days:
        return (
            False,
            f"latest fiscal year ended {peer_end}, {behind} days before the company's ({company_end})",
        )
    return True, None


def build_peer_values(
    peer_views: Mapping[str, tuple[StatementsView, Market, str | None, str | None]],
    company_end: dt.date | None,
) -> tuple[dict[str, dict[str, RatioValue]], list[PeerExcluded]]:
    """The latest fiscal year's ratios for each peer, with the peers left out and why."""
    values: dict[str, dict[str, RatioValue]] = {}
    excluded: list[PeerExcluded] = []
    for ticker, (view, market, sector, industry) in peer_views.items():
        pairs = build_periods(view, None, market, financial=is_financial_issuer(sector, industry))
        if not pairs:
            excluded.append(PeerExcluded(ticker=ticker, reason="no filed annual statements"))
            continue
        col, period = pairs[-1]
        ok, why = peer_period_ok(company_end, col.end)
        if not ok:
            excluded.append(PeerExcluded(ticker=ticker, reason=why or "stale period"))
            continue
        values[ticker] = {r.id: evaluate(period, r) for r in COMPANY_RATIOS.values()}
    return values, excluded
