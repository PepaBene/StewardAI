# ruff: noqa: E501
"""Per-holding positions: open and closed, with the transactions behind them (insight I11.1, I11.3).

Pure: no I/O. From the classified ledger (account currency) and the owner's entered splits, per
ticker:

- the transaction list in date order: buys, sells, dividends and fees with their cash effect, plus
  one `split` row per entered split (no cash; it restates earlier units, so the running quantity
  stays in today's units);
- invested (cash paid for buys, fees included), proceeds of sales, dividends received, fees;
- the realised gain from the lot book, and for an open position its value and cost from the latest
  position snapshot;
- the annualised money-weighted return (XIRR) of the holding's own cash flows plus its ending value
  (for a closed position, the flows alone). It is `unavailable` with the reason when the flows do
  not admit one rate, and a holding held under 30 days gets no annual rate at all, since a short
  window turns a small move into a large annual figure.

A position is closed when the ledger's running quantity is zero (within a tolerance) and no
snapshot reports units today. Entries that need an exchange rate the ledger does not carry are
left out and the position says so.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from steward.analytics.contract import Status
from steward.analytics.returns import xirr
from steward.performance.ledger import Kind, LedgerEntry, Split
from steward.performance.lots import RealizedGain

ZERO = Decimal(0)
QUANTITY_TOLERANCE = Decimal("0.000001")
MIN_DAYS_FOR_ANNUAL = 30


@dataclass(frozen=True)
class PositionValue:
    """Today's open position as the latest snapshot states it, in the account currency."""

    quantity: Decimal
    value: Decimal
    cost: Decimal
    as_of: dt.date


@dataclass(frozen=True)
class TransactionRow:
    day: dt.date
    kind: str  # buy | sell | dividend | fee | split
    quantity: Decimal | None  # signed units (buy positive); a split's ratio is in `note`
    price: Decimal | None
    cash: Decimal  # signed cash effect on the account (buy negative)
    running_quantity: Decimal
    note: str | None = None
    id: str | None = None


@dataclass
class Position:
    ticker: str
    status: str  # open | closed
    first_trade: dt.date | None = None
    last_trade: dt.date | None = None
    quantity: Decimal = ZERO
    invested: Decimal = ZERO  # cash paid for buys, fees included (positive)
    proceeds: Decimal = ZERO  # cash received for sales (positive)
    dividends: Decimal = ZERO
    fees: Decimal = ZERO  # positive
    realised_gain: Decimal = ZERO
    value: Decimal | None = None
    cost: Decimal | None = None
    unrealised_gain: Decimal | None = None
    total_gain: Decimal | None = None
    annualised_return: Decimal | None = None
    annualised_reason: str | None = None
    days_held: int | None = None
    transactions: list[TransactionRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def build_positions(
    ledger: Sequence[LedgerEntry],
    splits: Iterable[Split],
    realised: Iterable[RealizedGain],
    open_values: Mapping[str, PositionValue],
    today: dt.date,
) -> list[Position]:
    """One position per ticker the ledger or the snapshot knows, open ones first, then by the
    most recent trade."""
    by_ticker: dict[str, list[LedgerEntry]] = defaultdict(list)
    for e in ledger:
        if e.ticker and e.kind in (Kind.BUY, Kind.SELL, Kind.DIVIDEND, Kind.FEE):
            by_ticker[e.ticker].append(e)
    split_rows: dict[str, list[Split]] = defaultdict(list)
    for s in splits:
        split_rows[s.ticker].append(s)
    gains: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for g in realised:
        gains[g.ticker] += g.gain
    out: list[Position] = []
    for ticker in sorted(set(by_ticker) | set(open_values)):
        out.append(
            _position(
                ticker,
                sorted(by_ticker.get(ticker, []), key=lambda e: (e.occurred_at, e.id)),
                split_rows.get(ticker, []),
                gains.get(ticker, ZERO),
                open_values.get(ticker),
            )
        )
    return sorted(
        out,
        key=lambda p: (p.status != "open", -(p.last_trade or dt.date.min).toordinal(), p.ticker),
    )


def _position(
    ticker: str,
    entries: list[LedgerEntry],
    splits: list[Split],
    realised_gain: Decimal,
    now: PositionValue | None,
) -> Position:
    p = Position(ticker=ticker, status="open", realised_gain=realised_gain)
    events: list[tuple[dt.date, int, LedgerEntry | Split]] = [
        *((e.day, 0, e) for e in entries),
        *((s.day, 1, s) for s in splits),
    ]
    flows: list[tuple[dt.date, Decimal]] = []
    skipped = 0
    running = ZERO
    for day, _order, item in sorted(events, key=lambda x: (x[0], x[1])):
        if isinstance(item, Split):
            p.transactions.append(
                TransactionRow(day, "split", None, None, ZERO, running,
                               note=f"{item.ratio.normalize():f} new units per old unit; earlier rows are in today's units")
            )  # fmt: skip
            continue
        e = item
        if e.needs_fx:
            skipped += 1
            continue
        if e.kind in (Kind.BUY, Kind.SELL):
            running += e.quantity
            p.first_trade = p.first_trade or day
            p.last_trade = day
        if e.kind is Kind.BUY:
            p.invested += -e.cash_effect
        elif e.kind is Kind.SELL:
            p.proceeds += e.cash_effect
        elif e.kind is Kind.DIVIDEND:
            p.dividends += e.cash_effect
        p.fees += (
            e.fees
            if e.kind in (Kind.BUY, Kind.SELL)
            else (-e.cash_effect if e.kind is Kind.FEE else ZERO)
        )
        flows.append((day, e.cash_effect))
        p.transactions.append(
            TransactionRow(day, str(e.kind), e.quantity if e.kind in (Kind.BUY, Kind.SELL) else None,
                           e.price, e.cash_effect, running, note="; ".join(e.flags) or None, id=e.id)
        )  # fmt: skip
    if skipped:
        p.warnings.append(
            f"{skipped} entr{'y' if skipped == 1 else 'ies'} in another currency than the account's are left out"
        )
    p.quantity = running
    held = now is not None and now.quantity > QUANTITY_TOLERANCE
    if held and now is not None:
        p.value, p.cost = now.value, now.cost
        p.unrealised_gain = now.value - now.cost
        if abs(now.quantity - running) > QUANTITY_TOLERANCE * max(Decimal(1), abs(now.quantity)):
            p.warnings.append(
                f"the ledger's units ({running.normalize():f}) differ from the broker's ({now.quantity.normalize():f}): "
                "history before the first stored fill or an unentered corporate action"
            )
        flows.append((now.as_of, now.value))
    elif abs(running) <= QUANTITY_TOLERANCE:
        p.status = "closed"
    elif running < 0:
        # More sold than the ledger bought: the first buys predate the stored history, and the
        # broker no longer holds it, so the position is closed with an incomplete cost.
        p.status = "closed"
        p.quantity = ZERO
        p.warnings.append(
            f"the ledger sold {(-running).normalize():f} more units than it bought: buys before the"
            " first stored fill are missing, so the realised gain overstates it"
        )
    else:
        # Units on the ledger but none in the snapshot: sold elsewhere or not priced today.
        p.status = "open"
        p.warnings.append("the ledger holds units the latest snapshot does not report")
    p.total_gain = (
        p.realised_gain + (p.unrealised_gain or ZERO) + p.dividends
        if p.status == "closed" or p.unrealised_gain is not None
        else None
    )
    end = now.as_of if held and now is not None else p.last_trade
    if p.first_trade and end:
        p.days_held = (end - p.first_trade).days
    if p.days_held is not None and p.days_held < MIN_DAYS_FOR_ANNUAL:
        p.annualised_reason = f"held {p.days_held} days; an annual rate needs {MIN_DAYS_FOR_ANNUAL}"
    elif not flows:
        p.annualised_reason = "no cash flows stored"
    else:
        r = xirr(flows)
        if r.status is Status.COMPLETE and r.value is not None:
            p.annualised_return = r.value
        else:
            p.annualised_reason = "; ".join(r.warnings) or "no stable rate"

    return p
