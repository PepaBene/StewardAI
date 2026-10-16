"""Forecast accuracy (plan I1.6). Pure: no I/O.

A forecast snapshot at least a year old is compared with what was actually received in the
twelve months after it: a forecast row is a *hit* when a payment of the same holding landed within
the window of its pay date; the error of a hit is the forecast per-share amount against the
received gross per share. Hidden (with the date it becomes available) until a year of snapshots
exists.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from steward.dividends.model import ReceivedPayment


@dataclass(frozen=True)
class Accuracy:
    available: bool
    available_from: dt.date | None
    snapshot_as_of: dt.date | None
    rows: int = 0
    hits: int = 0
    hit_rate: Decimal | None = None
    mean_error: Decimal | None = None
    forecast_total: Decimal | None = None
    note: str | None = None


def forecast_accuracy(
    snapshots: Mapping[str, Mapping[str, object]],
    snapshot_as_of: dt.date | None,
    first_snapshot: dt.date | None,
    payments: Sequence[ReceivedPayment],
    today: dt.date,
    window_days: int = 21,
    horizon_days: int = 365,
) -> Accuracy:
    """`snapshots` maps ticker -> stored schedule body of one day (`snapshot_as_of`), compared
    with the payments of the `horizon_days` after it (a year; the clock retrospective also reads
    three and six months)."""
    if first_snapshot is None:
        return Accuracy(False, None, None, note="no forecast snapshot is stored yet")
    available_from = first_snapshot + dt.timedelta(days=horizon_days)
    if snapshot_as_of is None or today < available_from:
        return Accuracy(
            False,
            available_from,
            snapshot_as_of,
            note=f"available from {available_from.isoformat()}",
        )
    end = snapshot_as_of + dt.timedelta(days=horizon_days)
    rows = hits = 0
    errors: list[Decimal] = []
    total = Decimal(0)
    by_ticker: dict[str, list[ReceivedPayment]] = {}
    for p in payments:
        if snapshot_as_of < p.paid_on <= end:
            by_ticker.setdefault(p.ticker, []).append(p)
    for ticker, body in snapshots.items():
        raw_rows = body.get("rows") if isinstance(body, Mapping) else None
        wanted: list[tuple[dt.date, Decimal]] = []
        for r in raw_rows if isinstance(raw_rows, list) else []:
            if not isinstance(r, Mapping) or r.get("kind") == "unconfirmed":
                continue
            pay = dt.date.fromisoformat(str(r["pay_date"]))
            if not snapshot_as_of < pay <= end:
                continue
            rows += 1
            if r.get("amount") is not None:
                total += Decimal(str(r["amount"]))
            wanted.append((pay, Decimal(str(r["per_share"]))))
        # One payment confirms one forecast row: the closest pairs inside the window match first.
        paid = by_ticker.get(ticker, [])
        pairs = sorted(
            (abs((p.paid_on - w[0]).days), i, j)
            for i, w in enumerate(wanted)
            for j, p in enumerate(paid)
            if abs((p.paid_on - w[0]).days) <= window_days
        )
        rows_done: set[int] = set()
        paid_done: set[int] = set()
        for _gap, i, j in pairs:
            if i in rows_done or j in paid_done:
                continue
            rows_done.add(i)
            paid_done.add(j)
            hits += 1
            got = paid[j].gross_per_share
            if got is not None and got > 0:
                errors.append((wanted[i][1] - got) / got)
    return Accuracy(
        True,
        available_from,
        snapshot_as_of,
        rows=rows,
        hits=hits,
        hit_rate=Decimal(hits) / Decimal(rows) if rows else None,
        mean_error=sum(errors, Decimal(0)) / Decimal(len(errors)) if errors else None,
        forecast_total=total,
    )
