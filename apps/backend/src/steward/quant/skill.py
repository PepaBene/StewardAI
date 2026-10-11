"""Is the track record skill or luck? (quant plan Q6.2, Q6.4). Pure functions.

Probabilistic Sharpe Ratio (Bailey and Lopez de Prado 2012). With a per-period Sharpe ratio SR
estimated from n returns that have skewness g3 and (non-excess) kurtosis g4, the probability
that the true Sharpe ratio exceeds a benchmark's SR* is

    PSR = Phi( (SR - SR*) sqrt(n - 1) / sqrt(1 - g3 SR + (g4 - 1)/4 SR^2) )

and the Minimum Track Record Length, the number of periods before SR beats SR* at confidence a, is

    MinTRL = 1 + (1 - g3 SR + (g4 - 1)/4 SR^2) (z_a / (SR - SR*))^2

Brinson-Fachler attribution (Brinson and Fachler 1985). Segments i have portfolio weight wp,
portfolio return rp, benchmark weight wb and benchmark return rb over ONE period; R_B = sum wb rb.

    allocation_i  = (wp - wb) (rb - R_B)
    selection_i   = wb (rp - rb)
    interaction_i = (wp - wb) (rp - rb)

and, when both sets of weights add to one, the three add up to sum wp rp - R_B exactly. The
period is not chain-linked: each period is attributed on its own.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import NormalDist

import numpy as np


@dataclass(frozen=True)
class Psr:
    periods: int
    sharpe: float  # per period
    benchmark_sharpe: float
    probability: float | None
    min_track_record: float | None  # periods
    distinguishable: bool | None


def sharpe_moments(returns: Sequence[float]) -> tuple[float, float, float] | None:
    """(Sharpe per period, skewness, kurtosis) of a return series, or None if it has no spread."""
    x = np.asarray(returns, dtype=np.float64)
    if x.size < 3:
        return None
    sd = float(x.std(ddof=1))
    if sd <= 0:
        return None
    centred = x - x.mean()
    m2 = float(np.mean(centred**2))
    skew = float(np.mean(centred**3)) / m2**1.5
    kurt = float(np.mean(centred**4)) / m2**2
    return float(x.mean()) / sd, skew, kurt


def probabilistic_sharpe(
    returns: Sequence[float], benchmark_sharpe: float, confidence: float = 0.95
) -> Psr | None:
    """PSR of `returns` (excess of cash) against a per-period benchmark Sharpe ratio."""
    moments = sharpe_moments(returns)
    if moments is None:
        return None
    sr, skew, kurt = moments
    n = len(returns)
    spread = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    if spread <= 0:
        return Psr(n, sr, benchmark_sharpe, None, None, None)
    normal = NormalDist()
    probability = normal.cdf((sr - benchmark_sharpe) * math.sqrt(n - 1) / math.sqrt(spread))
    gap = sr - benchmark_sharpe
    min_trl = None if gap <= 0 else 1.0 + spread * (normal.inv_cdf(confidence) / gap) ** 2
    return Psr(n, sr, benchmark_sharpe, probability, min_trl, probability >= confidence)


@dataclass(frozen=True)
class Segment:
    name: str
    portfolio_weight: float
    portfolio_return: float
    benchmark_weight: float
    benchmark_return: float


@dataclass(frozen=True)
class SegmentEffect:
    name: str
    portfolio_weight: float
    benchmark_weight: float
    allocation: float
    selection: float
    interaction: float

    @property
    def total(self) -> float:
        return self.allocation + self.selection + self.interaction


@dataclass(frozen=True)
class Brinson:
    segments: list[SegmentEffect]
    portfolio_return: float
    benchmark_return: float
    excess_return: float
    allocation: float
    selection: float
    interaction: float
    reconciliation_error: float  # excess return minus the three effects; ~0 by construction


def brinson_fachler(segments: Sequence[Segment]) -> Brinson:
    rb_total = sum(s.benchmark_weight * s.benchmark_return for s in segments)
    rp_total = sum(s.portfolio_weight * s.portfolio_return for s in segments)
    rows = [
        SegmentEffect(
            s.name,
            s.portfolio_weight,
            s.benchmark_weight,
            (s.portfolio_weight - s.benchmark_weight) * (s.benchmark_return - rb_total),
            s.benchmark_weight * (s.portfolio_return - s.benchmark_return),
            (s.portfolio_weight - s.benchmark_weight) * (s.portfolio_return - s.benchmark_return),
        )
        for s in segments
    ]
    allocation = sum(r.allocation for r in rows)
    selection = sum(r.selection for r in rows)
    interaction = sum(r.interaction for r in rows)
    excess = rp_total - rb_total
    return Brinson(
        rows, rp_total, rb_total, excess, allocation, selection, interaction,
        excess - (allocation + selection + interaction),
    )  # fmt: skip
