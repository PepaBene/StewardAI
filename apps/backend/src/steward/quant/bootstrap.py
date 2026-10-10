"""The chance of the fall the strategy fears, by simulation (plan Q2.2). Pure.

Stationary bootstrap (Politis and Romano, 1994) of the portfolio's own weekly returns: each path
is built from blocks of consecutive weeks whose length is geometric with the given mean, wrapping
round the sample, so volatility clustering and short-run dependence survive. The generator is
seeded, so the same series always gives the same answer.

It resamples what happened to today's book; it knows nothing a history does not contain, and its
paths are only as long a memory as the series (see the method notes on the page).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

METHOD_VERSION = "bootstrap-1"
DEFAULT_PATHS = 10_000
DEFAULT_SEED = 20261010
DEFAULT_BLOCK = 8.0  # weeks, the mean block length
TAIL = 0.05  # the share of worst paths averaged for Conditional Drawdown at Risk
MIN_WEEKS = 52

Array = npt.NDArray[np.float64]


@dataclass(frozen=True)
class BootstrapResult:
    weeks: int
    paths: int
    seed: int
    block: float
    threshold: float
    probability_of_drawdown: float
    drawdown_quantiles: dict[str, float]
    terminal_quantiles: dict[str, float]
    conditional_drawdown_at_risk: float
    recovery_median_weeks: float | None
    recovered_share: float | None
    # Value of 1 invested today at each FAN_STEP weeks (starting at 0), by percentile of the paths.
    fan: dict[str, list[float]]


FAN_STEP = 4
FAN_LEVELS = {"p05": 0.05, "p25": 0.25, "p50": 0.5, "p75": 0.75, "p95": 0.95}


def stationary_indices(
    n: int, weeks: int, paths: int, block: float, rng: np.random.Generator
) -> np.ndarray:
    """Row i of the result indexes the sample for path i: a block restarts with probability
    1/block at every step, else it continues with the next observation (wrapping)."""
    restart = rng.random((paths, weeks)) < 1.0 / block
    restart[:, 0] = True
    starts = rng.integers(0, n, (paths, weeks))
    idx = np.empty((paths, weeks), dtype=np.int64)
    idx[:, 0] = starts[:, 0]
    for t in range(1, weeks):
        idx[:, t] = np.where(restart[:, t], starts[:, t], (idx[:, t - 1] + 1) % n)
    return idx


def max_drawdowns(growth: Array) -> Array:
    """Largest peak-to-trough fall of each path (positive fraction); `growth` is the path of
    values starting from 1, one row per path."""
    peaks = np.maximum.accumulate(
        np.concatenate([np.ones((len(growth), 1)), growth], axis=1), axis=1
    )
    path = np.concatenate([np.ones((len(growth), 1)), growth], axis=1)
    return (1.0 - path / peaks).max(axis=1)


def _recovery_weeks(growth: Array, threshold: float) -> tuple[float | None, float | None]:
    """Among paths that fell `threshold` below a peak: the median weeks from the deepest point of
    that fall back to the peak, and the share of such paths that recovered before the end."""
    path = np.concatenate([np.ones((len(growth), 1)), growth], axis=1)
    peaks = np.maximum.accumulate(path, axis=1)
    fall = 1.0 - path / peaks
    waits: list[int] = []
    hit = 0
    for i in range(len(path)):
        if fall[i].max() < threshold:
            continue
        hit += 1
        trough = int(fall[i].argmax())
        peak_level = peaks[i, trough]
        back = np.nonzero(path[i, trough:] >= peak_level)[0]
        if len(back):
            waits.append(int(back[0]))
    if hit == 0:
        return None, None
    return (float(np.median(waits)) if waits else None), len(waits) / hit


def simulate(
    weekly_returns: Array,
    weeks: int,
    threshold: float = 0.20,
    paths: int = DEFAULT_PATHS,
    seed: int = DEFAULT_SEED,
    block: float = DEFAULT_BLOCK,
) -> BootstrapResult | None:
    """Simulate `paths` futures of `weeks` weeks from simple weekly returns of the portfolio."""
    r = np.asarray(weekly_returns, dtype=float)
    r = r[~np.isnan(r)]
    if len(r) < MIN_WEEKS or weeks < 1:
        return None
    rng = np.random.default_rng(seed)
    idx = stationary_indices(len(r), weeks, paths, block, rng)
    growth = np.cumprod(1.0 + r[idx], axis=1)
    drawdown = max_drawdowns(growth)
    worst = np.sort(drawdown)[::-1]
    count = max(1, round(TAIL * paths))
    wait, share = _recovery_weeks(growth, threshold)
    levels = {"p50": 0.50, "p90": 0.90, "p95": 0.95, "p99": 0.99}
    terminal = growth[:, -1]
    return BootstrapResult(
        weeks=weeks,
        paths=paths,
        seed=seed,
        block=block,
        threshold=threshold,
        probability_of_drawdown=float((drawdown >= threshold).mean()),
        drawdown_quantiles={k: float(np.quantile(drawdown, q)) for k, q in levels.items()},
        terminal_quantiles={
            k: float(np.quantile(terminal, q))
            for k, q in {"p05": 0.05, "p25": 0.25, "p50": 0.5, "p75": 0.75, "p95": 0.95}.items()
        },
        conditional_drawdown_at_risk=float(worst[:count].mean()),
        recovery_median_weeks=wait,
        recovered_share=share,
        fan=_fan(growth),
    )


def _fan(growth: Array) -> dict[str, list[float]]:
    steps = [*range(FAN_STEP - 1, growth.shape[1], FAN_STEP)]
    if not steps or steps[-1] != growth.shape[1] - 1:
        steps.append(growth.shape[1] - 1)
    sampled = growth[:, steps]
    return {
        k: [1.0, *(float(v) for v in np.quantile(sampled, q, axis=0))]
        for k, q in FAN_LEVELS.items()
    }
