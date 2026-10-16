"""Consensus read model (insight I8): source choice per day, agreement classes, coverage states
and the 12-month track record."""

import datetime as dt
from decimal import Decimal as D
from typing import Any

from steward.sources import consensus_view as cv


def row(day: dt.date, source: str, mean: str | None, **kw: Any) -> dict[str, Any]:
    return {
        "ticker": "AAA",
        "as_of": day,
        "source": source,
        "analyst_count": kw.get("count"),
        "target_mean": D(mean) if mean else None,
        "target_median": None,
        "target_high": D(kw["high"]) if "high" in kw else None,
        "target_low": D(kw["low"]) if "low" in kw else None,
        "target_currency": "USD",
        "price_at_snapshot": D(kw.get("price", "100")),
        "access_state": kw.get("state", "complete" if mean else "paid_only"),
        "detail": kw.get("detail"),
        "raw": {},
    }


def test_best_source_per_day_prefers_finnhub() -> None:
    d = dt.date(2026, 1, 5)
    pts = cv.best_per_day(
        [row(d, "fmp", "120"), row(d, "finnhub", "125", count=12), row(d, "twelve_data", None)]
    )
    assert len(pts) == 1 and pts[0].source == "finnhub" and pts[0].count == 12


def test_agreement_classes() -> None:
    assert cv.agreement(D("0.1"), 0.25, 0.5) == "close"
    assert cv.agreement(D("0.3"), 0.25, 0.5) == "moderate"
    assert cv.agreement(D("0.5"), 0.25, 0.5) == "wide"
    assert cv.agreement(None, 0.25, 0.5) is None
    p = cv.best_per_day(
        [row(dt.date(2026, 1, 5), "finnhub", "120", high="150", low="90", count=8)]
    )[0]
    latest = cv.latest_of(p, None, 0.25, 0.5)
    assert (
        latest.upside == D("0.2") and latest.dispersion == D("0.5") and latest.agreement == "wide"
    )
    assert "8 analysts" in latest.agreement_text


def test_coverage_states_never_invent_a_target() -> None:
    d = dt.date(2026, 1, 5)
    states = [cv.SourceState(source="finnhub", access_state="paid_only", as_of=d, detail="premium")]
    cov, reason = cv.coverage_of(states, [])
    assert cov == "unavailable" and "finnhub: paid only (premium)" in reason
    assert cv.coverage_of([], [])[0] == "unavailable"
    ok = [cv.SourceState(source="fmp", access_state="complete", as_of=d, detail=None)]
    assert cv.coverage_of(ok, [])[0] == "complete"


def test_track_record_waits_twelve_months_then_scores() -> None:
    start = dt.date(2025, 1, 6)
    hist = cv.best_per_day([row(start, "fmp", "120"), row(dt.date(2025, 6, 2), "fmp", "130")])
    early = cv.track_record(hist, [], "USD", dt.date(2025, 12, 1))
    assert not early.available and early.starts == dt.date(2026, 1, 6)
    closes = [(start + dt.timedelta(days=7 * k), D(100 + k)) for k in range(0, 80)]
    rec = cv.track_record(hist, closes, "USD", dt.date(2026, 2, 1))
    assert rec.available and rec.n == 1  # only the January snapshot is a year old
    r = rec.rows[0]
    assert r.reached is True and r.price_after is not None and r.error is not None
