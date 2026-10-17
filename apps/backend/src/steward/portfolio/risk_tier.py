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
    # Annual loss years among the stored annual results (needs MIN_PROFIT_YEARS of them); None
    # when there is not enough history to say.
    loss_years: int | None = None


MIN_PROFIT_YEARS = 3

# `instrument.instrument_type` is empty for the broker's instruments; `security_type` (from the
# identity lookup) is what is stored. Either one can say what an instrument is.
_STOCK_SECURITY_TYPES = ("common stock", "depositary receipt")
_FUND_SECURITY_TYPES = ("mutual fund", "etf", "etp", "etc")


def instrument_is_stock(instrument_type: str | None, security_type: str | None) -> bool:
    return (instrument_type or "").upper() == "STOCK" or (
        (security_type or "").lower() in _STOCK_SECURITY_TYPES
    )


def instrument_is_fund(instrument_type: str | None, security_type: str | None) -> bool:
    return (instrument_type or "").upper() == "ETF" or (
        (security_type or "").lower() in _FUND_SECURITY_TYPES
    )


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
        if facts is not None and facts.loss_years:
            return make(
                "growth", f"it made a loss in {facts.loss_years} of its stored annual results"
            )
        if (
            cap is not None
            and cap > limits.tier_core_min_market_cap
            and volatility is not None
            and volatility <= core_vol
            and profitable is True
        ):
            return make("core", "large, profitable and calm")
        if cap is None and profitable is None:
            return make("growth", "no fundamentals are stored, so it cannot qualify as core")
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
    in USD, then SEC net income). A ticker with no row is a fund unless the broker lists it as
    a stock."""
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
    losses = await _loss_years(pool, tickers)
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
        out[ticker] = Fundamentals(is_stock, cap, profitable, losses.get(ticker))
    # A stock with no fundamentals row is still a stock: judged as a fund it could be called
    # "core" on volatility alone. The broker's instrument type decides.
    bare = [t for t in tickers if t not in out]
    if bare:
        try:
            kinds = await pool.fetch(
                "select t212_ticker, instrument_type, security_type from public.instrument"
                " where t212_ticker = any($1::text[])",
                bare,
            )
            for r in kinds:
                if instrument_is_stock(r["instrument_type"], r["security_type"]):
                    out[r["t212_ticker"]] = Fundamentals(
                        True, None, None, losses.get(r["t212_ticker"])
                    )
        except Exception as exc:  # the tier must not depend on this
            log.warning("could not read instrument types for the tiers: %s", exc)
    return out


# Annual (about one year, fiscal-year) net results of the 10-K and 20-F filings inside the SEC
# companyfacts payload, one value per year-end (the latest filed).
_COMPANYFACTS_NET_INCOME = """
select distinct on (ticker, period_end) ticker, period_end, value from (
  select f.ticker, (e->>'end')::date as period_end, (e->>'val')::float8 as value,
         e->>'filed' as filed
  from public.fundamentals f
  cross join lateral (
    select jsonb_path_query(f.raw::jsonb, p::jsonpath) as e
    from unnest(array[
      '$.companyfacts.facts."us-gaap".NetIncomeLoss.units.*[*]',
      '$.companyfacts.facts."us-gaap".ProfitLoss.units.*[*]',
      '$.companyfacts.facts."ifrs-full".ProfitLoss.units.*[*]'
    ]) as p
  ) x
  where f.source = 'sec' and f.ticker = any($1::text[])
    and e->>'fp' = 'FY' and e->>'form' in ('10-K', '20-F', '10-K/A', '20-F/A') and e ? 'start'
    and (e->>'end')::date - (e->>'start')::date between 350 and 380
) t
order by ticker, period_end, filed desc
"""


async def _loss_years(pool: asyncpg.Pool, tickers: Sequence[str]) -> dict[str, int]:
    """Per ticker, the number of loss years among the latest five annual net results, for the
    tickers that have at least MIN_PROFIT_YEARS of them. One value per year (the latest filed)."""
    try:
        rows = await pool.fetch(
            "select ticker, period_end, value from (select distinct on (ticker, period_end)"
            " ticker, period_end, value from public.financial_statement_fact"
            " where ticker = any($1::text[]) and published"
            " and concept in ('us-gaap:NetIncomeLoss', 'us-gaap:ProfitLoss',"
            " 'ifrs-full:ProfitLoss')"
            " and period_start is not null and period_end - period_start between 350 and 380"
            " and coalesce(dimensions->'members', '[]'::jsonb) = '[]'::jsonb"
            " order by ticker, period_end, known_at desc) t",
            list(tickers),
        )
    except Exception as exc:  # the tier must not depend on this
        log.warning("could not read annual results for the tiers: %s", exc)
        return {}
    # The filed facts hold only the latest annual report (three years of results). The SEC
    # companyfacts payload kept with the fundamentals goes back much further, so it fills the
    # earlier years; where both give a year, the filed fact wins.
    by_year: dict[str, dict[Any, float]] = {}
    for r in rows:
        by_year.setdefault(r["ticker"], {})[r["period_end"]] = float(r["value"])
    try:
        history = await pool.fetch(_COMPANYFACTS_NET_INCOME, list(tickers))
        for r in history:
            by_year.setdefault(r["ticker"], {}).setdefault(r["period_end"], float(r["value"]))
    except Exception as exc:  # the filed facts alone still answer
        log.warning("could not read the stored companyfacts for the tiers: %s", exc)
    per = {t: list(years.items()) for t, years in by_year.items()}
    return {
        t: sum(1 for _, v in sorted(vals)[-5:] if v < 0)
        for t, vals in per.items()
        if len(vals) >= MIN_PROFIT_YEARS
    }


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
    """The strategy with the stored tiers filled in, when it asks for them, and the derived
    sectors and countries (`classify`). Read paths use this; it never fails a page."""
    from steward.portfolio.classify import with_derived_classes  # classify imports this module

    strategy = await with_derived_classes(pool, strategy)
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
    changes: list[str] | None = None,
) -> dict[str, DerivedTier]:
    """Work out, store and return the tiers of the book's holdings (nothing when the strategy
    does not ask for them). A holding whose tier differs from the stored one is described in
    `changes`: with derived tiers the volatility criterion re-tiers it silently, so the change
    itself is what the owner is told to look at (principle D6)."""
    if not strategy.risk.auto_tiers:
        return {}
    facts = await load_fundamentals(pool, tickers)
    vols = volatilities_of(daily_closes)
    tiers = derive_tiers({t: vols.get(t) for t in tickers}, facts, strategy.risk)
    if changes is not None:
        try:
            before = {
                r["ticker"]: r["tier"]
                for r in await pool.fetch("select ticker, tier from public.holding_risk_tier")
            }
        except Exception as exc:
            log.warning("could not read the stored risk tiers: %s", exc)
            before = {}
        for t, d in sorted(tiers.items()):
            if t in before and before[t] != d.tier:
                changes.append(f"{t}: {before[t]} to {d.tier} ({d.reason})")
    await save_tiers(pool, tiers)
    return tiers
