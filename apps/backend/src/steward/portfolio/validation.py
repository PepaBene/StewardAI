"""Back-test of the ex-ante risk model on its own history (quant plan Q6.1).

The question: had the model been run at each past week on TODAY's holdings, would its volatility
forecast and its 95 % and 99 % value-at-risk have matched what then happened? Each week's
forecast uses only the weeks before it (zero-mean EWMA, the half-life of the risk model). The
tests are in `quant.validation`; this module builds the weekly series of today's holdings (short
histories backfilled from their proxies, as the risk model does) and turns the results into a
plain verdict.

It tests the model on a constant-weight book: the weights the portfolio actually held in the past
are not used, so this is a test of the covariance method, not of past decisions.
"""

import logging
from typing import Literal

import asyncpg
import numpy as np
from pydantic import BaseModel, Field

from steward.config.strategy import StrategyConfig
from steward.portfolio import exante as ea
from steward.quant import proxy as px
from steward.quant import validation as qv

log = logging.getLogger(__name__)

METHOD_VERSION = "validation-1"
METHOD = (
    "At each past week the volatility of today's holdings is forecast from the weeks before it "
    "(zero-mean exponentially weighted, half-life 26 weeks). A week is a hit when the portfolio "
    "falls by more than the 95 % or 99 % value-at-risk of that forecast. Kupiec's test checks "
    "the number of hits, Christoffersen's whether they cluster; the standard deviation of the "
    "weekly return divided by its forecast should be 1."
)
CONFIDENCES = (0.95, 0.99)
MIN_TEST_WEEKS = 26


class BacktestOut(BaseModel):
    confidence: float
    weeks: int
    hits: int
    expected_hits: float
    kupiec_p: float
    independence_p: float
    passed: bool
    reason: str | None


class CalibrationOut(BaseModel):
    weeks: int
    realised_over_forecast: float | None
    standard_error: float | None
    biased: bool
    direction: str | None
    forecast_volatility: float | None = Field(description="Annualised, average over the weeks.")
    realised_volatility: float | None = Field(description="Annualised, the same weeks.")


class ModelValidation(BaseModel):
    available: bool
    reason: str | None
    as_of: str | None = None
    method_version: str = METHOD_VERSION
    method: str = METHOD
    confidence: Literal["ok", "reduced", "untested"] = "untested"
    verdict: str = ""
    review_item: str | None = Field(
        default=None, description="Set when a test failed: what to look at."
    )
    backtests: list[BacktestOut] = Field(default_factory=list)
    calibration: CalibrationOut | None = None
    weeks_tested: int = 0
    warnings: list[str] = Field(default_factory=list)


def _unavailable(reason: str) -> ModelValidation:
    return ModelValidation(
        available=False, reason=reason, verdict=reason, warnings=[], weeks_tested=0
    )


def validate_book(book: ea.LoadedBook, strategy: StrategyConfig) -> ModelValidation:
    """The back-test of a loaded book (pure: no I/O)."""
    if book.total_value <= 0 or not book.positions:
        return _unavailable("no open positions")
    candidates = [k for k in book.weekly.keys if k == ea.BENCHMARK_KEY or k.startswith("proxy:")]
    filled, status = px.backfill(
        book.weekly, candidates, preferred=ea.proxy_preferences(book, strategy)
    )
    usable = [p for p in book.positions if (s := status.get(p.ticker)) and s.status != "excluded"]
    if not usable:
        return _unavailable("no position has enough history for the model")
    weights = np.array([p.value_base for p in usable])
    weights = weights / weights.sum()
    # Simple returns, as the tail report uses: the weights apply to value, not to log growth.
    series = np.expm1(np.column_stack([filled.column(p.ticker) for p in usable])) @ weights
    returns = [float(x) for x in series]
    sigmas = qv.rolling_ewma_sigma(returns, 26.0)
    tested = sum(s is not None for s in sigmas)
    if tested < MIN_TEST_WEEKS:
        return _unavailable(
            f"only {tested} weeks can be tested; at least {MIN_TEST_WEEKS} are needed "
            "(the first 52 weeks only seed the forecast)"
        )
    backtests = [qv.var_backtest(returns, sigmas, c) for c in CONFIDENCES]
    cal = qv.calibration(returns, sigmas)
    out = [
        BacktestOut(
            confidence=b.confidence, weeks=b.weeks, hits=b.hits, expected_hits=b.expected_hits,
            kupiec_p=b.kupiec_p, independence_p=b.independence_p, passed=b.passed,
            reason=b.reason,
        )
        for b in backtests
    ]  # fmt: skip
    failed = [b for b in backtests if not b.passed]
    problems = [f"{b.confidence:.0%} VaR: {b.reason}" for b in failed]
    if cal.biased and cal.direction:
        problems.append(
            f"the volatility forecast {cal.direction} the realised moves "
            f"(ratio {cal.realised_over_forecast:.2f})"
        )
    confidence: Literal["ok", "reduced"] = "reduced" if problems else "ok"
    verdict = (
        "The model's forecasts held up over the weeks tested."
        if not problems
        else "Treat the model's figures with less confidence: " + "; ".join(problems) + "."
    )
    warnings = [
        f"{tested} weeks tested. The 99 % test has about {tested * 0.01:.1f} expected hits, "
        "so it can rarely fail."
    ]
    return ModelValidation(
        available=True,
        reason=None,
        as_of=filled.dates[-1].isoformat(),
        confidence=confidence,
        verdict=verdict,
        review_item=(
            "Model check failed: " + "; ".join(problems) + ". Review the risk model before relying "
            "on its limits."
            if problems
            else None
        ),
        backtests=out,
        calibration=CalibrationOut(
            weeks=cal.weeks,
            realised_over_forecast=cal.realised_over_forecast,
            standard_error=cal.standard_error,
            biased=cal.biased,
            direction=cal.direction,
            forecast_volatility=cal.mean_forecast_vol,
            realised_volatility=cal.realised_vol,
        ),
        weeks_tested=tested,
        warnings=warnings,
    )


async def model_validation(pool: asyncpg.Pool) -> ModelValidation:
    """The back-test for the latest synced book."""
    book, strategy = await ea.book_from_snapshot(pool)
    if book is None or strategy is None:
        return _unavailable("no portfolio snapshot yet; run the portfolio sync")
    return validate_book(book, strategy)
