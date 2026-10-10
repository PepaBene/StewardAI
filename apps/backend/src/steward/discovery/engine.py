"""The screening engine shared by stock and fund screens (plan M1.2, M1.3, M1.5).

Pure: takes already-loaded records, applies validated filters and accounts for every member of
the universe. Each result row carries a per-filter check ("why did this pass or fail?"). A
metric that is unknown for a record is never read as passing or as zero: with `missing="exclude"`
the record is left out and counted under *unknown*, with `keep_unknown` it stays and is marked.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from steward.analytics.contract import input_hash
from steward.analytics.versions import version
from steward.discovery.metrics import DiscoverFilter, MetricDef, MetricValue

Missing = Literal["exclude", "keep_unknown"]
Outcome = Literal["pass", "fail", "unknown"]
PAGE_MAX = 200


@dataclass
class Record:
    key: str
    name: str | None
    identity: dict[str, str | None] = field(default_factory=dict)
    values: dict[str, MetricValue] = field(default_factory=dict)
    # Why the record has no metrics at all (e.g. no stored statements); None when it has some.
    no_data_reason: str | None = None


class Check(BaseModel):
    metric: str
    op: str
    threshold: Decimal | list[str]
    actual: Decimal | str | None
    outcome: Outcome
    reason: str | None = None


class ResultRow(BaseModel):
    key: str
    name: str | None
    identity: dict[str, str | None]
    values: dict[str, MetricValue]
    checks: list[Check]
    unknown: list[str]
    notes: list[str] = Field(
        default_factory=list, description="Extra why-it-passed lines, e.g. the idea-pipeline gate."
    )


class DiscoverExcluded(BaseModel):
    reason: str
    count: int
    keys: list[str]


class FilterCoverage(BaseModel):
    metric: str
    label: str
    known: int
    total: int
    fraction: Decimal | None


class DiscoveryResult(BaseModel):
    kind: Literal["stock", "fund"]
    method_version: str
    definition_hash: str
    result_hash: str
    as_of: dt.date
    universe_key: str
    universe_label: str
    universe_definition: str
    universe_size: int
    evaluated: int = Field(description="Records with at least one metric value.")
    matched: int
    failed_filter: int
    unknown_excluded: int = Field(description="Left out because a filtered metric had no value.")
    no_data: int = Field(description="Records with no data to test at all.")
    kept_unknown: int
    missing_treatment: Missing
    filters: list[DiscoverFilter]
    filter_coverage: list[FilterCoverage]
    excluded: list[DiscoverExcluded]
    rows: list[ResultRow]
    next_cursor: str | None
    formula_definitions: dict[str, str]
    warnings: list[str]
    caveats: list[str]


def _check(f: DiscoverFilter, value: MetricValue | None) -> Check:
    actual = None if value is None else value.value
    base = {"metric": f.metric, "op": f.op, "threshold": f.value, "actual": actual}
    if actual is None:
        reason = value.reason if value and value.reason else "no value"
        return Check(**base, outcome="unknown", reason=reason)
    if f.op == "in":
        allowed = {str(x).upper() for x in (f.value if isinstance(f.value, list) else [])}
        ok = str(actual).upper() in allowed
    else:
        assert isinstance(f.value, Decimal) and isinstance(actual, Decimal)
        ok = actual >= f.value if f.op == "gte" else actual <= f.value
    return Check(**base, outcome="pass" if ok else "fail")


def definition_hash(
    kind: str,
    filters: Sequence[DiscoverFilter],
    missing: str,
    sort_by: str | None,
    universe_key: str,
) -> str:
    return input_hash(
        {
            "kind": kind,
            "filters": [f.model_dump(mode="json") for f in filters],
            "missing": missing,
            "sort_by": sort_by,
            "universe": universe_key,
            "method": version("screen"),
        }
    )


def _sort_key(metric_id: str, row: ResultRow) -> tuple[int, Decimal | str]:
    v = row.values.get(metric_id)
    raw = v.value if v else None
    if raw is None:
        return (1, "")  # unknown sorts last in either direction
    return (0, raw)


def encode_cursor(offset: int, result_hash: str) -> str:
    return base64.urlsafe_b64encode(json.dumps([offset, result_hash]).encode()).decode()


def decode_cursor(cursor: str | None, result_hash: str) -> tuple[int, str | None]:
    """(offset, warning). A cursor from another result set restarts at the top, with a warning."""
    if not cursor:
        return 0, None
    try:
        offset, h = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        offset = int(offset)
    except (ValueError, TypeError):
        return 0, "unreadable cursor ignored"
    if h != result_hash:
        return 0, "the underlying result changed since this cursor was issued; restarted at the top"
    return max(offset, 0), None


def run_discovery(
    records: Sequence[Record],
    catalogue: Mapping[str, MetricDef],
    filters: Sequence[DiscoverFilter],
    *,
    kind: Literal["stock", "fund"],
    as_of: dt.date,
    universe_key: str,
    universe_label: str,
    universe_definition: str,
    missing: Missing = "exclude",
    sort_by: str | None = None,
    descending: bool | None = None,
    limit: int = 50,
    cursor: str | None = None,
    caveats: Sequence[str] = (),
    gate: Mapping[str, Sequence[str]] | None = None,
    gate_label: str = "gate",
    notes: Mapping[str, Sequence[str]] | None = None,
) -> DiscoveryResult:
    """`gate`: key -> reasons the record fails an extra, non-metric rule (the idea pipeline);
    an empty or absent entry passes. `notes`: key -> lines shown with a matched row."""
    limit = max(1, min(limit, PAGE_MAX))
    matched: list[ResultRow] = []
    reasons: dict[str, list[str]] = {}
    failed = unknown_excluded = no_data = kept_unknown = evaluated = 0

    def exclude(reason: str, key: str) -> None:
        reasons.setdefault(reason, []).append(key)

    for rec in sorted(records, key=lambda r: r.key):
        if rec.no_data_reason:
            no_data += 1
            exclude(rec.no_data_reason, rec.key)
            continue
        evaluated += 1
        checks = [_check(f, rec.values.get(f.metric)) for f in filters]
        failing = next((c for c in checks if c.outcome == "fail"), None)
        if failing:
            failed += 1
            label = (
                catalogue[failing.metric].label if failing.metric in catalogue else failing.metric
            )
            exclude(f"Failed the {label.lower()} filter", rec.key)
            continue
        unknown = [c.metric for c in checks if c.outcome == "unknown"]
        if unknown and missing == "exclude":
            unknown_excluded += 1
            first = next(c for c in checks if c.outcome == "unknown")
            label = catalogue[first.metric].label if first.metric in catalogue else first.metric
            exclude(f"{label}: {first.reason or 'no value'}", rec.key)
            continue
        if gate is not None and gate.get(rec.key):
            failed += 1
            exclude(f"{gate_label}: {gate[rec.key][0]}", rec.key)
            continue
        if unknown:
            kept_unknown += 1
        matched.append(
            ResultRow(
                key=rec.key,
                name=rec.name,
                identity=rec.identity,
                values=rec.values,
                checks=checks,
                unknown=unknown,
                notes=list((notes or {}).get(rec.key, ())),
            )
        )

    key_metric = sort_by or (filters[0].metric if filters else None)
    if key_metric is not None:
        meta = catalogue.get(key_metric)
        desc = (
            descending
            if descending is not None
            else (True if meta is None or meta.higher_is_better is None else meta.higher_is_better)
        )
        known = sorted(
            (r for r in matched if _sort_key(key_metric, r)[0] == 0),
            key=lambda r: (_sort_key(key_metric, r)[1], r.key),
            reverse=desc,
        )
        matched = known + [r for r in matched if _sort_key(key_metric, r)[0] == 1]

    coverage = [
        FilterCoverage(
            metric=f.metric,
            label=catalogue[f.metric].label if f.metric in catalogue else f.metric,
            known=(
                known_n := sum(
                    1 for r in records if (v := r.values.get(f.metric)) and v.value is not None
                )
            ),
            total=len(records),
            fraction=None if not records else Decimal(known_n) / Decimal(len(records)),
        )
        for f in filters
    ]
    dh = definition_hash(kind, filters, missing, sort_by, universe_key)
    rh = input_hash({"def": dh, "as_of": as_of, "keys": [r.key for r in matched]})
    offset, cursor_warning = decode_cursor(cursor, rh)
    page = matched[offset : offset + limit]
    nxt = encode_cursor(offset + limit, rh) if offset + limit < len(matched) else None
    counts = Counter({k: len(v) for k, v in reasons.items()})
    used = {f.metric for f in filters} | ({key_metric} if key_metric else set())
    return DiscoveryResult(
        kind=kind,
        method_version=version("screen"),
        definition_hash=dh,
        result_hash=rh,
        as_of=as_of,
        universe_key=universe_key,
        universe_label=universe_label,
        universe_definition=universe_definition,
        universe_size=len(records),
        evaluated=evaluated,
        matched=len(matched),
        failed_filter=failed,
        unknown_excluded=unknown_excluded,
        no_data=no_data,
        kept_unknown=kept_unknown,
        missing_treatment=missing,
        filters=list(filters),
        filter_coverage=coverage,
        excluded=[
            DiscoverExcluded(reason=r, count=c, keys=sorted(reasons[r])[:25])
            for r, c in counts.most_common()
        ],
        rows=page,
        next_cursor=nxt,
        formula_definitions={
            m: f"{catalogue[m].formula} [{catalogue[m].period}, v{catalogue[m].definition_version}]"
            for m in sorted(used)
            if m in catalogue
        },
        warnings=[w for w in [cursor_warning] if w],
        caveats=list(caveats),
    )
