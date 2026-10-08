"""Pre-trade check for strategy principles 8, 9 and 10 (plan M6.7).

Run on any proposed buy, add or sale, and on a switch (a sale that funds a buy). The result is a
pass/flag list, each line citing the principle it tests and the figures behind it. It never
blocks and never places a trade; a check whose input is not stored says `unavailable` and names
what is missing instead of passing.

Principles used (the owner's strategy notes):

- 8a: a stock is bought or added to only with a thesis, an expected return of at least the hurdle
  (default 30 % to the thesis sell target) and a base-case upside of at least twice the bear-case
  loss. The thesis stores no bear case yet, so the asymmetry gate is `unavailable`.
- 8b: a new stock starts at no more than 5 % (core), 3 % (growth) or 1.5 % (speculative).
- 9f: a switch needs the expected-return gap to exceed the tax on the exact lots sold, plus costs,
  plus a safety margin of 5 points, and the replacement is sized to equal risk.
- 10b: a lot within 6 months of passing the holding-period test should usually wait, and the
  year's gross proceeds from lots still inside the test are counted against the exemption limit.
- The owner's own caps, tier limits and core-share rules are evaluated on the post-trade
  portfolio with the same code that raises them after a sync.

Expected return is `sell target / price - 1`, over the thesis horizon, as the thesis stores it.
The risk-adjusted score is `(annualised expected return - risk-free rate) / volatility`, a stage A
figure on one year of the holding's own price history.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from steward.analytics.versions import version
from steward.config.strategy import StrategyConfig
from steward.decision import covariance
from steward.decision.covariance import CovModel
from steward.decision.lot_impact import SaleImpact
from steward.portfolio.breaches import evaluate
from steward.portfolio.drift import DriftReport

Status = Literal["pass", "flag", "unavailable", "info"]


class PretradeRules(BaseModel):
    """The thresholds of principles 8, 9 and 10. Defaults are the strategy's stated values; each
    can be changed for one check."""

    return_hurdle: float = Field(default=0.30, ge=0, le=5)
    asymmetry_gate: float = Field(default=2.0, ge=0, le=20)
    start_size_core: float = Field(default=0.05, ge=0, le=1)
    start_size_growth: float = Field(default=0.03, ge=0, le=1)
    start_size_speculative: float = Field(default=0.015, ge=0, le=1)
    switch_safety_margin: float = Field(default=0.05, ge=0, le=1)
    short_term_tax_rate: float = Field(default=0.15, ge=0, le=1)
    trade_cost_bps: float = Field(default=0, ge=0, le=500)
    fx_cost_bps: float = Field(default=15, ge=0, le=500)
    horizon_years: float | None = Field(
        default=None, gt=0, le=30, description="Defaults to the strategy's horizon, else 3 years."
    )
    risk_contribution_cap: float | None = Field(
        default=None,
        gt=0,
        le=1,
        description="Largest share of the portfolio's volatility one stock may account for. "
        "Unset: the share is shown but not judged, because the strategy states no number.",
    )

    def start_cap(self, tier: str | None) -> float | None:
        return {
            "core": self.start_size_core,
            "growth": self.start_size_growth,
            "speculative": self.start_size_speculative,
        }.get((tier or "").lower())


class TradeLeg(BaseModel):
    side: Literal["buy", "sell"]
    ticker: str = Field(min_length=1, max_length=32)
    value_base: float = Field(gt=0, description="Size of the trade in the base currency.")
    bear_case_price: float | None = Field(
        default=None,
        gt=0,
        description="Buys only: the price you expect in the bear case, in the quote currency. "
        "A thesis stores no bear case, so the asymmetry gate needs it here.",
    )


class PretradeCheck(BaseModel):
    id: str
    principle: str
    ticker: str | None
    status: Status
    title: str
    detail: str
    values: dict[str, float | str | None] = Field(default_factory=dict)


class PretradeResult(BaseModel):
    method_version: str
    trades: list[TradeLeg]
    is_switch: bool
    checks: list[PretradeCheck]
    passed: int
    flagged: int
    unavailable: int
    notes: list[str]
    disclaimer: str


DISCLAIMER = (
    "A pass/flag list against your own stated principles. It never blocks a trade, places one, "
    "or states a tax liability; the holding-period facts and the exemption headroom are your "
    "thresholds applied to the stored lots."
)


@dataclass
class TickerFacts:
    ticker: str
    name: str | None = None
    held: bool = False
    tier: str | None = None
    value_now: float = 0.0
    price: float | None = None  # in the quote currency
    quote_currency: str | None = None
    sell_target: float | None = None
    bear_case: float | None = None  # the thesis's bear-case price, in the quote currency
    target_currency: str | None = None
    has_thesis: bool = False
    thesis_gaps: list[str] = field(default_factory=list)  # requirements the thesis does not meet
    volatility: float | None = None  # annualised, 1 year of the holding's own price history
    currency: str | None = None  # instrument currency, for the cost estimate


@dataclass
class PretradeContext:
    strategy: StrategyConfig
    rules: PretradeRules
    base_currency: str
    total_value: float
    cash_base: float
    facts: dict[str, TickerFacts]
    drift_before: DriftReport
    drift_after: DriftReport
    sale_impacts: dict[str, SaleImpact] = field(default_factory=dict)
    risk_free: float | None = None  # annual, fraction
    exemption_headroom_after: float | None = None  # in the exemption currency
    exemption_currency: str | None = None
    exemption_limit: float | None = None
    year_proceeds_so_far: float | None = None
    sale_within_test_exemption_ccy: dict[str, float | None] = field(default_factory=dict)
    warn_months: float = 6.0
    extra_checks: list[PretradeCheck] = field(default_factory=list)
    horizon_years: float = 3.0
    notes: list[str] = field(default_factory=list)
    cov: CovModel | None = None
    weights_before: dict[str, float] = field(default_factory=dict)  # of total value, by ticker
    weights_after: dict[str, float] = field(default_factory=dict)


def expected_return(f: TickerFacts) -> tuple[float | None, str | None]:
    """(return to the sell target, reason it is unavailable)."""
    if not f.has_thesis:
        return None, "no thesis is stored for this ticker"
    if f.sell_target is None:
        return None, "the thesis has no sell target"
    if f.price is None or f.price <= 0:
        return None, "no current price"
    if f.target_currency and f.quote_currency and f.target_currency != f.quote_currency:
        return None, f"the target is in {f.target_currency} but the price is in {f.quote_currency}"
    return f.sell_target / f.price - 1, None


def _pct(x: float) -> str:
    return f"{x:.1%}"


def run(trades: list[TradeLeg], ctx: PretradeContext) -> PretradeResult:
    checks: list[PretradeCheck] = []
    sells = [t for t in trades if t.side == "sell"]
    buys = [t for t in trades if t.side == "buy"]
    for t in buys:
        checks += _buy_checks(t, ctx)
    for t in sells:
        checks += _sell_checks(t, ctx)
    is_switch = len(sells) == 1 and len(buys) == 1
    if is_switch:
        checks += _switch_checks(sells[0], buys[0], ctx)
    checks += _portfolio_checks(ctx)
    checks += ctx.extra_checks
    return PretradeResult(
        method_version=version("pretrade"),
        trades=trades,
        is_switch=is_switch,
        checks=checks,
        passed=sum(c.status == "pass" for c in checks),
        flagged=sum(c.status == "flag" for c in checks),
        unavailable=sum(c.status == "unavailable" for c in checks),
        notes=ctx.notes,
        disclaimer=DISCLAIMER,
    )


# ---------------------------------------------------------------------------------------- buys


def _buy_checks(t: TradeLeg, ctx: PretradeContext) -> list[PretradeCheck]:
    f = ctx.facts.get(t.ticker, TickerFacts(t.ticker))
    rules = ctx.rules
    out: list[PretradeCheck] = []
    er, why = expected_return(f)
    label = f.name or t.ticker
    if f.tier == "core" and not f.has_thesis:
        out.append(
            PretradeCheck(
                id="buy.thesis",
                principle="8a",
                ticker=t.ticker,
                status="info",
                title="No thesis stored",
                detail=f"{label} has no stored thesis; a core holding may be bought on the core "
                "ETF rule, which has no return hurdle.",
            )
        )
    elif not f.has_thesis:
        out.append(
            PretradeCheck(
                id="buy.thesis",
                principle="8a",
                ticker=t.ticker,
                status="flag",
                title="No thesis",
                detail=f"{label} has no stored thesis. A stock is bought only on a written, "
                "intact thesis; the core ETFs are the exception.",
            )
        )
    elif f.thesis_gaps and f.tier != "core":
        out.append(
            PretradeCheck(
                id="buy.thesis",
                principle="8a",
                ticker=t.ticker,
                status="flag",
                title="Thesis incomplete",
                detail=f"The thesis for {label} lacks: {'; '.join(f.thesis_gaps)}. "
                "A stock is bought on a thesis that states what the price implies, how it "
                "could fail and what you know that the market does not.",
            )
        )
    else:
        out.append(
            PretradeCheck(
                id="buy.thesis",
                principle="8a",
                ticker=t.ticker,
                status="info",
                title="Thesis stored",
                detail="Whether it is intact is for the last review to confirm: this check "
                "cannot read it.",
            )
        )
    if er is None:
        out.append(
            PretradeCheck(
                id="buy.hurdle",
                principle="8a",
                ticker=t.ticker,
                status="unavailable",
                title="Return hurdle not checked",
                detail=why or "no expected return",
            )
        )
    else:
        ok = er >= rules.return_hurdle
        out.append(
            PretradeCheck(
                id="buy.hurdle",
                principle="8a",
                ticker=t.ticker,
                status="pass" if ok else "flag",
                title=f"Expected return {_pct(er)} against a {_pct(rules.return_hurdle)} hurdle",
                detail=(
                    f"Price {f.price:,.2f} to the thesis sell target {f.sell_target:,.2f} "
                    f"{f.target_currency or f.quote_currency or ''} is {_pct(er)} "
                    f"over the thesis horizon."
                ),
                values={"expected_return": er, "hurdle": rules.return_hurdle},
            )
        )
    out.append(_asymmetry(t, f, er, rules))
    after = (f.value_now + t.value_base) / ctx.total_value if ctx.total_value > 0 else 0.0
    cap = rules.start_cap(f.tier)
    if not f.held:
        if cap is None:
            out.append(
                PretradeCheck(
                    id="buy.start_size",
                    principle="8b",
                    ticker=t.ticker,
                    status="flag" if f.tier is None else "unavailable",
                    title="No risk tier to size against",
                    detail="The starting size depends on the tier (core, growth, speculative). "
                    "Classify the stock in the strategy's risk dimension.",
                    values={"weight_after": after},
                )
            )
        else:
            out.append(
                PretradeCheck(
                    id="buy.start_size",
                    principle="8b",
                    ticker=t.ticker,
                    status="pass" if after <= cap + 1e-9 else "flag",
                    title=f"Starting size {_pct(after)} against {_pct(cap)} ({f.tier})",
                    detail=f"A new {f.tier} stock starts at no more than {_pct(cap)} of the "
                    "portfolio.",
                    values={"weight_after": after, "cap": cap},
                )
            )
    else:
        out.append(
            PretradeCheck(
                id="buy.add",
                principle="8b",
                ticker=t.ticker,
                status="info",
                title=f"An add: {_pct(f.value_now / ctx.total_value)} to {_pct(after)}",
                detail="The starting-size ceiling applies to a new position; an add is held to "
                "your caps and tier limits below.",
                values={"weight_after": after},
            )
        )
    out.append(_risk_budget(t, ctx))
    return out


def _asymmetry(
    t: TradeLeg, f: TickerFacts, er: float | None, rules: PretradeRules
) -> PretradeCheck:
    base = dict(id="buy.asymmetry", principle="8a", ticker=t.ticker)
    bear = t.bear_case_price if t.bear_case_price is not None else f.bear_case
    if bear is None:
        return PretradeCheck(
            **base,
            status="unavailable",
            title="Upside against loss not checked",
            detail=f"The gate needs a base-case upside of at least {rules.asymmetry_gate:g} times "
            "the bear-case loss. The thesis has no bear-case price: set one in the thesis, or "
            "enter it on the buy to run the check.",
        )
    if er is None or f.price is None:
        return PretradeCheck(
            **base,
            status="unavailable",
            title="Upside against loss not checked",
            detail="The upside to the sell target is not available, so there is nothing to "
            "compare with the bear case.",
        )
    loss = 1 - bear / f.price
    if loss <= 0:
        return PretradeCheck(
            **base,
            status="pass",
            title="Bear case is not below the price",
            detail=f"Your bear-case price {bear:,.2f} is at or above {f.price:,.2f}, "
            "so there is no loss to weigh the upside against.",
            values={"upside": er, "bear_loss": loss},
        )
    ratio = er / loss
    return PretradeCheck(
        **base,
        status="pass" if ratio >= rules.asymmetry_gate else "flag",
        title=f"Upside {_pct(er)} against bear-case loss {_pct(loss)}: {ratio:.1f} times",
        detail=f"The gate wants at least {rules.asymmetry_gate:g} times. The bear case is your "
        f"own price of {bear:,.2f}.",
        values={"upside": er, "bear_loss": loss, "ratio": ratio, "gate": rules.asymmetry_gate},
    )


def _risk_budget(t: TradeLeg, ctx: PretradeContext) -> PretradeCheck:
    base = dict(id="buy.risk_budget", principle="4e", ticker=t.ticker)
    model = ctx.cov
    if model is None or model.index(t.ticker) is None:
        return PretradeCheck(
            **base,
            status="unavailable",
            title="Risk budget not checked",
            detail="The covariance model has too little shared price history for this stock "
            "(it needs about 120 common trading days with the rest of the portfolio).",
        )
    before = covariance.contribution(model, ctx.weights_before, t.ticker) or 0.0
    after = covariance.contribution(model, ctx.weights_after, t.ticker)
    if after is None:
        return PretradeCheck(
            **base, status="unavailable", title="Risk budget not checked",
            detail="The portfolio after the trade has no variance in the model.",
        )  # fmt: skip
    cap = ctx.rules.risk_contribution_cap
    vol_after = covariance.volatility(model, ctx.weights_after)
    detail = (
        f"After the trade this stock accounts for {_pct(after)} of the portfolio's volatility "
        f"(before: {_pct(before)}); portfolio volatility is {_pct(vol_after)} a year on "
        f"{model.observations} shared trading days."
    )
    values: dict[str, float | str | None] = {
        "contribution_before": before, "contribution_after": after, "volatility_after": vol_after,
    }  # fmt: skip
    if cap is None:
        return PretradeCheck(
            **base, status="info", title=f"Risk contribution {_pct(after)}",
            detail=detail + " Your strategy sets no cap on this, so it is not judged.",
            values=values,
        )  # fmt: skip
    return PretradeCheck(
        **base,
        status="pass" if after <= cap + 1e-9 else "flag",
        title=f"Risk contribution {_pct(after)} against a {_pct(cap)} cap",
        detail=detail,
        values={**values, "cap": cap},
    )


# --------------------------------------------------------------------------------------- sells


def _sell_checks(t: TradeLeg, ctx: PretradeContext) -> list[PretradeCheck]:
    f = ctx.facts.get(t.ticker, TickerFacts(t.ticker))
    impact = ctx.sale_impacts.get(t.ticker)
    out: list[PretradeCheck] = []
    label = f.name or t.ticker
    if not f.held:
        return [
            PretradeCheck(
                id="sell.not_held",
                principle="10b",
                ticker=t.ticker,
                status="flag",
                title=f"{label} is not held",
                detail="There is nothing to sell.",
            )
        ]
    if t.value_base > f.value_now * (1 + 1e-9):
        out.append(
            PretradeCheck(
                id="sell.too_large",
                principle="10b",
                ticker=t.ticker,
                status="flag",
                title="Sale larger than the position",
                detail=f"{t.value_base:,.0f} {ctx.base_currency} exceeds the position of "
                f"{f.value_now:,.0f}.",
            )
        )
    if impact is None:
        out.append(
            PretradeCheck(
                id="sell.lots",
                principle="10b",
                ticker=t.ticker,
                status="unavailable",
                title="Tax lots not read",
                detail="The ledger holds no open lots for this ticker, so the lots this sale "
                "would consume and its time-test dates are unknown.",
            )
        )
        return out
    out.append(
        PretradeCheck(
            id="sell.lots",
            principle="10b",
            ticker=t.ticker,
            status="info",
            title=f"Consumes {len(impact.lots)} lot(s), gain {impact.gain:,.0f}",
            detail=impact.method_note
            + (
                f" {impact.units_unmatched:g} unit(s) exceed the stored lots and have no cost."
                if impact.units_unmatched > 1e-9
                else ""
            ),
            values={"gain": impact.gain, "gross_proceeds": impact.gross_proceeds},
        )
    )
    if impact.units_unmatched > 1e-9:
        out.append(
            PretradeCheck(
                id="sell.unmatched",
                principle="10b",
                ticker=t.ticker,
                status="flag",
                title="Part of the sale has no stored lot",
                detail="History before the first stored fill, or a corporate action not entered, "
                "leaves some units without a cost or a buy date.",
            )
        )
    if impact.near_test_lots:
        when = impact.earliest_pass.isoformat() if impact.earliest_pass else "soon"
        out.append(
            PretradeCheck(
                id="sell.time_test",
                principle="10b",
                ticker=t.ticker,
                status="flag",
                title=f"{impact.near_test_lots} lot(s) pass the time test within "
                f"{ctx.warn_months:g} months",
                detail=f"Selling now uses lots that pass the holding-period test on {when} at "
                "the earliest; waiting may keep the exemption. A sale is still your call.",
                values={"earliest_pass": when},
            )
        )
    else:
        waiting = sum(1 for lot in impact.lots if not lot.passed)
        out.append(
            PretradeCheck(
                id="sell.time_test",
                principle="10b",
                ticker=t.ticker,
                status="pass",
                title="No lot is close to the time test",
                detail=(
                    "Every lot consumed has passed the test."
                    if waiting == 0
                    else f"{waiting} lot(s) are still inside the test but more than "
                    f"{ctx.warn_months:g} months from passing."
                ),
            )
        )
    within = ctx.sale_within_test_exemption_ccy.get(t.ticker)
    if (
        ctx.exemption_limit is None
        or ctx.year_proceeds_so_far is None
        or within is None
        or ctx.exemption_currency is None
    ):
        out.append(
            PretradeCheck(
                id="sell.exemption",
                principle="10b",
                ticker=t.ticker,
                status="unavailable",
                title="Exemption headroom not computed",
                detail="No stored exchange rate to the exemption currency, or no exemption limit.",
            )
        )
    else:
        headroom = ctx.exemption_limit - ctx.year_proceeds_so_far - within
        out.append(
            PretradeCheck(
                id="sell.exemption",
                principle="10b",
                ticker=t.ticker,
                status="pass" if headroom >= 0 else "flag",
                title=f"Exemption headroom after the sale: {headroom:,.0f} "
                f"{ctx.exemption_currency}",
                detail=f"This year's gross proceeds from lots inside the test so far: "
                f"{ctx.year_proceeds_so_far:,.0f}; this sale adds {within:,.0f}; the limit is "
                f"{ctx.exemption_limit:,.0f} {ctx.exemption_currency}.",
                values={"headroom": headroom, "this_sale": within},
            )
        )
    return out


# ------------------------------------------------------------------------------------ switches


def _horizon_years(ctx: PretradeContext) -> float:
    return ctx.rules.horizon_years or ctx.horizon_years


def _annualised(er: float, years: float) -> float:
    return (1 + er) ** (1 / years) - 1 if er > -1 else -1.0


def _switch_checks(sell: TradeLeg, buy: TradeLeg, ctx: PretradeContext) -> list[PretradeCheck]:
    rules = ctx.rules
    fa = ctx.facts.get(sell.ticker, TickerFacts(sell.ticker))
    fb = ctx.facts.get(buy.ticker, TickerFacts(buy.ticker))
    out: list[PretradeCheck] = []
    er_a, why_a = expected_return(fa)
    er_b, why_b = expected_return(fb)
    impact = ctx.sale_impacts.get(sell.ticker)

    if er_a is None or er_b is None:
        missing = "; ".join(
            f"{x.name or x.ticker}: {why}"
            for x, why in ((fa, why_a), (fb, why_b))
            if why is not None
        )
        out.append(
            PretradeCheck(
                id="switch.gap",
                principle="9f",
                ticker=None,
                status="unavailable",
                title="Expected-return gap not computed",
                detail=missing,
            )
        )
    else:
        quoted_a = fa.currency or ctx.base_currency
        quoted_b = fb.currency or ctx.base_currency
        fx_a = rules.fx_cost_bps if quoted_a != ctx.base_currency else 0.0
        fx_b = rules.fx_cost_bps if quoted_b != ctx.base_currency else 0.0
        costs = (
            sell.value_base * (fx_a + rules.trade_cost_bps)
            + buy.value_base * (fx_b + rules.trade_cost_bps)
        ) / 10_000
        tax = _tax_estimate(sell, impact, ctx)
        tax_fraction = (tax or 0.0) / sell.value_base
        hurdle = tax_fraction + costs / sell.value_base + rules.switch_safety_margin
        gap = er_b - er_a
        tax_note = (
            "tax on the lots sold is not estimated (no lots or no exchange rate)"
            if tax is None
            else f"estimated tax {tax:,.0f} {ctx.base_currency}"
        )
        out.append(
            PretradeCheck(
                id="switch.gap",
                principle="9f",
                ticker=None,
                status="pass" if gap > hurdle else "flag",
                title=f"Gap {_pct(gap)} against a switching hurdle of {_pct(hurdle)}",
                detail=f"Expected return {_pct(er_b)} ({fb.name or fb.ticker}) minus {_pct(er_a)} "
                f"({fa.name or fa.ticker}). Hurdle: {tax_note}, costs {costs:,.0f} "
                f"{ctx.base_currency} and a safety margin of {_pct(rules.switch_safety_margin)}.",
                values={"gap": gap, "hurdle": hurdle, "tax": tax, "costs": costs},
            )
        )
        if tax and impact and impact.near_test_lots:
            out.append(
                PretradeCheck(
                    id="switch.wait",
                    principle="9f",
                    ticker=None,
                    status="info",
                    title="Switch now or after the time test",
                    detail=f"Switching now carries about {tax:,.0f} {ctx.base_currency} of tax on "
                    f"lots still inside the test. The first lot passes on "
                    f"{impact.earliest_pass}; after that the same sale carries none of it.",
                    values={"tax_now": tax},
                )
            )

    vol_a, vol_b = fa.volatility, fb.volatility
    rf = ctx.risk_free
    years = _horizon_years(ctx)
    if None in (er_a, er_b, vol_a, vol_b, rf) or not vol_a or not vol_b:
        gaps = []
        if er_a is None or er_b is None:
            gaps.append("an expected return")
        if not vol_a or not vol_b:
            gaps.append("a one-year volatility for both")
        if rf is None:
            gaps.append("a risk-free rate")
        out.append(
            PretradeCheck(
                id="switch.ranking",
                principle="7c",
                ticker=None,
                status="unavailable",
                title="Risk-adjusted ranking not computed",
                detail="Missing: " + ", ".join(gaps) + ".",
            )
        )
    else:
        assert er_a is not None and er_b is not None and rf is not None
        score_a = (_annualised(er_a, years) - rf) / vol_a
        score_b = (_annualised(er_b, years) - rf) / vol_b
        out.append(
            PretradeCheck(
                id="switch.ranking",
                principle="7c",
                ticker=None,
                status="pass" if score_b > score_a else "flag",
                title=f"Risk-adjusted score {score_b:.2f} for the purchase against {score_a:.2f}",
                detail=f"(Annualised expected return over {years:g} years minus the risk-free rate "
                f"of {_pct(rf)}) divided by one-year volatility: {fb.name or fb.ticker} "
                f"{_pct(vol_b)}, {fa.name or fa.ticker} {_pct(vol_a)}.",
                values={"score_buy": score_b, "score_sell": score_a},
            )
        )
        equal_risk = sell.value_base * vol_a / vol_b
        out.append(
            PretradeCheck(
                id="switch.size",
                principle="9f",
                ticker=buy.ticker,
                status="pass" if buy.value_base <= equal_risk * 1.05 else "flag",
                title=f"Equal-risk size {equal_risk:,.0f} {ctx.base_currency}",
                detail=f"Replacing {sell.value_base:,.0f} of a {_pct(vol_a)}-volatility stock "
                f"with one at {_pct(vol_b)} carries the same risk at about {equal_risk:,.0f}; "
                "the rest goes to the next bench idea or the core ETFs.",
                values={"equal_risk_value": equal_risk, "proposed": buy.value_base},
            )
        )
    out.append(_switch_portfolio(sell, buy, ctx))
    return out


def _switch_portfolio(sell: TradeLeg, buy: TradeLeg, ctx: PretradeContext) -> PretradeCheck:
    """Does the switch lower portfolio volatility, and does the bought stock expect more than the
    sold one? Only the two legs have expected returns (from theses), so the rest of the
    portfolio's return is the same before and after and cancels in the comparison."""
    base = dict(id="switch.portfolio", principle="9f", ticker=None)
    model = ctx.cov
    if model is None:
        return PretradeCheck(
            **base, status="unavailable",
            title="Portfolio risk-adjusted improvement not checked",
            detail="The covariance model could not be built from the stored price history.",
        )  # fmt: skip
    vol_before = covariance.volatility(model, ctx.weights_before)
    vol_after = covariance.volatility(model, ctx.weights_after)
    er_sell, why_sell = expected_return(ctx.facts.get(sell.ticker, TickerFacts(sell.ticker)))
    er_buy, why_buy = expected_return(ctx.facts.get(buy.ticker, TickerFacts(buy.ticker)))
    values: dict[str, float | str | None] = {"volatility_before": vol_before,
                                             "volatility_after": vol_after}  # fmt: skip
    risk_text = f"Portfolio volatility goes from {_pct(vol_before)} to {_pct(vol_after)} a year."
    if er_sell is None or er_buy is None:
        return PretradeCheck(
            **base, status="info", title="Switch changes risk; expected return unknown",
            detail=f"{risk_text} The expected-return comparison needs a sell target on both "
            f"stocks ({why_sell or why_buy}).",
            values=values,
        )  # fmt: skip
    gap = er_buy - er_sell
    values["return_gap"] = gap
    lower = vol_after <= vol_before + 1e-9
    if gap > 0 and lower:
        status: Status = "pass"
        title = "Switch raises expected return and does not raise risk"
    elif gap <= 0 and not lower:
        status = "flag"
        title = "Switch lowers expected return and raises risk"
    else:
        status = "info"
        title = "Switch trades expected return against risk"
    return PretradeCheck(
        **base, status=status, title=title,
        detail=f"{risk_text} Expected return to the thesis targets: {_pct(er_buy)} for "
        f"{buy.ticker} against {_pct(er_sell)} for {sell.ticker} (gap {gap:+.1%}). Caps and "
        "tier limits are checked below.",
        values=values,
    )  # fmt: skip


def _tax_estimate(sell: TradeLeg, impact: SaleImpact | None, ctx: PretradeContext) -> float | None:
    """Estimated tax in the base currency: the short-term rate on the gain of lots still inside
    the test, applied only when the year's proceeds would exceed the exemption."""
    if impact is None or ctx.exemption_limit is None or ctx.year_proceeds_so_far is None:
        return None
    within = ctx.sale_within_test_exemption_ccy.get(sell.ticker)
    if within is None or impact.gross_proceeds <= 0:
        return None
    if ctx.year_proceeds_so_far + within <= ctx.exemption_limit:
        return 0.0
    gain_inside = sum(max(0.0, lot.gain) for lot in impact.lots if not lot.passed)
    share = sell.value_base / impact.gross_proceeds if impact.gross_proceeds else 0.0
    return gain_inside * share * ctx.rules.short_term_tax_rate


# ------------------------------------------------------------------------- portfolio-level


def _portfolio_checks(ctx: PretradeContext) -> list[PretradeCheck]:
    """Post-trade caps, tiers and core share: the strategy's own rules, evaluated on the
    portfolio after the trade with the code that raises them after a sync."""
    before = {f.key: f for f in evaluate(ctx.drift_before, ctx.strategy)}
    after = {f.key: f for f in evaluate(ctx.drift_after, ctx.strategy)}
    out: list[PretradeCheck] = []
    new = [f for k, f in after.items() if k not in before]
    still = [f for k, f in after.items() if k in before]
    cleared = [f for k, f in before.items() if k not in after]
    for f in new:
        out.append(
            PretradeCheck(
                id=f"after.{f.key}",
                principle="4",
                ticker=f.ticker,
                status="flag",
                title="Breached after the trade",
                detail=f.detail,
                values={"observed": f.observed, "limit": f.limit},
            )
        )
    for f in still:
        out.append(
            PretradeCheck(
                id=f"after.{f.key}",
                principle="4",
                ticker=f.ticker,
                status="info",
                title="Already breached before the trade, still breached",
                detail=f.detail,
            )
        )
    for f in cleared:
        out.append(
            PretradeCheck(
                id=f"cleared.{f.key}",
                principle="4",
                ticker=f.ticker,
                status="pass",
                title="Breach cleared by the trade",
                detail=f.detail,
            )
        )
    if not new:
        out.append(
            PretradeCheck(
                id="after.rules",
                principle="4",
                ticker=None,
                status="pass",
                title="No new rule, cap or band breach after the trade",
                detail=f"{len(ctx.strategy.enabled_rules)} rule(s) and the target bands were "
                "evaluated on the post-trade weights.",
            )
        )
    return out


def decimal_or_none(x: float | None) -> Decimal | None:
    return None if x is None else Decimal(str(x))
