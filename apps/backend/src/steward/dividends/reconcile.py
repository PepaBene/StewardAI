# ruff: noqa: E501, RUF003
"""Event reconciliation (plan I1.1, I1.3). Pure: no I/O.

Every Trading212 dividend payment is matched to one declared `cash_dividend` of the same holding
whose pay date (or, without one, whose ex-date) falls in the matching window. The match carries
the received amount, the declared amount for the shares held, the FX rate used and the
withholding gap. Nothing is dropped: a payment with no declared record and a declared event with
no payment are both listed.

Declared events from several sources (Twelve Data, Yahoo, the owner) for the same payment are
grouped by ex-date; a group whose amounts disagree is reported as a source disagreement.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from steward.dividends.model import ZERO, DeclaredEvent, ReceivedPayment

# Units of `to` for one `from` on a day; None without a stored rate.
FxFn = Callable[[str, str, dt.date], Decimal | None]

SAME_EVENT_DAYS = 4  # ex-dates this close from two sources are the same payment
AGREE_PER_SHARE = Decimal("0.02")  # two per-share figures within 2 % agree
EX_TO_PAY_MAX_DAYS = 75  # without a pay date, a payment may land this long after the ex-date
SOURCE_PRIORITY = {"owner_review": 0, "manual": 0, "owner": 0, "twelve_data": 1, "yahoo": 2}


@dataclass
class EventGroup:
    """One payment as declared by one or more sources; `event` is the one used."""

    event: DeclaredEvent
    alternatives: list[DeclaredEvent] = field(default_factory=list)

    @property
    def sources(self) -> list[str]:
        return sorted({self.event.source, *(a.source for a in self.alternatives)})

    @property
    def status(self) -> str:
        return self.event.status

    def disagreement(self) -> str | None:
        amounts = [self.event, *self.alternatives]
        base = self.event.amount
        for other in amounts[1:]:
            if base > 0 and abs(other.amount - base) / base > AGREE_PER_SHARE:
                return (
                    f"{other.source} states {other.amount} {other.currency or ''} per share against "
                    f"{self.event.source}'s {base} {self.event.currency or ''} (ex-date "
                    f"{self.event.ex_date.isoformat()})"
                ).replace("  ", " ")
            if other.currency and self.event.currency and other.currency != self.event.currency:
                return (
                    f"{other.source} states the payment in {other.currency}, {self.event.source} "
                    f"in {self.event.currency} (ex-date {self.event.ex_date.isoformat()})"
                )
        return None


def _rank(e: DeclaredEvent) -> tuple[int, int, float]:
    status = {"confirmed": 0, "suggested": 1}.get(e.status, 2)
    known = -(e.known_at.timestamp() if e.known_at else 0.0)
    return (status, SOURCE_PRIORITY.get(e.source, 3), known)


def group_events(events: Iterable[DeclaredEvent]) -> list[EventGroup]:
    """Rejected events are left out. Events of one holding with ex-dates within
    `SAME_EVENT_DAYS` are one payment; the confirmed, then highest-priority source leads."""
    live = sorted(
        (e for e in events if e.status != "rejected"), key=lambda e: (e.ticker, e.ex_date)
    )
    clusters: list[list[DeclaredEvent]] = []
    for e in live:
        last = clusters[-1] if clusters else None
        if (
            last is not None
            and last[0].ticker == e.ticker
            and (e.ex_date - last[-1].ex_date).days <= SAME_EVENT_DAYS
        ):
            last.append(e)
        else:
            clusters.append([e])
    groups: list[EventGroup] = []
    for cluster in clusters:
        ordered = sorted(cluster, key=_rank)
        groups.append(EventGroup(ordered[0], ordered[1:]))
    return groups


@dataclass
class Match:
    payment: ReceivedPayment
    group: EventGroup | None
    declared_amount: Decimal | None = None  # per share × shares, declared currency
    declared_currency: str | None = None
    declared_in_account: Decimal | None = None
    fx_rate: Decimal | None = None
    withholding_gap: Decimal | None = None  # declared (account currency) − received
    withholding_share: Decimal | None = None
    per_share_agrees: bool | None = None
    agrees: bool | None = None
    note: str = ""


@dataclass
class Reconciliation:
    matches: list[Match]
    unmatched_events: list[EventGroup]
    disagreements: list[str]

    @property
    def matched(self) -> list[Match]:
        return [m for m in self.matches if m.group is not None]

    @property
    def unmatched_payments(self) -> list[Match]:
        return [m for m in self.matches if m.group is None]

    def withholding_share(self, ticker: str) -> Decimal | None:
        """Median withholding share of the holding's matched payments, when any is known."""
        shares = sorted(
            m.withholding_share
            for m in self.matches
            if m.payment.ticker == ticker and m.withholding_share is not None
        )
        if not shares:
            return None
        mid = len(shares) // 2
        return shares[mid] if len(shares) % 2 else (shares[mid - 1] + shares[mid]) / 2


def _distance(group: EventGroup, payment: ReceivedPayment, window: int) -> int | None:
    e = group.event
    if e.payment_date is not None:
        gap = abs((payment.paid_on - e.payment_date).days)
        return gap if gap <= window else None
    lag = (payment.paid_on - e.ex_date).days
    return lag if 0 <= lag <= EX_TO_PAY_MAX_DAYS + window else None


def _measure(m: Match, account: str | None, fx: FxFn, tolerance: Decimal) -> None:
    e = m.group.event if m.group else None
    p = m.payment
    if e is None:
        m.note = "no declared record"
        return
    if p.gross_per_share is not None and e.amount > 0:
        m.per_share_agrees = abs(p.gross_per_share - e.amount) / e.amount <= AGREE_PER_SHARE
    if p.quantity is None or p.quantity <= 0:
        m.note = "Trading212 sent no share count for the payment"
        m.agrees = m.per_share_agrees
        return
    m.declared_amount = e.amount * p.quantity
    m.declared_currency = e.currency
    if e.currency and account:
        m.fx_rate = fx(e.currency, account, p.paid_on)
        if m.fx_rate is not None:
            m.declared_in_account = m.declared_amount * m.fx_rate
            m.withholding_gap = m.declared_in_account - p.amount
            if m.declared_in_account > 0:
                m.withholding_share = m.withholding_gap / m.declared_in_account
        else:
            m.note = f"no stored {e.currency}/{account} rate for {p.paid_on.isoformat()}"
    elif not e.currency:
        m.note = "the declared record states no currency"
    if m.per_share_agrees is not None:
        m.agrees = m.per_share_agrees
    elif m.declared_in_account is not None and m.declared_in_account > 0:
        ratio = p.amount / m.declared_in_account
        # Received is net of withholding: at most the declared gross, not less than the tolerance.
        m.agrees = Decimal(1) - tolerance <= ratio <= Decimal(1) + AGREE_PER_SHARE


def reconcile(
    events: Sequence[DeclaredEvent],
    payments: Sequence[ReceivedPayment],
    *,
    account_currency: str | None,
    fx: FxFn,
    window_days: int,
    tolerance: Decimal,
    today: dt.date,
    held_from: dict[str, dt.date] | None = None,
) -> Reconciliation:
    """Greedy nearest match per holding: each declared payment matches at most one receipt."""
    groups = group_events(events)
    by_ticker: dict[str, list[EventGroup]] = {}
    for g in groups:
        by_ticker.setdefault(g.event.ticker, []).append(g)
    disagreements = [text for g in groups if (text := g.disagreement())]
    matches: list[Match] = []
    used: set[int] = set()
    candidates: list[tuple[int, int, int]] = []  # (distance, payment index, group id)
    ordered = sorted(payments, key=lambda p: (p.ticker, p.paid_on, p.id))
    index = {id(g): g for g in groups}
    for i, p in enumerate(ordered):
        for g in by_ticker.get(p.ticker, []):
            d = _distance(g, p, window_days)
            if d is not None:
                candidates.append((d, i, id(g)))
    chosen: dict[int, EventGroup] = {}
    for _dist, i, gid in sorted(candidates):
        if i in chosen or gid in used:
            continue
        chosen[i] = index[gid]
        used.add(gid)
    for i, p in enumerate(ordered):
        m = Match(p, chosen.get(i))
        _measure(m, account_currency, fx, tolerance)
        matches.append(m)
    unmatched: list[EventGroup] = []
    for g in groups:
        if id(g) in used:
            continue
        e = g.event
        due = e.pay_or_ex + dt.timedelta(days=window_days)
        start = (held_from or {}).get(e.ticker)
        if due < today and start is not None and e.ex_date >= start:
            unmatched.append(g)
    return Reconciliation(matches, unmatched, disagreements)


def auto_confirmable(rec: Reconciliation) -> list[tuple[EventGroup, Match]]:
    """Suggested events whose matched receipt agrees with them: the documented auto-confirm rule
    (same holding, receipt in the pay-date window, per-share gross within 2 % or the received
    amount between the declared gross less the tolerance and the declared gross)."""
    out: list[tuple[EventGroup, Match]] = []
    for m in rec.matched:
        assert m.group is not None
        if m.group.event.status == "suggested" and m.agrees:
            out.append((m.group, m))
    return out


@dataclass(frozen=True)
class FiledDividend:
    """One quarter's per-share dividend as the issuer filed it (XBRL, declared or paid)."""

    ticker: str
    period_start: dt.date
    period_end: dt.date
    per_share: Decimal
    currency: str | None
    concept: str
    form: str | None
    url: str


# Days an ex-date may fall before / after a filed quarter: a dividend paid in the quarter goes
# ex shortly before it at the earliest, one declared in it goes ex up to ~2 months after.
FILED_WINDOWS = {"CashPaid": (25, 0), "Declared": (0, 75)}
FILED_WINDOW_OTHER = (25, 75)
SPLIT_RATIOS = (2, 3, 4, 5, 10, 20)  # provider history is split-adjusted, older filings are not
MIN_CORROBORATED = 4  # filed matches needed before a series' older history counts


@dataclass(frozen=True)
class FilingCheck:
    group: EventGroup
    filed: FiledDividend | None  # the filed quarter that agrees; None for older trusted history
    split: int = 1  # the split ratio between the filed and the provider's figure
    corroborated: int = 0  # for older history: events of the series the filings agree with


def _near(f: FiledDividend, e: DeclaredEvent) -> bool:
    """The filed quarter may hold this event: same stated currency, ex-date in its window."""
    if not f.currency or f.currency != e.currency:
        return False
    before, after = next(
        (w for key, w in FILED_WINDOWS.items() if f.concept.endswith(key)), FILED_WINDOW_OTHER
    )
    return (
        f.period_start - dt.timedelta(days=before)
        <= e.ex_date
        <= f.period_end + dt.timedelta(days=after)
    )


def _split_agreeing(filed: Decimal, amount: Decimal) -> int | None:
    for k in (1, *SPLIT_RATIOS):
        target = amount * k
        if target > 0 and abs(filed - target) / target <= AGREE_PER_SHARE:
            return k
    return None


def filing_confirmable(
    groups: Sequence[EventGroup], filed: Sequence[FiledDividend]
) -> list[FilingCheck]:
    """Suggested events the issuer's filings back. An event is backed when a filed quarter near
    its ex-date states the same per-share amount (or a whole split multiple of it). Events older
    than every filing are backed by the series: only when at least `MIN_CORROBORATED` events of
    the holding agree with the filings and none inside the filed record contradicts them."""
    facts: dict[str, list[FiledDividend]] = {}
    for f in filed:
        facts.setdefault(f.ticker, []).append(f)
    by_ticker: dict[str, list[EventGroup]] = {}
    for g in groups:
        by_ticker.setdefault(g.event.ticker, []).append(g)
    out: list[FilingCheck] = []
    for ticker, gs in by_ticker.items():
        rows = facts.get(ticker)
        if not rows:
            continue
        first = min(f.period_start for f in rows)
        agreed: list[FilingCheck] = []
        older: list[EventGroup] = []
        contradicted = 0
        for g in gs:
            e = g.event
            near = [f for f in rows if _near(f, e)]
            if not near:
                if e.ex_date < first:
                    older.append(g)
                continue
            hit = next(
                ((f, k) for f in near if (k := _split_agreeing(f.per_share, e.amount))), None
            )
            if hit is None:
                contradicted += 1
            else:
                agreed.append(FilingCheck(g, hit[0], hit[1]))
        out.extend(c for c in agreed if c.group.status == "suggested")
        if len(agreed) >= MIN_CORROBORATED and contradicted == 0:
            out.extend(
                FilingCheck(g, None, corroborated=len(agreed))
                for g in older
                if g.status == "suggested"
            )
    return out


def total_received(payments: Iterable[ReceivedPayment]) -> Decimal:
    return sum((p.amount for p in payments), ZERO)
