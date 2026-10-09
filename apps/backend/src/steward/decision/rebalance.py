"""Rebalance workbench (plan M6.4): proposed buys and sells that bring positions back to the
strategy's target weights, with cash, costs and the drift before and after.

Read-only. The workbench only proposes; nothing is sent to Trading212, and the owner takes any
action there. The arithmetic is plain and every assumption is an input shown back to the owner:

- weights are shares of total value (positions plus cash), as the strategy's `weight_basis`
  "total" defines them; on an "invested" basis the plan says so and still uses total value;
- a position inside its band does not trade (`to_target` moves an out-of-band position to its
  target, `to_band` to the nearest band edge, which trades less);
- the cash the plan may spend is current cash plus a deposit (or minus a withdrawal) plus sale
  proceeds net of costs, less the cash floor (the lower edge of the cash band, if one is set);
  buys that the cash cannot fund are scaled down pro rata and the shortfall is reported; a
  withdrawal the cash cannot meet is raised by selling the targeted positions pro rata;
- bands are judged on the weight after the deposit or withdrawal;
- a trade costs `|value| x (fx_cost_bps if the instrument is not quoted in the base currency, plus
  trade_cost_bps)`. The defaults are assumptions the owner edits; they are not read from a
  broker schedule;
- a position with no target is never traded.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from steward.analytics.versions import version
from steward.config.strategy import StrategyConfig
from steward.fx.convert import normalise_currency
from steward.portfolio.drift import DriftReport, HoldingValue, compute_drift

EPS = 1e-9
Mode = Literal["to_target", "to_band"]


@dataclass(frozen=True)
class Holding:
    ticker: str
    name: str | None
    value_base: float
    quantity: float | None
    currency: str  # instrument currency as the broker quotes it (GBX allowed)


class RebalanceRequest(BaseModel):
    mode: Mode = "to_target"
    deposit: float = Field(
        default=0, ge=-1e12, le=1e12, description="Cash added (positive) or taken out (negative)."
    )
    fx_cost_bps: float = Field(
        default=15, ge=0, le=500, description="Assumed cost of converting currency, basis points."
    )
    trade_cost_bps: float = Field(
        default=0,
        ge=0,
        le=500,
        description="Assumed commission and spread per trade, basis points.",
    )
    target_overrides: dict[str, Annotated[float, Field(ge=0, le=1)]] = Field(
        default_factory=dict,
        description="Ticker -> target weight (0 to 1) to use instead of the strategy's "
        "(what-if only).",
    )
    note: str | None = Field(default=None, max_length=500)


class Trade(BaseModel):
    ticker: str
    name: str | None
    action: Literal["buy", "sell", "hold"]
    value_now: float
    weight_now: float
    target_weight: float | None
    lower: float | None
    upper: float | None
    status_now: str
    value_after: float
    weight_after: float
    trade_value: float = Field(description="Signed: positive buys, negative sells (base currency).")
    estimated_units: float | None = Field(
        description="Units at the last price (null without a stored quantity)."
    )
    cost: float
    funded: float = Field(description="Share of the wanted buy that the cash funds, 0 to 1.")
    held: bool
    reason: str


class RebalancePlan(BaseModel):
    method_version: str
    mode: Mode
    weight_basis: str
    total_now: float
    total_after_costs: float
    cash_now: float
    cash_after: float
    cash_floor: float
    deposit: float
    trades: list[Trade]
    buys: float
    sells: float
    costs: float
    unfunded: float = Field(description="Buy value the cash could not fund, base currency.")
    unrelated_positions: int = Field(description="Positions with no target: never traded.")
    drift_before: DriftReport
    drift_after: DriftReport
    assumptions: dict[str, float | str]
    warnings: list[str]
    method: str


METHOD = (
    "Weights are shares of total value. A position inside its band does not trade; one outside "
    "is moved to its target (or the nearest band edge). The cash that may be spent is cash plus "
    "deposit plus sale proceeds net of costs, less the cash floor; buys the cash cannot fund "
    "are scaled down together and the shortfall is shown. A withdrawal the cash cannot meet is "
    "raised by selling the targeted positions pro rata. Bands are judged on the weights after "
    "the deposit or withdrawal. Costs use the stated basis-point assumptions. Nothing is sent "
    "to the broker."
)


def _cost_rate(currency: str, base: str, request: RebalanceRequest) -> float:
    quoted = normalise_currency(currency)[0]
    fx = request.fx_cost_bps if quoted != base else 0.0
    return (fx + request.trade_cost_bps) / 10_000


def plan(
    holdings: list[Holding],
    cash_base: float,
    strategy: StrategyConfig,
    request: RebalanceRequest,
    *,
    unheld_currency: dict[str, str] | None = None,
) -> RebalancePlan:
    base = strategy.base_currency
    warnings: list[str] = []
    if strategy.weight_basis == "invested":
        warnings.append(
            "The strategy weighs positions against invested value; the plan uses total value "
            "(positions plus cash), so a weight here can differ from the Overview."
        )
    invested = sum(h.value_base for h in holdings)
    total_now = invested + cash_base
    if total_now <= 0:
        raise ValueError("The portfolio has no value to rebalance.")
    after_deposit = total_now + request.deposit
    if after_deposit <= 0:
        raise ValueError("The withdrawal is larger than the portfolio.")

    targets = {t.ticker: t for t in strategy.target_positions}
    for ticker, weight in request.target_overrides.items():
        # `model_copy` below skips validation, so an override is checked here as well.
        if not 0 <= weight <= 1:
            raise ValueError(f"{ticker}: a target weight must be between 0 and 100%.")
        base_target = targets.get(ticker)
        if base_target is not None:
            targets[ticker] = base_target.model_copy(update={"target_weight": weight})
        else:
            warnings.append(
                f"{ticker}: an override needs a target in the strategy to carry a band."
            )
    held = {h.ticker: h for h in holdings}
    drift_before = compute_drift(
        [HoldingValue(h.ticker, h.currency, Decimal(str(h.value_base))) for h in holdings],
        Decimal(str(cash_base)),
        strategy,
    )
    status_of = {p.ticker: p for p in drift_before.positions}

    cash_target = strategy.cash_target
    floor = (
        max(0.0, cash_target.target_weight - cash_target.band) * after_deposit
        if cash_target
        else 0.0
    )

    wanted: dict[str, float] = {}
    reasons: dict[str, str] = {}
    for ticker, target in targets.items():
        h = held.get(ticker)
        value = h.value_base if h else 0.0
        # The band is judged on the weight after the deposit or withdrawal moves the total.
        weight = value / after_deposit
        lower = max(0.0, target.target_weight - target.band)
        upper = target.target_weight + target.band
        goal = value
        reason = "inside its band: no trade"
        if weight > upper + EPS:
            edge = target.target_weight if request.mode == "to_target" else upper
            goal = edge * after_deposit
            reason = f"above its band ({weight:.2%} > {upper:.2%})"
        elif weight < lower - EPS:
            edge = target.target_weight if request.mode == "to_target" else lower
            goal = edge * after_deposit
            reason = f"below its band ({weight:.2%} < {lower:.2%})"
        if target.hard_limit is not None and goal > target.hard_limit * after_deposit:
            goal = target.hard_limit * after_deposit
            reason += "; capped at its hard limit"
        wanted[ticker] = goal
        reasons[ticker] = reason

    rows: list[dict] = []  # type: ignore[type-arg]
    for ticker, goal in wanted.items():
        h = held.get(ticker)
        value = h.value_base if h else 0.0
        currency = h.currency if h else (unheld_currency or {}).get(ticker, base)
        rate = _cost_rate(currency, base, request)
        rows.append({"ticker": ticker, "value": value, "delta": goal - value, "rate": rate})

    proceeds = sum(-r["delta"] * (1 - r["rate"]) for r in rows if r["delta"] < -EPS)
    # A withdrawal the cash cannot meet (down to the floor) is raised by selling the targeted
    # positions not being bought, pro rata to what they hold after the planned sales.
    shortfall = max(0.0, floor - (cash_base + request.deposit + proceeds))
    if shortfall > EPS:
        sellable = [r for r in rows if r["delta"] <= EPS and r["value"] + r["delta"] > EPS]
        net = sum((r["value"] + r["delta"]) * (1 - r["rate"]) for r in sellable)
        k = min(1.0, shortfall / net) if net > EPS else 0.0
        for r in sellable:
            r["delta"] -= k * (r["value"] + r["delta"])
            reasons[r["ticker"]] += "; sold pro rata to fund the withdrawal"
        if net * k < shortfall - 1e-6:
            warnings.append(
                f"Selling every targeted position covers {net * k:,.0f} of the "
                f"{shortfall:,.0f} {base} the withdrawal needs; positions with no target are "
                "never sold here."
            )
        proceeds = sum(-r["delta"] * (1 - r["rate"]) for r in rows if r["delta"] < -EPS)

    buys = [r for r in rows if r["delta"] > EPS]
    wanted_spend = sum(r["delta"] * (1 + r["rate"]) for r in buys)
    available = max(0.0, cash_base + request.deposit + proceeds - floor)
    scale = 1.0 if wanted_spend <= EPS else min(1.0, available / wanted_spend)
    unfunded = max(0.0, wanted_spend - available) if wanted_spend > EPS else 0.0
    if scale < 1 - 1e-9:
        warnings.append(
            f"The cash can fund {scale:.0%} of the wanted buys; each buy is scaled to that share "
            f"and {unfunded:,.0f} {base} stays unfunded."
        )
    if request.deposit < 0 and cash_base + request.deposit + proceeds < floor - 1e-6:
        warnings.append("The withdrawal takes cash below the floor of the cash band.")

    trades: list[Trade] = []
    spent = earned = costs = 0.0
    for r in rows:
        ticker = r["ticker"]
        h = held.get(ticker)
        target = targets[ticker]
        delta = r["delta"]
        funded = 1.0
        if delta > EPS:
            funded = scale
            delta *= scale
        cost = abs(delta) * r["rate"] if abs(delta) > EPS else 0.0
        if delta > EPS:
            spent += delta + cost
        elif delta < -EPS:
            earned += -delta - cost
        costs += cost
        action: Literal["buy", "sell", "hold"] = (
            "buy" if delta > EPS else "sell" if delta < -EPS else "hold"
        )
        value_after = r["value"] + delta
        price = (h.value_base / h.quantity) if h and h.quantity else None
        status = status_of.get(ticker)
        trades.append(
            Trade(
                ticker=ticker,
                name=h.name if h else None,
                action=action,
                value_now=r["value"],
                weight_now=r["value"] / total_now,
                target_weight=target.target_weight,
                lower=max(0.0, target.target_weight - target.band),
                upper=target.target_weight + target.band,
                status_now=status.status if status else "no_target",
                value_after=value_after,
                weight_after=0.0,  # set below, once the total after costs is known
                trade_value=delta,
                estimated_units=(delta / price) if price else None,
                cost=cost,
                funded=funded,
                held=h is not None,
                reason=reasons[ticker] + ("" if funded >= 1 - 1e-9 else f"; {funded:.0%} funded"),
            )
        )

    cash_after = cash_base + request.deposit - spent + earned
    total_after = after_deposit - costs
    for t in trades:
        t.weight_after = t.value_after / total_after if total_after > 0 else 0.0
    order = {"sell": 0, "buy": 1, "hold": 2}
    trades.sort(key=lambda t: (order[t.action], -abs(t.trade_value)))

    after_values: dict[str, float] = defaultdict(float)
    currencies: dict[str, str] = {}
    for h in holdings:
        after_values[h.ticker] = h.value_base
        currencies[h.ticker] = h.currency
    for t in trades:
        after_values[t.ticker] += t.trade_value
        currencies.setdefault(t.ticker, (unheld_currency or {}).get(t.ticker, base))
    drift_after = compute_drift(
        [
            HoldingValue(k, currencies[k], Decimal(str(v)))
            for k, v in after_values.items()
            if v > EPS
        ],
        Decimal(str(cash_after)),
        strategy,
    )
    if cash_after < -EPS:
        warnings.append("Cash would go negative; the plan spends more than the cash available.")
    untargeted = sum(1 for h in holdings if h.ticker not in targets)
    if not targets:
        warnings.append(
            "The strategy has no target positions, so there is nothing to rebalance to."
        )
    return RebalancePlan(
        method_version=version("rebalance"),
        mode=request.mode,
        weight_basis=strategy.weight_basis,
        total_now=total_now,
        total_after_costs=total_after,
        cash_now=cash_base,
        cash_after=cash_after,
        cash_floor=floor,
        deposit=request.deposit,
        trades=trades,
        buys=spent - sum(t.cost for t in trades if t.action == "buy"),
        sells=earned + sum(t.cost for t in trades if t.action == "sell"),
        costs=costs,
        unfunded=unfunded,
        unrelated_positions=untargeted,
        drift_before=drift_before,
        drift_after=drift_after,
        assumptions={
            "mode": request.mode,
            "fx_cost_bps": request.fx_cost_bps,
            "trade_cost_bps": request.trade_cost_bps,
            "cash_floor": floor,
        },
        warnings=warnings,
        method=METHOD,
    )
