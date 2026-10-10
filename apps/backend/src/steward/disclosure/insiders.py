"""P4: insider economic activity, as pure functions over already-classified Form 4/5 rows.

The deterministic classifier (`ownership/insider_classify.py`) decides what each row is. This module
decides what may be COUNTED as discretionary buying or selling and how it is grouped:

  * only open-market purchases and sales count: awards, option exercises, tax withholding, gifts,
    conversions and unknowns never do;
  * an option exercise and a same-day sale of the exercised shares are one bundle, not an insider
    choosing to sell, so a bundled sale is shown but not counted;
  * a trade reported under a Rule 10b5-1 plan is kept out of the headline and counted apart, and a
    filing with no plan indicator is counted apart as "unknown". A plan is never inferred;
  * a row is visible only once its filing was known, and an amendment replaces the original only
    once the amendment was known;
  * a reviewer override replaces the class only from the moment it was made, and keeps its reason;
  * nothing here says why anyone traded.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Literal

ZERO = Decimal(0)
ECONOMIC_CLASSES = (
    "open_market_buy",
    "open_market_sell",
    "option_exercise",
    "grant_award",
    "gift",
    "tax_withholding",
    "conversion",
    "other",
    "holding_snapshot",
)
WINDOWS = (30, 90, 365)
Role = Literal["officer", "director", "ten_percent_owner", "other"]
ROLES: tuple[Role, ...] = ("officer", "director", "ten_percent_owner", "other")
PlanStatus = Literal["plan", "not_plan", "unknown"]


@dataclass(frozen=True)
class InsiderTx:
    accession: str
    row_index: int
    insider: str  # reporting owner CIK: one person is one insider across direct and indirect rows
    name: str | None
    role: Role
    tx_date: dt.date | None
    filed: dt.date
    known_at: dt.datetime
    economic_class: str | None
    derivative: bool = False
    shares: Decimal | None = None
    price: Decimal | None = None
    shares_after: Decimal | None = None
    direct_indirect: str | None = None  # "D" or "I"
    plan: bool | None = None  # the form's Rule 10b5-1 indicator, as stated; None if absent
    equity_swap: bool | None = None
    # Set when a later filing restated this one, with the moment that filing became known.
    superseded_by_known_at: dt.datetime | None = None
    # True when the class came from an unambiguous code and footnotes, not from a guess.
    class_confirmed: bool = False

    @property
    def key(self) -> tuple[str, int]:
        return (self.accession, self.row_index)


def role_of(is_officer: bool, is_director: bool, is_ten_percent_owner: bool) -> Role:
    """The single role a person is counted under: officer first, then director, then 10% owner."""
    if is_officer:
        return "officer"
    if is_director:
        return "director"
    if is_ten_percent_owner:
        return "ten_percent_owner"
    return "other"


# ----------------------------------------------------------------------------------- overrides
@dataclass(frozen=True)
class Override:
    accession: str
    row_index: int
    new_class: str
    reason: str
    decided_by: str
    decided_at: dt.datetime
    original_class: str | None = None

    def __post_init__(self) -> None:
        if self.new_class not in ECONOMIC_CLASSES:
            raise ValueError(f"unknown class {self.new_class!r}")
        if len(self.reason.strip()) < 10:
            raise ValueError("an override needs a reason of at least 10 characters")
        if not self.decided_by.strip():
            raise ValueError("an override needs to say who made it")


def effective_class(tx: InsiderTx, overrides: Iterable[Override], as_of: dt.datetime) -> str | None:
    """The class used at `as_of`: the latest override already made, else the classifier's."""
    mine = [o for o in overrides if (o.accession, o.row_index) == tx.key and o.decided_at <= as_of]
    return max(mine, key=lambda o: o.decided_at).new_class if mine else tx.economic_class


# ------------------------------------------------------------------------------- visibility
def visible(txs: Iterable[InsiderTx], as_of: dt.datetime) -> list[InsiderTx]:
    """Rows known by `as_of`, minus rows whose restating amendment was itself known by then."""
    return [
        t
        for t in txs
        if t.known_at <= as_of
        and not (t.superseded_by_known_at is not None and t.superseded_by_known_at <= as_of)
    ]


def adjust_for_splits(
    shares: Decimal | None,
    price: Decimal | None,
    tx_date: dt.date | None,
    splits: Iterable[tuple[dt.date, Decimal]],
    as_of: dt.date,
) -> tuple[Decimal | None, Decimal | None]:
    """Restate a trade to today's share count: each split after the trade date and on or before
    `as_of` multiplies shares and divides the price by its ratio. A split not yet effective at
    `as_of` is never applied, so a replay does not know about it."""
    if tx_date is None:
        return shares, price
    ratio = Decimal(1)
    for when, r in splits:
        if tx_date < when <= as_of:
            ratio *= r
    return (
        None if shares is None else shares * ratio,
        None if price is None else price / ratio,
    )


def restate_for_splits(
    txs: Iterable[InsiderTx],
    splits: Sequence[tuple[dt.date, Decimal]],
    as_of: dt.date,
) -> list[InsiderTx]:
    """Every row restated to the share count at `as_of`. Holdings after the trade scale with the
    shares; the price is divided so value is unchanged. `splits` must already be limited to splits
    known by the replay moment."""
    out: list[InsiderTx] = []
    for t in txs:
        shares, price = adjust_for_splits(t.shares, t.price, t.tx_date, splits, as_of)
        after, _ = adjust_for_splits(t.shares_after, None, t.tx_date, splits, as_of)
        out.append(replace(t, shares=shares, price=price, shares_after=after))
    return out


# ------------------------------------------------------------------------------- classification
def plan_status(tx: InsiderTx) -> PlanStatus:
    return "unknown" if tx.plan is None else "plan" if tx.plan else "not_plan"


def countable(tx: InsiderTx, cls: str | None) -> bool:
    """Open-market buy or sell, on a non-derivative line, with a real share count and price."""
    return (
        cls in ("open_market_buy", "open_market_sell")
        and not tx.derivative
        and tx.shares is not None
        and tx.shares > 0
        and tx.price is not None
        and tx.price > 0
    )


@dataclass(frozen=True)
class Bundle:
    insider: str
    day: dt.date
    exercised: Decimal
    sold: Decimal
    proceeds: Decimal
    rows: tuple[tuple[str, int], ...]

    @property
    def retained(self) -> Decimal:
        return self.exercised - self.sold


def find_bundles(
    txs: Sequence[InsiderTx], overrides: Iterable[Override], as_of: dt.datetime
) -> list[Bundle]:
    """An exercise and a same-day sale by the same insider, where the sale does not exceed the
    exercise, are one bundle. A sale larger than the exercise is not bundled: part of it is a
    choice, and the bundle rule never hides that."""
    ovr = list(overrides)
    ex: dict[tuple[str, dt.date], list[InsiderTx]] = defaultdict(list)
    sells: dict[tuple[str, dt.date], list[InsiderTx]] = defaultdict(list)
    for t in txs:
        if t.tx_date is None or t.shares is None:
            continue
        cls = effective_class(t, ovr, as_of)
        if cls == "option_exercise":
            ex[(t.insider, t.tx_date)].append(t)
        elif countable(t, cls) and cls == "open_market_sell":
            sells[(t.insider, t.tx_date)].append(t)
    out: list[Bundle] = []
    for k, sold_rows in sells.items():
        if k not in ex:
            continue
        # One exercise is reported twice on Form 4: the option leaves Table II (derivative) and
        # the shares arrive in Table I. Count the shares received when Table I states them, else
        # the options exercised, never both.
        received = [r for r in ex[k] if not r.derivative]
        exercised = sum((r.shares or ZERO for r in (received or ex[k])), ZERO)
        sold = sum((r.shares or ZERO for r in sold_rows), ZERO)
        if sold <= exercised:
            proceeds = sum(((r.shares or ZERO) * (r.price or ZERO) for r in sold_rows), ZERO)
            rows = tuple(r.key for r in [*ex[k], *sold_rows])
            out.append(Bundle(k[0], k[1], exercised, sold, proceeds, rows))
    return sorted(out, key=lambda b: (b.day, b.insider))


def _bundled_keys(bundles: Iterable[Bundle]) -> set[tuple[str, int]]:
    return {r for b in bundles for r in b.rows}


# ------------------------------------------------------------------------------- aggregation
@dataclass(frozen=True)
class NetRow:
    window_days: int
    role: str  # a role, or "all"
    buy_shares: Decimal
    sell_shares: Decimal
    buy_value: Decimal
    sell_value: Decimal
    trades: int
    insiders: int
    plan_trades_excluded: int  # reported under a 10b5-1 plan: counted apart, not in the net
    plan_unknown_trades: int  # no plan indicator: in the net, and counted here so it is visible
    bundled_sales_excluded: int

    @property
    def net_shares(self) -> Decimal:
        return self.buy_shares - self.sell_shares

    @property
    def net_value(self) -> Decimal:
        return self.buy_value - self.sell_value


def net_by_role(
    txs: Sequence[InsiderTx],
    as_of: dt.datetime,
    *,
    windows: Sequence[int] = WINDOWS,
    overrides: Iterable[Override] = (),
) -> list[NetRow]:
    """Discretionary net open-market buying by role and trailing window, from visible rows only.
    The window counts back from `as_of` by TRANSACTION date; a row with no transaction date has
    none to place and is left out of every window rather than guessed."""
    ovr = list(overrides)
    rows = visible(txs, as_of)
    bundled = _bundled_keys(find_bundles(rows, ovr, as_of))
    today = as_of.date()
    out: list[NetRow] = []
    for w in windows:
        start = today - dt.timedelta(days=w)
        buckets: dict[str, list[InsiderTx]] = defaultdict(list)
        excluded_plan: dict[str, int] = defaultdict(int)
        unknown_plan: dict[str, int] = defaultdict(int)
        bundled_ct: dict[str, int] = defaultdict(int)
        for t in rows:
            if t.tx_date is None or not start < t.tx_date <= today:
                continue
            cls = effective_class(t, ovr, as_of)
            if not countable(t, cls):
                continue
            if t.key in bundled:
                for r in (t.role, "all"):
                    bundled_ct[r] += 1
                continue
            if t.plan is True:
                for r in (t.role, "all"):
                    excluded_plan[r] += 1
                continue
            for r in (t.role, "all"):
                buckets[r].append(t)
                if t.plan is None:
                    unknown_plan[r] += 1
        for role in (*ROLES, "all"):
            members = buckets.get(role, [])
            buy = [m for m in members if effective_class(m, ovr, as_of) == "open_market_buy"]
            sell = [m for m in members if effective_class(m, ovr, as_of) == "open_market_sell"]
            out.append(
                NetRow(
                    w,
                    role,
                    sum((m.shares or ZERO for m in buy), ZERO),
                    sum((m.shares or ZERO for m in sell), ZERO),
                    sum(((m.shares or ZERO) * (m.price or ZERO) for m in buy), ZERO),
                    sum(((m.shares or ZERO) * (m.price or ZERO) for m in sell), ZERO),
                    len(members),
                    len({m.insider for m in members}),
                    excluded_plan.get(role, 0),
                    unknown_plan.get(role, 0),
                    bundled_ct.get(role, 0),
                )
            )
    return out


@dataclass(frozen=True)
class Cluster:
    start: dt.date
    end: dt.date
    insiders: tuple[str, ...]
    shares: Decimal
    value: Decimal


def clusters(
    txs: Sequence[InsiderTx],
    as_of: dt.datetime,
    *,
    min_insiders: int = 3,
    window_days: int = 30,
    overrides: Iterable[Override] = (),
) -> list[Cluster]:
    """Runs of discretionary open-market buys by at least `min_insiders` DIFFERENT people within
    `window_days`. A person buying directly and through a trust is one insider, because insiders
    are counted by reporting-owner id. Plan trades and bundled sales are not buys of choice."""
    ovr = list(overrides)
    rows = visible(txs, as_of)
    bundled = _bundled_keys(find_bundles(rows, ovr, as_of))
    buys = sorted(
        (
            t
            for t in rows
            if t.tx_date is not None
            and t.key not in bundled
            and t.plan is not True
            and countable(t, effective_class(t, ovr, as_of))
            and effective_class(t, ovr, as_of) == "open_market_buy"
        ),
        key=lambda t: (t.tx_date, t.insider),
    )
    found: list[Cluster] = []
    i = 0
    while i < len(buys):
        first = buys[i].tx_date
        assert first is not None
        run = [
            b for b in buys[i:] if b.tx_date is not None and (b.tx_date - first).days <= window_days
        ]
        people = sorted({b.insider for b in run})
        if len(people) >= min_insiders:
            last = max(b.tx_date for b in run if b.tx_date is not None)
            found.append(
                Cluster(
                    first,
                    last,
                    tuple(people),
                    sum((b.shares or ZERO for b in run), ZERO),
                    sum(((b.shares or ZERO) * (b.price or ZERO) for b in run), ZERO),
                )
            )
            i += len(run)
        else:
            i += 1
    return found


def position_change(tx: InsiderTx, cls: str | None = None) -> Decimal | None:
    """Percent change of the insider's holding in this line caused by the trade, from the
    post-trade holding the form reports. None when it cannot be worked out. `cls` is the class in
    force (the owner's override if there is one); it defaults to the classifier's."""
    if tx.shares is None or tx.shares_after is None or tx.derivative:
        return None
    sign = -1 if (cls or tx.economic_class) == "open_market_sell" else 1
    before = tx.shares_after - sign * tx.shares
    if before <= 0:
        return None
    return sign * tx.shares / before * 100


def caveats(tx: InsiderTx) -> list[str]:
    """Plain statements a reader needs beside a trade. None says anything about motive."""
    out: list[str] = []
    if tx.plan is True:
        out.append("Reported under a Rule 10b5-1 trading plan.")
    elif tx.plan is None:
        out.append("The filing gives no plan indicator.")
    if tx.derivative:
        out.append("A derivative line, not shares of the stock.")
    if tx.direct_indirect == "I":
        out.append("Held indirectly (for example through a trust or entity).")
    if tx.equity_swap:
        out.append("An equity swap is involved.")
    if tx.price is not None and tx.price == 0:
        out.append("Reported at a price of zero.")
    if tx.tx_date is None:
        out.append("No transaction date on the filing.")
    return out


# -------------------------------------------------------------------------------------- alerts
@dataclass(frozen=True)
class AlertCandidate:
    tx: InsiderTx  # the first line of the group, for the headline and links
    value: Decimal  # the whole group's value
    reason: str
    rows: tuple[InsiderTx, ...] = ()  # every line of one person, day and direction

    @property
    def shares(self) -> Decimal | None:
        lines = self.rows or (self.tx,)
        if any(r.shares is None for r in lines):
            return None
        return sum((r.shares or ZERO for r in lines), ZERO)


def alerts(
    txs: Sequence[InsiderTx],
    as_of: dt.datetime,
    *,
    min_value: Decimal,
    since: dt.datetime | None = None,
    overrides: Iterable[Override] = (),
) -> list[AlertCandidate]:
    """Material, confirmed, open-market trades not already alerted. A collection failure never
    alerts (nothing visible means nothing returned). Duplicates of one trade, such as an original
    and an unlinked amendment, collapse to the earliest known."""
    ovr = list(overrides)
    rows = visible(txs, as_of)
    bundled = _bundled_keys(find_bundles(rows, ovr, as_of))
    # A line repeated in a LATER filing (an amendment not linked to its original) is the same
    # trade and counts once; identical lines inside ONE filing are separate fills and all count.
    first_filing: dict[
        tuple[str, dt.date | None, str | None, Decimal | None, Decimal | None], str
    ] = {}
    groups: dict[tuple[str, dt.date | None, str | None], list[InsiderTx]] = defaultdict(list)
    for t in sorted(rows, key=lambda r: (r.known_at, r.accession, r.row_index)):
        cls = effective_class(t, ovr, as_of)
        confirmed = t.class_confirmed or any((o.accession, o.row_index) == t.key for o in ovr)
        if not (confirmed and countable(t, cls)) or t.key in bundled or t.plan is True:
            continue
        line = (t.insider, t.tx_date, cls, t.shares, t.price)
        if first_filing.setdefault(line, t.accession) != t.accession:
            continue
        groups[(t.insider, t.tx_date, cls)].append(t)
    # Materiality is judged per person, day and direction: a large sale reported as many small
    # lots is one material sale. A group alerts once something in it is newly known.
    out: list[AlertCandidate] = []
    for (_who, _day, cls), members in groups.items():
        if since is not None and all(m.known_at <= since for m in members):
            continue
        value = sum(((m.shares or ZERO) * (m.price or ZERO) for m in members), ZERO)
        if value >= min_value:
            reason = "open-market purchase" if cls == "open_market_buy" else "open-market sale"
            out.append(AlertCandidate(members[0], value, reason, tuple(members)))
    return sorted(out, key=lambda a: (a.tx.known_at, a.tx.accession, a.tx.row_index))


@dataclass(frozen=True)
class InsiderView:
    """What the page shows for one holding at one date."""

    as_of: dt.datetime
    net: tuple[NetRow, ...]
    clusters: tuple[Cluster, ...]
    bundles: tuple[Bundle, ...]
    undated: int
    notes: tuple[str, ...] = field(default_factory=tuple)


def build_view(
    txs: Sequence[InsiderTx],
    as_of: dt.datetime,
    *,
    overrides: Iterable[Override] = (),
) -> InsiderView:
    ovr = list(overrides)
    rows = visible(txs, as_of)
    bundles = find_bundles(rows, ovr, as_of)
    undated = sum(1 for t in rows if t.tx_date is None)
    notes = ["Counts open-market purchases and sales only. This does not say why anyone traded."]
    if undated:
        notes.append(f"{undated} rows have no transaction date and are not in any window.")
    return InsiderView(
        as_of,
        tuple(net_by_role(txs, as_of, overrides=ovr)),
        tuple(clusters(txs, as_of, overrides=ovr)),
        tuple(bundles),
        undated,
        tuple(notes),
    )


def by_class(
    txs: Iterable[InsiderTx], overrides: Iterable[Override], as_of: dt.datetime
) -> Mapping[str, int]:
    """Counts of visible rows per effective class, `unclassified` when the classifier gave none."""
    counts: dict[str, int] = defaultdict(int)
    ovr = list(overrides)
    for t in visible(txs, as_of):
        counts[effective_class(t, ovr, as_of) or "unclassified"] += 1
    return dict(counts)
