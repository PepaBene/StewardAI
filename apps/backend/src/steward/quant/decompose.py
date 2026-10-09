"""Where a portfolio's risk comes from (quant plan Q1.2).

All figures take a WEEKLY covariance matrix `cov` and weights `w` (shares of total portfolio
value, so the invested weights sum to less than one when cash is held).

- Euler risk contribution: RC_i = w_i (Cov w)_i / sigma_p, in volatility units; the share is
  RC_i / sigma_p = w_i (Cov w)_i / sigma_p^2. Shares sum to exactly 100 % (Euler's theorem for
  a homogeneous degree-one risk measure). A group's share is the sum of its members' shares.
- Marginal contribution to risk: MCR_i = (Cov w)_i / sigma_p, the change in portfolio volatility
  per unit of weight moved into i.
- Diversification ratio: sum_i w_i sigma_i / sigma_p. 1 for one position or fully correlated
  ones, higher the more the book diversifies.
- Effective number of bets (Meucci 2009, "Managing diversification"): the principal-component
  decomposition Cov = E L E'; the variance share of component k is p_k = L_k (E'w)_k^2 / sigma_p^2,
  and ENB = exp(-sum_k p_k ln p_k), the entropy of those shares. Equals N for equally weighted
  uncorrelated assets of equal volatility and 1 when one factor drives everything.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from steward.quant.covariance import WEEKS_PER_YEAR

Vector = npt.NDArray[np.float64]
Matrix = npt.NDArray[np.float64]


@dataclass(frozen=True)
class PositionRisk:
    key: str
    weight: float
    volatility: float  # standalone, annualised
    risk_share: float  # Euler share of portfolio variance, sums to 1
    contribution: float  # RC_i, annualised volatility units
    marginal: float  # MCR_i, annualised


@dataclass(frozen=True)
class Decomposition:
    volatility: float  # annualised
    positions: list[PositionRisk]
    diversification_ratio: float | None
    effective_bets: float | None


def decompose(keys: Sequence[str], weights: Vector, cov: Matrix) -> Decomposition:
    """Risk contributions, diversification ratio and effective bets. A portfolio with no
    variance (no positions, or nothing that moves) returns zero shares and no ratios."""
    annual = cov * WEEKS_PER_YEAR
    variance = float(weights @ annual @ weights)
    sigma = math.sqrt(max(variance, 0.0))
    sd = np.sqrt(np.clip(np.diag(annual), 0.0, None))
    if sigma <= 0:
        return Decomposition(
            0.0,
            [
                PositionRisk(k, float(w), float(s), 0.0, 0.0, 0.0)
                for k, w, s in zip(keys, weights, sd, strict=True)
            ],
            None,
            None,
        )
    marginal = (annual @ weights) / sigma
    contribution = weights * marginal
    positions = [
        PositionRisk(k, float(w), float(s), float(c / sigma), float(c), float(m))
        for k, w, s, c, m in zip(keys, weights, sd, contribution, marginal, strict=True)
    ]
    return Decomposition(
        sigma,
        positions,
        float(weights @ sd) / sigma,
        effective_bets(weights, annual),
    )


def effective_bets(weights: Vector, annual_cov: Matrix) -> float | None:
    """Meucci's entropy-based number of independent bets (see the module docstring)."""
    variance = float(weights @ annual_cov @ weights)
    if variance <= 0:
        return None
    eigenvalues, eigenvectors = np.linalg.eigh(annual_cov)
    exposure = eigenvectors.T @ weights
    p = np.clip(eigenvalues, 0.0, None) * exposure**2 / variance
    p = p[p > 1e-15]
    p = p / p.sum()
    return float(math.exp(-float((p * np.log(p)).sum())))


def group_shares(
    positions: Sequence[PositionRisk], labels: Mapping[str, str], unlabelled: str = "Unclassified"
) -> list[tuple[str, float, float]]:
    """(label, weight, risk share) summed per group, biggest risk share first."""
    weight: dict[str, float] = {}
    share: dict[str, float] = {}
    for p in positions:
        label = labels.get(p.key) or unlabelled
        weight[label] = weight.get(label, 0.0) + p.weight
        share[label] = share.get(label, 0.0) + p.risk_share
    return sorted(((g, weight[g], share[g]) for g in weight), key=lambda row: -row[2])
