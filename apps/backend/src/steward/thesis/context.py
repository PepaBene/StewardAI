"""What the app already holds about a holding and the owner's book, as text for the thesis author.

The research report reaches the author as cited sentences only, and the tool evidence behind it
is cut to a few thousand characters a section. So a thesis could be written without the saved
valuation scenarios, the earlier memos, the owner's notes, the rest of the portfolio or the
strategy's rules. This block hands the author those records directly, through the same typed
read tools chat answers rest on, each compacted and capped. A tool that fails or has nothing is
named as such and never stops the thesis.
"""

import asyncio
import json
import logging
from collections.abc import Iterable
from typing import Any

import asyncpg

from steward.chat.envelope import neutralise_tree
from steward.chat.owner import owner_of
from steward.chat.tools import ChatTools
from steward.earnings.periods import period_label
from steward.earnings.verdicts import FactRow
from steward.research.sources import Registry

log = logging.getLogger(__name__)

CALL_TIMEOUT_SECONDS = 30.0
MAX_TOOL_CHARS = 2500
MAX_CONTEXT_CHARS = 24000
LATEST_METRICS = 40
# Envelope keys that carry ids and provenance for chat's citations, not facts for the author.
DROP_KEYS = frozenset(
    {"source_id", "source_ids", "sources", "freshness", "envelope", "url", "urls"}
)


def context_calls(ticker: str, name: str) -> list[tuple[str, dict[str, Any]]]:
    """The reads, in the order the author should weigh them."""
    return [
        ("holding", {"ticker": ticker}),
        ("valuation_scenarios", {"ticker": ticker}),
        ("thesis_memos", {"ticker": ticker}),
        ("holding_notes", {"query": name, "ticker": ticker, "limit": 5}),
        ("portfolio_overview", {}),
        ("over_cap_positions", {}),
        ("filing_evidence", {"ticker": ticker, "section": "institutional", "limit": 5}),
        ("filing_evidence", {"ticker": ticker, "section": "insider", "limit": 5}),
        ("filing_evidence", {"ticker": ticker, "section": "consensus", "limit": 5}),
    ]


def compact(value: Any) -> Any:
    """`value` without citation plumbing and empty fields, so the cap keeps the facts."""
    if isinstance(value, dict):
        out = {k: compact(v) for k, v in value.items() if k not in DROP_KEYS}
        return {k: v for k, v in out.items() if v not in (None, "", [], {})}
    if isinstance(value, list):
        return [compact(v) for v in value]
    return value


def tool_block(tool: str, result: dict[str, Any]) -> str:
    if result.get("error"):
        return f"### {tool}\nnot available: {str(result['error'])[:200]}"
    text = json.dumps(compact(neutralise_tree(result)), default=str, separators=(",", ":"))
    if len(text) > MAX_TOOL_CHARS:
        text = text[:MAX_TOOL_CHARS] + "…(cut)"
    return f"### {tool}\n{text}"


async def app_context(pool: asyncpg.Pool, ticker: str, name: str) -> str:
    """The app's records about `ticker` and the owner's book, one block per read tool."""
    tools = ChatTools(pool, Registry(), None, None, owner_id=await owner_of(pool))
    blocks: list[str] = []
    for tool, args in context_calls(ticker, name):
        try:
            result = await asyncio.wait_for(tools.call(tool, args), CALL_TIMEOUT_SECONDS)
        except Exception as exc:
            log.warning("thesis context %s for %s failed: %s", tool, ticker, exc)
            result = {"error": type(exc).__name__}
        label = f"{tool} {args['section']}" if "section" in args else tool
        blocks.append(tool_block(label, result))
    return "\n\n".join(blocks)[:MAX_CONTEXT_CHARS]


def latest_figures(facts: Iterable[FactRow]) -> list[str]:
    """The newest verified value of each metric: what a KPI's limit is set against."""
    newest: dict[tuple[str, str], FactRow] = {}
    for f in facts:
        key = (f.metric, f.basis)
        seen = newest.get(key)
        if seen is None or (f.fiscal_year, _order(f.fiscal_period)) > (
            seen.fiscal_year,
            _order(seen.fiscal_period),
        ):
            newest[key] = f
    rows = sorted(newest.values(), key=lambda f: (f.metric, f.basis))[:LATEST_METRICS]
    return [
        f"{f.metric} ({f.basis}): {f.value_units.normalize():f} {_unit(f)}"
        f" in {period_label(f.fiscal_year, f.fiscal_period)}"
        for f in rows
    ]


def _unit(f: FactRow) -> str:
    """'USD' for an amount, 'USD per share' for EPS, the stored unit otherwise."""
    if f.unit == "currency":
        return f.currency or "currency"
    if f.unit == "per_share":
        return f"{f.currency} per share" if f.currency else "per share"
    return f.unit


def _order(period: str) -> int:
    """Quarters before the full year within one fiscal year."""
    return {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4, "H1": 2, "H2": 4, "FY": 5}.get(period, 0)
