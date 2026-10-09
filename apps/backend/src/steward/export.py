"""CSV export of report rows (plan M4.4, M5.1). Pure: rows in, text out.

Cells that start with a spreadsheet formula character are prefixed with a quote so a ticker or
name from a provider cannot run as a formula when the file is opened.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from typing import Any

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str) and value.startswith(_FORMULA_START):
        # A bare negative number is data, not a formula.
        try:
            float(value)
        except ValueError:
            return "'" + value
    return value


def to_csv(header: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([_cell(v) for v in row])
    return out.getvalue()
