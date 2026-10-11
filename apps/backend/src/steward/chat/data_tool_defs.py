"""Model-facing definitions of the tools in `data_tools` (name, description, input schema)."""

from typing import Any

from steward.chat.data_tools import ACTION_TYPES, SEGMENT_KINDS, STATEMENTS


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": properties,
            **({"required": required} if required else {}),
            "additionalProperties": False,
        },
    }


_TICKER = {"type": "string", "description": "e.g. AAPL_US_EQ"}
_LIMIT = {"type": "integer", "description": "Rows to return (bounded)."}

DATA_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    _tool(
        "fx_rates",
        "Stored ECB and CNB exchange-rate fixings for one currency (home currency per one "
        "unit), newest first, with the date of each. Use it when a result depends on currency.",
        {
            "currency": {"type": "string", "description": "Three-letter code, e.g. USD."},
            "days": {"type": "integer", "description": "Look-back days, 1-365 (default 30)."},
            "limit": _LIMIT,
        },
        ["currency"],
    ),
    _tool(
        "latest_quote",
        "The most recent stored quote (intraday or session close) of one covered ticker with "
        "its time, previous close and change; says when it is stale or absent.",
        {"ticker": _TICKER},
        ["ticker"],
    ),
    _tool(
        "listing_liquidity",
        "How liquid a covered ticker's listing is: the regulatory liquid flag and average "
        "daily turnover, and the average daily volume over the last stored bars.",
        {"ticker": _TICKER},
        ["ticker"],
    ),
    _tool(
        "statement_lines",
        "Raw filed statement lines (as published, with unit and period) for one ticker, "
        f"optionally one statement ({', '.join(STATEMENTS)}) or concepts containing a text.",
        {
            "ticker": _TICKER,
            "statement": {"type": "string", "enum": list(STATEMENTS)},
            "concept": {"type": "string", "description": "Part of a concept name."},
            "limit": _LIMIT,
        },
        ["ticker"],
    ),
    _tool(
        "share_counts",
        "Shares outstanding history for one ticker from its filings, with the method and date.",
        {"ticker": _TICKER},
        ["ticker"],
    ),
    _tool(
        "segment_revenue",
        "Revenue by segment, product or geography from the parsed filing of one ticker, "
        f"latest period first. Optional kind: {', '.join(SEGMENT_KINDS)}.",
        {
            "ticker": _TICKER,
            "kind": {"type": "string", "enum": list(SEGMENT_KINDS)},
            "limit": _LIMIT,
        },
        ["ticker"],
    ),
    _tool(
        "corporate_actions",
        "Stored corporate actions of one ticker by ex-date: "
        f"{', '.join(ACTION_TYPES)}, with ratio or cash amount and status.",
        {"ticker": _TICKER, "days": {"type": "integer"}, "limit": _LIMIT},
        ["ticker"],
    ),
    _tool(
        "peers",
        "The stored peer companies of one ticker and how each was chosen (confirmed or suggested).",
        {"ticker": _TICKER},
        ["ticker"],
    ),
    _tool(
        "etf_breakdown",
        "The latest stored holdings snapshot of an ETF by ISIN: top holdings and the weights "
        "by sector and country.",
        {"fund_isin": {"type": "string"}, "limit": _LIMIT},
        ["fund_isin"],
    ),
    _tool(
        "fund_nav",
        "Stored net asset value points of a fund by ISIN (owner-entered or issuer-synced), "
        "newest first.",
        {"fund_isin": {"type": "string"}, "limit": _LIMIT},
        ["fund_isin"],
    ),
    _tool(
        "coverage_review",
        "The data-coverage review queue: datasets and entities a provider does not cover, "
        "has blocked or failed to fetch. Use it to explain why a figure is missing.",
        {"limit": _LIMIT},
    ),
]
