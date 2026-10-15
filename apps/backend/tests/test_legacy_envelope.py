"""The tools written before the shared envelope report status, date and units like the rest."""

import datetime as dt

from steward.chat import legacy_envelope as le
from steward.research.sources import Registry

TODAY = dt.date(2026, 10, 11)


def registry_with(as_of: str | None) -> tuple[Registry, str]:
    registry = Registry()
    return registry, registry.add("observation", "x", "x", as_of=as_of)


def test_unavailable_becomes_a_missing_status_with_the_reason() -> None:
    out = le.wrap("risk_forecast", {"unavailable": "no history"}, Registry(), TODAY)
    assert out == {"status": "missing", "reason": "no history"}


def test_figures_get_units_and_the_source_date() -> None:
    registry, sid = registry_with("2026-10-09")
    result = {
        "source_id": sid,
        "volatility_forecast": 0.18,
        "effective_bets": None,
        "largest_risk_contributors": [{"ticker": "A", "risk_share": 0.4, "weight": 0.3}],
    }
    out = le.wrap("risk_forecast", result, registry, TODAY)
    assert out["status"] == "ok" and out["as_of"] == "2026-10-09"
    assert out["volatility_forecast"] == {
        "value": 0.18,
        "status": "ok",
        "source_id": sid,
        "as_of": "2026-10-09",
        "unit": "fraction_per_year",
    }
    # An absent figure stays absent, not zero.
    assert out["effective_bets"]["status"] == "missing" and out["effective_bets"]["value"] is None
    row = out["largest_risk_contributors"][0]
    assert row["risk_share"]["unit"] == "fraction" and row["risk_share"]["value"] == 0.4
    assert row["ticker"] == "A"


def test_an_old_source_marks_the_result_stale_but_keeps_the_values() -> None:
    registry, sid = registry_with("2026-09-01")
    out = le.wrap("risk_forecast", {"source_id": sid, "volatility_forecast": 0.2}, registry, TODAY)
    assert out["status"] == "stale" and out["volatility_forecast"]["value"] == 0.2


def test_currency_comes_from_the_sibling_field() -> None:
    registry, sid = registry_with("2026-10-10")
    out = le.wrap(
        "performance_explain",
        {"source_id": sid, "base_currency": "EUR", "gain": 120.5, "residual": 0.0},
        registry,
        TODAY,
    )
    assert out["gain"]["currency"] == "EUR" and out["gain"]["unit"] == "amount"
    assert out["residual"]["value"] == 0.0  # a real zero is kept as a zero


def test_errors_and_newer_tools_pass_through() -> None:
    err = {"error": "bad"}
    assert le.wrap("stress_test", err, Registry(), TODAY) is err
    newer = {"status": "ok", "rows": []}
    assert le.wrap("price_history", newer, Registry(), TODAY) is newer


def test_a_result_without_a_date_says_so() -> None:
    out = le.wrap("start_size", {"suggested": 0.03}, Registry(), TODAY)
    assert out["status"] == "ok" and "as_of_note" in out and "as_of" not in out
