"""Forward observations: their identity, and consensus series that never mix sources.

- `observation_key`: the content key of a forward observation. A refresh that fetches the same
  figures is the same observation (the table used to gain a row on every refresh); a changed
  figure is a new row, so the history of estimates stays point in time. The key is computed
  here and, for rows that existed before the column, by the migration's backfill with the same
  fields and number formatting.
- `consensus_series`: groups published-consensus rows by (metric, fiscal period, provider,
  currency, basis). Consensus of two providers, or in two currencies, or on two bases
  (GAAP / non-GAAP), is not one series: a "revision" between a Finnhub row and a different
  provider's row is a difference of sources, not an estimate revision (B32).
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections import defaultdict
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any


def _number(value: Decimal | float | int | str | None) -> str:
    """Number as PostgreSQL's `trim_scale(x)::text` writes it ('1.5', '100', '')."""
    if value is None:
        return ""
    number = Decimal(str(value))
    if number == 0:
        return "0"
    text = format(number.normalize(), "f")
    return text


def observation_key(
    *,
    kind: str,
    provider: str | None,
    metric: str,
    fiscal_period: str,
    basis: str | None,
    currency: str | None,
    vintage: str | None,
    period_end: dt.date | None,
    value: Decimal | float | int | str | None,
    low: Decimal | float | int | str | None,
    high: Decimal | float | int | str | None,
    analyst_count: int | None,
) -> str:
    parts = [
        kind,
        provider or "",
        metric,
        fiscal_period,
        basis or "",
        currency or "",
        vintage or "",
        period_end.isoformat() if period_end else "",
        _number(value),
        _number(low),
        _number(high),
        "" if analyst_count is None else str(analyst_count),
    ]
    return hashlib.md5("|".join(parts).encode(), usedforsecurity=False).hexdigest()


SeriesKey = tuple[str, str, str, str, str]


def series_key(row: Mapping[str, Any]) -> SeriesKey:
    return (
        str(row["metric"]),
        str(row["fiscal_period"]),
        str(row.get("provider") or ""),
        str(row.get("currency") or ""),
        str(row.get("basis") or ""),
    )


def consensus_series(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Published-consensus vintages grouped by source, currency and basis, oldest first, with
    the revision between the last two vintages of the same series."""
    grouped: dict[SeriesKey, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("kind") == "published_analyst_consensus":
            grouped[series_key(row)].append(row)
    out: list[dict[str, Any]] = []
    for (metric, period, provider, currency, basis), items in sorted(grouped.items()):
        items.sort(key=lambda r: (r["known_at"], r["id"]))
        revision: Decimal | None = None
        inputs: list[int] = []
        if len(items) >= 2:
            previous, last = items[-2], items[-1]
            inputs = [previous["id"], last["id"]]
            if previous.get("value") is not None and last.get("value") is not None:
                revision = Decimal(str(last["value"])) - Decimal(str(previous["value"]))
        out.append(
            {
                "metric": metric,
                "fiscal_period": period,
                "provider": provider or None,
                "currency": currency or None,
                "basis": basis or None,
                "vintages": [dict(i) for i in items],
                "revision_since_previous": revision,
                "revision_inputs": inputs,
            }
        )
    return out
