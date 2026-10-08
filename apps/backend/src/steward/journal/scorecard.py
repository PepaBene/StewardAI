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
    hit_on: dt.date | None = Field(
        default=None, description="First close at or above the sell target after the decision."
    )

    @property
    def change(self) -> float | None:
        """Price change since the decision; None when a price is missing or currencies differ."""
        if not self.price_at_decision or self.price_now is None:
            return None
        if (
            self.currency_at_decision
            and self.currency_now
            and self.currency_at_decision != self.currency_now
        ):
            return None
        return self.price_now / self.price_at_decision - 1


class Row(BaseModel):
    label: str
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


def unavailable(id_: str, title: str, reason: str) -> Section:
    return Section(id=id_, title=title, status="unavailable", note=reason)


def build_scorecard(
    outcomes: list[DecisionOutcome],
    *,
    since: dt.date,
    until: dt.date,
    ticker: str | None = None,
    reason: str | None = None,
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
        unavailable(
            "switches",
            "Switches",
            "No switch log exists yet, so a bought stock cannot be compared with the one sold.",
        ),
        unavailable(
            "risk_held",
            "Did the risk estimates hold",
            "A thesis stores no volatility band or bear case yet, so realised volatility and "
            "losses have nothing to be compared with.",
        ),
    ]
    suggestions: list[str] = []
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
