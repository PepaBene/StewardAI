"""Screening over the filed-statement metrics the app already computes (D7 screen builder).

A screen runs each issuer's latest computable period (`peer_metrics.latest_period`) against
numeric filters on the six `METRIC_NAMES`. Nothing is estimated and no universe is implied: the
universe is whatever the owner chose (covered issuers, the watchlist or holdings), every issuer
that does not reach the result list is counted with its reason, and a metric that could not be
computed is never read as zero:

* `missing="exclude"` drops an issuer whose metric is missing, counted under `no_data`;
* `missing="keep_unknown"` keeps it, listing the metrics that are unknown for it.

`filtered_out` is different from `no_data`: the first had a value that failed a filter, the
second had no value to test.
"""

from collections import Counter
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from steward.peer_metrics import FORMULAS, METRIC_NAMES, latest_period

MetricName = Literal[
    "revenue_growth",
    "gross_margin",
    "operating_margin",
    "roic",
    "net_debt_to_equity",
    "free_cash_flow_conversion",
]
Op = Literal["gte", "lte"]
Missing = Literal["exclude", "keep_unknown"]


class ScreenFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: MetricName
    op: Op = Field(description="gte: value >= threshold. lte: value <= threshold.")
    value: Decimal = Field(
        description="Threshold in the metric's own unit: fractions for margins, growth, ROIC and "
        "cash conversion (0.15 = 15%), a ratio for net debt to equity."
    )


class RowGate(BaseModel):
    usable_for_valuation: bool | None = Field(
        description="Null when no gate could be computed for this period: never read as a pass."
    )
    reasons: list[str]


class ScreenRow(BaseModel):
    ticker: str
    valuation_gate: RowGate | None = Field(
        default=None, description="Whether this period's statements passed the valuation checks."
    )
    name: str | None = None
    basis: str
    period_end: str
    metrics: dict[str, Decimal | None]
    unknown: list[str] = Field(
        description="Filtered metrics with no value for this issuer (only with keep_unknown)."
    )


class ExcludedGroup(BaseModel):
    reason: str
    count: int
    tickers: list[str]


class ScreenResult(BaseModel):
    universe: str
    universe_definition: str
    universe_size: int
    evaluated: int = Field(description="Issuers with a computable latest period.")
    matched: int
    shown: int
    no_data: int = Field(description="Left out because a filtered metric had no value.")
    filtered_out: int = Field(description="Had every filtered value and failed a filter.")
    excluded: list[ExcludedGroup]
    missing_treatment: Missing
    filters: list[ScreenFilter]
    rows: list[ScreenRow]
    formula_definitions: dict[str, str]
    caveats: list[str]


CAVEATS = [
    "Each issuer is measured on its own latest period and accounting basis (US GAAP or IFRS); "
    "the basis is shown per row and the formulas are the same for every issuer.",
    "Periods differ between issuers. Compare the period end shown, not just the value.",
    "Only issuers the app holds filed statements for can appear. This is not a market screen.",
]


def _passes(value: Decimal, flt: ScreenFilter) -> bool:
    return value >= flt.value if flt.op == "gte" else value <= flt.value


def run_screen(
    metrics: dict[str, dict[str, object]],
    tickers: list[str],
    filters: list[ScreenFilter],
    *,
    names: dict[str, str] | None = None,
    missing: Missing = "exclude",
    sort_by: MetricName | None = None,
    limit: int = 100,
    universe: str = "covered",
    universe_definition: str = "",
) -> ScreenResult:
    names = names or {}
    matched: list[ScreenRow] = []
    reasons: dict[str, list[str]] = {}
    no_data = filtered_out = evaluated = 0

    def exclude(reason: str, ticker: str) -> None:
        reasons.setdefault(reason, []).append(ticker)

    for ticker in sorted(set(tickers)):
        periods: list[dict[str, Any]] = list(metrics.get(ticker, {}).get("periods", []))  # type: ignore[call-overload]
        period = latest_period(periods)
        if period is None:
            exclude("No computable period in the stored statements", ticker)
            continue
        evaluated += 1
        unknown: list[str] = []
        failed: str | None = None
        absent: str | None = None
        for flt in filters:
            value = period.get(flt.metric)
            if value is None:
                unknown.append(flt.metric)
                absent = absent or flt.metric
            elif not _passes(value, flt):
                failed = failed or flt.metric
        if failed is not None:
            filtered_out += 1
            exclude(f"Failed the {failed.replace('_', ' ')} filter", ticker)
            continue
        if unknown and missing == "exclude":
            no_data += 1
            reason = (period.get("not_meaningful") or {}).get(absent or "", "no value")
            exclude(
                f"{(absent or '').replace('_', ' ').capitalize()} not computable: {reason}", ticker
            )
            continue
        matched.append(
            ScreenRow(
                ticker=ticker,
                name=names.get(ticker),
                basis=str(period["basis"]),
                period_end=str(period["period_end"]),
                metrics={m: period.get(m) for m in METRIC_NAMES},
                unknown=unknown,
            )
        )

    key = sort_by or (filters[0].metric if filters else None)
    if key is not None:
        # Unknown values sort last in either direction, never as zero.
        known = [r for r in matched if r.metrics.get(key) is not None]
        unknown_rows = [r for r in matched if r.metrics.get(key) is None]
        descending = not filters or next((f.op == "gte" for f in filters if f.metric == key), True)
        known.sort(key=lambda r: r.metrics[key] or Decimal(0), reverse=descending)
        matched = known + unknown_rows

    counts = Counter({k: len(v) for k, v in reasons.items()})
    groups = [
        ExcludedGroup(reason=reason, count=count, tickers=sorted(reasons[reason])[:25])
        for reason, count in counts.most_common()
    ]
    return ScreenResult(
        universe=universe,
        universe_definition=universe_definition,
        universe_size=len(set(tickers)),
        evaluated=evaluated,
        matched=len(matched),
        shown=min(len(matched), limit),
        no_data=no_data,
        filtered_out=filtered_out,
        excluded=groups,
        missing_treatment=missing,
        filters=filters,
        rows=matched[:limit],
        formula_definitions={k: FORMULAS[k] for k in METRIC_NAMES if k in FORMULAS},
        caveats=CAVEATS,
    )
