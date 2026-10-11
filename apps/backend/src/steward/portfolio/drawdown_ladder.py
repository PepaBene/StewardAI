"""What is driving a drawdown, for the early-warning notes ahead of the freeze (plan Q7.3).

The rungs themselves are in `RiskLimits.drawdown_warnings`, the freeze in `max_drawdown`. This
module only names the positions behind a fall: over the last `WEEKS` weeks, on today's weights
(short histories backfilled from their proxies, as in the risk model), find the week the
portfolio of those holdings peaked and, for each position, weight x its move since then. The
largest losses are the drivers. It is an approximation of the real path (it ignores trades made
since the peak), and the note says so.
"""

import numpy as np

from steward.config.strategy import StrategyConfig
from steward.portfolio import exante as ea
from steward.quant import proxy as px

WEEKS = 52
TOP = 3
FLOOR = 0.002  # a position that cost less than 0.2 % of the portfolio is not a driver


def drivers(
    book: ea.LoadedBook, strategy: StrategyConfig, weeks: int = WEEKS
) -> list[tuple[str, float]]:
    """(ticker, contribution to the fall as a fraction of portfolio value), worst first."""
    if book.total_value <= 0 or not book.positions:
        return []
    candidates = [k for k in book.weekly.keys if k == ea.BENCHMARK_KEY or k.startswith("proxy:")]
    filled, status = px.backfill(
        book.weekly, candidates, preferred=ea.proxy_preferences(book, strategy)
    )
    usable = [p for p in book.positions if (s := status.get(p.ticker)) and s.status != "excluded"]
    if not usable:
        return []
    last = filled.last(weeks)
    data = np.column_stack([last.column(p.ticker) for p in usable])
    weights = np.array([p.value_base / book.total_value for p in usable])
    path = np.concatenate([[0.0], np.cumsum(data @ weights)])  # log-ish cumulative return
    peak = int(np.argmax(path))
    if peak == len(path) - 1:
        return []
    moves = np.expm1(data[peak:].sum(axis=0))  # each position since the peak week
    contrib = weights * moves
    ranked = sorted(zip((p.ticker for p in usable), contrib, strict=True), key=lambda x: x[1])
    return [(t, float(c)) for t, c in ranked[:TOP] if c < -FLOOR]
