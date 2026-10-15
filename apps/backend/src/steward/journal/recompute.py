"""A second, independent computation of the scorecard's figures (engine plan, phase 5.2).

`scorecard.py` turns outcomes into rows. This module recomputes each row's median and share from
the raw prices alone, written differently (no shared helper), and reports every figure that
differs. It does not correct anything: a difference means one of the two is wrong and a person
should look.
"""

from statistics import fmean

from steward.journal.scorecard import DecisionOutcome, Row, Scorecard, SwitchOutcome
from steward.sources.market import quote_unit_of

# Sections whose rows are price changes of journal decisions, and whether a rise is a win.
CHECKED = {
    "by_conviction": True,
    "by_reason": True,
    "bought_vs_passed": True,
    "after_trim": False,
    "after_sell": False,
}
TOLERANCE = 1e-9
BUYS = ("buy", "add")


def _rise(
    then: float | None, now: float | None, ccy_then: str | None, ccy_now: str | None
) -> float | None:
    if not then or now is None:
        return None
    if ccy_then and (not ccy_now or quote_unit_of(ccy_then) != quote_unit_of(ccy_now)):
        return None
    return (now - then) / then


def _move(o: DecisionOutcome) -> float | None:
    return _rise(o.price_at_decision, o.price_now, o.currency_at_decision, o.currency_now)


def _middle(values: list[float]) -> float:
    ordered = sorted(values)
    half = len(ordered) // 2
    return ordered[half] if len(ordered) % 2 else fmean(ordered[half - 1 : half + 1])


def _differs(shown: float | None, again: float | None) -> bool:
    if (shown is None) != (again is None):
        return True
    return shown is not None and again is not None and abs(shown - again) > TOLERANCE


def _compare(where: str, row: Row, median: float | None, share: float | None) -> list[str]:
    found = []
    for name, shown, again in (("median", row.median_change, median), ("share", row.share, share)):
        if _differs(shown, again):
            found.append(f"{where}/{row.label}: {name} shows {shown}, recomputed {again}")
    return found


def _price_changes(card: Scorecard, by_id: dict[int, DecisionOutcome], problems: list[str]) -> None:
    for section in card.sections:
        if section.status != "ok" or section.id not in CHECKED:
            continue
        rises_win = CHECKED[section.id]
        for row in section.rows:
            missing = [i for i in row.entry_ids if i not in by_id]
            if missing:
                problems.append(f"{section.id}/{row.label}: entries {missing} are not in the data")
                continue
            moves = [m for i in row.entry_ids if (m := _move(by_id[i])) is not None]
            wins = sum(1 for m in moves if m != 0 and (m > 0) == rises_win)
            problems += _compare(
                section.id,
                row,
                _middle(moves) if moves else None,
                wins / len(moves) if moves else None,
            )


def _targets_and_risk(
    card: Scorecard, by_id: dict[int, DecisionOutcome], problems: list[str]
) -> None:
    for section in card.sections:
        if section.status != "ok" or section.id not in ("sell_targets", "risk_held"):
            continue
        for row in section.rows:
            rows = [by_id[i] for i in row.entry_ids if i in by_id]
            if len(rows) != len(row.entry_ids):
                problems.append(f"{section.id}/{row.label}: entries are not in the data")
                continue
            if section.id == "sell_targets":
                hit = sum(1 for o in rows if o.hit_on is not None)
                early = [o.id for o in rows if o.hit_on is not None and o.hit_on < o.decided_on]
                if early:
                    problems.append(
                        f"sell_targets: entries {early} reached the target before the decision"
                    )
                problems += _compare(section.id, row, None, hit / len(rows) if rows else None)
            elif row.share is not None:
                low = [o for o in rows if o.low_since is not None and o.bear_case is not None]
                breached = len([o for o in low if (o.low_since or 0.0) <= (o.bear_case or 0.0)])
                problems += _compare(section.id, row, None, breached / len(low) if low else None)
            else:
                falls = [
                    o.low_since / o.price_at_decision - 1
                    for o in rows
                    if o.low_since and o.price_at_decision
                ]
                problems += _compare(section.id, row, _middle(falls) if falls else None, None)


def _switches(card: Scorecard, switches: list[SwitchOutcome], problems: list[str]) -> None:
    section = next((s for s in card.sections if s.id == "switches" and s.status == "ok"), None)
    if section is None:
        return
    by_id = {s.id: s for s in switches}
    for row in section.rows:
        diffs: list[float] = []
        for i in row.entry_ids:
            s = by_id.get(i)
            if s is None:
                problems.append(f"switches/{row.label}: switch {i} is not in the data")
                break
            bought = _rise(s.bought_price, s.bought_now, s.bought_currency, s.bought_currency_now)
            sold = _rise(s.sold_price, s.sold_now, s.sold_currency, s.sold_currency_now)
            if bought is not None and sold is not None:
                diffs.append(bought - sold)
        else:
            if row.share is not None:
                problems += _compare(
                    "switches",
                    row,
                    None,
                    sum(1 for d in diffs if d > 0) / len(diffs) if diffs else None,
                )
            else:
                problems += _compare("switches", row, _middle(diffs) if diffs else None, None)


def check(
    card: Scorecard,
    outcomes: list[DecisionOutcome],
    switches: list[SwitchOutcome] | None = None,
) -> list[str]:
    """Every row whose median or share differs from the recomputed value."""
    by_id = {o.id: o for o in outcomes}
    problems: list[str] = []
    _price_changes(card, by_id, problems)
    _targets_and_risk(card, by_id, problems)
    if switches is not None:
        _switches(card, switches, problems)
    return problems
