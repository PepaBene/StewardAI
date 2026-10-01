"""Deterministic calculations shared by price, fundamentals and patent read models.

These functions deliberately require compatible units and explicit corporate actions.
They never invent a dividend, currency conversion or peer value when source coverage is absent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal, cast


@dataclass(frozen=True, slots=True)
class PricePoint:
    day: date
    close: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class Action:
    kind: Literal["split", "cash_dividend", "stock_dividend"]
    ex_date: date
    ratio: Decimal | None = None
    cash: Decimal | None = None
    currency: str | None = None


@dataclass(frozen=True, slots=True)
class ReturnResult:
    value: Decimal | None
    basis: str
    reason: str | None
    observations: int


def calculate_return(
    points: list[PricePoint],
    actions: list[Action],
    *,
    reinvest_dividends: bool = False,
    fx_to_quote: dict[date, Decimal] | None = None,
) -> ReturnResult:
    """Return from chronologically ordered raw closes using only confirmed actions.

    Cash dividends are reinvested at the ex-date close. Foreign-currency dividends require
    an explicitly supplied ex-date FX rate. Missing/ambiguous action data makes total return
    unavailable while leaving a raw price return computable.
    """
    basis = "total_return" if reinvest_dividends else "price_raw"
    if len(points) < 2:
        return ReturnResult(None, basis, "at least two closes are required", len(points))
    points = sorted(points, key=lambda p: p.day)
    if len({p.day for p in points}) != len(points):
        return ReturnResult(None, basis, "duplicate sessions", len(points))
    if len({p.currency for p in points}) != 1:
        return ReturnResult(None, basis, "currency changes within the price series", len(points))
    if any(p.close <= 0 for p in points):
        return ReturnResult(None, basis, "non-positive close", len(points))
    by_day = {p.day: p for p in points}
    for action in actions:
        if action.kind == "split" and (action.ratio is None or action.ratio <= 0):
            return ReturnResult(None, basis, "split ratio is unavailable or invalid", len(points))
        if action.kind == "cash_dividend" and reinvest_dividends:
            if action.cash is None or action.cash < 0 or action.ex_date not in by_day:
                return ReturnResult(
                    None, basis, "dividend amount or ex-date close is unavailable", len(points)
                )
            if action.currency != points[0].currency:
                if fx_to_quote is None or action.ex_date not in fx_to_quote:
                    return ReturnResult(
                        None, basis, "dividend FX on ex-date is unavailable", len(points)
                    )
    # Split-neutralize the earlier price scale before deriving price performance.
    adjusted = [p.close for p in points]
    for action in actions:
        if action.kind in {"split", "stock_dividend"} and action.ratio is not None:
            factor = action.ratio if action.kind == "split" else Decimal(1) + action.ratio
            if factor <= 0:
                return ReturnResult(None, basis, "invalid corporate-action ratio", len(points))
            for idx, point in enumerate(points):
                if point.day < action.ex_date:
                    adjusted[idx] /= factor
    units = Decimal(1)
    for idx in range(1, len(points)):
        day = points[idx].day
        if reinvest_dividends:
            for action in actions:
                if action.kind == "cash_dividend" and action.ex_date == day:
                    amount = action.cash or Decimal(0)
                    if action.currency != points[0].currency:
                        amount *= (fx_to_quote or {})[day]
                    units *= Decimal(1) + amount / adjusted[idx]
        # Split ratios were applied to historical units above via price normalization.
    result = adjusted[-1] / adjusted[0] - Decimal(1)
    if reinvest_dividends:
        result = adjusted[-1] * units / adjusted[0] - Decimal(1)
    return ReturnResult(result, basis, None, len(points))


def compatible_peer_mean(
    values: list[tuple[str, Decimal | None, str]], *, metric: str
) -> dict[str, object]:
    """Mean of explicitly comparable peer facts; excludes missing/incompatible values."""
    included = [
        (ticker, value)
        for ticker, value, definition in values
        if value is not None and definition == metric
    ]
    excluded = [
        ticker for ticker, value, definition in values if value is None or definition != metric
    ]
    mean = sum((value for _, value in included), Decimal(0)) / len(included) if included else None
    return {
        "metric": metric,
        "mean": mean,
        "covered_peers": len(included),
        "included": [ticker for ticker, _ in included],
        "excluded": excluded,
    }


def financial_peer_metrics(rows: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    """Derive conservative peer ratios from same-period, same-basis, same-unit facts.

    XBRL concepts are matched by their local name. Dimensional facts are deliberately
    excluded here: segment totals and consolidated totals must not be mixed. Missing
    formula inputs produce a missing value with a reason rather than a guessed proxy.
    """
    aliases = {
        "revenue": ("revenuefromcontractwithcustomerexcludingassessedtax", "revenues", "revenue"),
        "gross_profit": ("grossprofit",),
        "operating_income": ("operatingincomeloss",),
        "net_income": ("profitloss", "netincomeloss"),
        "tax": ("incometaxexpensebenefit",),
        "pretax": (
            "incomelossfromcontinuingoperationsbeforeincometaxesextraordinaryitemsnoncontrollinginterest",
        ),
        "debt": ("longtermdebt", "longtermdebtcurrent", "longtermdebtnoncurrent"),
        "equity": ("stockholdersequity", "equity"),
        "cash": ("cashandcashequivalentsatcarryingvalue",),
        "cfo": ("netcashprovidedbyusedinoperatingactivities",),
        "capex": ("paymentstoacquirepropertyplantandequipment",),
    }
    groups: dict[tuple[object, object, object], list[dict[str, object]]] = {}
    for row in rows:
        dims = row.get("dimensions") or {}
        if isinstance(dims, dict) and dims.get("members"):
            continue
        key = (row.get("ticker"), row.get("period_end"), row.get("basis"))
        groups.setdefault(key, []).append(row)

    def fact(values: list[dict[str, object]], name: str) -> Decimal | None:
        wanted = aliases[name]
        matches = [
            r
            for r in values
            if isinstance(r.get("value"), Decimal)
            and str(r.get("concept", "")).split(":")[-1].lower() in wanted
        ]
        if not matches:
            return None
        return cast(Decimal, matches[-1]["value"])

    by_ticker: dict[str, list[dict[str, object]]] = {}
    for (ticker, period, basis), values in groups.items():
        if ticker is None:
            continue
        named = {name: fact(values, name) for name in aliases}
        revenue = named["revenue"]
        metrics: dict[str, Decimal | None] = {
            "gross_margin": (
                named["gross_profit"] / revenue
                if named["gross_profit"] is not None and revenue not in (None, Decimal(0))
                else None
            ),
            "operating_margin": (
                named["operating_income"] / revenue
                if named["operating_income"] is not None and revenue not in (None, Decimal(0))
                else None
            ),
            "net_debt_to_equity": (
                (named["debt"] - named["cash"]) / named["equity"]
                if named["debt"] is not None
                and named["cash"] is not None
                and named["equity"] not in (None, Decimal(0))
                else None
            ),
            "free_cash_flow_conversion": (
                (named["cfo"] - abs(named["capex"])) / named["net_income"]
                if named["cfo"] is not None
                and named["capex"] is not None
                and named["net_income"] not in (None, Decimal(0))
                else None
            ),
            "roic": (
                named["operating_income"]
                * (Decimal(1) - named["tax"] / named["pretax"])
                / (named["debt"] + named["equity"] - named["cash"])
                if named["operating_income"] is not None
                and named["tax"] is not None
                and named["pretax"] not in (None, Decimal(0))
                and named["debt"] is not None
                and named["equity"] is not None
                and named["cash"] is not None
                and named["debt"] + named["equity"] - named["cash"] != 0
                else None
            ),
        }
        by_ticker.setdefault(str(ticker), []).append(
            {
                "period_end": period,
                "period_start": next(
                    (r.get("period_start") for r in values if r.get("period_start") is not None),
                    None,
                ),
                "basis": basis,
                "revenue": revenue,
                **metrics,
            }
        )
    for periods in by_ticker.values():
        periods.sort(key=lambda row: str(row["period_end"]))
        for current in periods:
            start = current["period_start"]
            end = current["period_end"]
            current["revenue_growth"] = None
            current_revenue = cast("Decimal | None", current["revenue"])
            if not isinstance(start, date) or not isinstance(end, date) or current_revenue is None:
                continue
            duration = (end - start).days
            prior = [
                row
                for row in periods
                if row["basis"] == current["basis"]
                and isinstance(row["period_start"], date)
                and isinstance(row["period_end"], date)
                and row["period_end"] < end
                and abs((row["period_end"] - row["period_start"]).days - duration) <= 7
                and 330 <= (end - row["period_end"]).days <= 400
                and row["revenue"] not in (None, Decimal(0))
            ]
            if prior:
                prior.sort(key=lambda row: cast(date, row["period_end"]))
                current["revenue_growth"] = current_revenue / cast(
                    Decimal, prior[-1]["revenue"]
                ) - Decimal(1)
    formulas = {
        "gross_margin": "gross profit / revenue",
        "revenue_growth": "current-period revenue / comparable prior-year revenue - 1",
        "operating_margin": "operating income / revenue",
        "roic": "operating income * (1 - tax expense / pretax income) / (debt + equity - cash)",
        "net_debt_to_equity": "(debt - cash) / equity",
        "free_cash_flow_conversion": (
            "(cash from operations - absolute capital expenditure) / net income"
        ),
    }
    return {
        ticker: {
            "periods": sorted(periods, key=lambda x: str(x["period_end"]), reverse=True),
            "formulas": formulas,
        }
        for ticker, periods in by_ticker.items()
    }


def patent_trend(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Counts distinct patent families by CPC and priority year, with citation signal."""
    buckets: dict[tuple[str, int], dict[str, object]] = {}
    for row in rows:
        priority = row.get("priority_date")
        if not isinstance(priority, date):
            continue
        codes = row.get("cpc_codes") or []
        if not isinstance(codes, (list, tuple)):
            continue
        for code in set(str(c) for c in codes):
            item = buckets.setdefault(
                (code, priority.year),
                {"cpc": code, "year": priority.year, "families": set(), "forward_citations": 0},
            )
            cast("set[str]", item["families"]).add(str(row["family_key"]))
            item["forward_citations"] = cast(int, item["forward_citations"]) + int(
                row.get("citations_forward") or 0  # type: ignore[call-overload]
            )
    return [
        {
            "cpc": cpc,
            "year": year,
            "families": len(cast("set[str]", item["families"])),
            "forward_citations": item["forward_citations"],
            "interpretation": "counts and citations are research signals, not patent value",
        }
        for (cpc, year), item in sorted(buckets.items())
    ]
