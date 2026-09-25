"""Verdicts are computed in code from verified facts; unknown is explicit, never omitted."""

from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from steward.config.thesis import ThesisConfig, ThesisKPI
from steward.earnings.metrics import derivation, metrics_to_extract
from steward.earnings.periods import (
    calendar_period_end,
    likely_reported_period,
    previous_quarter,
)
from steward.earnings.units import KpiUnit, parse_kpi_unit
from steward.earnings.verdicts import FactRow, judge_thesis

_ids = iter(range(1, 10_000))


def fact(
    metric: str, fy: int, fp: str, value: str, unit: str = "currency", currency: str | None = "USD"
) -> FactRow:
    return FactRow(next(_ids), metric, "reported", fy, fp, Decimal(value), unit, currency)


def kpi(**overrides: Any) -> ThesisKPI:
    base: dict[str, Any] = {
        "id": "k",
        "metric": "revenue_growth_yoy",
        "operator": ">=",
        "threshold": 5,
        "unit": "%",
    }
    return ThesisKPI(**{**base, **overrides})


def judge(k: ThesisKPI, facts: list[FactRow]) -> list[Any]:
    return judge_thesis(ThesisConfig(ticker="AAPL_US_EQ", why_owned="x", kpis=[k]), facts)


APPLE = [
    fact("revenue", 2025, "Q3", "94036000000"),
    fact("revenue", 2024, "Q3", "85777000000"),
    fact("gross_profit", 2025, "Q3", "43718000000"),
    fact("eps_diluted", 2025, "Q3", "1.57", "per_share"),
]


def test_growth_is_recomputed_from_raw_figures() -> None:
    [latest, older] = judge(kpi(), APPLE)
    assert latest.period == "FY2025 Q3" and latest.status == "pass"
    assert latest.observed_value == Decimal("9.628455")
    assert len(latest.fact_ids) == 2 and "85.78 bn USD" in latest.rationale
    assert older.period == "FY2024 Q3" and older.status == "unknown"


def test_a_changed_threshold_changes_the_verdict() -> None:
    [latest, _] = judge(kpi(threshold=10), APPLE)
    assert latest.status == "breach"


def test_margin_and_money_and_per_share_units() -> None:
    [margin] = judge(kpi(metric="gross_margin", threshold=46.5), APPLE)
    assert margin.status == "breach" and margin.observed_value == Decimal("46.490706")
    rev = judge(kpi(metric="revenue", operator=">", threshold=90, unit="USD bn"), APPLE)
    assert [v.status for v in rev] == ["pass", "breach"]
    [eps] = judge(kpi(metric="eps_diluted", threshold=1.5, unit="USD"), APPLE)
    assert eps.status == "pass"


def test_currency_mismatch_is_unknown_not_converted() -> None:
    [latest, _] = judge(kpi(metric="revenue", unit="EUR bn", threshold=1), APPLE)
    assert latest.status == "unknown" and "in USD, the KPI in EUR" in latest.rationale


def test_no_facts_gives_one_unknown_verdict() -> None:
    [only] = judge(kpi(), [])
    assert only.status == "unknown" and only.period is None
    assert "No verified reported revenue yet" in only.rationale


def test_ttm_needs_four_consecutive_quarters() -> None:
    quarters = [fact("revenue", 2025, q, "10") for q in ("Q1", "Q2", "Q3")]
    k = kpi(metric="revenue", period="ttm", unit="USD", threshold=35)
    assert judge(k, quarters)[0].status == "unknown"
    [latest, *_] = judge(k, [*quarters, fact("revenue", 2024, "Q4", "6")])
    assert latest.observed_value == Decimal("36") and latest.status == "pass"


def test_newest_fact_wins_when_a_restatement_is_approved() -> None:
    old = fact("revenue", 2025, "Q3", "94036000000")
    new = fact("revenue", 2025, "Q3", "80000000000")
    prior = fact("revenue", 2024, "Q3", "85777000000")
    [latest, _] = judge(kpi(), [old, prior, new])
    assert latest.status == "breach" and new.id in latest.fact_ids


def test_stated_margin_is_used_when_components_are_missing() -> None:
    stated = fact("gross_margin", 2025, "Q3", "46.5", "percent", None)
    [v] = judge(kpi(metric="gross_margin", threshold=46), [stated])
    assert v.status == "pass" and v.observed_value == Decimal("46.5")


@pytest.mark.parametrize(
    ("unit", "parsed"),
    [
        ("%", KpiUnit("percent")),
        ("x", KpiUnit("ratio")),
        ("EUR bn", KpiUnit("currency", "billions", "EUR")),
        ("USD m", KpiUnit("currency", "millions", "USD")),
        ("USD per share", KpiUnit("per_share", "units", "USD")),
        ("CZK", KpiUnit("currency", "units", "CZK")),
    ],
)
def test_kpi_units(unit: str, parsed: KpiUnit) -> None:
    assert parse_kpi_unit(unit) == parsed


def test_unknown_kpi_unit_fails_at_save_time() -> None:
    with pytest.raises(ValidationError, match="unrecognised unit"):
        kpi(unit="lots")


def test_derivations_and_requested_metrics() -> None:
    assert derivation("revenue_growth_yoy").components == ("revenue",)
    assert derivation("fcf_margin").components == ("free_cash_flow", "revenue")
    assert derivation("backlog").kind == "direct"
    wanted = metrics_to_extract(["fcf_margin", "backlog"])
    assert {"revenue", "free_cash_flow", "fcf_margin", "backlog"} <= set(wanted)


@pytest.mark.parametrize(
    ("fy", "fp", "fye", "end"),
    [
        (2025, "Q3", 9, "2025-06-30"),
        (2026, "Q1", 6, "2025-09-30"),
        (2026, "Q2", 1, "2025-07-31"),
        (2025, "FY", 12, "2025-12-31"),
        (2025, "H1", 12, "2025-06-30"),
    ],
)
def test_fiscal_periods_map_to_calendar_months(fy: int, fp: str, fye: int, end: str) -> None:
    assert calendar_period_end(fy, fp, fye).isoformat() == end


def test_period_helpers() -> None:
    import datetime as dt

    assert previous_quarter(2025, "Q1") == (2024, "Q4")
    assert likely_reported_period(dt.date(2025, 7, 31), 9) == (2025, "Q3")
    assert likely_reported_period(dt.date(2025, 10, 29), 6) == (2026, "Q1")
