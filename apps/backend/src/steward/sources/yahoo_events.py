"""Dividends and splits from Yahoo's chart response (`events=div,splits`).

The same free, unofficial endpoint as the daily bars (see `steward.sources.yahoo`); one request
returns both, so the closes and the events always come from one answer. Shapes, checked against a
live response on 2026-10-03::

    "events": {"dividends": {"<epoch>": {"amount": 0.91, "date": <epoch>}},
               "splits":    {"<epoch>": {"date": <epoch>, "numerator": 10.0,
                                         "denominator": 1.0, "splitRatio": "10:1"}}}

* Closes are split-adjusted and not dividend-adjusted (NVDA's 10:1 split of 2024-06-10 shows as
  no jump), and dividend amounts are split-adjusted too: both are on today's share basis, so a
  dividend divided by the close of its ex-date is the yield without any further split factor.
* Amounts are in the chart's `meta.currency`, which is the bars' currency (`GBp` for London:
  the pence unit, never pounds).
* `events` is absent when the range has none. That means "Yahoo lists none in this range", and
  only the caller that knows the range it asked for can say what range that is.
* The date is the epoch of the ex-date's session open; it is converted to the exchange's own
  calendar day (`exchangeTimezoneName`), the same way the daily bars are dated.

Yahoo is Tier 3 and unofficial: its list is as complete as Yahoo makes it and nothing here can
verify that, so these events are stored as `suggested` and never as confirmed.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True, slots=True)
class YahooDividend:
    ex_date: dt.date
    amount: Decimal
    currency: str | None


@dataclass(frozen=True, slots=True)
class YahooSplit:
    ex_date: dt.date
    numerator: Decimal
    denominator: Decimal

    @property
    def ratio(self) -> Decimal:
        """New shares per old share (10:1 -> 10; a 1:10 reverse split -> 0.1)."""
        return self.numerator / self.denominator


@dataclass(frozen=True, slots=True)
class YahooEvents:
    dividends: tuple[YahooDividend, ...]
    splits: tuple[YahooSplit, ...]


def _positive(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and number > 0 else None


def parse_events(body: str | dict[str, Any]) -> YahooEvents:
    """The dividends and splits of one chart body (the stored payload text or the parsed JSON).

    A malformed entry (no usable date, a non-positive amount or ratio) is skipped, never
    guessed; a body with no result raises `ValueError`."""
    doc = json.loads(body) if isinstance(body, str) else body
    try:
        result = doc["chart"]["result"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("not a Yahoo chart response") from exc
    meta = result.get("meta") or {}
    currency = meta.get("currency")
    try:
        zone = ZoneInfo(meta.get("exchangeTimezoneName") or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")

    def day(entry: dict[str, Any]) -> dt.date | None:
        stamp = entry.get("date")
        if not isinstance(stamp, int | float):
            return None
        return dt.datetime.fromtimestamp(stamp, dt.UTC).astimezone(zone).date()

    events = result.get("events") or {}
    dividends: list[YahooDividend] = []
    for entry in (events.get("dividends") or {}).values():
        ex_date, amount = day(entry), _positive(entry.get("amount"))
        if ex_date is not None and amount is not None:
            dividends.append(YahooDividend(ex_date, amount, currency))
    splits: list[YahooSplit] = []
    for entry in (events.get("splits") or {}).values():
        ex_date = day(entry)
        numerator, denominator = (
            _positive(entry.get("numerator")),
            _positive(entry.get("denominator")),
        )
        if ex_date is not None and numerator is not None and denominator is not None:
            splits.append(YahooSplit(ex_date, numerator, denominator))
    return YahooEvents(
        tuple(sorted(dividends, key=lambda d: (d.ex_date, d.amount))),
        tuple(sorted(splits, key=lambda s: s.ex_date)),
    )
