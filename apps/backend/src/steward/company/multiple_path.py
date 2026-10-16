# ruff: noqa: E501, RUF001, RUF002
"""Earnings-multiple scenario method (insight plan I6, method `multiple_path`).

A second valuation method beside the explicit-year DCF, saved in the same scenario table with
`method = 'multiple_path'`. For each scenario:

    revenue_t   = revenue_{t-1} × (1 + growth_t)              t = 1..N, revenue_0 = TTM or last fiscal year
    earnings_t  = revenue_t × net_margin_t
    shares_t    = shares_{t-1} × (1 + share_change_t)
    eps_N       = earnings_N / shares_N
    price_N     = eps_N × exit P/E                            (market cap at exit = earnings_N × exit P/E)
    value_today = price_N / (1 + r)^N                          r = the DCF discount-rate build-up (or override)

Interim dividends are not added (the value is the discounted exit price only, stated as such).
Every intermediate is returned as an ordered calculation chain (label, expression, value, unit),
printed exactly as computed, so the screen and the AI layer quote the same steps.

The reverse solve finds the exit P/E, or the constant revenue growth, that makes the value equal
today's price with the other inputs unchanged. Staleness: a saved scenario is stale after the
configured days, and drifts when the last four quarters' revenue growth or net margin departs
from the assumed first-year path by more than the configured band.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from decimal import Decimal
from statistics import median

from pydantic import BaseModel, Field, model_validator

from steward.analytics.contract import AnalyticsResult, input_hash, result, rounded, unavailable
from steward.analytics.versions import version
from steward.company.ratios import Market, RatiosView, figures_by_column
from steward.company.statements import StatementsView

D = Decimal
METHOD = "multiple_path"
SCENARIO_NAMES = ("bear", "base", "bull")


class MultipleAssumptions(BaseModel):
    """One scenario's inputs. Every one is the owner's assumption, shown beside the company's own
    history and the peer value."""

    name: str = Field(min_length=1, max_length=80)
    years: int = Field(default=5, ge=3, le=10, description="Horizon in years.")
    base_revenue: Decimal | None = Field(
        default=None, gt=0, description="Starting revenue; null uses TTM or the last fiscal year."
    )
    revenue_growth: list[Decimal] = Field(
        min_length=1,
        max_length=10,
        description="Revenue growth per year (fractions); one value is constant.",
    )
    net_margin: list[Decimal] = Field(
        min_length=1,
        max_length=10,
        description="Net margin per year (fractions); one value is constant.",
    )
    exit_pe: Decimal = Field(gt=0, le=200, description="P/E at the end of the horizon.")
    share_change: list[Decimal] = Field(
        default_factory=lambda: [D(0)],
        min_length=1,
        max_length=10,
        description="Diluted share count change per year; negative is a buyback.",
    )
    diluted_shares: Decimal | None = Field(default=None, gt=0)
    risk_free: Decimal = Field(default=D("0.04"), ge=0, le=D("0.2"))
    equity_risk_premium: Decimal = Field(default=D("0.05"), ge=0, le=D("0.2"))
    beta: Decimal = Field(default=D(1), ge=0, le=D(3))
    extra_premium: Decimal = Field(default=D(0), ge=D("-0.05"), le=D("0.2"))
    discount_rate: Decimal | None = Field(default=None, ge=D("0.01"), le=D("0.4"))

    @model_validator(mode="after")
    def _lengths(self) -> MultipleAssumptions:
        for label, values in (
            ("revenue_growth", self.revenue_growth),
            ("net_margin", self.net_margin),
            ("share_change", self.share_change),
        ):
            if len(values) not in (1, self.years):
                raise ValueError(f"{label} has one value (constant) or one per year")
        if any(m <= D(-1) or m >= D(1) for m in self.net_margin):
            raise ValueError("net margin must be between -100 % and 100 %")
        if any(g <= D(-1) for g in self.revenue_growth):
            raise ValueError("revenue growth must be above -100 %")
        return self

    def path(self, values: list[Decimal]) -> list[Decimal]:
        return list(values) if len(values) == self.years else [values[0]] * self.years

    def rate(self) -> Decimal:
        if self.discount_rate is not None:
            return self.discount_rate
        return self.risk_free + self.beta * self.equity_risk_premium + self.extra_premium

    def rate_build(self) -> str:
        r = self.rate()
        if self.discount_rate is not None:
            return f"override {r:.2%}"
        extra = f" + extra {self.extra_premium:.2%}" if self.extra_premium else ""
        return f"risk-free {self.risk_free:.2%} + beta {self.beta} × premium {self.equity_risk_premium:.2%}{extra} = {r:.2%}"


class MultipleRequest(BaseModel):
    scenarios: list[MultipleAssumptions] = Field(default_factory=list, max_length=5)
    selected: str | None = None


class CalcStep(BaseModel):
    label: str
    expression: str
    value: Decimal | None
    unit: str


class YearRow(BaseModel):
    year: int
    revenue: Decimal
    growth: Decimal
    net_margin: Decimal
    earnings: Decimal
    shares: Decimal
    eps: Decimal


class MultipleScenarioResult(BaseModel):
    name: str
    assumptions: MultipleAssumptions
    discount_rate: Decimal
    discount_rate_build: str
    years: list[YearRow]
    exit_market_cap: Decimal | None
    exit_price: Decimal | None
    value_per_share: AnalyticsResult
    chain: list[CalcStep]
    margin_of_safety: Decimal | None = None
    upside: Decimal | None = None
    selected: bool = False


class Seed(BaseModel):
    """What the defaults were seeded from, shown beside each input."""

    key: str
    label: str
    company: Decimal | None
    company_basis: str | None
    peer: Decimal | None
    peer_basis: str | None


class ReverseSolve(BaseModel):
    exit_pe: Decimal | None
    exit_pe_reason: str | None
    growth: Decimal | None
    growth_reason: str | None
    scenario: str | None


class FiledYear(BaseModel):
    year: int
    end: dt.date
    revenue: Decimal | None
    net_income: Decimal | None
    net_margin: Decimal | None
    eps: Decimal | None


class MultipleValuation(BaseModel):
    ticker: str
    currency: str | None
    price: Decimal | None
    price_date: dt.date | None
    base_revenue: Decimal | None
    base_revenue_basis: str
    shares: Decimal | None
    seeds: list[Seed]
    history: list[FiledYear]
    scenarios: list[MultipleScenarioResult]
    range_low: Decimal | None
    range_high: Decimal | None
    reverse: ReverseSolve
    warnings: list[str]
    method: str = METHOD
    method_version: str
    note: str = (
        "Each scenario's value is its exit price (forecast earnings × the exit P/E ÷ shares) discounted "
        "to today. Interim dividends are not added. These are your assumptions, not forecasts the app makes."
    )


# ------------------------------------------------------------------------------- inputs


def _annual_cols(annual: StatementsView) -> list[str]:
    return [c.id for c in annual.columns if c.kind == "annual"]


def history(annual: StatementsView) -> list[FiledYear]:
    fig = figures_by_column(annual)
    out: list[FiledYear] = []
    for c in annual.columns:
        if c.kind != "annual":
            continue
        f = fig[c.id]
        rev, ni = f.get("revenue"), f.get("net_income")
        out.append(FiledYear(
            year=c.end.year, end=c.end, revenue=rev, net_income=ni,
            net_margin=rounded(ni / rev, "fraction") if rev and ni is not None and rev > 0 else None,
            eps=f.get("eps_diluted"),
        ))  # fmt: skip
    return out


def starting_revenue(
    annual: StatementsView, quarterly: StatementsView | None
) -> tuple[Decimal | None, str]:
    ttm = next((c for c in (quarterly.columns if quarterly else []) if c.kind == "ttm"), None)
    acols = _annual_cols(annual)
    last_end = annual.columns[len(acols) - 1].end if acols else None
    if quarterly is not None and ttm is not None and (last_end is None or ttm.end > last_end):
        v = figures_by_column(quarterly)[ttm.id].get("revenue")
        if v is not None and v > 0:
            return v, f"trailing twelve months to {ttm.end.isoformat()}"
    if acols:
        v = figures_by_column(annual)[acols[-1]].get("revenue")
        if v is not None and v > 0:
            return v, f"fiscal year to {last_end.isoformat() if last_end else ''}"
    return None, "revenue is not filed"


def _ratio_row(
    ratios: RatiosView | None, rid: str
) -> tuple[Decimal | None, Decimal | None, Decimal | None, str | None]:
    """(latest, own 5y median, peer median, peer basis) of a ratio."""
    if ratios is None:
        return None, None, None, None
    for g in ratios.groups:
        for r in g.ratios:
            if r.id == rid:
                peer = r.peer.median if r.peer is not None else None
                basis = (
                    f"{r.peer.scope}, {r.peer.count} peers"
                    if r.peer is not None and peer is not None
                    else (r.peer.withheld_reason if r.peer else None)
                )
                return (r.latest.value if r.latest else None), r.own_median_5y, peer, basis
    return None, None, None, None


def seeds(annual: StatementsView, ratios: RatiosView | None) -> list[Seed]:
    hist = [h for h in history(annual) if h.revenue]
    growth = None
    gbasis = None
    if len(hist) >= 2:
        first, last = hist[max(0, len(hist) - 6)], hist[-1]
        n = last.year - first.year
        if n > 0 and first.revenue and last.revenue and first.revenue > 0:
            growth = D(str(float(last.revenue / first.revenue) ** (1 / n) - 1))
            gbasis = f"{n}-year revenue CAGR {first.year}–{last.year}"
    margins = [h.net_margin for h in hist[-5:] if h.net_margin is not None]
    margin = D(str(median(margins))) if margins else None
    pe_latest, pe_own, pe_peer, pe_basis = _ratio_row(ratios, "pe")
    dil, _, _, _ = _ratio_row(ratios, "dilution_1y")
    rg_peer = _ratio_row(ratios, "revenue_cagr_5y")[2]
    nm_peer = _ratio_row(ratios, "net_margin")[2]
    return [
        Seed(key="revenue_growth", label="Revenue growth a year", company=rounded(growth, "fraction"), company_basis=gbasis,
             peer=rg_peer, peer_basis="peer median 5-year revenue CAGR" if rg_peer is not None else None),
        Seed(key="net_margin", label="Net margin", company=rounded(margin, "fraction"),
             company_basis=f"median of the last {len(margins)} fiscal years" if margins else None,
             peer=nm_peer, peer_basis="peer median net margin" if nm_peer is not None else None),
        Seed(key="exit_pe", label="Exit P/E", company=pe_own if pe_own is not None else pe_latest,
             company_basis="own 5-year median P/E" if pe_own is not None else ("current P/E" if pe_latest is not None else None),
             peer=pe_peer, peer_basis=pe_basis),
        Seed(key="share_change", label="Share count change a year", company=dil,
             company_basis="last year's diluted share change" if dil is not None else None, peer=None, peer_basis=None),
    ]  # fmt: skip


def default_scenarios(
    annual: StatementsView, ratios: RatiosView | None
) -> list[MultipleAssumptions]:
    """Starting values from the company's own history and peers, clearly the owner's to edit."""
    s = {x.key: x for x in seeds(annual, ratios)}
    g = s["revenue_growth"].company
    g = D("0.05") if g is None else max(D("-0.05"), min(D("0.15"), g))
    m = s["net_margin"].company or s["net_margin"].peer or D("0.10")
    m = max(D("0.01"), min(D("0.6"), m))
    pe = s["exit_pe"].peer or s["exit_pe"].company or D(15)
    pe = max(D(5), min(D(40), pe))
    sc = s["share_change"].company or D(0)
    sc = max(D("-0.05"), min(D("0.05"), sc))
    q = D("0.0001")

    def mk(name: str, dg: Decimal, mm: Decimal, pm: Decimal, extra: Decimal) -> MultipleAssumptions:
        return MultipleAssumptions(
            name=name, revenue_growth=[max(D("-0.5"), g + dg).quantize(q)], net_margin=[(m * mm).quantize(q)],
            exit_pe=(pe * pm).quantize(D("0.1")), share_change=[sc.quantize(q)], extra_premium=extra,
        )  # fmt: skip

    return [mk("bear", D("-0.04"), D("0.8"), D("0.8"), D("0.01")), mk("base", D(0), D(1), D(1), D(0)),
            mk("bull", D("0.04"), D("1.1"), D("1.2"), D(0))]  # fmt: skip


# ------------------------------------------------------------------------------- model


def project(a: MultipleAssumptions, revenue0: Decimal, shares0: Decimal) -> list[YearRow]:
    rows: list[YearRow] = []
    rev, sh = revenue0, shares0
    for t, (g, m, s) in enumerate(
        zip(a.path(a.revenue_growth), a.path(a.net_margin), a.path(a.share_change), strict=True),
        start=1,
    ):
        rev = rev * (D(1) + g)
        sh = sh * (D(1) + s)
        earn = rev * m
        rows.append(
            YearRow(
                year=t, revenue=rev, growth=g, net_margin=m, earnings=earn, shares=sh, eps=earn / sh
            )
        )
    return rows


def value_of(a: MultipleAssumptions, revenue0: Decimal, shares0: Decimal) -> Decimal:
    rows = project(a, revenue0, shares0)
    last = rows[-1]
    return last.eps * a.exit_pe / (D(1) + a.rate()) ** a.years


def chain(a: MultipleAssumptions, revenue0: Decimal, rev_basis: str, shares0: Decimal, rows: Sequence[YearRow],
          price: Decimal | None, currency: str | None) -> list[CalcStep]:  # fmt: skip
    ccy = currency or ""
    last = rows[-1]
    r = a.rate()
    exit_cap = last.earnings * a.exit_pe
    exit_price = exit_cap / last.shares
    factor = (D(1) + r) ** a.years
    value = exit_price / factor
    steps = [
        CalcStep(
            label="Starting revenue",
            expression=rev_basis,
            value=rounded(revenue0, "money"),
            unit=ccy,
        ),
        CalcStep(
            label="Starting diluted shares",
            expression="latest stored count",
            value=rounded(shares0, "count"),
            unit="shares",
        ),
    ]
    prev = revenue0
    for row in rows:
        steps.append(
            CalcStep(
                label=f"Revenue year {row.year}",
                expression=f"{rounded(prev, 'money')} × (1 + {row.growth:.2%})",
                value=rounded(row.revenue, "money"),
                unit=ccy,
            )
        )
        prev = row.revenue
    steps += [
        CalcStep(
            label=f"Earnings year {a.years}",
            expression=f"{rounded(last.revenue, 'money')} × net margin {last.net_margin:.2%}",
            value=rounded(last.earnings, "money"),
            unit=ccy,
        ),
        CalcStep(
            label=f"Diluted shares year {a.years}",
            expression=f"{rounded(shares0, 'count')} × compounded share change",
            value=rounded(last.shares, "count"),
            unit="shares",
        ),
        CalcStep(
            label=f"EPS year {a.years}",
            expression=f"{rounded(last.earnings, 'money')} ÷ {rounded(last.shares, 'count')}",
            value=rounded(last.eps, "per_share"),
            unit=ccy,
        ),
        CalcStep(
            label="Market cap at exit",
            expression=f"{rounded(last.earnings, 'money')} × exit P/E {a.exit_pe}",
            value=rounded(exit_cap, "money"),
            unit=ccy,
        ),
        CalcStep(
            label="Price at exit",
            expression=f"{rounded(exit_cap, 'money')} ÷ {rounded(last.shares, 'count')}",
            value=rounded(exit_price, "per_share"),
            unit=ccy,
        ),
        CalcStep(
            label="Discount rate",
            expression=a.rate_build(),
            value=rounded(r, "fraction"),
            unit="fraction",
        ),
        CalcStep(
            label="Value per share today",
            expression=f"{rounded(exit_price, 'per_share')} ÷ (1 + {r:.2%})^{a.years}",
            value=rounded(value, "per_share"),
            unit=ccy,
        ),
    ]
    if price is not None and price > 0:
        steps.append(
            CalcStep(
                label="Upside to today's price",
                expression=f"{rounded(value, 'per_share')} ÷ {price} − 1",
                value=rounded(value / price - 1, "fraction"),
                unit="fraction",
            )
        )
    return steps


def solve_exit_pe(
    a: MultipleAssumptions, revenue0: Decimal, shares0: Decimal, price: Decimal
) -> tuple[Decimal | None, str | None]:
    last = project(a, revenue0, shares0)[-1]
    if last.eps <= 0:
        return None, "forecast earnings are not positive: no P/E justifies the price"
    pe = price * (D(1) + a.rate()) ** a.years / last.eps
    return pe, None


def solve_growth(
    a: MultipleAssumptions, revenue0: Decimal, shares0: Decimal, price: Decimal
) -> tuple[Decimal | None, str | None]:
    """Constant revenue growth that gives today's price (bisection on [-50 %, 100 %])."""
    lo, hi = D("-0.5"), D("1.0")

    def f(g: Decimal) -> Decimal:
        return value_of(a.model_copy(update={"revenue_growth": [g]}), revenue0, shares0) - price

    flo, fhi = f(lo), f(hi)
    if flo > 0:
        return (
            None,
            "even revenue falling 50 % a year gives more than the price (check the margin and exit P/E)",
        )
    if fhi < 0:
        return None, "even revenue doubling every year gives less than the price"
    for _ in range(80):
        mid = (lo + hi) / 2
        if f(mid) > 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2, None


def _shares(market: Market, annual: StatementsView) -> Decimal | None:
    if market.shares:
        return market.shares
    fig = figures_by_column(annual)
    for cid in reversed(_annual_cols(annual)):
        v = fig[cid].get("weighted_shares_diluted") or fig[cid].get("weighted_shares_basic")
        if v:
            return v
    return None


def build(
    ticker: str,
    annual: StatementsView,
    quarterly: StatementsView | None,
    market: Market,
    ratios: RatiosView | None,
    request: MultipleRequest,
) -> MultipleValuation:
    method = version(METHOD)
    warnings: list[str] = []
    rev0, rev_basis = starting_revenue(annual, quarterly)
    shares = _shares(market, annual)
    hit = market.price_on(dt.date.today())
    compatible = not (market.currency and annual.currency and market.currency != annual.currency)
    price, price_date = hit if hit and compatible else (None, None)
    if hit and not compatible:
        warnings.append(
            f"Price is in {market.currency} but statements are in {annual.currency}: the comparison with the price and the reverse solve are withheld."
        )
    scen = request.scenarios or default_scenarios(annual, ratios)
    results: list[MultipleScenarioResult] = []
    for a in scen:
        start = a.base_revenue or rev0
        sh = a.diluted_shares or shares
        inputs = {
            "a": a.model_dump(mode="json"),
            "rev0": start,
            "shares": sh,
            "statements": annual.input_hash,
        }
        if start is None or sh is None:
            reason = (
                "starting revenue is not filed" if start is None else "no share count is stored"
            )
            results.append(MultipleScenarioResult(
                name=a.name, assumptions=a, discount_rate=a.rate(), discount_rate_build=a.rate_build(), years=[],
                exit_market_cap=None, exit_price=None, value_per_share=unavailable("per_share", method, reason, inputs=inputs), chain=[],
            ))  # fmt: skip
            continue
        rows = project(a, start, sh)
        last = rows[-1]
        exit_cap = last.earnings * a.exit_pe
        exit_price = exit_cap / last.shares
        if last.earnings <= 0:
            vps = unavailable(
                "per_share",
                method,
                "n/m: forecast earnings at the exit year are not positive",
                inputs=inputs,
            )
        else:
            vps = result(exit_price / (D(1) + a.rate()) ** a.years, "per_share", method, as_of=dt.date.today(),
                         sources=annual.sources, inputs=inputs)  # fmt: skip
        results.append(MultipleScenarioResult(
            name=a.name, assumptions=a, discount_rate=a.rate(), discount_rate_build=a.rate_build(),
            years=[YearRow(**{k: (rounded(v, "money") if k in ("revenue", "earnings") else rounded(v, "per_share") if k == "eps" else v)
                              for k, v in r.model_dump().items()}) for r in rows],
            exit_market_cap=rounded(exit_cap, "money"), exit_price=rounded(exit_price, "per_share"), value_per_share=vps,
            chain=chain(a, start, "your base revenue" if a.base_revenue else rev_basis, sh, rows, price, annual.currency),
            upside=rounded(vps.value / price - 1, "fraction") if vps.value is not None and price else None,
        ))  # fmt: skip
    for r in results:
        r.selected = r.name == request.selected
        if r.selected and r.value_per_share.value is not None and price:
            r.margin_of_safety = rounded(
                (r.value_per_share.value - price) / r.value_per_share.value, "fraction"
            )
    values = [r.value_per_share.value for r in results if r.value_per_share.value is not None]
    sel = next(
        (r for r in results if r.selected),
        next((r for r in results if r.name == "base"), results[0] if results else None),
    )
    reverse = ReverseSolve(
        exit_pe=None,
        exit_pe_reason="no price or scenario",
        growth=None,
        growth_reason="no price or scenario",
        scenario=None,
    )
    if sel is not None and price is not None and rev0 is not None and shares is not None:
        a = sel.assumptions
        pe, pe_reason = solve_exit_pe(a, a.base_revenue or rev0, a.diluted_shares or shares, price)
        g, g_reason = solve_growth(a, a.base_revenue or rev0, a.diluted_shares or shares, price)
        reverse = ReverseSolve(
            exit_pe=rounded(pe, "multiple"),
            exit_pe_reason=pe_reason,
            growth=rounded(g, "fraction"),
            growth_reason=g_reason,
            scenario=sel.name,
        )
    return MultipleValuation(
        ticker=ticker, currency=annual.currency, price=price, price_date=price_date, base_revenue=rounded(rev0, "money"),
        base_revenue_basis=rev_basis, shares=shares, seeds=seeds(annual, ratios), history=history(annual), scenarios=results,
        range_low=min(values) if values else None, range_high=max(values) if values else None, reverse=reverse,
        warnings=warnings, method_version=method,
    )  # fmt: skip


# --------------------------------------------------------------------------- staleness


class Drift(BaseModel):
    stale: bool
    drifting: bool
    reasons: list[str]
    age_days: int | None
    ttm_revenue_growth: Decimal | None
    ttm_net_margin: Decimal | None


def _ttm_pairs(quarterly: StatementsView | None) -> tuple[Decimal | None, Decimal | None]:
    """(revenue growth of the last four quarters against the four before, net margin of the last four)."""
    if quarterly is None:
        return None, None
    fig = figures_by_column(quarterly)
    q = [c for c in quarterly.columns if c.kind == "quarter"]
    if len(q) < 4:
        return None, None
    last4 = q[-4:]
    rev = [fig[c.id].get("revenue") for c in last4]
    ni = [fig[c.id].get("net_income") for c in last4]
    margin = None
    if all(x is not None for x in rev) and all(x is not None for x in ni):
        total_rev = sum((x for x in rev if x is not None), D(0))
        if total_rev > 0:
            margin = sum((x for x in ni if x is not None), D(0)) / total_rev
    growth = None
    if len(q) >= 8:
        prev = [fig[c.id].get("revenue") for c in q[-8:-4]]
        if all(x is not None for x in rev + prev):
            a = sum((x for x in prev if x is not None), D(0))
            b = sum((x for x in rev if x is not None), D(0))
            if a > 0:
                growth = b / a - 1
    return growth, margin


def drift(
    assumptions: Sequence[MultipleAssumptions],
    selected: str | None,
    saved_at: dt.datetime | None,
    quarterly: StatementsView | None,
    *,
    stale_after_days: int,
    band: Decimal,
    today: dt.date,
) -> Drift:
    reasons: list[str] = []
    age = (today - saved_at.date()).days if saved_at else None
    stale = age is not None and age > stale_after_days
    if stale:
        reasons.append(f"inputs saved {age} days ago (stale after {stale_after_days})")
    a = next(
        (x for x in assumptions if x.name == selected),
        next((x for x in assumptions if x.name == "base"), assumptions[0] if assumptions else None),
    )
    g, m = _ttm_pairs(quarterly)
    drifting = False
    if a is not None:
        if g is not None and abs(g - a.path(a.revenue_growth)[0]) > band:
            drifting = True
            reasons.append(
                f"last four quarters' revenue growth {g:.1%} vs assumed {a.path(a.revenue_growth)[0]:.1%}"
            )
        if m is not None and abs(m - a.path(a.net_margin)[0]) > band:
            drifting = True
            reasons.append(
                f"last four quarters' net margin {m:.1%} vs assumed {a.path(a.net_margin)[0]:.1%}"
            )
    return Drift(stale=stale, drifting=drifting, reasons=reasons, age_days=age,
                 ttm_revenue_growth=rounded(g, "fraction"), ttm_net_margin=rounded(m, "fraction"))  # fmt: skip


def request_hash(request: MultipleRequest, statements_hash: str) -> str:
    return input_hash({"statements": statements_hash, "request": request.model_dump(mode="json")})


def saved_payload(request: MultipleRequest) -> Mapping[str, object]:
    return {
        "scenarios": [s.model_dump(mode="json") for s in request.scenarios],
        "selected": request.selected,
    }
