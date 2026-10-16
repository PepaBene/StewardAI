"""Valuation pictures (insight I7): key-multiple rule, histogram bins, first-reported EPS with
split adjustment, history windows and the fair-multiple regression."""

import datetime as dt
from decimal import Decimal as D

from steward.valuation import pictures as p


def test_key_multiple_rule() -> None:
    assert p.choose_multiple(D(20), D(3), D(2))[0] == "pe"
    assert p.choose_multiple(None, D(3), D(2))[0] == "ps"
    assert p.choose_multiple(D(-5), None, D(2))[0] == "pb"
    key, reason = p.choose_multiple(None, None, None)
    assert key is None and "No P/E" in reason


def test_histogram_marks_the_company_and_opens_the_tails() -> None:
    values = [D(x) for x in range(5, 45)] + [D(400)]
    bins = p.histogram(values, D(20))
    assert len(bins) == p.HIST_BINS
    assert sum(b.count for b in bins) == len(values)
    assert sum(b.has_company for b in bins) == 1
    assert bins[-1].open_high and bins[0].open_low
    assert p.percentile_rank(D(20), values) == D("0.3780")


def test_first_reported_eps_wins_over_restatements() -> None:
    rows = [
        {
            "period_end": dt.date(2023, 12, 31),
            "value": D("2.0"),
            "known_at": dt.datetime(2024, 2, 1),
        },
        {
            "period_end": dt.date(2023, 12, 31),
            "value": D("1.8"),
            "known_at": dt.datetime(2025, 2, 1),
        },
        {
            "period_end": dt.date(2024, 12, 31),
            "value": D("3.0"),
            "known_at": dt.datetime(2025, 2, 1),
        },
    ]
    eps = p.first_reported(rows)
    assert [(e[0].year, e[1]) for e in eps] == [(2023, D("2.0")), (2024, D("3.0"))]


def test_pe_series_uses_known_eps_and_splits() -> None:
    eps = [(dt.date(2023, 12, 31), D("2.0"), dt.date(2024, 2, 1))]
    closes = [(dt.date(2024, 1, 5), D(40)), (dt.date(2024, 3, 1), D(40))]
    # A 2-for-1 split after the fiscal year: adjusted EPS is 1.0, so P/E 40.
    pts = p.pe_series(closes, eps, [(dt.date(2024, 6, 1), D(2))])
    assert pts[0].pe is None and pts[0].eps is None  # EPS not yet known on 5 January
    assert pts[1].pe == D(40) and pts[1].eps_period_end == dt.date(2023, 12, 31)


def test_history_window_needs_coverage() -> None:
    today = dt.date(2026, 1, 1)
    eps = [(dt.date(2020, 12, 31), D(2), dt.date(2021, 1, 1))]
    closes = [(today - dt.timedelta(days=7 * k), D(40 + k % 5)) for k in range(0, 160)][::-1]
    pts = p.pe_series(closes, eps, [])
    one = p.window_stats(pts, 1, today)
    assert one.available and one.low is not None and one.high is not None
    assert one.low < one.median < one.high  # type: ignore[operator]
    ten = p.window_stats(pts, 10, today)
    assert not ten.available and ten.reason and "starts after" in ten.reason


def test_fair_multiple_fit_recovers_a_linear_rule() -> None:
    pts = [
        (f"P{i}", D(str(g)), D(str(m)), D(str(round(5 + 50 * g + 40 * m, 4))))
        for i, (g, m) in enumerate(
            [(0.05, 0.1), (0.1, 0.15), (0.2, 0.05), (0.15, 0.2), (0.0, 0.3), (0.25, 0.25)]
        )
    ]
    got = p.fit(pts)
    assert got is not None
    beta, r2, _ = got
    assert abs(beta[0] - 5) < 1e-6 and abs(beta[1] - 50) < 1e-6 and abs(beta[2] - 40) < 1e-6
    assert r2 > 0.999
    assert p.fit(pts[:3]) is None
