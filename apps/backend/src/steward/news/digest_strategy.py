"""How the portfolio deviates from the owner's strategy, at the start and end of a digest's
window (all four digest kinds).

The weights come from `compute_drift` on the portfolio snapshot nearest before each end of the
window, against the active strategy, the same calculation the portfolio page and the sync's
breach checks use, so the figures match the app's strategy page. The deviation list itself is
a pure function of the two drift reports and the strategy (`strategy_deviations`), and every
implication sentence is written in code from the owner's own targets and rules: nothing here
calls a model or advises.

Limits: the strategy read is today's (a rule edited inside the window is judged by its new
text at both ends), and a rule with no weight to read at an end (no snapshot, cash unknown on
an invested-weight basis) has that end `None`, so its change is "unknown".
"""

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

import asyncpg
from pydantic import BaseModel, Field

from steward.config.store import load_active
from steward.config.strategy import (
    CashWeightRule,
    CategoryTarget,
    CategoryWeightRule,
    PositionCountRule,
    PositionWeightRule,
    StrategyConfig,
    StrategyRule,
    TargetBand,
)
from steward.portfolio.breaches import _holds
from steward.portfolio.drift import UNCLASSIFIED, DriftReport, HoldingValue, compute_drift
from steward.portfolio.risk_tier import with_saved_tiers

DeviationStatus = Literal["within", "outside", "unknown"]
DeviationChange = Literal["new", "cleared", "worse", "better", "unchanged", "unknown"]

# A move smaller than this is not "worse" or "better": half a percentage point of weight, or
# one position of a count.
WEIGHT_EPS = 0.005
COUNT_EPS = 0.5
_BAND_EPS = 1e-9


class StrategyDeviation(BaseModel):
    key: str = Field(description="The rule id, 'cash', or 'category:<dimension>:<bucket>'.")
    label: str = Field(description="The target or limit in plain words.")
    subject: str | None = Field(
        default=None,
        description="The position (broker ticker) or bucket the row reads; null for cash, a "
        "count or a target that is not a rule.",
    )
    target: str = Field(description="What the strategy asks, e.g. '5% (band +/-5%)'.")
    unit: Literal["weight", "count"] = Field(
        default="weight",
        description="'weight': start_value and end_value are fractions of the portfolio. "
        "'count': they are numbers of positions.",
    )
    start_value: float | None = Field(
        description="At the start of the period; null when it cannot be read."
    )
    end_value: float | None = Field(description="At the end of the period.")
    status: DeviationStatus = Field(description="At the end of the period.")
    change: DeviationChange = Field(
        description="From the start to the end of the period: it opened (new), closed "
        "(cleared), widened (worse), narrowed (better) or stayed (unchanged)."
    )
    implication: str = Field(
        description="A sentence written in code from the owner's own target or rule."
    )


@dataclass(frozen=True)
class Reading:
    """One strategy item read on one snapshot: its value and how far outside its limit."""

    value: float | None
    outside: float  # 0 when it holds; the amount past the limit otherwise
    holds: bool


@dataclass(frozen=True)
class _Item:
    """A strategy item and how to read it from a drift report."""

    key: str
    label: str
    target: str
    unit: Literal["weight", "count"]
    rule: StrategyRule | None
    readings: dict[str, tuple[Reading, Reading]]  # subject -> (start, end)


def _pct(x: float) -> str:
    text = f"{x * 100:.1f}".rstrip("0").rstrip(".")
    return f"{text}%"


def _shown(value: float, unit: str) -> str:
    return str(round(value)) if unit == "count" else _pct(value)


def _band_reading(value: float | None, target: float, band: float) -> Reading:
    if value is None:
        return Reading(None, 0.0, False)
    lower, upper = max(0.0, target - band), target + band
    if value > upper + _BAND_EPS:
        return Reading(value, value - upper, False)
    if value < lower - _BAND_EPS:
        return Reading(value, lower - value, False)
    return Reading(value, 0.0, True)


def _rule_reading(value: float | None, operator: str, limit: float) -> Reading:
    if value is None:
        return Reading(None, 0.0, False)
    if _holds(value, operator, limit):
        return Reading(value, 0.0, True)
    return Reading(value, abs(value - limit), False)


def _rule_values(rule: StrategyRule, drift: DriftReport) -> dict[str, float]:
    """The values `evaluate` would check for the rule on this report, by subject. A position or
    bucket the portfolio does not hold counts as 0, as the review's compliance table judges it."""
    if isinstance(rule, PositionWeightRule):
        weights = {p.ticker: p.weight for p in drift.positions if p.held}
        if rule.ticker is not None:
            return {rule.ticker: weights.get(rule.ticker, 0.0)}
        return {t: w for t, w in weights.items() if t not in rule.exclude}
    if isinstance(rule, CategoryWeightRule):
        found = next((d for d in drift.categories if d.dimension == rule.dimension), None)
        buckets = {b.bucket: b.weight for b in found.buckets} if found else {}
        if rule.bucket is not None:
            return {rule.bucket: buckets.get(rule.bucket, 0.0)}
        return buckets
    if isinstance(rule, CashWeightRule):
        return {} if drift.cash.weight is None else {"cash": drift.cash.weight}
    if isinstance(rule, PositionCountRule):
        return {"count": float(sum(1 for p in drift.positions if p.held))}
    return {}


def _operator_words(operator: str) -> str:
    return {
        "<": "below",
        "<=": "at most",
        ">": "above",
        ">=": "at least",
        "==": "exactly",
    }.get(operator, operator)


def _rule_label(rule: StrategyRule, subject: str, names: Mapping[str, str]) -> str:
    """A short plain name for what the rule reads, with the holding's name, not its broker id:
    the rule's own description follows in the implication."""
    shown = names.get(subject, subject)
    if isinstance(rule, PositionWeightRule):
        return f"Position {shown}" if rule.ticker else f"Largest position ({shown})"
    if isinstance(rule, CategoryWeightRule):
        if rule.dimension == "risk":
            return f"{subject.capitalize()} tier"
        return (
            f"{shown} ({rule.dimension})" if rule.bucket else f"Largest {rule.dimension} ({shown})"
        )
    if isinstance(rule, CashWeightRule):
        return "Cash ceiling" if rule.operator in ("<", "<=") else "Cash floor"
    if isinstance(rule, PositionCountRule):
        return "Number of positions"
    return rule.description or f"Rule {rule.id}"


def untiered(drift: DriftReport | None) -> bool:
    """True when no holding carries a risk tier: every tier rule then reads 0%, which says
    nothing about the portfolio until tiers are assigned."""
    if drift is None:
        return False
    risk = next((d for d in drift.categories if d.dimension == "risk"), None)
    return risk is None or all(b.bucket == UNCLASSIFIED or b.weight == 0 for b in risk.buckets)


def _unit_of(rule: StrategyRule) -> Literal["weight", "count"]:
    return "count" if isinstance(rule, PositionCountRule) else "weight"


def _unknown() -> Reading:
    return Reading(None, 0.0, False)


def _cash_reading(drift: DriftReport | None, target: TargetBand) -> Reading:
    if drift is None:
        return _unknown()
    return _band_reading(drift.cash.weight, target.target_weight, target.band)


def _bucket_reading(drift: DriftReport | None, target: CategoryTarget) -> Reading:
    """A bucket the portfolio holds nothing in weighs 0, which a target above the band's floor
    reads as below its band."""
    if drift is None:
        return _unknown()
    found = next((d for d in drift.categories if d.dimension == target.dimension), None)
    weight = next(
        (b.weight for b in (found.buckets if found else []) if b.bucket == target.bucket), 0.0
    )
    return _band_reading(weight, target.target_weight, target.band)


def _items(
    start: DriftReport | None, end: DriftReport | None, strategy: StrategyConfig
) -> list[_Item]:
    items: list[_Item] = []
    cash = strategy.cash_target
    if cash is not None:
        items.append(
            _Item(
                "cash",
                "Cash",
                f"{_pct(cash.target_weight)} (band +/-{_pct(cash.band)})",
                "weight",
                None,
                {"cash": (_cash_reading(start, cash), _cash_reading(end, cash))},
            )
        )
    for t in strategy.target_categories:
        items.append(
            _Item(
                f"category:{t.dimension}:{t.bucket}",
                f"{t.bucket.capitalize()} {t.dimension}",
                f"{_pct(t.target_weight)} (band +/-{_pct(t.band)})",
                "weight",
                None,
                {t.bucket: (_bucket_reading(start, t), _bucket_reading(end, t))},
            )
        )
    for rule in strategy.enabled_rules:
        limit = float(rule.value)
        unit = _unit_of(rule)
        start_values = _rule_values(rule, start) if start is not None else {}
        end_values = _rule_values(rule, end) if end is not None else {}
        readings: dict[str, tuple[Reading, Reading]] = {}
        for subject in end_values.keys() | start_values.keys():
            before = (
                _rule_reading(start_values.get(subject, 0.0), rule.operator, limit)
                if start is not None
                else _unknown()
            )
            after = (
                _rule_reading(end_values.get(subject, 0.0), rule.operator, limit)
                if end is not None
                else _unknown()
            )
            readings[subject] = (before, after)
        if not readings:
            readings["-"] = (_unknown(), _unknown())
        items.append(
            _Item(
                rule.id,
                rule.description or f"Rule {rule.id}",
                f"{_operator_words(rule.operator)} {_shown(limit, unit)}",
                unit,
                rule,
                readings,
            )
        )
    return items


def classify_change(before: Reading, after: Reading, eps: float) -> DeviationChange:
    """How an item moved over the period, by whether it held at each end and, when it was
    outside at both, whether it sat further or less far outside."""
    if before.value is None or after.value is None:
        return "unknown"
    if before.holds and not after.holds:
        return "new"
    if not before.holds and after.holds:
        return "cleared"
    if before.holds and after.holds:
        return "unchanged"
    if after.outside > before.outside + eps:
        return "worse"
    if after.outside < before.outside - eps:
        return "better"
    return "unchanged"


def _implication(
    item: _Item,
    label: str,
    status: DeviationStatus,
    change: DeviationChange,
    start_value: float | None,
    end_value: float | None,
    no_tiers: bool = False,
    start_words: str = "at the start of the period",
) -> str:
    """A factual sentence: the weight against the owner's target, the move, and the owner's own
    rule text where there is one. It never says what to do."""
    if end_value is None:
        return f"{label}: no reading at the end of the period against {item.target}."
    text = f"{label} is {_shown(end_value, item.unit)} against {item.target}"
    if start_value is not None:
        moved = abs(end_value - start_value) > (COUNT_EPS if item.unit == "count" else WEIGHT_EPS)
        if moved:
            verb = "up" if end_value > start_value else "down"
            text += f", {verb} from {_shown(start_value, item.unit)}"
        else:
            text += f", about where it was ({_shown(start_value, item.unit)})"
        if start_words != "at the start of the period":
            text += f" {start_words}"
    text += "."
    phrase = {
        "new": f" It was within the target {start_words}.",
        "cleared": f" It was outside the target {start_words}.",
        "worse": " It was already outside the target and is further outside now.",
        "better": " It was already outside the target and is closer now.",
        "unchanged": " It was outside the target throughout." if status == "outside" else "",
        "unknown": " The start of the period could not be read.",
    }[change]
    text += phrase
    if status == "outside" and item.rule is not None and item.rule.description:
        text += f" Your rule {item.rule.id}: {item.rule.description.rstrip('.')}."
    elif status == "outside" and item.rule is None:
        text += " This is outside the band set in your strategy."
    if no_tiers and isinstance(item.rule, CategoryWeightRule) and item.rule.dimension == "risk":
        text += (
            " No holding carries a risk tier yet, so every tier reads 0%: assign tiers, or turn"
            " on automatic tiers in the strategy's risk settings, before reading this rule."
        )
    return text


def strategy_deviations(
    start: DriftReport | None,
    end: DriftReport | None,
    strategy: StrategyConfig,
    names: Mapping[str, str] | None = None,
    first_snapshot: dt.date | None = None,
) -> list[StrategyDeviation]:
    """Every enabled rule plus the cash target and the category targets, read on the drift
    reports at the start and end of the window. Outside items come first, the furthest outside
    first, then the rest by label. A rule over many positions or buckets is shown by its worst
    subject (the one furthest outside at the end, else the one with the largest value).
    `first_snapshot` is set when the window opens before the first portfolio snapshot and
    `start` was read on that snapshot instead; the sentences then name its date."""
    start_words = (
        f"on {first_snapshot.day} {first_snapshot:%b}, the first portfolio snapshot"
        if first_snapshot is not None
        else "at the start of the period"
    )
    out: list[tuple[tuple[int, float, str], StrategyDeviation]] = []
    names = names or {}
    no_tiers = untiered(end)
    for item in _items(start, end, strategy):
        many = len(item.readings) > 1
        subject = max(
            item.readings,
            key=lambda s: (
                item.readings[s][1].value is not None,
                item.readings[s][1].outside,
                item.readings[s][1].value or 0.0,
            ),
        )
        before, after = item.readings[subject]
        status: DeviationStatus = (
            "unknown" if after.value is None else ("within" if after.holds else "outside")
        )
        eps = COUNT_EPS if item.unit == "count" else WEIGHT_EPS
        change = classify_change(before, after, eps)
        label = item.label
        key = item.key
        shown_subject: str | None = None
        if item.rule is not None and subject not in ("-", "cash", "count"):
            shown_subject = subject
        if item.rule is not None and subject != "-":
            label = _rule_label(item.rule, subject, names)
            key = f"{item.key}:{subject}" if many else item.key
        deviation = StrategyDeviation(
            key=key,
            label=label,
            subject=shown_subject,
            target=item.target,
            unit=item.unit,
            start_value=before.value,
            end_value=after.value,
            status=status,
            change=change,
            implication=_implication(
                item, label, status, change, before.value, after.value, no_tiers, start_words
            ),
        )
        rank = {"outside": 0, "within": 1, "unknown": 2}[status]
        out.append(((rank, -after.outside, label), deviation))
    out.sort(key=lambda pair: pair[0])
    return [d for _, d in out]


async def _drift_at(
    pool: asyncpg.Pool, day: dt.date, strategy: StrategyConfig, until: dt.date | None = None
) -> tuple[dt.date, DriftReport] | None:
    """The drift report of the newest snapshot taken on or before `day`. With `until`, when
    there is none, the oldest snapshot up to `until` (a window that opens before the first
    sync). None when there is no snapshot at all."""
    account = await pool.fetchrow(
        "select snapshot_date, cash_base from public.account_snapshot"
        " where snapshot_date <= $1 order by snapshot_date desc limit 1",
        day,
    )
    if account is None and until is not None:
        account = await pool.fetchrow(
            "select snapshot_date, cash_base from public.account_snapshot"
            " where snapshot_date <= $1 order by snapshot_date limit 1",
            until,
        )
    if account is None:
        return None
    rows = await pool.fetch(
        "select ticker, instrument_currency, value_base from public.position_snapshot"
        " where snapshot_date = $1",
        account["snapshot_date"],
    )
    holdings = [HoldingValue(r["ticker"], r["instrument_currency"], r["value_base"]) for r in rows]
    return account["snapshot_date"], compute_drift(holdings, account["cash_base"], strategy)


async def load_strategy_deviations(
    pool: asyncpg.Pool, window_start: dt.datetime, window_end: dt.datetime
) -> list[StrategyDeviation]:
    """The strategy deviations of a window, empty when no strategy is saved."""
    stored = await load_active(pool, "strategy", StrategyConfig)
    if stored is None:
        return []
    strategy = await with_saved_tiers(pool, stored.body)
    end = await _drift_at(pool, window_end.date(), strategy)
    start = await _drift_at(pool, window_start.date(), strategy, until=window_end.date())
    if start is not None and end is not None and start[0] == end[0]:
        start = None  # one snapshot only: there is no start to compare with
    first = start[0] if start is not None and start[0] > window_start.date() else None
    rows = await pool.fetch(
        "select t212_ticker, name from public.instrument where name is not null"
    )
    names = {r["t212_ticker"]: r["name"] for r in rows}
    return strategy_deviations(
        start[1] if start else None, end[1] if end else None, strategy, names, first
    )
