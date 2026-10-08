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
from steward.journal import service as journal_service
from steward.research.sources import Registry
from steward.xray import service as xray_service

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
    definition = ThemeDefinition(
        name=f"{kind}: {value}",
        selectors=[ThemeSelector(kind=kind, value=value)],
    )
    result = theme_exposure(definition, xray_service.view_context(loaded))
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
