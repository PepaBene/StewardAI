"""Tax-lot accounting and holding-period tracking (plan M4.4 and M4.8).

Pure: no I/O. A jurisdiction-neutral cost-basis ledger built from `ledger` fills: FIFO or
average cost, realized gain, dividend income and fees. The holding-period view (the Czech
3-year time test of strategy principle 10b) is a set of dated facts and owner-set thresholds on
those lots. Neither is a tax liability or filing advice.

Rules that keep it honest:
- A sale larger than the units the ledger holds consumes what exists; the rest is
  `unmatched_quantity` with no gain figure (history before the first stored fill, or a corporate
  action). It is never given a zero cost.
- A lot's buy cost is the cash it took, fees included, in the account currency.
- The time-test date is the first day strictly after the third anniversary of the buy date.
  Whether a lot qualifies is the owner's to confirm against the rules that apply to them.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from steward.performance.ledger import Kind, LedgerEntry, SpinOff

ZERO = Decimal(0)
CostMethod = Literal["fifo", "average"]
MONTH_DAYS = Decimal("30.4375")


@dataclass
class Lot:
    lot_id: str
    ticker: str
    bought_on: dt.date
    original_quantity: Decimal
    remaining_quantity: Decimal
    unit_cost: Decimal  # account currency per unit, fees included
    currency: str | None


@dataclass(frozen=True)
class RealizedGain:
    sale_id: str
    ticker: str
    sold_on: dt.date
    quantity: Decimal
    proceeds: Decimal  # net of fees, account currency
    gross_proceeds: Decimal
    # Part of the gross proceeds from lots that had not yet passed the time test on the sale
    # date: the part that counts toward an annual exemption limit.
    gross_within_time_test: Decimal
    cost: Decimal
    gain: Decimal
    holding_days: int | None
    lot_ids: tuple[str, ...]


@dataclass(frozen=True)
class UnmatchedSale:
    sale_id: str
    ticker: str
    sold_on: dt.date
    quantity: Decimal
    reason: str


@dataclass
class LotBook:
    method: CostMethod
    lots: list[Lot]
    realized: list[RealizedGain]
    unmatched: list[UnmatchedSale]
    dividends: Decimal
    fees: Decimal  # fill fees plus cash fees, account currency


def _apply_spin_off(
    s: SpinOff,
    queues: dict[str, deque[Lot]],
    pooled_cost: dict[str, Decimal],
    pooled_qty: dict[str, Decimal],
) -> None:
    """Split every open parent lot: `cost_share` of its cost and `ratio` units per parent unit go
    to a child lot that keeps the parent lot's purchase date (the owner's tax rules decide whether
    that is right for them), and the parent lot keeps the rest of its cost."""
    moved_total = ZERO
    child_units = ZERO
    for lot in list(queues[s.parent]):
        if lot.remaining_quantity <= 0:
            continue
        moved = lot.remaining_quantity * lot.unit_cost * s.cost_share
        units = lot.remaining_quantity * s.ratio
        lot.unit_cost -= lot.unit_cost * s.cost_share
        queues[s.child].append(
            Lot(f"{lot.lot_id}:spin-off:{s.child}", s.child, lot.bought_on, units, units,
                moved / units, lot.currency)
        )  # fmt: skip
        moved_total += moved
        child_units += units
    if queues[s.child]:
        queues[s.child] = deque(sorted(queues[s.child], key=lambda x: (x.bought_on, x.lot_id)))
    pooled_cost[s.parent] -= moved_total
    pooled_cost[s.child] += moved_total
    pooled_qty[s.child] += child_units


def build_lots(
    entries: Iterable[LedgerEntry],
    method: CostMethod = "fifo",
    spin_offs: Sequence[SpinOff] = (),
) -> LotBook:
    """Replay fills in time order. `average` pools cost per ticker for the realized gain but
    keeps the FIFO lots, because the time test is dated per purchase. A spin-off the owner
    entered applies at the start of its day, before that day's fills."""
    queues: dict[str, deque[Lot]] = defaultdict(deque)
    book = LotBook(method, [], [], [], ZERO, ZERO)
    pooled_cost: dict[str, Decimal] = defaultdict(Decimal)
    pooled_qty: dict[str, Decimal] = defaultdict(Decimal)
    pending = sorted(spin_offs, key=lambda x: x.day)
    for e in entries:
        while pending and pending[0].day <= e.day:
            _apply_spin_off(pending.pop(0), queues, pooled_cost, pooled_qty)
        if e.needs_fx:
            continue
        if e.kind is Kind.DIVIDEND:
            book.dividends += e.gross
        elif e.kind is Kind.FEE:
            book.fees += -e.cash_effect
        elif e.kind is Kind.BUY and e.ticker and e.quantity > 0:
            cost = -e.cash_effect
            lot = Lot(e.id, e.ticker, e.day, e.quantity, e.quantity, cost / e.quantity, e.currency)
            queues[e.ticker].append(lot)
            pooled_cost[e.ticker] += cost
            pooled_qty[e.ticker] += e.quantity
            book.fees += e.fees
        elif e.kind is Kind.SELL and e.ticker and e.quantity < 0:
            book.fees += e.fees
            _sell(e, queues[e.ticker], pooled_cost, pooled_qty, book, method)
    for s in pending:
        _apply_spin_off(s, queues, pooled_cost, pooled_qty)
    book.lots = [lot for q in queues.values() for lot in q if lot.remaining_quantity > 0]
    book.lots.sort(key=lambda lot: (lot.ticker, lot.bought_on, lot.lot_id))
    return book


def _sell(
    e: LedgerEntry,
    queue: deque[Lot],
    pooled_cost: dict[str, Decimal],
    pooled_qty: dict[str, Decimal],
    book: LotBook,
    method: CostMethod,
) -> None:
    ticker = e.ticker or ""
    wanted = -e.quantity
    held = sum((lot.remaining_quantity for lot in queue), ZERO)
    matched = min(wanted, held)
    if wanted > held:
        book.unmatched.append(
            UnmatchedSale(
                e.id, ticker, e.day, wanted - held,
                "sale exceeds the units the ledger holds (earlier history or a corporate action)",
            )
        )  # fmt: skip
    if matched <= 0:
        return
    share = matched / wanted
    fifo_cost = ZERO
    used: list[str] = []
    to_take = matched
    holding_days_weighted = ZERO
    within_test = ZERO
    while to_take > 0 and queue:
        lot = queue[0]
        take = min(lot.remaining_quantity, to_take)
        fifo_cost += take * lot.unit_cost
        holding_days_weighted += take * (e.day - lot.bought_on).days
        if e.day < time_test_date(lot.bought_on):
            within_test += take
        lot.remaining_quantity -= take
        to_take -= take
        used.append(lot.lot_id)
        if lot.remaining_quantity == 0:
            queue.popleft()
    if method == "average" and pooled_qty[ticker] > 0:
        cost = matched * (pooled_cost[ticker] / pooled_qty[ticker])
    else:
        cost = fifo_cost
    pooled_cost[ticker] -= cost
    pooled_qty[ticker] -= matched
    proceeds = e.cash_effect * share
    book.realized.append(
        RealizedGain(
            e.id, ticker, e.day, matched, proceeds, e.gross * share,
            e.gross * share * (within_test / matched), cost, proceeds - cost,
            int(holding_days_weighted / matched), tuple(used),
        )
    )  # fmt: skip


# ---------------------------------------------------------------------------
# Holding periods (principle 10b)
# ---------------------------------------------------------------------------


def time_test_date(bought_on: dt.date, years: int = 3) -> dt.date:
    """First day strictly after the `years`-year anniversary of the buy date."""
    try:
        anniversary = bought_on.replace(year=bought_on.year + years)
    except ValueError:  # 29 February
        anniversary = dt.date(bought_on.year + years, 3, 1) - dt.timedelta(days=1)
    return anniversary + dt.timedelta(days=1)


@dataclass(frozen=True)
class HoldingPeriod:
    lot_id: str
    ticker: str
    bought_on: dt.date
    quantity: Decimal
    passes_on: dt.date
    months_remaining: Decimal
    passed: bool
    sell_target: Decimal | None
    current_price: Decimal | None
    target_reached: bool | None
    forfeits_exemption_warning: bool


def holding_periods(
    lots: Sequence[Lot],
    as_of: dt.date,
    *,
    sell_targets: Mapping[str, Decimal] | None = None,
    prices: Mapping[str, Decimal] | None = None,
    years: int = 3,
    warn_within_months: Decimal = Decimal(6),
) -> list[HoldingPeriod]:
    """Per open lot: buy date, the date it passes the time test and months remaining.

    `forfeits_exemption_warning` is set when the lot's ticker has reached its sell target and
    the lot passes the test within `warn_within_months`: a full sale now would forfeit the
    exemption. Without a target or a price the flag is off and `target_reached` is null.
    """
    out: list[HoldingPeriod] = []
    for lot in lots:
        if lot.remaining_quantity <= 0:
            continue
        passes = time_test_date(lot.bought_on, years)
        remaining_days = (passes - as_of).days
        passed = remaining_days <= 0
        months = ZERO if passed else (Decimal(remaining_days) / MONTH_DAYS).quantize(Decimal("0.1"))
        target = (sell_targets or {}).get(lot.ticker)
        price = (prices or {}).get(lot.ticker)
        reached = None if target is None or price is None else price >= target
        out.append(
            HoldingPeriod(
                lot.lot_id, lot.ticker, lot.bought_on, lot.remaining_quantity, passes, months,
                passed, target, price, reached,
                bool(reached) and not passed and months <= warn_within_months,
            )
        )  # fmt: skip
    return sorted(out, key=lambda h: (h.passes_on, h.ticker))


@dataclass(frozen=True)
class ExemptionYear:
    year: int
    gross_proceeds: Decimal
    limit: Decimal
    headroom: Decimal
    exceeded: bool
    sales_counted: int
    sales_without_rate: int


def exemption_year(
    year: int,
    sales: Iterable[tuple[dt.date, Decimal | None]],
    limit: Decimal,
) -> ExemptionYear:
    """The year's running gross sale proceeds against the owner-configured exemption limit.

    `sales` are (date, gross proceeds in the limit's currency); None marks a sale with no rate
    to that currency, counted but not summed, so the total is a lower bound and says so.
    """
    total = ZERO
    counted = missing = 0
    for day, amount in sales:
        if day.year != year:
            continue
        if amount is None:
            missing += 1
        else:
            total += amount
            counted += 1
    return ExemptionYear(year, total, limit, limit - total, total > limit, counted, missing)
