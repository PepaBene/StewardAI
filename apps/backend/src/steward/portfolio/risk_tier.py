"""Risk tiers worked out for each holding from its own numbers.

The strategy sets the rules (`risk.auto_tiers` and the thresholds beside it) and names no
holding. The tiers are stored per holding in `holding_risk_tier` when the book is loaded, and
every page that groups by tier reads them from there. A tier the owner entered in the strategy's
classifications still wins."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import asyncpg

from steward.config.strategy import RiskLimits, StrategyConfig
from steward.reference import risk as ref_risk

log = logging.getLogger(__name__)

Tier = Literal["core", "growth", "speculative"]
Kind = Literal["stock", "fund"]

DEFAULT_SPECULATIVE_VOLATILITY = 0.35
DEFAULT_CORE_VOLATILITY = 0.25


@dataclass(frozen=True)
class Fundamentals:
    """What the stored fundamentals say about one holding; None where they are silent."""

    is_stock: bool
    market_cap: float | None
    profitable: bool | None


@dataclass(frozen=True)
class DerivedTier:
    ticker: str
    tier: Tier
    kind: Kind
    volatility: float | None
    market_cap: float | None
    profitable: bool | None
    reason: str


def derive_tier(
    ticker: str, volatility: float | None, facts: Fundamentals | None, limits: RiskLimits
) -> DerivedTier | None:
    """The tier of one holding, or None when there is nothing to judge it by."""
    spec_vol = limits.tier_speculative_volatility or DEFAULT_SPECULATIVE_VOLATILITY
    core_vol = limits.tier_core_max_volatility or DEFAULT_CORE_VOLATILITY
    stock = facts is not None and facts.is_stock
    cap = facts.market_cap if facts else None
    profitable = facts.profitable if facts else None
    kind: Kind = "stock" if stock else "fund"

    def make(tier: Tier, reason: str) -> DerivedTier:
        return DerivedTier(ticker, tier, kind, volatility, cap, profitable, reason)

    if stock:
        if volatility is not None and volatility > spec_vol:
            return make("speculative", f"volatility {volatility:.0%} is above {spec_vol:.0%}")
        if cap is not None and cap < limits.tier_speculative_market_cap:
            return make(
                "speculative", f"market cap is below ${limits.tier_speculative_market_cap:,.0f}"
            )
        if profitable is False:
            return make("speculative", "it is not profitable")
        if (
            cap is not None
            and cap > limits.tier_core_min_market_cap
            and volatility is not None
            and volatility <= core_vol
            and profitable is True
        ):
            return make("core", "large, profitable and calm")
        return make("growth", "meets neither the speculative nor the core criteria")
    if volatility is None:
        return None
    if volatility > spec_vol:
        return make("speculative", f"fund volatility {volatility:.0%} is above {spec_vol:.0%}")
    if volatility <= core_vol:
        return make("core", f"fund volatility {volatility:.0%} is at or below {core_vol:.0%}")
    return make("growth", "fund volatility between the core and speculative levels")


def derive_tiers(
    volatilities: Mapping[str, float | None],
    facts: Mapping[str, Fundamentals],
    limits: RiskLimits,
) -> dict[str, DerivedTier]:
    out: dict[str, DerivedTier] = {}
    for ticker, vol in volatilities.items():
        d = derive_tier(ticker, vol, facts.get(ticker), limits)
        if d is not None:
            out[ticker] = d
    return out


def apply_tiers(strategy: StrategyConfig, tiers: Mapping[str, str]) -> StrategyConfig:
    """The strategy with each derived tier filled in where the owner entered none."""
    if not tiers:
        return strategy
    merged = {t: dict(b) for t, b in strategy.classifications.items()}
    for ticker, tier in tiers.items():
        buckets = merged.setdefault(ticker, {})
        buckets.setdefault("risk", tier)
    return strategy.model_copy(update={"classifications": merged})


def volatilities_of(
    daily_closes: Mapping[str, Sequence[tuple[Any, float]]],
) -> dict[str, float | None]:
    """The holding page's volatility: one year of daily local-currency returns."""
    return {
        t: ref_risk.volatility(list(c), ref_risk.TRADING_DAYS) if c else None
        for t, c in daily_closes.items()
    }


def _number(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN is no number


async def load_fundamentals(pool: asyncpg.Pool, tickers: Sequence[str]) -> dict[str, Fundamentals]:
    """Market cap and profitability per ticker from the stored fundamentals (Alpha Vantage,
    in USD, then SEC net income). A ticker with no row is treated as a fund."""
    rows = await pool.fetch(
        "select ticker, source, figures from public.fundamentals"
        " where ticker = any($1::text[]) and source in ('alphavantage', 'sec')",
        list(tickers),
    )
    import json

    per: dict[str, dict[str, dict[str, Any]]] = {}
    for r in rows:
        figures = r["figures"]
        figures = json.loads(figures) if isinstance(figures, str) else figures
        per.setdefault(r["ticker"], {})[r["source"]] = figures or {}
    out: dict[str, Fundamentals] = {}
    for ticker, sources in per.items():
        av = sources.get("alphavantage", {})
        sec = sources.get("sec", {})
        is_stock = av.get("AssetType") == "Common Stock" or bool(sec)
        cap = _number(av.get("MarketCapitalization"))
        if av.get("Currency") not in (None, "USD"):
            cap = None  # the threshold is in USD; a cap in another currency is not compared
        margin = _number(av.get("ProfitMargin"))
        net_income = _number(sec.get("net_income_ttm"))
        profitable = (
            margin > 0
            if margin is not None
            else (net_income > 0 if net_income is not None else None)
        )
        out[ticker] = Fundamentals(is_stock, cap, profitable)
    return out


async def save_tiers(pool: asyncpg.Pool, tiers: Mapping[str, DerivedTier]) -> None:
    """Replace the stored tiers with these; holdings no longer held are dropped."""
    try:
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "delete from public.holding_risk_tier where not (ticker = any($1::text[]))",
                list(tiers),
            )
            await conn.executemany(
                "insert into public.holding_risk_tier"
                " (ticker, tier, kind, volatility, market_cap, profitable, reason, computed_at)"
                " values ($1, $2, $3, $4, $5, $6, $7, now())"
                " on conflict (ticker) do update set tier = excluded.tier, kind = excluded.kind,"
                " volatility = excluded.volatility, market_cap = excluded.market_cap,"
                " profitable = excluded.profitable, reason = excluded.reason,"
                " computed_at = excluded.computed_at",
                [
                    (d.ticker, d.tier, d.kind, d.volatility, d.market_cap, d.profitable, d.reason)
                    for d in tiers.values()
                ],
            )
    except Exception as exc:
        log.warning("could not save the risk tiers: %s", exc)


async def with_saved_tiers(pool: asyncpg.Pool, strategy: StrategyConfig) -> StrategyConfig:
    """The strategy with the stored tiers filled in, when it asks for them. Read paths use this;
    it never fails a page."""
    if not strategy.risk.auto_tiers:
        return strategy
    try:
        rows = await pool.fetch("select ticker, tier from public.holding_risk_tier")
    except Exception as exc:
        log.warning("could not read the risk tiers: %s", exc)
        return strategy
    return apply_tiers(strategy, {r["ticker"]: r["tier"] for r in rows})


async def derive_for_book(
    pool: asyncpg.Pool,
    daily_closes: Mapping[str, Sequence[tuple[Any, float]]],
    tickers: Sequence[str],
    strategy: StrategyConfig,
) -> dict[str, DerivedTier]:
    """Work out, store and return the tiers of the book's holdings (nothing when the strategy
    does not ask for them)."""
    if not strategy.risk.auto_tiers:
        return {}
    facts = await load_fundamentals(pool, tickers)
    vols = volatilities_of(daily_closes)
    tiers = derive_tiers({t: vols.get(t) for t in tickers}, facts, strategy.risk)
    await save_tiers(pool, tiers)
    return tiers
