# ruff: noqa: E501 - messages stay whole
"""Investable universe and point-in-time membership (plan M1.1, M1.7).

The universe is the *covered* universe: instruments the app has identity for, with the data
behind them. A member is active over `[active_from, active_to]`; a delisted member stays in
every snapshot dated inside its interval, so a past screen never silently drops it.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from steward.analytics.contract import input_hash


@dataclass(frozen=True)
class UniverseMember:
    ticker: str
    name: str | None = None
    isin: str | None = None
    asset_type: str | None = None
    issuer_id: int | None = None
    security_id: int | None = None
    listing_id: int | None = None
    mic: str | None = None
    exchange: str | None = None
    country: str | None = None
    currency: str | None = None
    status: str = "active"  # active, suspended, terminated, cancelled
    active_from: dt.date | None = None
    active_to: dt.date | None = None
    identity_status: str | None = None
    has_statements: bool = False
    has_prices: bool = False
    history_from: dt.date | None = None
    price_symbol: str | None = None

    def active_on(self, day: dt.date) -> bool:
        """Interval membership. An unknown start counts as "since the beginning of our data",
        an unknown end as "not known to have ended"; `interval_unknown_end` says which members
        rest on that assumption."""
        if self.active_from is not None and day < self.active_from:
            return False
        return not (self.active_to is not None and day > self.active_to)

    @property
    def interval_unknown_end(self) -> bool:
        return self.status in ("terminated", "cancelled") and self.active_to is None


def members_at(members: Iterable[UniverseMember], as_of: dt.date) -> list[UniverseMember]:
    """The members whose active interval contains `as_of` (delisted names included while they
    were listed)."""
    return [m for m in members if m.active_on(as_of)]


def scope_for_date(
    members: Iterable[UniverseMember], as_of: dt.date, today: dt.date
) -> tuple[list[UniverseMember], list[str]]:
    """Members to screen on `as_of`. Today: listed members (terminated/cancelled are left out
    and counted). A past date: interval membership, so delisted names stay; a member whose end
    date is unknown is kept and named in a warning, never silently dropped or inferred out."""
    items = list(members)
    warnings: list[str] = []
    if as_of >= today:
        kept = [m for m in items if m.active_on(as_of) and m.status in ("active", "suspended")]
        gone = sum(1 for m in items if m.status in ("terminated", "cancelled"))
        if gone:
            warnings.append(
                f"{gone} terminated or cancelled listing(s) are not in today's universe"
            )
        return kept, warnings
    kept = members_at(items, as_of)
    unknown_end = sum(1 for m in kept if m.interval_unknown_end)
    if unknown_end:
        warnings.append(
            f"{unknown_end} member(s) are terminated with no known end date; kept in the "
            f"{as_of} universe because their delisting date is unknown"
        )
    return kept, warnings


def inclusion_reason(member: UniverseMember) -> str:
    parts = []
    if member.has_statements:
        parts.append("filed statements")
    if member.has_prices:
        parts.append("price history")
    if member.identity_status == "confirmed":
        parts.append("confirmed identity")
    return "covered: " + (", ".join(parts) if parts else "instrument known, no data yet")


@dataclass(frozen=True)
class UniverseSnapshot:
    as_of: dt.date
    known_at: dt.datetime
    source: str
    members: tuple[UniverseMember, ...]

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            ("|".join(sorted(m.ticker for m in self.members)) + f"@{self.as_of}").encode()
        ).hexdigest()[:16]

    def tickers(self) -> list[str]:
        return sorted(m.ticker for m in self.members)


def freeze(
    members: Sequence[UniverseMember],
    as_of: dt.date,
    known_at: dt.datetime,
    source: str = "investment_universe",
) -> UniverseSnapshot:
    """Freeze membership at `as_of`. Members that were not active on that date are left out;
    members that later ended are kept, which is the point."""
    return UniverseSnapshot(as_of, known_at, source, tuple(members_at(members, as_of)))


@dataclass(frozen=True)
class RebalanceSlice:
    rebalance_date: dt.date
    universe: UniverseSnapshot
    metrics_cutoff: dt.datetime  # only facts known by this time may be used
    cost_bps: int


@dataclass
class FrozenBacktestPlan:
    """The universe, metric cutoff and cost assumption fixed per rebalance date. Nothing here
    is recomputed from the current universe: `assert_point_in_time` rejects a plan whose slice
    was built from a later snapshot."""

    slices: list[RebalanceSlice] = field(default_factory=list)
    transaction_cost_bps: int = 0

    def add(
        self, rebalance_date: dt.date, universe: UniverseSnapshot, metrics_cutoff: dt.datetime
    ) -> None:
        self.slices.append(
            RebalanceSlice(rebalance_date, universe, metrics_cutoff, self.transaction_cost_bps)
        )

    def assert_point_in_time(self) -> None:
        for s in self.slices:
            if s.universe.as_of > s.rebalance_date:
                raise ValueError(
                    f"universe {s.universe.as_of} is later than rebalance {s.rebalance_date}"
                )
            if s.metrics_cutoff.date() > s.rebalance_date:
                raise ValueError(
                    f"metrics known {s.metrics_cutoff:%Y-%m-%d} are later than rebalance {s.rebalance_date}"
                )

    @property
    def fingerprint(self) -> str:
        return input_hash(
            [
                (s.rebalance_date, s.universe.fingerprint, s.metrics_cutoff, s.cost_bps)
                for s in self.slices
            ]
        )


def survivorship_warning(plan: FrozenBacktestPlan, current: Sequence[UniverseMember]) -> list[str]:
    """Names in a rebalance universe that are no longer active today: proof that delisted
    securities stayed in. Empty when none ever left, which for a backtest is itself a warning."""
    now_active = {m.ticker for m in current if m.status == "active" and m.active_to is None}
    left = sorted({t for s in plan.slices for t in s.universe.tickers() if t not in now_active})
    if not left and plan.slices:
        return ["no delisted member appears in any rebalance universe: check for survivorship bias"]
    return []


def describe_universe(members: Sequence[UniverseMember]) -> dict[str, object]:
    """Coverage numbers shown with every screen: count, markets and data availability."""
    n = len(members)

    def share(flag: str) -> int:
        return sum(1 for m in members if getattr(m, flag))

    countries: dict[str, int] = {}
    for m in members:
        countries[m.country or "unknown"] = countries.get(m.country or "unknown", 0) + 1
    return {
        "members": n,
        "active": sum(1 for m in members if m.status == "active"),
        "with_statements": share("has_statements"),
        "with_prices": share("has_prices"),
        "countries": dict(sorted(countries.items(), key=lambda kv: (-kv[1], kv[0]))),
        "exchanges": sorted({m.mic or m.exchange or "unknown" for m in members}),
    }
