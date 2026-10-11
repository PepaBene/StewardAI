"""The yearly decision scorecard (M7.9): pure arithmetic on journal entries and the prices that
followed them. Every section shows its sample size and says "too few decisions" below
`MIN_DECISIONS` rather than reporting a median of two. Returns are price changes in the
instrument's own currency, without dividends, so a section never mixes currencies."""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable
from statistics import median
from typing import Literal

from pydantic import BaseModel, Field

from steward.sources.market import quote_unit_of

MIN_DECISIONS = 5
BUYS = ("buy", "add")
SALES = ("trim", "sell")

SectionStatus = Literal["ok", "too_few", "unavailable"]


class DecisionOutcome(BaseModel):
    """One journal entry joined to what the market did afterwards."""

    id: int
    ticker: str | None
    decision: str
    decided_on: dt.date
    price_at_decision: float | None = None
    price_now: float | None = None
    price_now_date: dt.date | None = None
    currency_at_decision: str | None = None
    currency_now: str | None = None
    conviction: int | None = None
    main_reason: str | None = None
    sell_target: float | None = None
    bear_case: float | None = Field(
        default=None, description="The thesis's bear-case price when the entry was written."
    )
    low_since: float | None = Field(
        default=None, description="Lowest stored close after the decision."
    )
    hit_on: dt.date | None = Field(
        default=None, description="First close at or above the sell target after the decision."
    )

    @property
    def change(self) -> float | None:
        """Price change since the decision; None when a price is missing or currencies differ."""
        if not self.price_at_decision or self.price_now is None:
            return None
        if self.currency_at_decision and (
            not self.currency_now
            or quote_unit_of(self.currency_at_decision) != quote_unit_of(self.currency_now)
        ):
            return None  # currency of the stored series unknown or different: no change
        return self.price_now / self.price_at_decision - 1


def _change(
    then: float | None, now: float | None, ccy_then: str | None, ccy_now: str | None
) -> float | None:
    """Price change between two prices of one stock; None when a price is missing or the
    currencies differ or are unknown for the stored series."""
    if not then or now is None:
        return None
    if ccy_then and (not ccy_now or quote_unit_of(ccy_then) != quote_unit_of(ccy_now)):
        return None
    return now / then - 1


class SwitchOutcome(BaseModel):
    """One logged switch joined to what the two stocks did afterwards."""

    id: int
    switched_on: dt.date
    sold: str
    bought: str
    sold_price: float | None = None
    sold_currency: str | None = None
    sold_now: float | None = None
    sold_currency_now: str | None = None
    bought_price: float | None = None
    bought_currency: str | None = None
    bought_now: float | None = None
    bought_currency_now: str | None = None

    @property
    def sold_change(self) -> float | None:
        return _change(self.sold_price, self.sold_now, self.sold_currency, self.sold_currency_now)

    @property
    def bought_change(self) -> float | None:
        return _change(
            self.bought_price, self.bought_now, self.bought_currency, self.bought_currency_now
        )

    @property
    def difference(self) -> float | None:
        """Bought minus sold, in price change since the switch; positive: the switch paid."""
        b, s = self.bought_change, self.sold_change
        return b - s if b is not None and s is not None else None


class Row(BaseModel):
    label: str
    entry_ids: list[int] = Field(
        default_factory=list, description="Journal entries the figure was computed from."
    )
    n: int
    median_change: float | None = None
    share: float | None = Field(default=None, description="Share of the n with the stated result.")
    detail: str | None = None


class Section(BaseModel):
    id: str
    title: str
    status: SectionStatus
    n: int = 0
    note: str
    rows: list[Row] = Field(default_factory=list)


class Scorecard(BaseModel):
    since: dt.date
    until: dt.date
    entries: int
    min_decisions: int = MIN_DECISIONS
    sections: list[Section]
    method: str = (
        "Price change from the price written in the journal to the latest stored close, in the "
        "instrument's currency, without dividends. A section needs at least "
        f"{MIN_DECISIONS} decisions; fewer are listed but not summarised."
    )
    suggestions: list[str] = Field(default_factory=list)
    filtered_by: list[str] = Field(
        default_factory=list, description="What the decisions were limited to, if anything."
    )


def _too_few(id_: str, title: str, n: int, what: str) -> Section:
    return Section(
        id=id_,
        title=title,
        status="too_few",
        n=n,
        note=f"Too few decisions: {n} {what}; {MIN_DECISIONS} are needed for a figure.",
    )


def _group(outcomes: Iterable[DecisionOutcome], key: str) -> dict[str, list[DecisionOutcome]]:
    groups: dict[str, list[DecisionOutcome]] = defaultdict(list)
    for o in outcomes:
        value = getattr(o, key)
        groups[str(value) if value is not None else "not recorded"].append(o)
    return groups


def _row(label: str, group: list[DecisionOutcome], *, win_when_up: bool = True) -> Row:
    changes = [c for o in group if (c := o.change) is not None]
    wins = [c for c in changes if (c > 0) == win_when_up and c != 0]
    return Row(
        label=label,
        entry_ids=sorted(o.id for o in group),
        n=len(group),
        median_change=median(changes) if changes else None,
        share=len(wins) / len(changes) if changes else None,
        detail=None
        if len(changes) == len(group)
        else f"{len(group) - len(changes)} without a comparable price",
    )


def sell_targets(outcomes: list[DecisionOutcome]) -> Section:
    """Share of sell targets reached, and the median days it took."""
    set_ = [o for o in outcomes if o.decision in BUYS and o.sell_target is not None]
    if len(set_) < MIN_DECISIONS:
        return _too_few("sell_targets", "Sell targets", len(set_), "with a sell target")
    hit = [o for o in set_ if o.hit_on is not None]
    days = [(o.hit_on - o.decided_on).days for o in hit if o.hit_on is not None]
    return Section(
        id="sell_targets",
        title="Sell targets",
        status="ok",
        n=len(set_),
        note=f"{len(hit)} of {len(set_)} sell targets were reached"
        + (f"; the median time to reach one was {median(days):.0f} days." if days else "."),
        rows=[
            Row(
                label="Reached",
                entry_ids=sorted(o.id for o in set_),
                n=len(set_),
                share=len(hit) / len(set_),
                detail=f"median {median(days):.0f} days" if days else None,
            )
        ],
    )


def by_dimension(outcomes: list[DecisionOutcome], key: str, title: str, id_: str) -> Section:
    """Returns of the buys, grouped by conviction level or by main reason."""
    buys = [o for o in outcomes if o.decision in BUYS]
    if len(buys) < MIN_DECISIONS:
        return _too_few(id_, title, len(buys), "buys")
    groups = _group(buys, key)
    rows = [_row(label, group) for label, group in sorted(groups.items())]
    thin = [r.label for r in rows if r.n < MIN_DECISIONS]
    return Section(
        id=id_,
        title=title,
        status="ok",
        n=len(buys),
        note="Median price change since each buy."
        + (
            f" Groups under {MIN_DECISIONS} decisions ({', '.join(thin)}) are indicative only."
            if thin
            else ""
        ),
        rows=rows,
    )


def bought_vs_passed(outcomes: list[DecisionOutcome]) -> Section:
    """Buys against the ideas that were researched and rejected, both tracked from their date."""
    buys = [o for o in outcomes if o.decision in BUYS]
    passed = [o for o in outcomes if o.decision == "pass"]
    if len(buys) < MIN_DECISIONS or len(passed) < MIN_DECISIONS:
        return _too_few(
            "bought_vs_passed",
            "Bought compared with passed",
            min(len(buys), len(passed)),
            f"in the smaller group ({len(buys)} bought, {len(passed)} passed)",
        )
    rows = [_row("Bought", buys), _row("Passed", passed)]
    return Section(
        id="bought_vs_passed",
        title="Bought compared with passed",
        status="ok",
        n=len(buys) + len(passed),
        note="Median price change since the decision, for what was bought and what was passed.",
        rows=rows,
    )


def sales(outcomes: list[DecisionOutcome], decision: str, id_: str, title: str) -> Section:
    """What a stock did after it was sold or trimmed; a fall afterwards means the sale was right."""
    sold = [o for o in outcomes if o.decision == decision]
    if len(sold) < MIN_DECISIONS:
        return _too_few(id_, title, len(sold), f"{decision} decisions")
    row = _row("After the sale", sold, win_when_up=False)
    return Section(
        id=id_,
        title=title,
        status="ok",
        n=len(sold),
        note="Price change since the sale. A fall afterwards counts as the sale having been right.",
        rows=[row],
    )


def risk_held(outcomes: list[DecisionOutcome]) -> Section:
    """Did the bear case hold: how often a bought stock fell to the bear-case price the thesis
    named, and how far it fell at worst, from the price written in the journal."""
    title = "Did the risk estimates hold"
    set_ = [
        o
        for o in outcomes
        if o.decision in BUYS
        and o.bear_case is not None
        and o.low_since is not None
        and o.price_at_decision
    ]
    if len(set_) < MIN_DECISIONS:
        return _too_few(
            "risk_held", title, len(set_), "buys with a bear-case price and a stored price series"
        )
    breached = [o for o in set_ if o.low_since is not None and o.bear_case is not None
                and o.low_since <= o.bear_case]  # fmt: skip
    falls = [
        o.low_since / o.price_at_decision - 1 for o in set_ if o.low_since and o.price_at_decision
    ]
    return Section(
        id="risk_held",
        title=title,
        status="ok",
        n=len(set_),
        note="The lowest stored close since each decision against the bear-case price the thesis "
        "named when the entry was written. A bear case that was reached often means the risk "
        "was understated.",
        rows=[
            Row(
                label="Fell to the bear-case price or below",
                entry_ids=sorted(o.id for o in set_),
                n=len(set_),
                share=len(breached) / len(set_),
            ),
            Row(
                label="Worst fall from the decision price",
                entry_ids=sorted(o.id for o in set_),
                n=len(falls),
                median_change=median(falls) if falls else None,
            ),
        ],
    )


def switch_section(switches: list[SwitchOutcome] | None) -> Section:
    """The stock bought against the stock sold, from each switch date. Price changes only, in
    each stock's own currency, without dividends or the tax and costs the switch carried."""
    title = "Switches"
    if switches is None:
        return unavailable("switches", title, "The switch log could not be read.")
    compared = [s for s in switches if s.difference is not None]
    if len(compared) < MIN_DECISIONS:
        return _too_few("switches", title, len(compared), "switches with prices for both stocks")
    diffs = [d for s in compared if (d := s.difference) is not None]
    paid = [d for d in diffs if d > 0]
    return Section(
        id="switches",
        title=title,
        status="ok",
        n=len(compared),
        note="Price change of the stock bought minus that of the stock sold, from the switch "
        "date to the latest stored close. Tax and costs the switch carried are not included.",
        rows=[
            Row(
                label="The stock bought did better than the one sold",
                entry_ids=sorted(s.id for s in compared),
                n=len(compared),
                share=len(paid) / len(compared),
            ),
            Row(
                label="Median difference since the switch",
                entry_ids=sorted(s.id for s in compared),
                n=len(diffs),
                median_change=median(diffs),
            ),
        ],
    )


def unavailable(id_: str, title: str, reason: str) -> Section:
    return Section(id=id_, title=title, status="unavailable", note=reason)


def build_scorecard(
    outcomes: list[DecisionOutcome],
    *,
    since: dt.date,
    until: dt.date,
    ticker: str | None = None,
    reason: str | None = None,
    switches: list[SwitchOutcome] | None = None,
    safety_margin: float | None = None,
) -> Scorecard:
    window = [o for o in outcomes if since <= o.decided_on <= until and o.decision != "review"]
    filtered_by: list[str] = []
    if ticker:
        window = [o for o in window if o.ticker == ticker]
        filtered_by.append(f"security {ticker}")
    if reason:
        window = [o for o in window if o.main_reason == reason]
        filtered_by.append(f"main reason {reason}")
    sections = [
        sell_targets(window),
        by_dimension(window, "conviction", "Returns by conviction", "by_conviction"),
        by_dimension(window, "main_reason", "Returns by main reason", "by_reason"),
        bought_vs_passed(window),
        sales(window, "trim", "after_trim", "Profit-taking after trims"),
        sales(window, "sell", "after_sell", "Stocks after they were sold"),
        switch_section(
            [s for s in switches if since <= s.switched_on <= until]
            if switches is not None
            else None
        ),
        risk_held(window),
    ]
    suggestions: list[str] = []
    switched = next((s for s in sections if s.id == "switches"), None)
    if switched and switched.status == "ok":
        share, diff = switched.rows[0].share, switched.rows[1].median_change
        if share is not None and diff is not None and (share < 0.5 or diff <= 0):
            now = f" (now {safety_margin:.0%})" if safety_margin is not None else ""
            suggestions.append(
                "Switches did not beat holding the stock sold; raise the switching safety "
                f"margin{now}."
            )
    bought = next((s for s in sections if s.id == "bought_vs_passed"), None)
    if bought and bought.status == "ok":
        b, p = bought.rows
        if (
            b.median_change is not None
            and p.median_change is not None
            and p.median_change > b.median_change
        ):
            suggestions.append(
                "Ideas you passed on did better than the ones you bought; revisit the entry bar."
            )
    return Scorecard(
        since=since,
        until=until,
        entries=len(window),
        sections=sections,
        suggestions=suggestions,
        filtered_by=filtered_by,
    )
