"""The dated portfolio ledger (plan M4.1): Trading212 fills, dividends and cash movements
classified into one cash-effect convention, and reconciled against the broker's snapshots.

Pure: no I/O. Everything is in the account currency, because that is what Trading212 reports
for fills, dividends and cash. A row in another currency is kept but never summed (`needs_fx`),
so a currency mix cannot distort the balance. Nothing is invented: a subtype the ledger does not
know becomes `unsupported` and is counted as a break, not guessed into a bucket.

Sign convention: `cash_effect` is the change to the account's cash. A buy is negative, a sale
and a dividend positive, a deposit positive, a withdrawal negative, a fee negative.
`external_flow` is the part that is money moving between the owner and the account (deposits,
withdrawals, transfers); investment income and fees are never external flows.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

ZERO = Decimal(0)
# The day boundary `portfolio.snapshot.SNAPSHOT_TIMEZONE` gives the daily snapshots.
LEDGER_TIMEZONE = ZoneInfo("Europe/Prague")


class Kind(StrEnum):
    BUY = "buy"
    SELL = "sell"
    DIVIDEND = "dividend"
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    TRANSFER = "transfer"
    FEE = "fee"
    UNSUPPORTED = "unsupported"


EXTERNAL_KINDS = frozenset({Kind.DEPOSIT, Kind.WITHDRAWAL, Kind.TRANSFER})
CASH_SUBTYPES: dict[str, Kind] = {
    "DEPOSIT": Kind.DEPOSIT,
    "WITHDRAW": Kind.WITHDRAWAL,
    "WITHDRAWAL": Kind.WITHDRAWAL,
    "TRANSFER": Kind.TRANSFER,
    "FEE": Kind.FEE,
}


@dataclass(frozen=True)
class RawTransaction:
    """One `public.transaction` row plus the fees stored for it."""

    id: str
    type: str
    subtype: str | None
    ticker: str | None
    quantity: Decimal | None
    price: Decimal | None
    amount: Decimal
    currency: str | None
    occurred_at: dt.datetime
    fee_basis: str | None = None
    fees_captured: bool = True
    # Charges on a fill, as stored (always positive).
    fee_amounts: tuple[Decimal, ...] = ()


@dataclass(frozen=True)
class LedgerEntry:
    id: str
    occurred_at: dt.datetime
    kind: Kind
    ticker: str | None
    # Signed units: bought positive, sold negative.
    quantity: Decimal
    price: Decimal | None
    # Principal of the trade or the dividend / cash amount, as a positive number.
    gross: Decimal
    fees: Decimal
    cash_effect: Decimal
    currency: str | None
    needs_fx: bool = False
    # Why the entry carries less than a full figure (unknown subtype, fees not captured, ...).
    flags: tuple[str, ...] = ()

    @property
    def external_flow(self) -> Decimal:
        return self.cash_effect if self.kind in EXTERNAL_KINDS else ZERO

    @property
    def day(self) -> dt.date:
        """The snapshot day it belongs to (Prague midnight boundary, as the snapshots use)."""
        stamp = self.occurred_at
        return (stamp.astimezone(LEDGER_TIMEZONE) if stamp.tzinfo else stamp).date()


def _trade_cash(side: Kind, principal: Decimal, fees: Decimal, basis: str | None) -> Decimal:
    """Cash moved by a fill. `fee_basis` says whether Trading212's net value already contains
    the fees (included) or leaves them out (excluded; also the reading for unknown)."""
    if basis == "included":
        return -principal if side is Kind.BUY else principal
    return -(principal + fees) if side is Kind.BUY else principal - fees


def classify(raw: RawTransaction, account_currency: str | None = None) -> LedgerEntry:
    flags: list[str] = []
    needs_fx = bool(account_currency and raw.currency and raw.currency != account_currency)
    if needs_fx:
        flags.append(f"in {raw.currency}, not the account currency {account_currency}")
    fees = sum(raw.fee_amounts, ZERO)
    gross = abs(raw.amount)
    quantity = ZERO
    kind: Kind
    cash: Decimal
    if raw.type == "order" and raw.subtype in ("BUY", "SELL"):
        kind = Kind.BUY if raw.subtype == "BUY" else Kind.SELL
        quantity = abs(raw.quantity or ZERO) * (1 if kind is Kind.BUY else -1)
        if not raw.fees_captured:
            flags.append("fees not captured for this fill")
        if raw.fee_basis == "unknown":
            flags.append("unclear whether the fill's net value already includes its fees")
        cash = _trade_cash(kind, gross, fees, raw.fee_basis)
    elif raw.type == "dividend":
        kind, cash = Kind.DIVIDEND, gross
    elif raw.type == "cash" and (raw.subtype or "").upper() in CASH_SUBTYPES:
        kind = CASH_SUBTYPES[(raw.subtype or "").upper()]
        if kind is Kind.DEPOSIT:
            cash = gross
        elif kind is Kind.WITHDRAWAL or kind is Kind.FEE:
            cash = -gross
        else:  # a transfer keeps the sign Trading212 reported
            cash = raw.amount
    else:
        kind, cash = Kind.UNSUPPORTED, ZERO
        flags.append(f"unsupported transaction type {raw.type}/{raw.subtype}")
    if needs_fx:
        cash = ZERO  # never added into the account-currency balance
    return LedgerEntry(
        id=raw.id,
        occurred_at=raw.occurred_at,
        kind=kind,
        ticker=raw.ticker,
        quantity=quantity,
        price=raw.price,
        gross=gross,
        fees=fees,
        cash_effect=cash,
        currency=raw.currency,
        needs_fx=needs_fx,
        flags=tuple(flags),
    )


def build_ledger(
    rows: Iterable[RawTransaction], account_currency: str | None = None
) -> list[LedgerEntry]:
    return sorted(
        (classify(r, account_currency) for r in rows), key=lambda e: (e.occurred_at, e.id)
    )


@dataclass(frozen=True)
class Split:
    """A split the owner entered: `ratio` new units for each old one (2 = 2-for-1, 0.1 = a
    1-for-10 reverse split). Fills before `day` are restated into the new units."""

    ticker: str
    day: dt.date
    ratio: Decimal


def apply_splits(entries: Iterable[LedgerEntry], splits: Sequence[Split]) -> list[LedgerEntry]:
    """Restate pre-split fills in post-split units: quantity x ratio, price / ratio, the money
    unchanged. The broker's own position snapshots are already in current units, so this is what
    lets fills, lots and the position check agree. Every restated fill says so in its flags."""
    by_ticker: dict[str, list[Split]] = defaultdict(list)
    for s in splits:
        by_ticker[s.ticker].append(s)
    out: list[LedgerEntry] = []
    for e in entries:
        applicable = [s for s in by_ticker.get(e.ticker or "", []) if e.day < s.day]
        if e.kind not in (Kind.BUY, Kind.SELL) or not applicable:
            out.append(e)
            continue
        ratio = Decimal(1)
        for s in applicable:
            ratio *= s.ratio
        out.append(
            replace(
                e,
                quantity=e.quantity * ratio,
                price=e.price / ratio if e.price is not None else None,
                flags=(*e.flags, f"restated for splits: x{ratio.normalize():f}"),
            )
        )
    return out


@dataclass(frozen=True)
class Merger:
    """A share-exchange merger the owner entered: each `old` share became `ratio` `new` shares.
    Fills of `old` before `day` are carried over to `new`; cost and purchase dates stay."""

    old: str
    new: str
    day: dt.date
    ratio: Decimal


def apply_mergers(entries: Iterable[LedgerEntry], mergers: Sequence[Merger]) -> list[LedgerEntry]:
    """Rewrite earlier fills of the old ticker onto the new one: quantity x ratio, price / ratio,
    the money, the date and so the lot's age unchanged (a share exchange is not a sale). Cash paid
    in lieu of a fraction is not modelled; it shows as a cash difference, never hidden."""
    current = list(entries)
    for m in sorted(mergers, key=lambda x: x.day):
        out: list[LedgerEntry] = []
        for e in current:
            if e.ticker != m.old or e.kind not in (Kind.BUY, Kind.SELL) or e.day >= m.day:
                out.append(e)
                continue
            out.append(
                replace(
                    e,
                    ticker=m.new,
                    quantity=e.quantity * m.ratio,
                    price=e.price / m.ratio if e.price is not None else None,
                    flags=(
                        *e.flags,
                        f"carried over from {m.old} in a merger: x{m.ratio.normalize():f}",
                    ),
                )
            )
        current = out
    return current


@dataclass(frozen=True)
class SpinOff:
    """A spin-off the owner entered: holders of `parent` on `day` received `ratio` `child` shares
    per parent share, and `cost_share` of the parent's cost moved to the child."""

    parent: str
    child: str
    day: dt.date
    ratio: Decimal
    cost_share: Decimal


def spin_off_units(
    entries: Iterable[LedgerEntry], spin_offs: Sequence[SpinOff]
) -> dict[str, Decimal]:
    """Child units each spin-off delivered: the parent units the fills show as held before `day`,
    times the ratio. Used to reconcile the broker's position, not to price anything."""
    fills = [e for e in entries if e.kind in (Kind.BUY, Kind.SELL) and e.ticker]
    received: dict[str, Decimal] = defaultdict(Decimal)
    for s in spin_offs:
        held = sum((e.quantity for e in fills if e.ticker == s.parent and e.day < s.day), ZERO)
        if held > 0:
            received[s.child] += held * s.ratio
    return dict(received)


def external_flows(entries: Iterable[LedgerEntry]) -> list[tuple[dt.date, Decimal]]:
    return [
        (e.day, e.external_flow) for e in entries if e.kind in EXTERNAL_KINDS and not e.needs_fx
    ]


# ---------------------------------------------------------------------------
# Reconciliation against Trading212's own figures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PositionBreak:
    ticker: str
    ledger_quantity: Decimal
    broker_quantity: Decimal
    difference: Decimal
    reason: str


def position_breaks(
    entries: Iterable[LedgerEntry],
    broker_quantities: dict[str, Decimal],
    *,
    tolerance: Decimal = Decimal("0.000001"),
    received: Mapping[str, Decimal] | None = None,
) -> list[PositionBreak]:
    """Held units rebuilt from fills against the latest broker position snapshot.

    A positive difference means the broker holds more than the fills explain: usually history
    older than the first stored fill, or a corporate action (split, merger, spin-off) that is
    not a fill. It is reported, never netted away. `received` adds units a spin-off the owner
    entered delivered.
    """
    held: dict[str, Decimal] = defaultdict(Decimal)
    for entry in entries:
        if entry.ticker and entry.kind in (Kind.BUY, Kind.SELL):
            held[entry.ticker] += entry.quantity
    for ticker, units in (received or {}).items():
        held[ticker] += units
    breaks: list[PositionBreak] = []
    for ticker in sorted(set(held) | set(broker_quantities)):
        ledger_q = held.get(ticker, ZERO)
        broker_q = broker_quantities.get(ticker, ZERO)
        diff = broker_q - ledger_q
        if abs(diff) <= tolerance:
            continue
        if ledger_q == 0:
            reason = "held at the broker with no fills stored (history before the first fill)"
        elif diff > 0:
            reason = "broker holds more than the fills explain (earlier history or a split)"
        else:
            reason = "fills explain more than the broker holds (missing sale or corporate action)"
        breaks.append(PositionBreak(ticker, ledger_q, broker_q, diff, reason))
    return breaks


@dataclass(frozen=True)
class CashReconciliation:
    start_date: dt.date
    end_date: dt.date
    opening_cash: Decimal
    closing_cash: Decimal
    expected_change: Decimal
    actual_change: Decimal
    difference: Decimal
    tolerance: Decimal
    within_tolerance: bool
    explanation: str
    entries_used: int
    unsupported: int


def reconcile_cash(
    entries: Sequence[LedgerEntry],
    *,
    start_date: dt.date,
    end_date: dt.date,
    opening_cash: Decimal,
    closing_cash: Decimal,
    tolerance: Decimal = Decimal("1"),
) -> CashReconciliation:
    """Cash change between two snapshot days explained by the ledger entries after the opening
    day's snapshot and up to and including the closing day's."""
    window = [e for e in entries if start_date < e.day <= end_date]
    expected = sum((e.cash_effect for e in window), ZERO)
    actual = closing_cash - opening_cash
    difference = actual - expected
    unsupported = sum(1 for e in window if e.kind is Kind.UNSUPPORTED or e.needs_fx)
    ok = abs(difference) <= tolerance
    if ok:
        explanation = "the ledger explains the change in cash within tolerance"
    elif unsupported:
        explanation = (
            f"{unsupported} entr{'y' if unsupported == 1 else 'ies'} the ledger cannot price"
        )
    else:
        explanation = (
            "cash moved by an amount no stored transaction explains "
            "(interest, a missing event, or the broker's own rounding)"
        )
    return CashReconciliation(
        start_date, end_date, opening_cash, closing_cash, expected, actual, difference,
        tolerance, ok, explanation, len(window), unsupported,
    )  # fmt: skip


@dataclass
class LedgerSummary:
    entries: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)
    unsupported: list[str] = field(default_factory=list)
    needs_fx: int = 0
    fees_not_captured: int = 0
    first: dt.date | None = None
    last: dt.date | None = None


def summarize(entries: Sequence[LedgerEntry]) -> LedgerSummary:
    counts: dict[str, int] = defaultdict(int)
    summary = LedgerSummary(entries=len(entries))
    for entry in entries:
        counts[entry.kind.value] += 1
        if entry.kind is Kind.UNSUPPORTED:
            summary.unsupported.append(entry.id)
        summary.needs_fx += entry.needs_fx
        summary.fees_not_captured += any("fees not captured" in f for f in entry.flags)
    summary.by_kind = dict(counts)
    if entries:
        summary.first, summary.last = entries[0].day, entries[-1].day
    return summary
