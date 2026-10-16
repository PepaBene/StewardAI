"""Forward dividend schedule (plan I1.4, method `dividend_income`). Pure: no I/O.

Per holding, for the next twelve months:

1. **Declared** rows: confirmed events whose ex-date or pay date is after today, exactly as
   stored. Suggested events in the same window are listed as **unconfirmed** and stay out of
   every total until the owner (or the documented auto-confirm rule) confirms them.
2. **Estimated** rows: the trailing cadence (monthly, quarterly, semi-annual or annual, detected
   from the regular payments of the last three years) projected from the last regular payment,
   each at the last regular amount. Only with "assume growth" on is that amount grown by the
   holding's 5-year dividend growth. A slot a declared or unconfirmed row already covers is not
   estimated again. Every estimated row is flagged `estimated`.
3. Irregular cadences and special dividends are never estimated; specials are listed apart.
4. Estimates need at least `min_payments` regular payments; with fewer the holding reads "no
   schedule, history too short", never 0.

History comes from confirmed events, Trading212 receipts not matched to a confirmed event,
suggested events that two independent sources agree on, and (rule `single_source_history`, on by
default) suggested events only one source reports, each labelled `single_source`. Amounts are
per share in the declared currency, multiplied by today's share count and converted to the
account currency at the latest stored reference rate (stated on each row).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from decimal import Decimal
from itertools import pairwise
from statistics import median

from steward.dividends.model import (
    SPECIAL_WORDS,
    ZERO,
    HoldingSchedule,
    PaymentPoint,
    ScheduleRow,
)
from steward.dividends.reconcile import EventGroup, Reconciliation

HORIZON_DAYS = 365
HISTORY_YEARS = 3
SUSPENDED_AFTER_CYCLES = 2  # this many missed cycles (plus a month's grace) mean suspended
SPECIAL_MULTIPLE = Decimal(2)  # a payment over twice the regular median is special
FREQUENCIES: tuple[tuple[str, int, int, int], ...] = (
    # name, payments a year, min and max median interval in days
    ("monthly", 12, 20, 45),
    ("quarterly", 4, 70, 115),
    ("semi_annual", 2, 150, 215),
    ("annual", 1, 300, 430),
)
PER_YEAR = {name: n for name, n, _, _ in FREQUENCIES}
CONSISTENT_SHARE = Decimal("0.6")

# Units of `to` for one `from` at the latest stored rate: (rate, rate date) or None.
LatestFx = Callable[[str, str], tuple[Decimal, dt.date | None] | None]


def history_points(
    ticker: str,
    rec: Reconciliation,
    groups: Sequence[EventGroup],
    instrument_currency: str | None,
    *,
    single_source: bool = False,
) -> list[PaymentPoint]:
    """Past payments per share for cadence and amounts (see the module rules). With
    `single_source`, a suggested event no second source corroborates counts too, as origin
    `single_source` (it is history only: declared rows still need a confirmed event)."""
    points: list[PaymentPoint] = []
    covered: set[int] = set()
    # The date the money actually arrived, for events whose provider gave only the ex-date.
    received_on = {
        id(m.group): m.payment.paid_on
        for m in rec.matches
        if m.group is not None and m.payment.ticker == ticker
    }
    for g in groups:
        e = g.event
        if e.ticker != ticker:
            continue
        corroborated = e.status == "suggested" and any(
            a.source != e.source and abs(a.amount - e.amount) <= e.amount * Decimal("0.02")
            for a in g.alternatives
        )
        lone = single_source and e.status == "suggested" and not corroborated
        if e.status != "confirmed" and not corroborated and not lone:
            continue
        covered.add(id(g))
        paid = e.payment_date or received_on.get(id(g))
        points.append(
            PaymentPoint(
                pay_date=paid or e.ex_date,
                ex_date=e.ex_date,
                per_share=e.amount,
                currency=e.currency,
                origin="single_source" if lone else "event",
                special=e.flagged_special,
                ref=f"corporate_action:{e.id}",
                pay_known=paid is not None,
            )
        )
    for m in rec.matches:
        p = m.payment
        if p.ticker != ticker:
            continue
        if m.group is not None and id(m.group) in covered:
            continue
        if p.gross_per_share is None or p.gross_per_share <= 0:
            continue
        points.append(
            PaymentPoint(
                pay_date=p.paid_on,
                ex_date=m.group.event.ex_date if m.group else None,
                per_share=p.gross_per_share,
                currency=instrument_currency,
                origin="receipt",
                ref=f"transaction:{p.id}",
            )
        )
    points.sort(key=lambda x: x.pay_date)
    # One point per payment: a receipt within a few days of an event point is the same payment.
    deduped: list[PaymentPoint] = []
    for pt in points:
        if deduped and abs((pt.pay_date - deduped[-1].pay_date).days) <= 4:
            if deduped[-1].origin == "receipt" and pt.origin != "receipt":
                deduped[-1] = pt
            continue
        deduped.append(pt)
    return deduped


def mark_specials(points: list[PaymentPoint]) -> None:
    """A payment flagged special by its source, or over twice the median of the payments around
    it, is special: excluded from cadence and estimates, listed apart."""
    amounts = [p.per_share for p in points if not p.special]
    if len(amounts) < 3:
        return
    typical = Decimal(str(median(amounts)))
    for p in points:
        if typical > 0 and p.per_share > typical * SPECIAL_MULTIPLE:
            p.special = True


def detect_frequency(dates: Sequence[dt.date]) -> str:
    """Cadence of regular payments from the median interval between consecutive pay dates."""
    if len(dates) < 2:
        return "unknown"
    gaps = [(b - a).days for a, b in pairwise(dates) if (b - a).days > 0]
    if not gaps:
        return "unknown"
    mid = Decimal(str(median(gaps)))
    near = sum(1 for g in gaps if abs(Decimal(g) - mid) <= mid * Decimal("0.35"))
    if Decimal(near) / Decimal(len(gaps)) < CONSISTENT_SHARE:
        return "irregular"
    for name, _n, low, high in FREQUENCIES:
        if low <= mid <= high:
            return name
    return "irregular"


def _lag_days(points: Sequence[PaymentPoint]) -> int | None:
    """Median ex-to-pay gap, from payments whose pay date is on record (an ex-date standing in
    for a missing pay date would make it 0, and "buy before" the payment date)."""
    lags = [
        (p.pay_date - p.ex_date).days
        for p in points
        if p.pay_known and p.ex_date and p.pay_date >= p.ex_date
    ]
    return int(median(lags)) if lags else None


def _convert(
    per_share: Decimal,
    currency: str | None,
    quantity: Decimal,
    account: str | None,
    fx: LatestFx,
) -> tuple[Decimal | None, Decimal | None, dt.date | None]:
    if currency is None or account is None:
        return None, None, None
    got = fx(currency, account)
    if got is None:
        return None, None, None
    rate, day = got
    return per_share * quantity * rate, rate, day


def build_schedule(
    ticker: str,
    *,
    today: dt.date,
    quantity: Decimal,
    account_currency: str | None,
    points: list[PaymentPoint],
    groups: Sequence[EventGroup],
    fx: LatestFx,
    min_payments: int,
    assume_growth: bool,
    growth_5y: Decimal | None,
    withholding_share: Decimal | None,
) -> HoldingSchedule:
    horizon = today + dt.timedelta(days=HORIZON_DAYS)
    mark_specials(points)
    regular = [p for p in points if not p.special]
    window_start = today - dt.timedelta(days=365 * HISTORY_YEARS + 30)
    recent = [p for p in regular if p.pay_date >= window_start] or regular
    frequency = detect_frequency([p.pay_date for p in recent])
    schedule = HoldingSchedule(
        ticker=ticker,
        frequency=frequency,
        payments_seen=len(regular),
        specials=[p for p in points if p.special],
        withholding_share=withholding_share,
    )
    if not points and not any(g.event.ticker == ticker for g in groups):
        schedule.frequency = "none"
        schedule.status = "unavailable"
        schedule.reason = "no dividend on record for this holding"
        return schedule

    def row(kind: str, pay: dt.date, ex: dt.date | None, per: Decimal, ccy: str | None,
            source: str, note: str | None = None, event_id: int | None = None,
            ex_estimated: bool = False) -> ScheduleRow:  # fmt: skip
        amount, rate, rate_day = _convert(per, ccy, quantity, account_currency, fx)
        net = None
        if amount is not None and withholding_share is not None:
            net = amount * (Decimal(1) - withholding_share)
        if amount is None and ccy and account_currency:
            note = "; ".join(x for x in (note, f"no stored {ccy}/{account_currency} rate") if x)
        return ScheduleRow(ticker, kind, ex, pay, per, ccy, quantity, amount, net, rate,
                           rate_day, source, note, event_id, ex_estimated)  # fmt: skip

    covered: list[dt.date] = []
    for g in groups:
        e = g.event
        if e.ticker != ticker:
            continue
        if not (e.pay_or_ex > today or e.ex_date > today) or e.pay_or_ex > horizon:
            continue
        kind = "declared" if e.status == "confirmed" else "unconfirmed"
        note = (
            None
            if kind == "declared"
            else "suggested by " + ", ".join(g.sources) + "; not in totals until confirmed"
        )
        if e.flagged_special:
            note = "; ".join(x for x in (note, "special dividend") if x)
        schedule.rows.append(row(kind, e.pay_or_ex, e.ex_date, e.amount, e.currency,
                                 ", ".join(g.sources), note, e.id))  # fmt: skip
        covered.append(e.pay_or_ex)

    if len(regular) < min_payments:
        schedule.status = "unavailable" if not schedule.rows else "partial"
        schedule.reason = (
            f"no schedule, history too short: {len(regular)} regular payment"
            f"{'s' if len(regular) != 1 else ''} on record; estimates need {min_payments}"
        )
        return schedule
    if frequency not in PER_YEAR:
        schedule.status = "partial"
        schedule.reason = f"cadence is {frequency}: payments are not estimated"
        return schedule

    last = regular[-1]
    schedule.last_amount = last.per_share
    per_year = PER_YEAR[frequency]
    step = Decimal(365) / Decimal(per_year)
    lag = _lag_days(regular)
    last_seen = last.ex_date or last.pay_date
    if (today - last_seen).days > int(step * SUSPENDED_AFTER_CYCLES) + (lag or 0) + 30:
        # Boeing's last dividend went ex in February 2020: repeating it every quarter since
        # would forecast income that stopped years ago.
        schedule.status = "unavailable" if not schedule.rows else "partial"
        schedule.reason = (
            f"no {frequency.replace('_', '-')} payment since {last_seen.isoformat()}: treated "
            "as suspended, so no payment is estimated"
        )
        return schedule
    growth = growth_5y if assume_growth else None
    schedule.growth_used = growth
    # Project from the last payment date; when only its ex-date is on record, from the ex-date
    # moved on by the usual gap. With no gap on record either, the projected date is an ex-date.
    anchor = last.pay_date if last.pay_known else last_seen
    shift = 0 if last.pay_known else (lag or 0)
    ex_only = not last.pay_known and lag is None
    k = 1
    while True:
        pay = anchor + dt.timedelta(days=int((step * k).to_integral_value()) + shift)
        k += 1
        if pay > horizon:
            break
        if pay <= today:
            continue
        if any(abs((pay - c).days) <= int(step / 2) for c in covered):
            continue
        amount = last.per_share
        if growth is not None:
            years = Decimal((pay - last.pay_date).days) / Decimal(365)
            amount = amount * (Decimal(1) + growth) ** years
        ex: dt.date | None
        if ex_only:
            ex = pay
            note = (
                "no payment date on record, only ex-dates: the date shown is the expected "
                "ex-date (own the shares before it); the payment follows some weeks later"
            )
        else:
            ex = pay - dt.timedelta(days=lag) if lag is not None else None
            note = None if lag is not None else "no ex-to-pay lag on record; ex-date not estimated"
        schedule.rows.append(row("estimated", pay, ex, amount, last.currency,
                                 f"{frequency} cadence from {last.ref}", note,
                                 ex_estimated=ex is not None))  # fmt: skip
    schedule.rows.sort(key=lambda r: r.pay_date)
    if any(r.amount is None for r in schedule.rows if r.kind != "unconfirmed"):
        schedule.status = "partial"
        schedule.reason = (
            schedule.reason or "some rows could not be converted to the account currency"
        )
    return schedule


def is_special_text(text: str | None) -> bool:
    low = (text or "").casefold()
    return any(w in low for w in SPECIAL_WORDS)


def schedule_total(schedule: HoldingSchedule) -> tuple[Decimal, Decimal, Decimal]:
    """(declared, estimated, unconfirmed) gross totals in the account currency; rows that could
    not be converted are left out (the schedule's status says so)."""
    declared = sum((r.amount for r in schedule.rows if r.kind == "declared" and r.amount), ZERO)
    estimated = sum((r.amount for r in schedule.rows if r.kind == "estimated" and r.amount), ZERO)
    unconfirmed = sum(
        (r.amount for r in schedule.rows if r.kind == "unconfirmed" and r.amount), ZERO
    )
    return declared, estimated, unconfirmed


def snapshot_body(s: HoldingSchedule) -> dict[str, object]:
    """What `dividend_forecast_snapshot.schedule` stores for one ticker."""
    return {
        "frequency": s.frequency,
        "status": s.status,
        "reason": s.reason,
        "payments_seen": s.payments_seen,
        "rows": [
            {
                "kind": r.kind,
                "ex_date": r.ex_date.isoformat() if r.ex_date else None,
                "pay_date": r.pay_date.isoformat(),
                "per_share": str(r.per_share),
                "currency": r.currency,
                "quantity": str(r.quantity),
                "amount": None if r.amount is None else str(r.amount),
            }
            for r in s.rows
        ],
    }
