"""One shape for every figure a research tool hands the model.

A figure is never a bare number: it says whether it exists (`status`), where it came from
(`source_id`, a registry id the answer cites), what it measures (`unit`, `currency`, `period`)
and how old it is (`as_of`). A figure that is missing, not applicable, stale or failed is
reported as such with a reason and `value: null`, never as zero, so the model cannot turn an
absence into a number.
"""

import datetime as dt
from collections.abc import Sequence
from decimal import Decimal
from typing import Any, Literal

from steward.research.untrusted import instruction_like, neutralise

Status = Literal["ok", "missing", "not_applicable", "stale", "failed"]
STATUSES: tuple[str, ...] = ("ok", "missing", "not_applicable", "stale", "failed")

# Returned rows are capped per call; a capped list says so and how to see the rest.
DEFAULT_LIMIT = 25
MAX_LIMIT = 100


def plain(value: Any) -> Any:
    """A JSON-friendly copy of a value: Decimals become floats, dates become ISO strings."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dt.datetime):
        return value.isoformat(timespec="minutes")
    if isinstance(value, dt.date):
        return value.isoformat()
    return value


def datum(
    value: Any,
    *,
    status: Status = "ok",
    source_id: str | None = None,
    as_of: dt.date | dt.datetime | str | None = None,
    unit: str | None = None,
    currency: str | None = None,
    period: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """One figure in the shared shape. A value is only kept when the status is `ok` or `stale`;
    a stale value stays visible because it is still the latest known, flagged as old."""
    keep = status in ("ok", "stale")
    out: dict[str, Any] = {"value": plain(value) if keep else None, "status": status}
    for key, item in (
        ("source_id", source_id),
        ("as_of", plain(as_of)),
        ("unit", unit),
        ("currency", currency),
        ("period", period),
        ("reason", reason),
    ):
        if item is not None:
            out[key] = item
    return out


def absent(
    status: Literal["missing", "not_applicable", "failed"],
    reason: str,
    **fields: Any,
) -> dict[str, Any]:
    """A figure that is not there, with the reason, in the same shape as a present one."""
    return datum(None, status=status, reason=reason, **fields)


def age_status(
    as_of: dt.date | dt.datetime | None, today: dt.date, max_age_days: int
) -> Literal["ok", "stale", "missing"]:
    """`missing` without a date, `stale` past `max_age_days`, else `ok`."""
    if as_of is None:
        return "missing"
    day = as_of.date() if isinstance(as_of, dt.datetime) else as_of
    return "stale" if (today - day).days > max_age_days else "ok"


def clamp_limit(limit: Any, default: int = DEFAULT_LIMIT, maximum: int = MAX_LIMIT) -> int:
    """A usable row cap: non-integers and values below 1 fall back to the default."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        return default
    return min(limit, maximum)


def page(rows: Sequence[Any], limit: int, total: int | None = None) -> dict[str, Any]:
    """The bounds of a returned list, so the model knows whether it saw everything. Without a
    total, a list that filled its limit may have been cut, so it is reported as truncated."""
    shown = len(rows)
    known_total = total if total is not None else shown
    return {
        "returned": shown,
        "limit": limit,
        "truncated": known_total > shown or (total is None and shown >= limit),
        **({"total": known_total} if total is not None else {}),
    }


def neutralise_tree(value: Any) -> Any:
    """A copy of a tool result in which each string that reads like an instruction to the model
    (stored news, filing or web text) has those sentences replaced by a marker. Other values,
    and strings that read normally, are returned untouched."""
    if isinstance(value, str):
        return neutralise(value) if instruction_like(value) else value
    if isinstance(value, dict):
        # Keys can hold stored text too (a sector or country name grouped on).
        return {neutralise_tree(k): neutralise_tree(v) for k, v in value.items()}
    if isinstance(value, list):
        return [neutralise_tree(v) for v in value]
    if isinstance(value, tuple):
        return tuple(neutralise_tree(v) for v in value)
    return value


def stamp(result: dict[str, Any], tool: str, now: dt.datetime) -> dict[str, Any]:
    """Adds the call's own provenance to a result: which tool produced it and when it ran.
    Errors and results that already carry a `retrieved_at` are left as they are."""
    if "error" in result or "retrieved_at" in result:
        return result
    return {**result, "tool": tool, "retrieved_at": now.isoformat(timespec="seconds")}
