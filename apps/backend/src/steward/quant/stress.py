"""Historical stress replay on today's weights (quant plan Q3.1).

Question: what would the book held now have lost in a named past episode? Method:

1. Episodes are fixed peak-to-trough windows (`EPISODES`). A series' move over one is
   `L_end / L_start - 1`, where L is the close in the base currency (the close times that day's
   rate, as in `returns`), so a US listing carries its currency move.
2. A position whose own series covers both ends of the episode is replayed on its own history
   (`real`).
3. Otherwise it is replayed through a proxy: its weekly regression beta on a proxy ETF or the
   benchmark, fitted on recent history, times the proxy's own move over the episode
   (`proxied`). The proxy with the highest R-squared among those that cover the episode is used.
   Alpha is left out. This is an approximation and is labelled as one everywhere.
4. A position with neither is `unavailable`: it is held flat, its weight is reported, and the
   total is a loss on the covered part only.

Weights are shares of total portfolio value, so cash is flat and the result reconciles by
construction: `after = before + the sum of the position results`.
"""

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt

from steward.quant import proxy as px
from steward.quant.returns import FxLookup, SeriesInput, levels

Basis = Literal["real", "proxied", "unavailable"]
MIN_FIT_WEEKS = px.MIN_FIT_WEEKS


@dataclass(frozen=True)
class Episode:
    key: str
    label: str
    start: dt.date  # the peak (or the day before the shock)
    end: dt.date  # the trough
    note: str


# Dates are the approximate market peak and trough of each episode, not official dates.
EPISODES: tuple[Episode, ...] = (
    Episode(
        "gfc",
        "Global financial crisis",
        dt.date(2008, 9, 12),
        dt.date(2009, 3, 9),
        "Lehman failure to the equity low.",
    ),
    Episode(
        "euro_crisis",
        "Euro crisis",
        dt.date(2011, 7, 22),
        dt.date(2011, 9, 22),
        "Downgrade of US debt and the Italian and Spanish sell-off.",
    ),
    Episode(
        "czk_floor",
        "End of the CZK floor",
        dt.date(2017, 4, 6),
        dt.date(2017, 8, 31),
        "The Czech National Bank ended its koruna floor; foreign assets lost in CZK.",
    ),
    Episode(
        "covid",
        "Covid crash",
        dt.date(2020, 2, 19),
        dt.date(2020, 3, 23),
        "The fastest 30 % fall in equity history.",
    ),
    Episode(
        "rates_2022",
        "2022 rate shock",
        dt.date(2022, 1, 3),
        dt.date(2022, 10, 12),
        "Inflation and rising rates hit both shares and bonds.",
    ),
)


def move(series: SeriesInput, fx: FxLookup, start: dt.date, end: dt.date) -> float | None:
    """Simple return in the base currency between two dates; None when either end has no
    close (within the carry limit) or no rate."""
    lv = levels(series, [start, end], fx)
    if np.isnan(lv).any() or lv[0] <= 0:
        return None
    return float(lv[1] / lv[0] - 1)


@dataclass(frozen=True)
class ProxyChoice:
    proxy: str
    beta: float
    r_squared: float
    weeks: int


def rank_proxies(
    own: npt.NDArray[np.float64],
    candidates: Mapping[str, npt.NDArray[np.float64]],
    min_weeks: int = MIN_FIT_WEEKS,
) -> list[ProxyChoice]:
    """Every candidate with enough overlapping weekly returns, best R-squared first."""
    out: list[ProxyChoice] = []
    for key, x in candidates.items():
        both = ~np.isnan(own) & ~np.isnan(x)
        n = int(both.sum())
        if n < min_weeks:
            continue
        fit = px.ols(own[both], x[both])
        if fit is None:
            continue
        _, beta, r2, _ = fit
        out.append(ProxyChoice(key, beta, r2, n))
    out.sort(key=lambda c: -c.r_squared)
    return out


@dataclass(frozen=True)
class Holding:
    ticker: str
    value: float  # base currency


@dataclass(frozen=True)
class PositionResult:
    ticker: str
    weight: float
    basis: Basis
    move: float | None  # the position's move over the episode
    proxy: str | None
    beta: float | None
    r_squared: float | None
    pnl: float  # base currency, zero when unavailable


@dataclass(frozen=True)
class EpisodeResult:
    episode: Episode
    before: float
    after: float
    pnl: float
    loss: float | None  # pnl / before; None when no position could be replayed
    positions: list[PositionResult]
    real_weight: float
    proxied_weight: float
    unavailable_weight: float
    worst: list[PositionResult]  # the three largest losses
    breaches_limit: bool | None  # None without a limit or without a result


def replay(
    episode: Episode,
    holdings: Sequence[Holding],
    total_value: float,
    own_moves: Mapping[str, float | None],
    proxy_moves: Mapping[str, float | None],
    ranked: Mapping[str, Sequence[ProxyChoice]],
    drawdown_limit: float | None = None,
) -> EpisodeResult:
    """Replay one episode on today's holdings. `own_moves` is the position's own move,
    `proxy_moves` the move of each proxy over the same episode, `ranked` each position's
    proxies from best fit down."""
    results: list[PositionResult] = []
    for h in holdings:
        weight = h.value / total_value if total_value > 0 else 0.0
        own = own_moves.get(h.ticker)
        if own is not None:
            results.append(
                PositionResult(h.ticker, weight, "real", own, None, None, None, h.value * own)
            )
            continue
        chosen = next(
            (c for c in ranked.get(h.ticker, ()) if proxy_moves.get(c.proxy) is not None), None
        )
        if chosen is None:
            results.append(
                PositionResult(h.ticker, weight, "unavailable", None, None, None, None, 0.0)
            )
            continue
        proxy_move = proxy_moves[chosen.proxy]
        assert proxy_move is not None
        # beta on a simple-return move: the regression ran on weekly log returns, so convert
        # the proxy's move to a log return, scale, and convert back.
        log_move = chosen.beta * float(np.log1p(proxy_move))
        simple = float(np.expm1(log_move))
        results.append(
            PositionResult(
                h.ticker,
                weight,
                "proxied",
                simple,
                chosen.proxy,
                chosen.beta,
                chosen.r_squared,
                h.value * simple,
            )
        )
    pnl = sum(r.pnl for r in results)
    real = sum(r.weight for r in results if r.basis == "real")
    proxied = sum(r.weight for r in results if r.basis == "proxied")
    missing = sum(r.weight for r in results if r.basis == "unavailable")
    covered = any(r.basis != "unavailable" for r in results)
    loss = pnl / total_value if covered and total_value > 0 else None
    worst = sorted((r for r in results if r.pnl < 0), key=lambda r: r.pnl)[:3]
    breach = None if loss is None or drawdown_limit is None else loss <= -drawdown_limit
    return EpisodeResult(
        episode,
        total_value,
        total_value + pnl,
        pnl,
        loss,
        results,
        real,
        proxied,
        missing,
        worst,
        breach,
    )
