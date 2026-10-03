"""The portfolio's correlation matrix, computed in code from stored daily closes.

Each holding's series is the one the holding page uses (`choose_series`: one whole series,
never stitched). Correlation is Pearson on daily simple returns of the adjusted closes over the
days both holdings traded, the same function as the per-holding `correlations` of
`GET /holdings/{ticker}/prices` (`risk.paired_correlation`), so the figures agree. A pair with
fewer than `min_shared` shared returns is null, never 0.
"""

import datetime as dt
import math

import asyncpg
from pydantic import BaseModel, Field

from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.store import load_active
from steward.reference import risk
from steward.reference.price_series import choose_series, holding_symbols
from steward.reference.read import current_tickers, load_series

METHOD = (
    "Pearson correlation of daily simple returns of adjusted closes, over the days both "
    "holdings have a stored close (returns span the same interval for both)."
)


class CorrelationHolding(BaseModel):
    ticker: str
    symbol: str | None = Field(description="Symbol of the series used; null when none is stored.")
    source: str | None = Field(description="Whose daily closes (Stooq, Yahoo, ...); null if none.")
    as_of: dt.date | None = Field(description="Date of the latest close inside the window.")
    observations: int = Field(description="Closes of this series inside the window.")


class PortfolioCorrelation(BaseModel):
    days: int
    min_shared: int
    start: dt.date | None = Field(description="First day of the window (latest close - days).")
    end: dt.date | None = Field(description="Latest stored close among the holdings.")
    max_returns: int = Field(
        description="Most recent shared returns used per pair (days scaled to trading days)."
    )
    method: str
    tickers: list[str] = Field(description="Current holdings in row/column order (by ticker).")
    holdings: list[CorrelationHolding] = Field(description="Same order as `tickers`.")
    matrix: list[list[float | None]] = Field(
        description="matrix[i][j]: correlation (-1..1, unitless) of tickers i and j; null when "
        "the pair shares fewer than `min_shared` returns or a series is flat. The diagonal is "
        "1.0 when the holding has enough returns."
    )
    counts: list[list[int]] = Field(
        description="counts[i][j]: shared daily returns used for the pair (0 when no series)."
    )
    notes: list[list[str | None]] = Field(
        description="notes[i][j]: why matrix[i][j] is null, and a caution when the two series "
        "come from different sources (their adjustments can differ); null when nothing to say."
    )


def _window_returns(days: int) -> int:
    return max(2, math.ceil(days * risk.TRADING_DAYS / 365))


async def portfolio_correlation(
    pool: asyncpg.Pool, days: int = 365, min_shared: int = 20
) -> PortfolioCorrelation:
    tickers = await current_tickers(pool)
    profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    symbols = (
        await holding_symbols(pool, tickers, profiles.body if profiles else HoldingProfilesConfig())
        if tickers
        else {}
    )
    chosen = await choose_series(pool, symbols) if symbols else {}
    full: dict[str, list[tuple[dt.date, float]]] = {}
    for t in tickers:
        ref = chosen.get(t)
        full[t] = await load_series(pool, ref) if ref else []
    end = max((s[-1][0] for s in full.values() if s), default=None)
    start = end - dt.timedelta(days=days) if end else None
    series = {
        t: [(d, c) for d, c in s if start is not None and d >= start] for t, s in full.items()
    }
    cap = _window_returns(days)

    n = len(tickers)
    matrix: list[list[float | None]] = [[None] * n for _ in range(n)]
    counts = [[0] * n for _ in range(n)]
    notes: list[list[str | None]] = [[None] * n for _ in range(n)]
    for i, a in enumerate(tickers):
        for j in range(i, n):
            b = tickers[j]
            corr: float | None = None
            shared = 0
            note: str | None = None
            if not series[a] or not series[b]:
                missing = [t for t in (a, b) if not series[t]]
                note = f"no stored prices in the window for {', '.join(dict.fromkeys(missing))}"
            else:
                corr, shared = risk.paired_correlation(series[a], series[b], cap, min_shared)
                if corr is None:
                    note = (
                        f"{shared} shared returns, fewer than the {min_shared} required"
                        if shared < min_shared
                        else "a series is flat over the shared days"
                    )
                ra, rb = chosen.get(a), chosen.get(b)
                if ra and rb and ra.source != rb.source:
                    caution = f"series from different sources ({ra.name} vs {rb.name})"
                    note = f"{note}; {caution}" if note else caution
            matrix[i][j] = matrix[j][i] = corr
            counts[i][j] = counts[j][i] = shared
            notes[i][j] = notes[j][i] = note
    holdings = []
    for t in tickers:
        ref = chosen.get(t)
        s = series[t]
        holdings.append(
            CorrelationHolding(
                ticker=t,
                symbol=ref.symbol if ref else None,
                source=ref.name if ref else None,
                as_of=s[-1][0] if s else None,
                observations=len(s),
            )
        )
    return PortfolioCorrelation(
        days=days,
        min_shared=min_shared,
        start=start,
        end=end,
        max_returns=cap,
        method=METHOD,
        tickers=tickers,
        holdings=holdings,
        matrix=matrix,
        counts=counts,
        notes=notes,
    )
