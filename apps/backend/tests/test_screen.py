"""Screening (D7): missing data is excluded or flagged, never read as zero, and every issuer
left out is counted with its reason. Pure tests: no database."""

import datetime as dt
from decimal import Decimal

import pytest
from pydantic import ValidationError

from steward.screen import ScreenFilter, run_screen

D = Decimal


def period(basis: str = "us-gaap", end: str = "2025-12-31", **values: Decimal | None) -> dict:
    base: dict = {
        m: None
        for m in (
            "revenue_growth",
            "gross_margin",
            "operating_margin",
            "roic",
            "net_debt_to_equity",
            "free_cash_flow_conversion",
        )
    }
    base.update(values)
    return {
        "periods": [
            {
                "period_end": dt.date.fromisoformat(end),
                "period_start": dt.date.fromisoformat(end).replace(month=1, day=1),
                "duration_days": 364,
                "basis": basis,
                "unit": "USD",
                "not_meaningful": {"roic": "n/m: pretax income not positive"},
                **base,
            }
        ]
    }


METRICS = {
    "AAA": period(gross_margin=D("0.60"), roic=D("0.20")),
    "BBB": period(gross_margin=D("0.30"), roic=D("0.25")),
    "CCC": period(gross_margin=D("0.70"), roic=None),
    "DDD": {"periods": []},
}
GM = ScreenFilter(metric="gross_margin", op="gte", value=D("0.5"))
ROIC = ScreenFilter(metric="roic", op="gte", value=D("0.1"))


def test_missing_metric_is_excluded_with_its_reason_not_treated_as_zero() -> None:
    res = run_screen(METRICS, list(METRICS), [GM, ROIC])
    assert [r.ticker for r in res.rows] == ["AAA"]
    assert res.universe_size == 4
    assert res.evaluated == 3
    assert (res.matched, res.no_data, res.filtered_out) == (1, 1, 1)
    reasons = {g.reason: g.tickers for g in res.excluded}
    assert reasons["Roic not computable: n/m: pretax income not positive"] == ["CCC"]
    assert reasons["Failed the gross margin filter"] == ["BBB"]
    assert reasons["No computable period in the stored statements"] == ["DDD"]
    # Every issuer is accounted for exactly once.
    assert res.matched + sum(g.count for g in res.excluded) == res.universe_size


def test_keep_unknown_keeps_the_issuer_and_lists_what_is_unknown() -> None:
    res = run_screen(METRICS, list(METRICS), [GM, ROIC], missing="keep_unknown")
    # Default order: the first filter's metric, best first (gross margin: CCC 70% before AAA 60%).
    assert [r.ticker for r in res.rows] == ["CCC", "AAA"]
    ccc = res.rows[0]
    assert ccc.unknown == ["roic"] and ccc.metrics["roic"] is None
    assert res.no_data == 0


def test_a_failing_value_wins_over_a_missing_one() -> None:
    res = run_screen(
        {"EEE": period(gross_margin=D("0.1"), roic=None)},
        ["EEE"],
        [GM, ROIC],
        missing="keep_unknown",
    )
    assert res.rows == [] and res.filtered_out == 1


def test_sorting_puts_unknown_last_and_respects_direction() -> None:
    low = ScreenFilter(metric="net_debt_to_equity", op="lte", value=D("5"))
    data = {
        "A": period(net_debt_to_equity=D("1.0")),
        "B": period(net_debt_to_equity=D("3.0")),
        "C": period(gross_margin=D("0.4"), net_debt_to_equity=None),
    }
    res = run_screen(data, ["A", "B", "C"], [low], missing="keep_unknown")
    # lte sorts ascending: lowest leverage first, unknown last.
    assert [r.ticker for r in res.rows] == ["A", "B", "C"]


def test_limit_caps_rows_but_not_the_counts() -> None:
    res = run_screen(METRICS, list(METRICS), [], limit=2)
    assert res.matched == 3 and res.shown == 2 and len(res.rows) == 2


def test_names_basis_and_period_are_carried_and_filters_are_validated() -> None:
    res = run_screen(
        {"AAA": period(basis="ifrs", gross_margin=D("0.6"))}, ["AAA"], [GM], names={"AAA": "Alpha"}
    )
    assert (res.rows[0].name, res.rows[0].basis, res.rows[0].period_end) == (
        "Alpha",
        "ifrs",
        "2025-12-31",
    )
    assert res.caveats and "market screen" in res.caveats[-1]
    with pytest.raises(ValidationError):
        ScreenFilter(metric="pe_ratio", op="gte", value=D("1"))  # type: ignore[arg-type]
