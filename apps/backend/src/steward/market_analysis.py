"""Deterministic calculations shared by price, fundamentals and patent read models.

These functions deliberately require compatible units and explicit corporate actions.
They never invent a dividend, currency conversion or peer value when source coverage is absent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

from steward.sources.market import major_currency, unit_factor


@dataclass(frozen=True, slots=True)
class PricePoint:
    day: date
    close: Decimal
    currency: str


ActionKind = Literal[
    "split",
    "cash_dividend",
    "stock_dividend",
    "symbol_change",
    "merger",
    "spin_off",
    "delisting",
]
# Actions a return cannot value from a price and a ratio/cash amount: a total return across one
# is unavailable, and a price return across one is flagged incomplete (a spin-off drops the
# price by the distributed value; a merger or delisting ends or replaces the series; a symbol
# change breaks the series' continuity until the successor bars are joined).
UNHANDLED_KINDS: frozenset[str] = frozenset({"symbol_change", "merger", "spin_off", "delisting"})


@dataclass(frozen=True, slots=True)
class Action:
    kind: ActionKind
    ex_date: date
    ratio: Decimal | None = None
    cash: Decimal | None = None
    currency: str | None = None
    # Which source reported it (two sources reporting the same action are one action).
    source: str | None = None
    # Listed units per unit the amount is quoted for: a dividend declared per ordinary share
    # pays `per_unit_multiplier` ordinary shares per ADS (the ADS ratio).
    per_unit_multiplier: Decimal = Decimal(1)
    # "as_declared": the amount is per share as it stood on the ex-date (later splits have not
    # touched it); "split_adjusted": a provider already restated it for later splits.
    amount_basis: Literal["as_declared", "split_adjusted"] = "as_declared"


@dataclass(frozen=True, slots=True)
class ReturnResult:
    value: Decimal | None
    basis: str
    reason: str | None
    observations: int
    # Reasons the value, though computed, does not cover every corporate action in the window.
    incomplete: tuple[str, ...] = ()
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DedupedActions:
    actions: list[Action]
    conflicts: list[str]


def _value_key(action: Action) -> tuple[object, ...]:
    if action.kind in ("split", "stock_dividend"):
        return (action.ratio,)
    if action.kind == "cash_dividend":
        return (action.cash, action.currency)
    return ()


def dedupe_actions(actions: list[Action]) -> DedupedActions:
    """One action per (kind, ex-date): the same action reported by two sources is applied once
    (a split recorded twice must not double), and two sources that disagree on its value are a
    conflict (the return is unavailable until the owner rejects one).

    Within one source, equal cash dividends collapse (the same payment under two URLs) and
    different amounts on one ex-date are separate payments (regular plus special)."""
    grouped: dict[tuple[str, date], list[Action]] = {}
    for action in actions:
        grouped.setdefault((action.kind, action.ex_date), []).append(action)
    out: list[Action] = []
    conflicts: list[str] = []
    for (kind, ex_date), group in sorted(grouped.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        by_source: dict[str, list[Action]] = {}
        for action in group:
            by_source.setdefault(action.source or "", []).append(action)
        per_source: dict[str, list[Action]] = {}
        for source, items in by_source.items():
            unique: dict[tuple[object, ...], Action] = {}
            for item in items:
                unique.setdefault(_value_key(item), item)
            per_source[source] = list(unique.values())
        signatures = {
            tuple(sorted(str(_value_key(a)) for a in items)) for items in per_source.values()
        }
        if len(signatures) > 1:
            conflicts.append(f"{kind} on {ex_date}: sources disagree")
            continue
        # Sources agree: keep one source's list. Splits and stock dividends are single events;
        # unhandled kinds collapse to one.
        chosen = next(iter(per_source.values()))
        single = kind in {"split", "stock_dividend"} | UNHANDLED_KINDS
        out.extend(chosen[:1] if single else chosen)
    return DedupedActions(out, conflicts)


def _unavailable(basis: str, reason: str, n: int, **details: object) -> ReturnResult:
    return ReturnResult(None, basis, reason, n, details=dict(details))


def _dividend_in_quote_units(
    action: Action,
    quote_currency: str,
    fx_to_quote: dict[date, Decimal] | None,
) -> tuple[Decimal | None, Decimal | None, str | None]:
    """(amount in the quote currency's own unit, FX rate used, reason when unavailable).

    The dividend currency may be a minor unit (GBp) and so may the quote (GBX): both go through
    the major currency. `fx_to_quote[ex_date]` is major dividend currency -> major quote
    currency; it is required only when the majors differ."""
    cash = action.cash
    if cash is None or cash < 0:
        return None, None, "dividend amount is unavailable"
    if not action.currency:
        return None, None, "dividend currency is unavailable"
    major_dividend = major_currency(action.currency)
    major_quote = major_currency(quote_currency)
    amount = cash * unit_factor(action.currency) * action.per_unit_multiplier
    rate: Decimal | None = None
    if major_dividend != major_quote:
        if fx_to_quote is None or action.ex_date not in fx_to_quote:
            return None, None, "dividend FX on ex-date is unavailable"
        rate = fx_to_quote[action.ex_date]
        if rate <= 0:
            return None, None, "dividend FX on ex-date is invalid"
        amount *= rate
    return amount / unit_factor(quote_currency), rate, None


def calculate_return(
    points: list[PricePoint],
    actions: list[Action],
    *,
    reinvest_dividends: bool = False,
    fx_to_quote: dict[date, Decimal] | None = None,
    withholding_rate: Decimal = Decimal(0),
) -> ReturnResult:
    """Return from chronologically ordered closes using only confirmed actions.

    - Splits and stock dividends restate the earlier closes onto the later share basis. Pass the
      raw closes and `actions=[]` for a truly raw price return.
    - Cash dividends are reinvested at the ex-date close (`reinvest_dividends`). The amount is
      split-adjusted by every later split (equivalent to cash / raw close on the ex-date),
      converted from its currency (GBp, GBX, GBP, EUR ...) into the quote unit with the
      supplied ex-date FX rate when the major currencies differ, scaled by the ADS ratio
      (`per_unit_multiplier`) and reduced by `withholding_rate`. The rate and FX dates used
      are returned in `details`.
    - The same action reported by several sources counts once; sources that disagree make the
      result unavailable. A confirmed spin-off, merger, delisting or symbol change inside the
      window makes total return unavailable and flags a price return incomplete.
    - A confirmed split that the closes do not show (the raw closes were already split
      adjusted) or that they show differently makes the result unavailable rather than
      adjusting twice.
    """
    basis = "total_return" if reinvest_dividends else "price_raw"
    n = len(points)
    if n < 2:
        return _unavailable(basis, "at least two closes are required", n)
    points = sorted(points, key=lambda p: p.day)
    if len({p.day for p in points}) != len(points):
        return _unavailable(basis, "duplicate sessions", n)
    if len({p.currency for p in points}) != 1:
        return _unavailable(basis, "currency changes within the price series", n)
    if any(p.close <= 0 for p in points):
        return _unavailable(basis, "non-positive close", n)
    if not Decimal(0) <= withholding_rate < Decimal(1):
        return _unavailable(basis, "withholding rate must be in [0, 1)", n)
    quote_currency = points[0].currency
    first, last = points[0].day, points[-1].day
    by_day = {p.day: p for p in points}

    deduped = dedupe_actions([a for a in actions if first < a.ex_date <= last])
    if deduped.conflicts:
        return _unavailable(
            basis, "conflicting corporate actions: " + "; ".join(deduped.conflicts), n
        )
    window = deduped.actions
    incomplete = tuple(
        f"{a.kind} on {a.ex_date.isoformat()} is not valued"
        for a in window
        if a.kind in UNHANDLED_KINDS
    )
    if incomplete and reinvest_dividends:
        return ReturnResult(
            None,
            basis,
            "total return unavailable across " + "; ".join(incomplete),
            n,
            incomplete=incomplete,
        )

    factors: list[tuple[date, Decimal]] = []
    for action in window:
        if action.kind == "split":
            if action.ratio is None or action.ratio <= 0:
                return _unavailable(basis, "split ratio is unavailable or invalid", n)
            factors.append((action.ex_date, action.ratio))
        elif action.kind == "stock_dividend":
            if action.ratio is None or action.ratio <= 0:
                return _unavailable(basis, "stock dividend ratio is unavailable or invalid", n)
            factors.append((action.ex_date, Decimal(1) + action.ratio))
    for ex_date, factor in factors:
        before = [p for p in points if p.day < ex_date]
        after = [p for p in points if p.day >= ex_date]
        if not before or not after or abs(factor - 1) < Decimal("0.5"):
            continue
        observed = before[-1].close / after[0].close
        if abs(observed / factor - 1) <= Decimal("0.25"):
            continue
        reason = (
            f"closes around {ex_date.isoformat()} do not show the {factor} split "
            f"(close ratio {observed:.4f}): "
            + (
                "the closes look already split-adjusted"
                if abs(observed - 1) <= Decimal("0.25")
                else "cannot verify the split against the closes"
            )
        )
        return _unavailable(basis, reason, n, split_check="failed")

    def after_factor(day: date) -> Decimal:
        out = Decimal(1)
        for ex_date, factor in factors:
            if ex_date > day:
                out *= factor
        return out

    adjusted = [p.close / after_factor(p.day) for p in points]
    units = Decimal(1)
    fx_dates: dict[str, str] = {}
    dividends = 0
    if reinvest_dividends:
        for action in window:
            if action.kind != "cash_dividend":
                continue
            if action.ex_date not in by_day:
                return _unavailable(basis, "dividend amount or ex-date close is unavailable", n)
            amount, rate, why = _dividend_in_quote_units(action, quote_currency, fx_to_quote)
            if amount is None:
                return _unavailable(basis, why or "dividend amount is unavailable", n)
            if rate is not None:
                fx_dates[action.ex_date.isoformat()] = str(rate)
            index = points.index(by_day[action.ex_date])
            net = amount * (Decimal(1) - withholding_rate)
            if action.amount_basis == "as_declared":
                net = net / after_factor(action.ex_date)
            units *= Decimal(1) + net / adjusted[index]
            dividends += 1
    result = adjusted[-1] * units / adjusted[0] - Decimal(1)
    details: dict[str, object] = {
        "dividends_reinvested": dividends,
        "splits_applied": [d.isoformat() for d, _ in factors],
        "withholding_rate": str(withholding_rate),
        "withholding_assumption": (
            "gross dividends (no withholding)"
            if withholding_rate == 0
            else f"dividends net of {withholding_rate:.1%} withholding"
        ),
        "fx_dates": fx_dates,
    }
    return ReturnResult(result, basis, None, n, incomplete=incomplete, details=details)


def compatible_peer_mean(
    values: list[tuple[str, Decimal | None, str]],
    *,
    metric: str,
    reasons: dict[str, str] | None = None,
) -> dict[str, object]:
    """Mean of explicitly comparable peer facts; excludes missing/incompatible values and
    says why. Implemented in `steward.peer_metrics`."""
    from steward.peer_metrics import compatible_peer_mean as _mean

    return _mean(values, metric=metric, reasons=reasons)


def financial_peer_metrics(
    rows: list[dict[str, object]], *, reconciled_only: bool = False
) -> dict[str, dict[str, object]]:
    """Peer ratios from filed statement facts (US GAAP and IFRS). The rules - period and unit
    grouping, concept priority, summed debt components, guards and "n/m" reasons - live in
    `steward.peer_metrics`; this is the entry point the read models import."""
    from steward.peer_metrics import financial_peer_metrics as _metrics

    return _metrics(rows, reconciled_only=reconciled_only)


def patent_trend(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Counts distinct patent families by CPC and priority year (see `steward.graph.patents`:
    one family counts once, with its legal-status split and grant / expiry timing)."""
    from steward.graph.patents import patent_trend as _trend

    return _trend(rows)
