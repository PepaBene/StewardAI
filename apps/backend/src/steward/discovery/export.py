# ruff: noqa: E501 - header comment lines stay whole
"""Personal CSV export with source/provenance metadata (plan M1.5).

The first lines are `#` comments naming the method version, definition and result hashes, data
date, universe and filters, so a file on its own says what produced it. Unknown values are
written as empty cells with the reason in a companion `<metric>__reason` column, never as 0.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping, Sequence
from decimal import Decimal

from steward.discovery.engine import DiscoveryResult
from steward.discovery.metrics import MetricDef


def to_csv(
    result: DiscoveryResult,
    catalogue: Mapping[str, MetricDef],
    columns: Sequence[str],
    *,
    generated_at: str,
) -> str:
    buf = io.StringIO()
    filters = "; ".join(f"{f.metric} {f.op} {f.value}" for f in result.filters) or "none"
    for line in (
        "StewardAI discovery export (personal use; computed from stored observations)",
        f"method_version={result.method_version} definition_hash={result.definition_hash} result_hash={result.result_hash}",
        f"kind={result.kind} data_date={result.as_of} generated_at={generated_at}",
        f"universe={result.universe_label} size={result.universe_size} ({result.universe_definition})",
        f"filters={filters} missing={result.missing_treatment}",
        f"matched={result.matched} failed={result.failed_filter} unknown={result.unknown_excluded} no_data={result.no_data}",
    ):
        buf.write(f"# {line}\n")
    writer = csv.writer(buf, lineterminator="\n")
    known = [c for c in columns if c in catalogue]
    header = ["key", "name"]
    for c in known:
        header += [c, f"{c}__reason", f"{c}__period_end", f"{c}__source"]
    writer.writerow(header)
    for row in result.rows:
        out: list[str] = [row.key, row.name or ""]
        for c in known:
            v = row.values.get(c)
            if v is None:
                out += ["", "no value", "", ""]
                continue
            value = (
                ""
                if v.value is None
                else (format(v.value, "f") if isinstance(v.value, Decimal) else str(v.value))
            )
            out += [
                value,
                v.reason or "",
                v.period_end.isoformat() if v.period_end else "",
                v.source or "",
            ]
        writer.writerow(out)
    if result.next_cursor:
        buf.write("# truncated: more rows match than this export contains\n")
    return buf.getvalue()
