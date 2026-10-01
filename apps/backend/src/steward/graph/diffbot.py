"""Diffbot Knowledge Graph organization relationships with explicit per-field direction.

Each Diffbot organization field states who did what to whom, and the graph edge type names the
*subject's* role, so a blanket keyword match ("acqui" -> `acquired`) gets half the fields
backwards. The table below fixes the meaning of every field once:

===============  ==========  ========  ==========================================
Diffbot field    edge_type   direction  reads as
===============  ==========  ========  ==========================================
parentCompany    parent      outgoing  holding's (accounting) parent is the object
subsidiaries     subsidiary  outgoing  object is a subsidiary of the holding
acquisitions     acquirer    outgoing  holding acquired the object
acquiredBy       acquired    incoming  holding was acquired by the object
investments      investor    outgoing  holding invested in the object
investors        investment  incoming  object invested in the holding
competitors      competitor  symmetric
partnerships     partner     symmetric
customers        customer    outgoing  holding sells to the object
suppliers        supplier    incoming  holding buys from the object
products         product     outgoing
ceo              executive   outgoing
===============  ==========  ========  ==========================================

Dates belong to each relationship (a Diffbot date object `{"str": "d2019-03-01", "precision":
3}` or an ISO string); a relationship with no date has no `valid_from`: the fetch date is never
substituted. Every edge is stored as `suggested` from a vendor source.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

FIELD_MAP: dict[str, tuple[str, str]] = {
    "parentCompany": ("parent", "outgoing"),
    "subsidiaries": ("subsidiary", "outgoing"),
    "acquisitions": ("acquirer", "outgoing"),
    "acquiredBy": ("acquired", "incoming"),
    "investments": ("investor", "outgoing"),
    "investors": ("investment", "incoming"),
    "competitors": ("competitor", "symmetric"),
    "partnerships": ("partner", "symmetric"),
    "customers": ("customer", "outgoing"),
    "suppliers": ("supplier", "incoming"),
    "products": ("product", "outgoing"),
    "ceo": ("executive", "outgoing"),
}
_ENTITY_KEYS = (
    "acquiree",
    "acquirer",
    "investor",
    "organization",
    "target",
    "company",
    "entity",
    "object",
)
_START_KEYS = ("startDate", "date", "acquisitionDate", "announcedDate", "fromDate")
_END_KEYS = ("endDate", "toDate")


@dataclass(frozen=True, slots=True)
class DiffbotRelation:
    provider_field: str
    edge_type: str
    direction: str
    object_key: str
    object_name: str
    valid_from: dt.date | None
    valid_to: dt.date | None
    detail: dict[str, object] = field(default_factory=dict)


def parse_diffbot_date(value: object) -> tuple[dt.date | None, int | None]:
    """(date, precision) from a Diffbot date object or ISO-like string; None when unreadable.
    Precision follows Diffbot: 1 year, 2 month, 3 day."""
    precision: int | None = None
    text: str | None = None
    if isinstance(value, dict):
        raw_precision = value.get("precision")
        precision = raw_precision if isinstance(raw_precision, int) else None
        raw = value.get("str")
        text = raw if isinstance(raw, str) else None
        if text is None and isinstance(value.get("timestamp"), (int, float)):
            try:
                stamp = dt.datetime.fromtimestamp(float(value["timestamp"]) / 1000, dt.UTC)
            except (OverflowError, OSError, ValueError):
                return None, precision
            return stamp.date(), precision or 3
    elif isinstance(value, str):
        text = value
    if not text:
        return None, precision
    match = re.match(r"^d?(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", text.strip())
    if not match:
        return None, precision
    year, month, day = match.groups()
    try:
        parsed = dt.date(int(year), int(month or 1), int(day or 1))
    except ValueError:
        return None, precision
    return parsed, precision or (3 if day else 2 if month else 1)


def _entity_ref(item: object) -> dict[str, object] | None:
    if not isinstance(item, dict):
        return None
    for key in _ENTITY_KEYS:
        nested = item.get(key)
        if isinstance(nested, dict) and nested.get("name"):
            return nested
    return item if item.get("name") else None


def _first_date(
    item: dict[str, object], keys: tuple[str, ...]
) -> tuple[dt.date | None, int | None]:
    for key in keys:
        if key in item:
            parsed = parse_diffbot_date(item[key])
            if parsed[0] is not None:
                return parsed
    return None, None


def parse_diffbot_relations(entity: dict[str, object]) -> list[DiffbotRelation]:
    """Relationships of one Diffbot Organization entity, by field, with their own dates."""
    out: list[DiffbotRelation] = []
    for provider_field, (edge_type, direction) in FIELD_MAP.items():
        raw = entity.get(provider_field)
        if raw is None:
            continue
        for item in raw if isinstance(raw, list) else [raw]:
            ref = _entity_ref(item)
            if ref is None:
                continue
            start, start_precision = (
                _first_date(item, _START_KEYS) if isinstance(item, dict) else (None, None)
            )
            end, _ = _first_date(item, _END_KEYS) if isinstance(item, dict) else (None, None)
            if start and end and end < start:
                end = None
            object_id = str(ref.get("diffbotUri") or ref.get("id") or ref["name"])
            detail: dict[str, object] = {"provider_field": provider_field}
            if start_precision is not None:
                detail["date_precision"] = start_precision
            for extra in ("price", "currency", "status"):
                if isinstance(item, dict) and extra in item:
                    detail[extra] = item[extra]
            out.append(
                DiffbotRelation(
                    provider_field,
                    edge_type,
                    direction,
                    object_id,
                    str(ref["name"]),
                    start,
                    end,
                    detail,
                )
            )
    return out
