"""Proactive suggestions (E3): when positions have drifted outside their bands, say what to
trade, how much and why, ready to act on.

The plan is the rebalance planner's own (`decision/rebalance.py`, mode `to_band`: each position
that is out of band moves to the nearest band edge, so it trades as little as it can). Nothing
here is a model's opinion and nothing is sent to the broker; the suggestion is one line in
"Needs you" that clears when the portfolio is back inside its bands, and the trade stays yours.
"""

import logging
from typing import Any

import asyncpg

from steward.autopilot import ledger
from steward.decision import service as decision
from steward.decision.rebalance import RebalancePlan, RebalanceRequest, Trade
from steward.earnings.verdicts import NEWEST_PERIOD_FIRST, in_active_thesis
from steward.ops import tasks
from steward.settings import get_settings

logger = logging.getLogger("steward.ops")

MAX_LINES = 6
DETAIL_CAP = 1200
SUBJECT = "rebalance"


def _line(t: Trade, currency: str) -> str:
    verb = "Sell" if t.action == "sell" else "Buy"
    name = t.name or t.ticker
    return (
        f"{verb} {name} for about {abs(t.trade_value):,.0f} {currency} "
        f"({t.weight_now:.1%} to {t.weight_after:.1%} of the portfolio): {t.reason}"
    )


def describe(plan: RebalancePlan, currency: str) -> tuple[str, str] | None:
    """(title, detail) for the trades the plan wants, or None when no position needs to move."""
    trades = sorted(
        (t for t in plan.trades if t.action != "hold" and abs(t.trade_value) > 0),
        key=lambda t: -abs(t.trade_value),
    )
    if not trades:
        return None
    sells = sum(1 for t in trades if t.action == "sell")
    buys = len(trades) - sells
    parts = [f"{n} {word}" for n, word in ((sells, "to sell"), (buys, "to buy")) if n]
    title = f"Positions have drifted outside their bands: {' and '.join(parts)}"
    lines = [_line(t, currency) for t in trades[:MAX_LINES]]
    if len(trades) > MAX_LINES:
        lines.append(f"{len(trades) - MAX_LINES} smaller trades are on the rebalance page.")
    lines.append(
        f"Estimated costs {plan.costs:,.0f} {currency}. Nothing is sent to your broker: check the "
        "tax lots on the rebalance page and place the trades yourself."
    )
    return title, " ".join(lines)[:DETAIL_CAP]


def describe_break(
    ticker: str, weight: float, breaches: list[tuple[str, str, str]]
) -> tuple[str, str]:
    """(title, detail) for a held stock whose thesis KPIs are breached. Each breach is
    (metric, period, rationale), the verdict job's own words."""
    n = len(breaches)
    title = f"{ticker}: {n} thesis KPI{'s' if n != 1 else ''} breached"
    lines = [f"{metric} ({period}): {why}" for metric, period, why in breaches[:4]]
    lines.append(
        f"It is {weight:.1%} of the portfolio. Re-read the thesis and decide whether to hold, "
        "trim or exit; the exit conditions you wrote are on the thesis page."
    )
    return title, " ".join(lines)[:DETAIL_CAP]


async def suggest_thesis_breaks(pool: asyncpg.Pool) -> dict[str, Any]:
    """One notice per held stock whose latest verdict on any KPI is a breach. It clears when the
    verdict job later passes the KPI, or the position is sold."""
    weights = await ledger.holding_weights(pool)
    rows = await pool.fetch(
        "select ticker, metric, coalesce(period, kpi_period) as period, rationale from ("  # noqa: S608
        " select distinct on (ticker, kpi_id) ticker, metric, period, kpi_period, status,"
        f"  rationale from public.verdict where {in_active_thesis('verdict')}"
        f" order by ticker, kpi_id, {NEWEST_PERIOD_FIRST}) latest"
        " where status = 'breach' and ticker = any($1::text[])",
        list(weights),
    )
    by_ticker: dict[str, list[tuple[str, str, str]]] = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], []).append((r["metric"], r["period"], r["rationale"]))
    for ticker, breaches in by_ticker.items():
        title, detail = describe_break(ticker, weights[ticker], breaches)
        await tasks.see(
            pool,
            "thesis_break",
            ticker,
            title=title,
            detail=detail,
            href="/theses",
            weight=min(1.0, 0.5 + weights[ticker]),
            status="needs_owner",
        )
    return {
        "breached": sorted(by_ticker),
        "resolved": await tasks.settle(pool, "thesis_break", set(by_ticker)),
    }


async def suggest_rebalance(pool: asyncpg.Pool) -> dict[str, Any]:
    owner_id = get_settings().owner_user_id
    if not owner_id:
        return {"skipped": "OWNER_USER_ID is not set"}
    try:
        strategy = await decision.load_strategy(pool)
        result = await decision.run_rebalance(pool, owner_id, RebalanceRequest(mode="to_band"))
    except (decision.NoStrategy, decision.NoHoldings) as exc:
        return {"skipped": type(exc).__name__}
    said = describe(result.plan, strategy.base_currency)
    if said is None:
        return {"trades": 0, "resolved": await tasks.settle(pool, "recommendation", set())}
    title, detail = said
    await tasks.see(
        pool,
        "recommendation",
        SUBJECT,
        title=title,
        detail=detail,
        href="/rebalance",
        weight=0.9,
        status="needs_owner",
    )
    return {"trades": sum(1 for t in result.plan.trades if t.action != "hold")}
