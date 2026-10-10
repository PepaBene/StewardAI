"""Risk-adjusted ranking and the switching rule (strategy principles 5d, 7c, 9c and 9f; plan Q8).

Pure functions and the response models; the loading is in `ranking_service`.

Risk profile (stage A, daily closes in the instrument's own currency):
- volatility over one year and over three months, the worst fall from a running peak over the
  stored history, and the correlation of its daily returns with the portfolio's;
- marginal risk is approximated as correlation x volatility (the stock's contribution per unit
  of weight if the portfolio were a single asset). Stage B replaces it with the marginal
  contribution from the weekly base-currency covariance model, labelled as such.

Score: (annualised expected return - cash rate) / standalone volatility. It ranks every thesis
with scenarios on the same footing, held or not. A stock "improves the portfolio" when its score
is above the portfolio's Sharpe ratio times its correlation with the portfolio: the return per
unit of risk it must beat to be worth adding to what is already owned.

Switching: the expected-return gap between the idea and the holding is compared with the
switching hurdle (tax on the lots sold, trading and FX costs, and a safety margin). The suggested
size scales with how far the gap clears the hurdle: nothing at or under it, the whole position at
twice the hurdle or more. Nothing here trades.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import BaseModel, Field

from steward.reference import risk as ref_risk

Series = Sequence[tuple[dt.date, float]]
Stage = Literal["A", "B"]
Action = Literal["hold", "partial", "full"]


class RiskProfile(BaseModel):
    stage: Stage = Field(description="A: daily closes in the stock's own currency.")
    observations: int = Field(description="Closes behind the figures.")
    window_start: dt.date | None = None
    window_end: dt.date | None = None
    volatility_1y: float | None = None
    volatility_3m: float | None = None
    max_drawdown: float | None = Field(
        default=None, description="Worst fall from a peak over the stored history (negative)."
    )
    correlation: float | None = Field(
        default=None, description="With the portfolio's daily returns over the last year."
    )
    marginal_risk: float | None = Field(
        default=None,
        description="Stage A: correlation x volatility. Stage B: change in the portfolio's "
        "volatility per unit of weight, from the covariance model.",
    )
    marginal_stage: Stage | None = None


class ReturnFigures(BaseModel):
    expected_value: float
    total: float = Field(description="Expected return over the horizon.")
    annualized: float
    horizon_years: float
    horizon_source: str
    upside: float = Field(description="Base case over the price.")
    loss: float = Field(description="Bear case under the price, zero when the bear is above.")
    asymmetry: float | None = Field(description="Base-case upside / bear-case loss.")
    meets_hurdle: bool | None
    meets_asymmetry: bool | None


class EarningsMoves(BaseModel):
    """How the price moved around past results, so a position ahead of a binary event can be
    sized down (principle 4e). Each move runs from the close before the report date to the first
    close after it, which covers results released before or after the market."""

    count: int
    median_abs: float
    largest_abs: float
    largest_date: dt.date


class RankingRow(BaseModel):
    rank: int | None = None
    ticker: str
    name: str | None = None
    held: bool
    weight: float | None = Field(default=None, description="Share of the invested value.")
    price: float | None = None
    price_date: dt.date | None = None
    currency: str | None = None
    scenarios: dict[str, float] | None = None
    returns: ReturnFigures | None = None
    risk: RiskProfile | None = None
    score: float | None = Field(
        default=None, description="(annualised expected return - cash rate) / volatility."
    )
    score_basis: str | None = None
    improves_portfolio: bool | None = None
    bear_loss_vs_history: str | None = None
    earnings_moves: EarningsMoves | None = None
    volatility_band: float | None = None
    reasons: list[str] = Field(
        default_factory=list, description="Why a figure is missing, in plain words."
    )


class RankingResponse(BaseModel):
    method_version: str
    as_of: dt.date
    base_currency: str | None
    cash_rate: float | None
    cash_rate_label: str | None
    portfolio_sharpe: float | None
    return_hurdle: float | None
    asymmetry_gate: float | None
    rows: list[RankingRow]
    bench_count: int
    bench_target: str = "5 to 10"
    formula: str = (
        "score = (annualised expected return - cash rate) / one-year volatility; the "
        "expected return is the probability-weighted value over the price, annualised to the "
        "catalyst date (two years when none)."
    )
    notes: list[str] = Field(default_factory=list)


def portfolio_index(
    series_by: Mapping[str, Series], values: Mapping[str, float]
) -> list[tuple[dt.date, float]]:
    """A level series of the portfolio built from the holdings' own closes at today's weights.

    Each day's return is the weighted mean of the returns of the holdings that traded on both
    that day and the day before, over the weight they carry; a day where less than half the
    weight is priced is skipped. Used only to correlate a stock with the portfolio."""
    total = sum(v for v in values.values() if v > 0)
    if total <= 0:
        return []
    returns: dict[str, dict[dt.date, float]] = {
        k: ref_risk.daily_returns(series_by[k]) for k in values if k in series_by and values[k] > 0
    }
    days = sorted({d for r in returns.values() for d in r})
    level = 100.0
    out: list[tuple[dt.date, float]] = []
    for day in days:
        priced = [(k, r[day]) for k, r in returns.items() if day in r]
        weight = sum(values[k] for k, _ in priced)
        if weight < 0.5 * total:
            continue
        level *= 1 + sum(values[k] * x for k, x in priced) / weight
        out.append((day, level))
    return out


def risk_profile(series: Series, index: Series | None) -> RiskProfile:
    """The stage A profile of one stock from its stored closes."""
    series = list(series)
    corr = ref_risk.correlation(series, index) if index else None
    vol = ref_risk.volatility(series)
    return RiskProfile(
        stage="A",
        observations=len(series),
        window_start=series[0][0] if series else None,
        window_end=series[-1][0] if series else None,
        volatility_1y=vol,
        volatility_3m=ref_risk.volatility(series, 63),
        max_drawdown=ref_risk.max_drawdown(series),
        correlation=corr,
        marginal_risk=corr * vol if corr is not None and vol is not None else None,
        marginal_stage="A" if corr is not None and vol is not None else None,
    )


def earnings_moves(series: Series, report_dates: Sequence[dt.date]) -> EarningsMoves | None:
    """The price moves around the given report dates; None without one inside the history."""
    moves: list[tuple[float, dt.date]] = []
    for day in sorted(set(report_dates)):
        before = [c for d, c in series if d < day]
        after = [c for d, c in series if d > day]
        if before and after:
            moves.append((abs(after[0] / before[-1] - 1), day))
    if not moves:
        return None
    sizes = sorted(m for m, _ in moves)
    mid = len(sizes) // 2
    median = sizes[mid] if len(sizes) % 2 else (sizes[mid - 1] + sizes[mid]) / 2
    largest = max(moves)
    return EarningsMoves(
        count=len(moves), median_abs=median, largest_abs=largest[0], largest_date=largest[1]
    )


def risk_adjusted_score(annualized: float, cash_rate: float, volatility: float) -> float:
    return (annualized - cash_rate) / volatility


def improves_portfolio(score: float, portfolio_sharpe: float, correlation: float) -> bool:
    """Whether the score beats the portfolio's Sharpe ratio times the correlation."""
    return score > portfolio_sharpe * correlation


def bear_vs_history(loss: float, max_drawdown: float | None) -> str | None:
    """The bear-case loss next to the worst fall the stock has already had, in words."""
    if max_drawdown is None:
        return None
    worst = abs(max_drawdown)
    if loss >= worst:
        return (
            f"The bear case ({loss:.0%} down) is at least as deep as its worst fall ({worst:.0%})."
        )
    return (
        f"The bear case ({loss:.0%} down) is milder than the {worst:.0%} fall the stock has "
        "already had."
    )


def switch_action(gap: float, hurdle: float) -> tuple[Action, float]:
    """(action, share of the position to switch). Nothing at or under the hurdle; the whole
    position when the gap is twice the hurdle or more; in between, in proportion."""
    if not math.isfinite(gap) or gap <= hurdle:
        return "hold", 0.0
    if hurdle <= 0:
        return "full", 1.0
    share = min(1.0, (gap - hurdle) / hurdle)
    return ("full", 1.0) if share >= 1.0 else ("partial", share)


def equal_risk_value(old_value: float, old_volatility: float, new_volatility: float) -> float:
    """The size of the replacement that carries the old position's risk."""
    return old_value * old_volatility / new_volatility


class SwitchLine(BaseModel):
    sell: str
    buy: str
    sell_name: str | None = None
    buy_name: str | None = None
    action: Action
    share: float = Field(description="Share of the holding to switch.")
    sell_value: float
    buy_value: float = Field(description="Replacement at equal risk, base currency.")
    remainder: float = Field(description="Proceeds not used by the equal-risk purchase.")
    return_gap: float | None
    hurdle: float | None
    tax: float | None
    costs: float | None
    safety_margin: float | None
    holding_return: float | None
    idea_return: float | None
    holding_volatility: float | None
    idea_volatility: float | None
    portfolio_improves: bool | None = Field(
        description="Risk-adjusted expected return of the book improves with the switch."
    )
    limits_held: bool | None = Field(description="No rule, cap or risk limit breaks or deepens.")
    flagged: bool = Field(description="Suggested: the gap clears the hurdle with every limit held.")
    wait_note: str | None = Field(default=None, description="Switch now or after the time test.")
    checks: list[str] = Field(default_factory=list, description="Pre-trade lines behind it.")
    reasons: list[str] = Field(default_factory=list)


class SwitchResponse(BaseModel):
    method_version: str
    as_of: dt.date
    base_currency: str | None
    lines: list[SwitchLine]
    notes: list[str] = Field(default_factory=list)
    disclaimer: str = (
        "Flags only. Nothing is traded: the owner decides in Trading212. A tax figure is the "
        "rule of thumb applied to the stored lots, not a tax computation."
    )
