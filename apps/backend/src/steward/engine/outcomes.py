"""Did the owner's saved rebalance plans point the right way? (engine plan, phase 5.1)

A saved rebalance plan names buys and sells. After a fixed horizon each is scored against the
prices that followed: a buy is a hit when the position beat the average of the plan's positions,
a sell when it lagged them. Nothing here trades or edits anything; it reports a hit rate, and a
notice when many trades in a row pointed the wrong way, so the owner can distrust the method.

The score uses adjusted bars of the instrument's price symbol. A trade without bars at both ends
is left out, and the report says how many were.
"""

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any

import asyncpg

from steward.config.jobs import JobsConfig
from steward.config.store import load_active
from steward.ops import tasks

KIND = "plan_outcomes"
HORIZON_DAYS = 60
LOOKBACK_DAYS = 400
MIN_TRADES = 20
WEAK_RATE = 0.4  # fewer than 40% of the trades pointed the right way


@dataclass(frozen=True)
class Scored:
    ticker: str
    action: str
    excess: float  # the position's return minus the plan average, as a fraction

    @property
    def hit(self) -> bool:
        return self.excess > 0 if self.action == "buy" else self.excess < 0


def score(returns: dict[str, float], trades: list[tuple[str, str]]) -> list[Scored]:
    """Score trades against the plan's own average return. Needs two priced positions at least,
    or there is nothing to compare with."""
    if len(returns) < 2:
        return []
    average = sum(returns.values()) / len(returns)
    return [
        Scored(ticker, action, returns[ticker] - average)
        for ticker, action in trades
        if action in ("buy", "sell") and ticker in returns
    ]


def hit_rate(scored: list[Scored]) -> float | None:
    return sum(1 for s in scored if s.hit) / len(scored) if scored else None


def versus(returns: dict[str, float], trades: list[tuple[str, str]], bench: float) -> list[Scored]:
    """The same trades scored against a market index's return over the same days."""
    return [
        Scored(ticker, action, returns[ticker] - bench)
        for ticker, action in trades
        if action in ("buy", "sell") and ticker in returns
    ]


async def _return(
    pool: asyncpg.Pool, symbol: str, start: dt.date, end: dt.date, any_type: bool = False
) -> float | None:
    kind = "" if any_type else " and bar_type = 'adjusted'"
    row = await pool.fetchrow(
        "select (select close from public.price_bar where symbol = $1"  # noqa: S608
        f"{kind} and date >= $2 order by date limit 1) as a,"
        " (select close from public.price_bar where symbol = $1"
        f"{kind} and date <= $3 order by date desc limit 1) as b",
        symbol,
        start,
        end,
    )
    if row is None or row["a"] is None or row["b"] is None or row["a"] <= 0:
        return None
    return float(row["b"] / row["a"] - 1)


async def _symbols(pool: asyncpg.Pool, tickers: set[str]) -> dict[str, str]:
    rows = await pool.fetch(
        "select t212_ticker, price_symbol from public.instrument"
        " where t212_ticker = any($1) and price_symbol is not null",
        sorted(tickers),
    )
    return {r["t212_ticker"]: r["price_symbol"] for r in rows}


async def backtest(pool: asyncpg.Pool, now: dt.datetime) -> dict[str, Any]:
    """Score every saved plan old enough, newest first, and report the hit rate."""
    plans = await pool.fetch(
        "select id, as_of, created_at::date as saved, payload from public.saved_analysis"
        " where kind = 'rebalance_plan' and created_at < $1 and created_at > $2"
        " order by created_at desc",
        now - dt.timedelta(days=HORIZON_DAYS),
        now - dt.timedelta(days=LOOKBACK_DAYS),
    )
    scored: list[Scored] = []
    against_index: list[Scored] = []
    jobs = await load_active(pool, "jobs", JobsConfig)
    symbols_cfg = (jobs.body if jobs else JobsConfig()).prices.benchmark_symbols
    index = symbols_cfg[0] if symbols_cfg else None
    unpriced = 0
    for plan in plans:
        payload = plan["payload"]
        payload = json.loads(payload) if isinstance(payload, str) else payload
        result = payload.get("result", payload) if isinstance(payload, dict) else {}
        positions = [t for t in result.get("trades", []) if isinstance(t, dict)]
        symbols = await _symbols(pool, {str(t["ticker"]) for t in positions if "ticker" in t})
        start = plan["as_of"] or plan["saved"]
        end = start + dt.timedelta(days=HORIZON_DAYS)
        returns: dict[str, float] = {}
        for t in positions:
            symbol = symbols.get(str(t.get("ticker")))
            r = await _return(pool, symbol, start, end) if symbol else None
            if r is not None:
                returns[str(t["ticker"])] = r
        trades = [
            (str(t["ticker"]), str(t["action"]))
            for t in positions
            if t.get("action") in ("buy", "sell")
        ]
        done = score(returns, trades)
        unpriced += len(trades) - len(done)
        scored += done
        bench = await _return(pool, index, start, end, any_type=True) if index else None
        if bench is not None:
            against_index += versus(returns, trades, bench)
    rate = hit_rate(scored)
    index_rate = hit_rate(against_index)
    out: dict[str, Any] = {
        "plans": len(plans),
        "trades": len(scored),
        "unpriced": unpriced,
        "hit_rate": None if rate is None else round(rate, 3),
        "index_trades": len(against_index),
        "hit_rate_vs_index": None if index_rate is None else round(index_rate, 3),
    }
    if rate is not None and len(scored) >= MIN_TRADES and rate < WEAK_RATE:
        await tasks.see(
            pool,
            KIND,
            "rebalance",
            title="Saved rebalance plans mostly pointed the wrong way",
            detail=(
                f"{len(scored)} buys and sells from your saved plans were scored {HORIZON_DAYS} "
                f"days later against the plan's other positions: {rate:.0%} pointed the right "
                "way. A plan is a way to restore your own targets, not a forecast, but this is "
                "worth a look at the targets and bands."
            ),
            href="/scenarios",
            weight=0.2,
            status="needs_owner",
        )
        out["flagged"] = True
    return out
