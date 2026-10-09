"""Valuation workbench (plan M2.5): trailing and normalized multiples, peer and own-history
multiples, an explicit-year DCF with a stated discount-rate build-up, a reverse DCF, scenario
ranges and a sensitivity grid. No single opaque "true value": every method is shown with its
inputs, and a margin of safety exists only against the scenario the owner selected.
"""

# ruff: noqa: E501, RUF001
from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from decimal import Decimal
from statistics import median
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from steward.analytics.contract import AnalyticsResult, SourceRef, rounded
from steward.analytics.ratio_set import debt_of
from steward.analytics.ratios import RatioValue
from steward.analytics.valuation import (
    DcfInputs,
    dcf_per_share,
    margin_of_safety,
    reverse_dcf_growth,
)
from steward.analytics.versions import version
from steward.company.ratios import Market, RatiosView, figures_by_column
from steward.company.statements import StatementsView

D = Decimal
SCENARIO_NAMES = ("bear", "base", "bull")


class Assumptions(BaseModel):
    """One scenario's explicit inputs. Everything here is the owner's assumption, not a fact."""

    name: str = Field(min_length=1, max_length=80)
    years: int = Field(default=5, ge=3, le=10, description="Explicit forecast years.")
    base_fcf: Decimal | None = Field(
        default=None, description="Starting free cash flow; null uses the trailing twelve months."
    )
    growth: list[Decimal] = Field(
        description="Free-cash-flow growth for each explicit year, as fractions.",
        min_length=1,
        max_length=10,
    )
    terminal_growth: Decimal = Field(default=D("0.02"), ge=D("-0.05"), le=D("0.06"))
    risk_free: Decimal = Field(default=D("0.04"), ge=0, le=D("0.2"))
    equity_risk_premium: Decimal = Field(default=D("0.05"), ge=0, le=D("0.2"))
    beta: Decimal = Field(default=D(1), ge=0, le=D(3))
    extra_premium: Decimal = Field(
        default=D(0), ge=D("-0.05"), le=D("0.2"), description="Size, country or company-specific."
    )
    discount_rate: Decimal | None = Field(
        default=None, ge=D("0.01"), le=D("0.4"), description="Overrides the build-up."
    )
    net_debt: Decimal | None = Field(default=None, description="Overrides the filed debt − cash.")
    minorities: Decimal | None = Field(
        default=None, description="Overrides the filed non-controlling interest."
    )
    diluted_shares: Decimal | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _growth_length(self) -> Assumptions:
        if len(self.growth) not in (1, self.years):
            raise ValueError("growth has one value (constant) or one per explicit year")
        return self

    def rate(self) -> Decimal:
        if self.discount_rate is not None:
            return self.discount_rate
        return self.risk_free + self.beta * self.equity_risk_premium + self.extra_premium

    def path(self) -> list[Decimal]:
        return (
            list(self.growth) if len(self.growth) == self.years else [self.growth[0]] * self.years
        )


class ValuationRequest(BaseModel):
    scenarios: list[Assumptions] = Field(default_factory=list, max_length=5)
    selected: str | None = Field(
        default=None, description="Scenario the margin of safety is measured against."
    )


class FiledInput(BaseModel):
    key: str
    label: str
    value: Decimal | None
    detail: str | None = None
    accession: str | None = None
    filing_url: str | None = None


class ScenarioResult(BaseModel):
    name: str
    assumptions: Assumptions
    discount_rate: Decimal
    discount_rate_build: str
    forecast_fcf: list[Decimal]
    enterprise_value: Decimal | None
    value_per_share: AnalyticsResult
    margin_of_safety: Decimal | None = None
    selected: bool = False


class GridCell(BaseModel):
    growth_shift: Decimal
    rate_shift: Decimal
    value: Decimal | None


class ReferenceValue(BaseModel):
    method: str
    basis: str
    low: Decimal | None
    mid: Decimal | None
    high: Decimal | None
    reason: str | None = None


class CompanyValuation(BaseModel):
    ticker: str
    currency: str | None
    price: Decimal | None
    price_date: dt.date | None
    inputs: list[FiledInput]
    scenarios: list[ScenarioResult]
    range_low: Decimal | None
    range_high: Decimal | None
    sensitivity: list[GridCell]
    reverse_dcf: AnalyticsResult | None
    reverse_dcf_rate: Decimal | None
    reference: list[ReferenceValue]
    normalized: list[FiledInput]
    warnings: list[str]
    method_version: str
    sources: list[SourceRef]
    note: str = (
        "Scenarios are assumptions you set, not forecasts the app makes. The range spans your scenarios; "
        "no single value is offered, and a margin of safety is shown only against the scenario you select."
    )


def _latest(
    fig: Mapping[str, Mapping[str, Decimal | None]], cols: Sequence[str], key: str
) -> Decimal | None:
    for cid in reversed(cols):
        v = fig[cid].get(key)
        if v is not None:
            return v
    return None


def default_scenarios(annual: StatementsView) -> list[Assumptions]:
    """Starting values from the company's own filed growth, clearly the owner's to edit."""
    fig = figures_by_column(annual)
    cols = [c.id for c in annual.columns if c.kind == "annual"]
    growth = D("0.05")
    if len(cols) >= 4:
        first, last = fig[cols[0]].get("revenue"), fig[cols[-1]].get("revenue")
        n = len(cols) - 1
        if first and last and first > 0 and last > 0:
            growth = max(D(0), min(D("0.12"), (last / first) ** (D(1) / D(n)) - D(1)))
    base = Assumptions(name="base", growth=[growth.quantize(D("0.0001"))])
    bear = Assumptions(
        name="bear",
        growth=[max(D(0), growth - D("0.04")).quantize(D("0.0001"))],
        extra_premium=D("0.01"),
    )
    bull = Assumptions(name="bull", growth=[(growth + D("0.04")).quantize(D("0.0001"))])
    return [bear, base, bull]


NO_DEBT_LIMITATION = (
    "No financial debt concept is mapped for this company, so debt is taken as zero. Debt filed under "
    "an unmapped or extension concept is not counted, which overstates the per-share value; enter a "
    "net debt in your scenarios to override."
)


def _net_debt(
    f: Mapping[str, Decimal | None], as_of: dt.date | None = None
) -> tuple[Decimal | None, str]:
    at = f" (balance sheet {as_of})" if as_of else ""
    debt, cash = debt_of(f), f.get("cash")
    if cash is None:
        return None, "debt or cash not filed"
    if debt is None:
        # A filed balance sheet with no debt concept is a company without financial debt, or one
        # whose debt sits under a concept we do not map; the plan states which assumption was made.
        if f.get("total_assets") is None and f.get("equity") is None:
            return None, "debt or cash not filed"
        return (
            cash * -1,
            f"no debt filed; taken as zero, so net debt is minus cash; leases excluded{at}",
        )
    return debt - cash, f"filed financial debt − cash; leases excluded{at}"


def build_valuation(
    ticker: str,
    annual: StatementsView,
    quarterly: StatementsView | None,
    market: Market,
    ratios: RatiosView | None,
    request: ValuationRequest,
) -> CompanyValuation:
    method = version("valuation_scenarios")
    warnings: list[str] = []
    fig = figures_by_column(annual)
    acols = [c.id for c in annual.columns if c.kind == "annual"]
    latest_id = acols[-1] if acols else None
    f_latest = fig[latest_id] if latest_id else {}
    ttm = next((c for c in (quarterly.columns if quarterly else []) if c.kind == "ttm"), None)
    ttm_fig = figures_by_column(quarterly)[ttm.id] if quarterly and ttm else None
    last_annual_end = annual.columns[len(acols) - 1].end if acols else None
    newer_ttm = ttm is not None and (last_annual_end is None or ttm.end > last_annual_end)
    use = ttm_fig if ttm_fig is not None and newer_ttm else f_latest
    # Balance sheet at the newest filed date, flows trailing twelve months.
    bal_src = ttm_fig if ttm_fig is not None else f_latest
    cfo, capex = use.get("cash_from_operations"), use.get("capex")
    fcf = None if cfo is None or capex is None else cfo - abs(capex)
    bal_end = ttm.end if ttm_fig is not None and ttm is not None else last_annual_end
    net_debt_filed, nd_note = _net_debt(bal_src, bal_end)
    if net_debt_filed is not None and debt_of(bal_src) is None:
        warnings.append(NO_DEBT_LIMITATION)
    nci = bal_src.get("noncontrolling_interest")
    shares = market.shares or use.get("weighted_shares_diluted") or use.get("weighted_shares_basic")
    hit = market.price_on(dt.date.today())
    compatible = not (market.currency and annual.currency and market.currency != annual.currency)
    price, price_date = hit if hit and compatible else (None, None)
    if hit and not compatible:
        warnings.append(
            f"Price is in {market.currency} but statements are in {annual.currency}: per-share comparisons are withheld."
        )
    if nci is None:
        warnings.append(
            "No non-controlling interest is filed; the DCF treats it as none unless you enter one."
        )
    inputs = [
        FiledInput(
            key="fcf",
            label="Free cash flow, trailing twelve months",
            value=fcf,
            detail="operating cash flow − capital expenditure",
        ),
        FiledInput(key="net_debt", label="Net debt", value=net_debt_filed, detail=nd_note),
        FiledInput(
            key="minorities",
            label="Non-controlling interest",
            value=nci,
            detail=None if nci is not None else "not filed; assumed none",
        ),
        FiledInput(
            key="shares",
            label="Diluted shares",
            value=shares,
            detail="latest stored count" if market.shares else "weighted diluted, latest filing",
        ),
        FiledInput(
            key="price", label=f"Price ({price_date})" if price_date else "Price", value=price
        ),
    ]
    scenarios_in = request.scenarios or default_scenarios(annual)
    selected = request.selected
    results: list[ScenarioResult] = []
    for a in scenarios_in:
        results.append(_scenario(a, fcf, net_debt_filed, nci, shares, price, method))
    if selected is None and any(r.name == "base" for r in results):
        selected = None  # no default selection: the margin of safety is the owner's choice
    for r in results:
        r.selected = r.name == selected
        if r.selected:
            r.margin_of_safety = rounded(
                margin_of_safety(r.value_per_share.value, price), "fraction"
            )
    values = [r.value_per_share.value for r in results if r.value_per_share.value is not None]
    # With nothing selected the grid and reverse DCF read the base case, not the first (bear) one.
    sel = next(
        (r for r in results if r.selected),
        next((r for r in results if r.name == "base"), results[0] if results else None),
    )
    grid: list[GridCell] = []
    if sel is not None:
        grid = _grid(sel.assumptions, fcf, net_debt_filed, nci, shares)
    rev = None
    implied_rate = None
    if sel is not None and price is not None:
        a = sel.assumptions
        implied_rate = a.rate()
        rev = reverse_dcf_growth(
            price=price,
            base_fcf=a.base_fcf if a.base_fcf is not None else fcf,
            years=a.years,
            discount_rate=implied_rate,
            terminal_growth=a.terminal_growth,
            net_debt=a.net_debt if a.net_debt is not None else net_debt_filed,
            minorities=a.minorities
            if a.minorities is not None
            else (nci if nci is not None else D(0)),
            diluted_shares=a.diluted_shares or shares,
        )
    reference = _reference(ratios, use, net_debt_filed, nci, shares)
    normalized = _normalized(annual, market, price, shares)
    return CompanyValuation(
        ticker=ticker,
        currency=annual.currency,
        price=price,
        price_date=price_date,
        inputs=inputs,
        scenarios=results,
        range_low=min(values) if values else None,
        range_high=max(values) if values else None,
        sensitivity=grid,
        reverse_dcf=rev,
        reverse_dcf_rate=implied_rate,
        reference=reference,
        normalized=normalized,
        warnings=warnings,
        method_version=method,
        sources=annual.sources,
    )


def _flows(a: Assumptions, fcf: Decimal | None) -> list[Decimal]:
    base = a.base_fcf if a.base_fcf is not None else fcf
    if base is None:
        return []
    out: list[Decimal] = []
    cur = base
    for g in a.path():
        cur = cur * (D(1) + g)
        out.append(cur)
    return out


def _dcf(
    a: Assumptions,
    fcf: Decimal | None,
    net_debt: Decimal | None,
    nci: Decimal | None,
    shares: Decimal | None,
    *,
    rate: Decimal | None = None,
    shift: Decimal | None = None,
) -> tuple[AnalyticsResult, list[Decimal]]:
    base = a.base_fcf if a.base_fcf is not None else fcf
    flows = _flows(a.model_copy(update={"growth": [g + (shift or D(0)) for g in a.path()]}), base)
    inputs = DcfInputs(
        forecast_fcf=flows,
        discount_rate=rate if rate is not None else a.rate(),
        terminal_growth=a.terminal_growth,
        net_debt=a.net_debt if a.net_debt is not None else net_debt,
        minorities=a.minorities if a.minorities is not None else (nci if nci is not None else D(0)),
        diluted_shares=a.diluted_shares or shares,
    )
    if base is None:
        from steward.analytics.contract import unavailable

        return unavailable(
            "per_share",
            version("dcf"),
            "starting free cash flow is not available (operating cash flow or capital expenditure not filed)",
            inputs=inputs,
        ), []
    if base <= 0:
        from steward.analytics.contract import unavailable

        return unavailable(
            "per_share",
            version("dcf"),
            "n/m: starting free cash flow is not positive; enter a base figure to model a turnaround",
            inputs=inputs,
        ), flows
    return dcf_per_share(inputs), flows


def _scenario(
    a: Assumptions,
    fcf: Decimal | None,
    net_debt: Decimal | None,
    nci: Decimal | None,
    shares: Decimal | None,
    price: Decimal | None,
    method: str,
) -> ScenarioResult:
    res, flows = _dcf(a, fcf, net_debt, nci, shares)
    rate = a.rate()
    build = (
        f"override {rate:.2%}"
        if a.discount_rate is not None
        else f"risk-free {a.risk_free:.2%} + beta {a.beta} × premium {a.equity_risk_premium:.2%}"
        + (f" + extra {a.extra_premium:.2%}" if a.extra_premium else "")
        + f" = {rate:.2%}"
    )
    ev = None
    if flows:
        from steward.analytics.valuation import enterprise_value

        ev = enterprise_value(flows, rate, a.terminal_growth)
    return ScenarioResult(
        name=a.name,
        assumptions=a,
        discount_rate=rate,
        discount_rate_build=build,
        forecast_fcf=[rounded(f, "money") or D(0) for f in flows],
        enterprise_value=rounded(ev, "money"),
        value_per_share=res,
    )


def _grid(
    a: Assumptions,
    fcf: Decimal | None,
    net_debt: Decimal | None,
    nci: Decimal | None,
    shares: Decimal | None,
) -> list[GridCell]:
    cells: list[GridCell] = []
    for gs in (D("-0.04"), D("-0.02"), D(0), D("0.02"), D("0.04")):
        for rs in (D("-0.02"), D("-0.01"), D(0), D("0.01"), D("0.02")):
            res, _ = _dcf(a, fcf, net_debt, nci, shares, rate=a.rate() + rs, shift=gs)
            cells.append(GridCell(growth_shift=gs, rate_shift=rs, value=res.value))
    return cells


def _reference(
    ratios: RatiosView | None,
    f: Mapping[str, Decimal | None],
    net_debt: Decimal | None,
    nci: Decimal | None,
    shares: Decimal | None,
) -> list[ReferenceValue]:
    """Implied equity value per share from a multiple applied to the company's own figure:
    its five-year median multiple, and the peer median with the peer range."""
    out: list[ReferenceValue] = []
    if ratios is None or not shares or shares <= 0:
        return out
    rows = {r.id: r for g in ratios.groups for r in g.ratios}
    ni, cfo, capex = f.get("net_income"), f.get("cash_from_operations"), f.get("capex")
    fcf = None if cfo is None or capex is None else cfo - abs(capex)
    ebit = f.get("operating_income")
    ebitda_da = f.get("depreciation_amortization")
    ebitda = None if ebit is None or ebitda_da is None else ebit + abs(ebitda_da)

    def equity_from_ev(ev: Decimal | None) -> Decimal | None:
        if ev is None or net_debt is None:
            return None
        return (ev - net_debt - (nci or D(0))) / shares

    spec: list[tuple[str, str, Decimal | None, Literal["equity", "ev"]]] = [
        ("pe", "P/E × net income", ni, "equity"),
        ("p_fcf", "P/FCF × free cash flow", fcf, "equity"),
        ("ev_to_ebit", "EV/EBIT × operating income", ebit, "ev"),
        ("ev_to_ebitda", "EV/EBITDA × EBITDA", ebitda, "ev"),
    ]
    for rid, label, figure, kind in spec:
        row = rows.get(rid)
        if row is None:
            continue

        def apply(
            multiple: Decimal | None, figure: Decimal | None = figure, kind: str = kind
        ) -> Decimal | None:
            if multiple is None or figure is None or figure <= 0:
                return None
            return (
                multiple * figure / shares
                if kind == "equity"
                else equity_from_ev(multiple * figure)
            )

        own = apply(row.own_median_5y)
        out.append(
            ReferenceValue(
                method=f"Own 5-year median {row.label}",
                basis=label,
                low=None,
                mid=rounded(own, "per_share"),
                high=None,
                reason=None
                if own is not None
                else "needs a 5-year median multiple and a positive figure",
            )
        )
        peer = row.peer
        if peer is not None:
            mid = apply(peer.median)
            out.append(
                ReferenceValue(
                    method=f"Peer median {row.label} ({peer.scope}, {peer.count} peers)",
                    basis=label,
                    low=rounded(apply(peer.low), "per_share"),
                    mid=rounded(mid, "per_share"),
                    high=rounded(apply(peer.high), "per_share"),
                    reason=None
                    if mid is not None
                    else (peer.withheld_reason or "needs a positive figure"),
                )
            )
    return out


def _normalized(
    annual: StatementsView, market: Market, price: Decimal | None, shares: Decimal | None
) -> list[FiledInput]:
    fig = figures_by_column(annual)
    cols = [c.id for c in annual.columns if c.kind == "annual"][-5:]
    out: list[FiledInput] = []
    for key, label in (("net_income", "Median net income"), ("fcf", "Median free cash flow")):
        vals: list[Decimal] = []
        for cid in cols:
            f = fig[cid]
            cfo, capex = f.get("cash_from_operations"), f.get("capex")
            if key != "fcf":
                v = f.get(key)
            else:
                v = None if cfo is None or capex is None else cfo - abs(capex)
            if v is not None:
                vals.append(v)
        if len(vals) < 3:
            out.append(
                FiledInput(
                    key=f"norm_{key}",
                    label=label,
                    value=None,
                    detail=f"{len(vals)} fiscal years filed; needs three",
                )
            )
            continue
        med = D(str(median(vals)))
        out.append(
            FiledInput(
                key=f"norm_{key}", label=f"{label}, last {len(vals)} fiscal years", value=med
            )
        )
        if price is not None and shares and med > 0:
            out.append(
                FiledInput(
                    key=f"norm_{key}_yield",
                    label=f"Yield on {label.lower()}",
                    value=rounded(med / (price * shares), "fraction"),
                    detail="median ÷ (price × shares)",
                )
            )
    return out


def implied_growth_value(v: CompanyValuation) -> RatioValue:
    r = v.reverse_dcf
    if r is None or r.value is None:
        return RatioValue(
            None, (r.warnings[0] if r and r.warnings else "no price or selected scenario")
        )
    return RatioValue(r.value)
