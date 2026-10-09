"""Chat tools that answer comparative questions with the app's deterministic analytics (M7.6).

Each result carries the method in words, what it covers and what it could not measure, and a
registry id for the page that shows the same figures. When an input is missing the tool says so
and returns no figure; the model is told to report that rather than estimate."""

from __future__ import annotations

from typing import Any

import asyncpg

from steward.decision import service as decision_service
from steward.decision.themes import METHOD as THEME_METHOD
from steward.decision.themes import ThemeDefinition, ThemeSelector, theme_exposure
from steward.events import feed as events_feed
from steward.events.paths import event_paths
from steward.journal import service as journal_service
from steward.lookthrough.read import UNCLASSIFIED
from steward.performance import service as performance_service
from steward.performance.settings import load_settings
from steward.research.sources import Registry
from steward.xray import reports as xray_reports
from steward.xray import service as xray_service
from steward.xray.views import build_view

SELECTOR_KINDS = [
    "sector", "industry", "country", "domicile", "listing", "currency", "revenue_country",
    "commodity", "trade_product", "issuer",
]  # fmt: skip

UNAVAILABLE = {"unavailable": "There is no portfolio snapshot yet, so nothing can be computed."}


async def conversation_owner(pool: asyncpg.Pool, conversation_id: int) -> str | None:
    value = await pool.fetchval(
        "select created_by::text from public.chat_conversation where id = $1", conversation_id
    )
    return str(value) if value else None


async def stress_test(pool: asyncpg.Pool, registry: Registry, owner_id: str) -> dict[str, Any]:
    """The named shocks (equity fall, rate rise, wider credit spreads, a fall in each foreign
    currency) applied to today's holdings."""
    try:
        table = await decision_service.run_standard(pool, owner_id)
    except decision_service.NoHoldings:
        return UNAVAILABLE
    sid = registry.add(
        "observation",
        f"stress:{table.as_of}",
        f"Stress test of {table.as_of}",
        app_path="/risk/stress",
        tier=1,
        snippet="; ".join(f"{line.name}: {line.pnl_pct:+.1%}" for line in table.lines)[:500],
        as_of=table.as_of,
    )
    return {
        "source_id": sid,
        "as_of": table.as_of,
        "base_currency": table.base_currency,
        "total_value": table.total_value,
        "formula": "Each holding moves by its sensitivity to the shock (measured beta where the "
        "history supports one, otherwise an owner value or the stated fallback); the parts "
        "reconcile to the shocked total.",
        "coverage": "unshocked_weight is the share of the portfolio with no valid sensitivity; "
        "it stays unchanged and is not counted as safe.",
        "lines": [
            {
                "name": line.name,
                "pnl_pct": line.pnl_pct,
                "pnl": line.pnl,
                "unshocked_weight": line.unshocked_weight,
            }
            for line in table.lines
        ],
        "notes": table.notes,
    }


async def theme(
    pool: asyncpg.Pool, registry: Registry, owner_id: str, kind: str, value: str
) -> dict[str, Any]:
    """How much of the portfolio sits in one sector, country, currency, revenue country,
    commodity, trade product or issuer, directly and through funds."""
    if kind not in SELECTOR_KINDS:
        return {"error": f"kind must be one of {', '.join(SELECTOR_KINDS)}"}
    loaded = await xray_service.load_cached(pool, owner_id)
    if loaded.data.portfolio is None:
        return UNAVAILABLE
    # A value the portfolio does not carry would read as 0 % exposure at "high" confidence; check
    # it against the labels the portfolio has, as the theme picker does.
    ctx = xray_service.view_context(loaded)
    labels = {
        b.key: b.label
        for b in build_view(kind, ctx).buckets  # type: ignore[arg-type]
        if b.kind == "classified" and b.key != UNCLASSIFIED
    }
    wanted = value.strip().casefold()
    match = next(
        (k for k, label in labels.items() if wanted in (str(k).casefold(), str(label).casefold())),
        None,
    )
    if match is None:
        sample = ", ".join(str(k) for k in list(labels)[:15])
        return {
            "error": f"The portfolio has no {kind} labelled {value!r}, so no exposure was "
            f"computed. Values it carries: {sample or 'none'}."
        }
    value = str(match)
    definition = ThemeDefinition(
        name=f"{kind}: {value}",
        selectors=[ThemeSelector(kind=kind, value=value)],
    )
    result = theme_exposure(definition, ctx)
    sid = registry.add(
        "observation",
        f"theme:{kind}:{value}",
        f"Exposure to {kind} {value}",
        app_path="/risk/themes",
        tier=1,
        snippet=f"known {result.exposed_weight:.1%}, upper bound {result.upper_bound:.1%}, "
        f"{result.confidence} confidence",
    )
    return {
        "source_id": sid,
        "theme": f"{kind}: {value}",
        "exposed_weight": result.exposed_weight,
        "direct_weight": result.direct_weight,
        "via_funds_weight": result.fund_weight,
        "unknown_weight": result.unknown.weight,
        "upper_bound": result.upper_bound,
        "confidence": result.confidence,
        "confidence_reason": result.confidence_reason,
        "largest_issuers": [
            {"issuer": i.issuer, "exposed": i.exposed, "direct": i.direct, "via_funds": i.via_funds}
            for i in result.issuers[:8]
        ],
        "formula": THEME_METHOD,
        "warnings": result.warnings,
    }


async def event_reach(
    pool: asyncpg.Pool,
    registry: Registry,
    owner_id: str,
    symbol: str,
    direction: str,
    state: str,
) -> dict[str, Any]:
    """Where an event at one company reaches the portfolio along the supply-chain links."""
    if direction not in ("downstream", "upstream"):
        return {"error": "direction must be downstream or upstream"}
    if state not in ("confirmed", "reported", "rumoured"):
        return {"error": "state must be confirmed, reported or rumoured"}
    chain, indicators = await events_feed.chain_and_indicators(pool, owner_id)
    wanted = symbol.split("_")[0].strip().upper()
    if not wanted:
        return {"error": "give a company symbol, e.g. TSM"}
    impact = event_paths(chain, wanted, direction, state, indicators)  # type: ignore[arg-type]
    sid = registry.add(
        "observation",
        f"event_paths:{wanted}:{direction}:{state}",
        f"Event at {wanted}, {direction}",
        app_path="/xray/supply-chain",
        tier=1,
        snippet=f"{len(impact.paths)} path(s), reaching {impact.reached_weight:.1%} of holdings",
    )
    return {
        "source_id": sid,
        "symbol": wanted,
        "direction": direction,
        "state": state,
        "reached_weight": impact.reached_weight,
        "own_weight": impact.own_weight,
        "paths": [
            {
                "start": p.start,
                "links": [f"{e.supplier} supplies {e.customer} ({e.product})" for e in p.edges],
                "end": p.end,
                "end_weight": p.weight,
                "confidence": p.confidence,
                "severity": p.severity,
                "revenue_share": p.revenue_share,
                "indicators": p.indicators,
            }
            for p in impact.paths[:8]
        ],
        "notes": impact.notes,
        "formula": impact.method,
    }


async def fund_overlap(
    pool: asyncpg.Pool, registry: Registry, owner_id: str, ticker: str
) -> dict[str, Any]:
    """Which held funds also hold a stock, and how much of the portfolio reaches it twice."""
    loaded = await xray_service.load_cached(pool, owner_id)
    if loaded.data.portfolio is None:
        return UNAVAILABLE
    wanted = ticker.split("_")[0].strip().upper()
    if not wanted:
        return {"error": "give a ticker, e.g. AAPL or AAPL_US_EQ"}
    report = xray_reports.intersection_response(loaded)
    hits = [
        i
        for i in report.issuers
        if any((s.ticker or "").split("_")[0].upper() == wanted for s in i.securities)
        or i.label.upper() == wanted
    ]
    if not hits:
        return {
            "ticker": wanted,
            "found": False,
            "note": "The ticker is not among the largest issuers the overlap report lists "
            "(it covers the top 100) or no held fund reports it.",
            "funds_looked_through": len(report.funds),
        }
    issuer = hits[0]
    sid = registry.add(
        "observation",
        f"overlap:{wanted}",
        f"Overlap in {issuer.label}",
        app_path="/xray/overlap",
        tier=1,
        snippet=f"{issuer.weight:.2%} of the portfolio, {issuer.overlap_weight:.2%} reached twice",
        as_of=report.as_of,
    )
    return {
        "source_id": sid,
        "ticker": wanted,
        "found": True,
        "issuer": issuer.label,
        "total_weight": issuer.weight,
        "direct_weight": issuer.direct_weight,
        "via_funds_weight": issuer.fund_weight,
        "reached_twice_weight": issuer.overlap_weight,
        "channels": [
            {"kind": c.kind, "label": c.label, "weight": c.weight} for c in issuer.channels
        ],
        "formula": "Securities of one issuer are merged. Weight is direct holding plus each "
        "fund's holding of it times the fund's share of the portfolio; overlap is the weight "
        "beyond the largest single channel.",
        "coverage": f"{len(report.funds)} funds looked through; weight of funds not looked "
        "through is in the unknown bucket and is not counted here.",
    }


async def performance_explain(
    pool: asyncpg.Pool, registry: Registry, owner_id: str, days: int = 365
) -> dict[str, Any]:
    """Where the gain, or the shortfall against the benchmark, came from over the last `days`."""
    settings, _ = await load_settings(pool, owner_id)
    report = await performance_service.attribution_report(pool, settings, max(7, min(days, 3650)))
    if report.status == "unavailable" or report.bridge is None:
        return {"unavailable": report.gap or "attribution is not available"}
    ranked = sorted(report.holdings, key=lambda h: h.total)
    sid = registry.add(
        "observation",
        f"attribution:{report.end}:{report.days}",
        f"Performance attribution to {report.end}",
        app_path="/performance/attribution",
        tier=1,
        snippet=f"gain {report.bridge.gain.value}; residual {report.bridge.residual}",
        as_of=str(report.end),
    )
    out: dict[str, Any] = {
        "source_id": sid,
        "start": str(report.start),
        "end": str(report.end),
        "base_currency": report.base_currency,
        "gain": report.bridge.gain.value,
        "components": report.bridge.components,
        "residual": report.bridge.residual,
        "net_external_flows": report.bridge.net_external_flows,
        "unattributed_tickers": report.bridge.unattributed_tickers,
        "largest_detractors": [
            {"ticker": h.ticker, "name": h.name, "total": h.total}
            for h in ranked[:5]
            if h.total < 0
        ],
        "largest_contributors": [
            {"ticker": h.ticker, "name": h.name, "total": h.total}
            for h in reversed(ranked[-5:])
            if h.total > 0
        ],
        "formula": report.explain.formula,
    }
    rel = report.benchmark
    if rel is None:
        out["versus_benchmark"] = "No benchmark comparison is available for these dates."
    else:
        out["versus_benchmark"] = {
            "benchmark": rel.label,
            "benchmark_return": rel.benchmark_return,
            "active_total": rel.active_total,
            "like_for_like": rel.like_for_like,
            "notes": rel.notes,
        }
    return out


async def decision_scorecard(
    pool: asyncpg.Pool, registry: Registry, owner_id: str
) -> dict[str, Any]:
    """How the owner's recorded decisions have done, with sample sizes."""
    card = await journal_service.scorecard(pool, owner_id)
    sid = registry.add(
        "observation",
        f"scorecard:{card.until}",
        f"Decision scorecard to {card.until}",
        app_path="/journal/scorecard",
        tier=1,
        snippet=f"{card.entries} decisions since {card.since}",
        as_of=str(card.until),
    )
    return {
        "source_id": sid,
        "since": str(card.since),
        "until": str(card.until),
        "decisions": card.entries,
        "method": card.method,
        "sections": [
            {
                "title": s.title,
                "status": s.status,
                "decisions": s.n,
                "note": s.note,
                "rows": [r.model_dump(exclude_none=True) for r in s.rows],
            }
            for s in card.sections
        ],
        "suggestions": card.suggestions,
    }
