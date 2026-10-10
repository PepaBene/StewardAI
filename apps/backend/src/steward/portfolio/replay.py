"""Named stress episodes replayed on today's weights (quant plan Q3.1).

`quant/stress.py` holds the method. This module loads the long price history the episodes need
(the weekly return window of `exante` is only three years), links each position to its proxy
fits and assembles the report. Episodes that start before the stored history are reported as
`no_data` with the reason; nothing is fetched from a provider.
"""

import datetime as dt
import logging
from collections.abc import Sequence
from typing import Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.config.strategy import StrategyConfig
from steward.fx.convert import normalise_currency
from steward.fx.history import load_fx_history
from steward.portfolio import exante as ea
from steward.portfolio.risk_snapshots import save_snapshot
from steward.quant import returns as rt
from steward.quant import stress as st
from steward.reference.price_series import PriceSymbols, choose_one, choose_series, holding_symbols
from steward.reference.read import index_series, load_series

log = logging.getLogger(__name__)

METHOD_VERSION = "replay-1"
METHOD = (
    "Each position is moved by its own base-currency price change between the peak and the "
    "trough of the episode. A position that did not exist or has no stored prices then is moved "
    "by its beta (weekly regression, last three years) on the best-fitting proxy that does "
    "cover the episode; those results are approximations and are labelled proxied. Positions "
    "with neither stay flat and are listed as unavailable. Cash is flat."
)


CAUTION = (
    "Part of this result is estimated from a proxy. The beta was measured over ordinary weeks "
    "and betas usually rise in a crash, so those losses are probably understated."
)


class ReplayPosition(BaseModel):
    ticker: str
    name: str | None
    weight: float = Field(description="Share of total portfolio value.")
    basis: Literal["real", "proxied", "unavailable"]
    move: float | None = Field(description="The position's move over the episode, fraction.")
    proxy: str | None = Field(description="Label of the proxy the move came from.")
    beta: float | None
    r_squared: float | None
    pnl: float = Field(description="Gain or loss in the base currency.")


class ReplayEpisode(BaseModel):
    key: str
    label: str
    start: dt.date
    end: dt.date
    note: str
    status: Literal["replayed", "no_data"]
    reason: str | None
    before: float
    after: float | None
    pnl: float | None
    loss: float | None = Field(
        description="pnl / total value; null when nothing could be replayed."
    )
    real_weight: float
    proxied_weight: float
    unavailable_weight: float
    breaches_limit: bool | None = Field(
        description="Whether the loss reaches the drawdown limit; null without a limit or result."
    )
    caution: str | None = Field(
        default=None,
        description="Set when part of the result is estimated through a proxy beta: that loss "
        "is probably understated.",
    )
    custom: bool = Field(default=False, description="An episode the owner added in the strategy.")
    worst: list[ReplayPosition]
    positions: list[ReplayPosition]


class StressReplay(BaseModel):
    as_of: dt.date | None
    base_currency: str | None
    method_version: str
    method: str
    total_value: float
    drawdown_limit: float | None
    history_start: dt.date | None = Field(
        description="Earliest date any series used here has a stored close."
    )
    episodes: list[ReplayEpisode]
    warnings: list[str]
    reason: str | None


def _empty(base: str | None, reason: str, limit: float | None = None) -> StressReplay:
    return StressReplay(
        as_of=None,
        base_currency=base,
        method_version=METHOD_VERSION,
        method=METHOD,
        total_value=0.0,
        drawdown_limit=limit,
        history_start=None,
        episodes=[],
        warnings=[],
        reason=reason,
    )


async def _long_inputs(
    pool: asyncpg.Pool, book: ea.LoadedBook, since: dt.date
) -> tuple[list[rt.SeriesInput], dict[str, str]]:
    """Closes back to `since` for the held positions, the benchmark and the proxy ETFs."""
    from steward.portfolio.risk import benchmark_currency

    jobs = await load_active(pool, "jobs", JobsConfig)
    settings = (jobs.body if jobs else JobsConfig()).prices
    profiles = await load_active(pool, "holding_profiles", HoldingProfilesConfig)
    tickers = sorted({p.ticker for p in book.positions})
    symbols = await holding_symbols(
        pool, tickers, profiles.body if profiles else HoldingProfilesConfig()
    )
    chosen = await choose_series(pool, symbols)
    currency_of = {p.ticker: p.currency for p in book.positions}
    inputs: list[rt.SeriesInput] = []
    for ticker in tickers:
        ref = chosen.get(ticker)
        if ref is None:
            continue
        closes = await load_series(pool, ref, since)
        if len(closes) >= 2:
            inputs.append(rt.SeriesInput(ticker, closes, currency_of[ticker]))
    labels: dict[str, str] = {}
    if settings.benchmark_symbols:
        symbol = settings.benchmark_symbols[0]
        quote = benchmark_currency(symbol)
        _, closes = await index_series(pool, settings, symbol, since)
        if quote and closes:
            inputs.append(rt.SeriesInput(ea.BENCHMARK_KEY, closes, quote))
            labels[ea.BENCHMARK_KEY] = f"Benchmark {symbol}"
    for proxy in settings.proxy_series:
        ref = await choose_one(pool, PriceSymbols(stooq=proxy.symbol, yahoo=proxy.yahoo()))
        if ref is None:
            continue
        closes = await load_series(pool, ref, since)
        if len(closes) >= 2:
            key = ea.PROXY_PREFIX + proxy.symbol
            inputs.append(rt.SeriesInput(key, closes, proxy.currency))
            labels[key] = proxy.label
    return inputs, labels


def _out(
    r: st.PositionResult, names: dict[str, str | None], labels: dict[str, str]
) -> ReplayPosition:
    return ReplayPosition(
        ticker=r.ticker,
        name=names.get(r.ticker),
        weight=r.weight,
        basis=r.basis,
        move=r.move,
        proxy=labels.get(r.proxy, r.proxy) if r.proxy else None,
        beta=r.beta,
        r_squared=r.r_squared,
        pnl=r.pnl,
    )


def build_replay(
    book: ea.LoadedBook,
    inputs: Sequence[rt.SeriesInput],
    labels: dict[str, str],
    fx: rt.FxLookup,
    drawdown_limit: float | None,
    episodes: Sequence[st.Episode] = st.EPISODES,
    custom_keys: frozenset[str] = frozenset(),
) -> StressReplay:
    """Replay every episode on the book. Pure given the loaded data."""
    by_key = {s.key: s for s in inputs}
    holdings = [st.Holding(p.ticker, p.value_base) for p in book.positions]
    names = {p.ticker: p.name for p in book.positions}
    candidates = {
        k: book.weekly.column(k)
        for k in book.weekly.keys
        if k == ea.BENCHMARK_KEY or k.startswith(ea.PROXY_PREFIX)
    }
    ranked = {
        p.ticker: st.rank_proxies(book.weekly.column(p.ticker), candidates)
        for p in book.positions
        if p.ticker in book.weekly.keys
    }
    first_close = min((s.closes[0][0] for s in inputs if s.closes), default=None)
    out: list[ReplayEpisode] = []
    for ep in episodes:
        own = {
            h.ticker: st.move(by_key[h.ticker], fx, ep.start, ep.end)
            for h in holdings
            if h.ticker in by_key
        }
        proxy = {k: st.move(by_key[k], fx, ep.start, ep.end) for k in candidates if k in by_key}
        res = st.replay(ep, holdings, book.total_value, own, proxy, ranked, drawdown_limit)
        reason = None
        if res.loss is None:
            reason = (
                "no stored prices reach back to this episode"
                if first_close is not None and first_close > ep.start
                else "no position or proxy has prices covering both ends of the episode"
            )
        out.append(
            ReplayEpisode(
                key=ep.key,
                label=ep.label,
                start=ep.start,
                end=ep.end,
                note=ep.note,
                status="replayed" if res.loss is not None else "no_data",
                reason=reason,
                before=res.before,
                after=res.after if res.loss is not None else None,
                pnl=res.pnl if res.loss is not None else None,
                loss=res.loss,
                real_weight=res.real_weight,
                proxied_weight=res.proxied_weight,
                unavailable_weight=res.unavailable_weight,
                breaches_limit=res.breaches_limit,
                caution=CAUTION if res.proxied_weight > 0.0005 and res.loss is not None else None,
                custom=ep.key in custom_keys,
                worst=[_out(r, names, labels) for r in res.worst],
                positions=[
                    _out(r, names, labels)
                    for r in sorted(res.positions, key=lambda r: (r.pnl, r.ticker))
                ],
            )
        )
    warnings: list[str] = []
    if any(e.status == "no_data" for e in out):
        warnings.append(
            "Episodes without data start before the stored price history. Raise the price "
            "history length in the Jobs settings and run the price job to replay them."
        )
    return StressReplay(
        as_of=book.as_of,
        base_currency=book.base_currency,
        method_version=METHOD_VERSION,
        method=METHOD,
        total_value=book.total_value,
        drawdown_limit=drawdown_limit,
        history_start=first_close,
        episodes=out,
        warnings=warnings,
        reason=None,
    )


async def stress_replay(pool: asyncpg.Pool) -> StressReplay:
    """The replay of every episode for the latest synced book."""
    book, strategy = await ea.book_from_snapshot(pool)
    if book is None or strategy is None:
        return _empty(None, "no portfolio snapshot yet; run the portfolio sync")
    limit = _drawdown_limit(strategy)
    if not book.positions:
        return _empty(book.base_currency, "the portfolio holds no positions", limit)
    episodes = episodes_for(strategy)
    since = min(e.start for e in episodes) - dt.timedelta(days=14)
    inputs, labels = await _long_inputs(pool, book, since)
    today = dt.date.today()
    currencies = {s.currency for s in inputs} | {book.base_currency}
    fx = await load_fx_history(pool, ea.FX_SOURCE, currencies, since, today)
    lookup = ea.fx_lookup(fx, normalise_currency(book.base_currency)[0])
    custom = frozenset(c.key for c in strategy.risk.stress_episodes)
    report = build_replay(book, inputs, labels, lookup, limit, episodes, custom)
    await save_snapshot(pool, "replay", report, report.as_of, report.method_version)
    return report


def episodes_for(strategy: StrategyConfig) -> list[st.Episode]:
    """The built-in episodes with the owner's own added; an owner entry with a built-in key
    replaces it. Sorted by start date."""
    by_key = {e.key: e for e in st.EPISODES}
    for c in strategy.risk.stress_episodes:
        by_key[c.key] = st.Episode(c.key, c.label, c.start, c.end, c.note)
    return sorted(by_key.values(), key=lambda e: e.start)


def _drawdown_limit(strategy: StrategyConfig) -> float | None:
    value = strategy.risk.max_drawdown
    return float(value) if value is not None else None


__all__ = ["METHOD_VERSION", "StressReplay", "build_replay", "stress_replay"]
