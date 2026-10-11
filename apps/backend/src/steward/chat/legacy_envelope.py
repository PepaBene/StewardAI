"""Puts the tools written before the shared envelope (`chat/envelope.py`) into it.

Those tools return their own shapes: bare numbers, an `unavailable` string when there is
nothing to compute. This module wraps the result once, after the tool ran, without touching the
tool: it adds `status` (and `reason` when nothing could be computed), `as_of` taken from the
tool's registry source, and turns each figure named in `FIGURES` into an envelope datum with its
unit, so a fraction, an amount and a count can no longer be confused and a missing figure stays
distinct from a zero. A field not named there is returned as it was.
"""

import datetime as dt
from typing import Any

from steward.chat import envelope as env
from steward.research.sources import Registry

# A path is "field" or "list[].field"; the value is (unit, name of the sibling field holding the
# currency, or None). Only units known from the tool's own code are declared here.
FIG = tuple[str, str | None]
FIGURES: dict[str, dict[str, FIG]] = {
    "stress_test": {
        "total_value": ("amount", "base_currency"),
        "lines[].pnl_pct": ("fraction", None),
        "lines[].pnl": ("amount", None),
        "lines[].unshocked_weight": ("fraction", None),
    },
    "risk_forecast": {
        "volatility_forecast": ("fraction_per_year", None),
        "volatility_forecast_if_left_out_were_average": ("fraction_per_year", None),
        "realized_volatility_last_year": ("fraction_per_year", None),
        "effective_bets": ("count", None),
        "diversification_ratio": ("ratio", None),
        "left_out_weight": ("fraction", None),
        "largest_risk_contributors[].risk_share": ("fraction", None),
        "largest_risk_contributors[].weight": ("fraction", None),
    },
    "past_episodes": {
        "drawdown_limit": ("fraction", None),
        "episodes[].loss": ("fraction", None),
        "episodes[].unavailable_weight": ("fraction", None),
    },
    "factor_tilts": {
        "r_squared": ("ratio", None),
        "weeks": ("weeks", None),
        "loadings[].beta": ("beta", None),
    },
    "model_check": {"weeks_tested": ("weeks", None)},
    "theme_exposure": {
        "exposed_weight": ("fraction", None),
        "direct_weight": ("fraction", None),
        "via_funds_weight": ("fraction", None),
        "unknown_weight": ("fraction", None),
        "upper_bound": ("fraction", None),
        "largest_issuers[].exposed": ("fraction", None),
        "largest_issuers[].direct": ("fraction", None),
        "largest_issuers[].via_funds": ("fraction", None),
    },
    "event_paths": {
        "reached_weight": ("fraction", None),
        "own_weight": ("fraction", None),
        "paths[].end_weight": ("fraction", None),
    },
    "fund_overlap": {
        "total_weight": ("fraction", None),
        "direct_weight": ("fraction", None),
        "via_funds_weight": ("fraction", None),
        "reached_twice_weight": ("fraction", None),
        "channels[].weight": ("fraction", None),
    },
    "performance_explain": {
        "gain": ("amount", "base_currency"),
        "residual": ("amount", "base_currency"),
        "net_external_flows": ("amount", "base_currency"),
        "largest_detractors[].total": ("amount", None),
        "largest_contributors[].total": ("amount", None),
    },
    "decision_scorecard": {"decisions": ("count", None)},
}

# Tools of the older shape that have no declared figures still get status and as_of.
LEGACY_TOOLS: frozenset[str] = frozenset(
    FIGURES
    | dict.fromkeys(
        (
            "position_risk",
            "idea_factor_fit",
            "risk_whatif",
            "start_size",
            "deposit_allocation",
            "rotation_candidates",
            "switch_effect",
            "over_cap_positions",
        )
    )
)


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _wrap(value: Any, unit: str, currency: str | None, source_id: str | None, as_of: Any) -> Any:
    if value is None:
        return env.absent("missing", "the source did not report this figure", unit=unit)
    if not _is_number(value):
        return value  # already a structure of the tool's own; left as it was
    return env.datum(value, unit=unit, currency=currency, source_id=source_id, as_of=as_of)


def _apply(node: dict[str, Any], path: str, fig: FIG, source_id: str | None, as_of: Any) -> None:
    unit, currency_field = fig
    head, _, rest = path.partition("[].")
    if not rest:
        if head in node:
            currency = node.get(currency_field) if currency_field else None
            node[head] = _wrap(
                node[head], unit, currency if isinstance(currency, str) else None, source_id, as_of
            )
        return
    rows = node.get(head)
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                _apply(row, rest, fig, source_id, as_of)


def wrap(
    tool: str, result: dict[str, Any], registry: Registry, today: dt.date | None = None
) -> dict[str, Any]:
    """The result in the shared envelope; errors and tools of the newer shape pass through."""
    if tool not in LEGACY_TOOLS or "error" in result or "status" in result:
        return result
    if "unavailable" in result:
        return {
            "status": "missing",
            "reason": str(result["unavailable"]),
            **{k: v for k, v in result.items() if k != "unavailable"},
        }
    source_id = result.get("source_id")
    source = registry.get(source_id) if isinstance(source_id, str) else None
    as_of = result.get("as_of") or (source.as_of if source else None)
    out = dict(result)
    status = "ok"
    if isinstance(as_of, str):
        try:
            age = env.age_status(dt.date.fromisoformat(as_of[:10]), today or dt.date.today(), 10)
            status = "stale" if age == "stale" else "ok"
        except ValueError:
            pass
    for path, fig in FIGURES.get(tool, {}).items():
        _apply(out, path, fig, source_id if isinstance(source_id, str) else None, as_of)
    out["status"] = status
    if as_of is not None:
        out["as_of"] = as_of
    elif status == "ok":
        out["as_of_note"] = "the tool did not report a data date"
    return out
