# ruff: noqa: E501
"""Golden examples for the forward dividend schedule (insight plan I1.4, I14.1).

Every expected figure below is worked out by hand in the comment next to it, not read from the
code under test. Today is 2026-01-01 throughout, so the horizon ends on 2027-01-01.
"""

import datetime as dt
from decimal import Decimal

from steward.dividends.forecast import build_schedule, detect_frequency, schedule_total
from steward.dividends.metrics import annual_dps, dps_cagr, years_without_cut
from steward.dividends.model import DeclaredEvent, PaymentPoint
from steward.dividends.reconcile import EventGroup

D = Decimal
TODAY = dt.date(2026, 1, 1)


def day(text: str) -> dt.date:
    return dt.date.fromisoformat(text)


def points(*rows: tuple[str, str]) -> list[PaymentPoint]:
    """Past payments: (pay date, per share); every ex-date is 30 days before the pay date."""
    return [
        PaymentPoint(
            pay_date=day(p),
            ex_date=day(p) - dt.timedelta(days=30),
            per_share=D(a),
            currency="USD",
            origin="event",
            ref=f"corporate_action:{i}",
        )
        for i, (p, a) in enumerate(rows)
    ]


def fx(a: str, b: str) -> tuple[Decimal, dt.date | None]:
    return (D(1), None) if a == b else (D("0.9"), day("2025-12-31"))


def schedule(pts: list[PaymentPoint], groups: list[EventGroup] | None = None, **kw: object):
    args: dict = {
        "today": TODAY,
        "quantity": D(10),
        "account_currency": "EUR",
        "points": pts,
        "groups": groups or [],
        "fx": fx,
        "min_payments": 4,
        "assume_growth": False,
        "growth_5y": None,
        "withholding_share": None,
    }
    args.update(kw)
    return build_schedule("ACME", **args)


QUARTERLY = (
    ("2025-01-15", "0.50"),
    ("2025-04-15", "0.50"),
    ("2025-07-15", "0.50"),
    ("2025-10-15", "0.50"),
)


def test_quarterly_payer_projects_four_payments_at_the_last_amount() -> None:
    s = schedule(points(*QUARTERLY))
    # Gaps 90, 91, 92 days: median 91, inside the quarterly band 70-115.
    assert s.frequency == "quarterly"
    # Step 365/4 = 91.25 days from 2025-10-15: +91 (half-even of 91.25) = 2026-01-14,
    # +182 (182.5 -> 182) = 2026-04-15, +274 (273.75) = 2026-07-16, +365 = 2026-10-15;
    # +456 = 2027-01-14 is past the horizon.
    assert [r.pay_date for r in s.rows] == [
        day("2026-01-14"),
        day("2026-04-15"),
        day("2026-07-16"),
        day("2026-10-15"),
    ]
    assert {r.kind for r in s.rows} == {"estimated"}
    # Each: 0.50 USD x 10 shares x 0.9 EUR per USD = 4.50 EUR; the ex-date is 30 days earlier.
    assert all(r.amount == D("4.50") for r in s.rows)
    assert s.rows[0].ex_date == day("2025-12-15")
    assert schedule_total(s) == (D(0), D("18.00"), D(0))


def test_a_declared_payment_takes_the_estimated_slot() -> None:
    event = DeclaredEvent(
        id=7, ticker="ACME", symbol="ACME", ex_date=day("2025-12-20"),
        payment_date=day("2026-01-20"), record_date=None, amount=D("0.55"), currency="USD",
        source="twelve_data", source_url="", status="confirmed",
    )  # fmt: skip
    s = schedule(points(*QUARTERLY), [EventGroup(event)])
    # 2026-01-20 lies within half a step (45 days) of the 2026-01-14 slot, so that slot is not
    # estimated again: one declared row (0.55 x 10 x 0.9 = 4.95) and three estimated (13.50).
    assert [r.kind for r in s.rows] == ["declared", "estimated", "estimated", "estimated"]
    assert schedule_total(s) == (D("4.950"), D("13.50"), D(0))


def test_annual_payer_projects_one_payment() -> None:
    pts = points(
        ("2022-05-10", "1.20"), ("2023-05-10", "1.20"), ("2024-05-10", "1.20"), ("2025-05-12", "1.20")
    )  # fmt: skip
    for p in pts:
        p.currency = "EUR"
    s = schedule(pts)
    # Only payments since 2022-12-03 (three years and 30 days) set the cadence: gaps 366 and
    # 367 days, annual. One payment 365 days after 2025-05-12: 2026-05-12, 1.20 x 10 = 12.00 EUR.
    assert s.frequency == "annual"
    assert [(r.pay_date, r.amount) for r in s.rows] == [(day("2026-05-12"), D("12.00"))]


def test_after_a_cut_the_estimate_uses_the_cut_amount() -> None:
    pts = points(*QUARTERLY[:3], ("2025-10-15", "0.25"))
    s = schedule(pts)
    # Last regular payment 0.25: 0.25 x 10 x 0.9 = 2.25 EUR per estimated row, four rows = 9.00.
    assert all(r.amount == D("2.250") for r in s.rows)
    assert schedule_total(s)[1] == D("9.000")
    # 2024: 2.00 per share, 2025: 1.75; the fall is over 1 %, so no year without a cut.
    annual, problem = annual_dps(
        points(("2024-03-01", "0.50"), ("2024-06-01", "0.50"), ("2024-09-01", "0.50"),
               ("2024-12-01", "0.50"), *QUARTERLY[:3], ("2025-10-15", "0.25"))
    )  # fmt: skip
    assert problem is None
    assert annual == {2024: D("2.00"), 2025: D("1.75")}
    assert years_without_cut(annual, TODAY) == 0


def test_special_dividend_is_listed_apart_and_never_estimated() -> None:
    pts = points(*QUARTERLY[:2], ("2025-06-01", "2.00"), *QUARTERLY[2:])
    s = schedule(pts)
    # Median of the five amounts is 0.50; 2.00 is over twice that, so it is special.
    assert [p.per_share for p in s.specials] == [D("2.00")]
    assert s.payments_seen == 4
    assert s.frequency == "quarterly"
    assert all(r.per_share == D("0.50") for r in s.rows)


def test_new_payer_has_no_schedule_rather_than_zero() -> None:
    s = schedule(points(("2025-07-15", "0.30"), ("2025-10-15", "0.30")))
    assert s.status == "unavailable"
    assert s.rows == []
    assert "history too short: 2 regular payments" in (s.reason or "")


def test_non_payer_is_unavailable_with_its_reason() -> None:
    s = schedule([])
    assert (s.frequency, s.status) == ("none", "unavailable")
    assert s.reason == "no dividend on record for this holding"


def test_growth_is_applied_only_when_the_owner_turns_it_on() -> None:
    flat = schedule(points(*QUARTERLY), growth_5y=D("0.05"))
    assert flat.rows[0].per_share == D("0.50")
    grown = schedule(points(*QUARTERLY), assume_growth=True, growth_5y=D("0.05"))
    # 0.50 x 1.05^(91/365) = 0.50 x 1.012238 = 0.506119.
    assert abs(grown.rows[0].per_share - D("0.506119")) < D("0.000001")
    assert grown.growth_used == D("0.05")


def test_irregular_cadence_is_not_estimated() -> None:
    # Gaps 20, 200, 90 days: median 90, and only one of three gaps lies within 35 % of it
    # (58.5 to 121.5), under the 60 % needed for a regular cadence.
    assert detect_frequency([day("2025-01-01"), day("2025-01-21"), day("2025-08-09"), day("2025-11-07")]) == "irregular"  # fmt: skip
    # Gaps 20, 200, 30: median 30, two of three within 35 % (19.5 to 40.5): monthly.
    assert detect_frequency([day("2025-01-01"), day("2025-01-21"), day("2025-08-09"), day("2025-09-08")]) == "monthly"  # fmt: skip


def test_dividend_growth_cagr() -> None:
    # 1.00 in 2020 to 1.61051 in 2025 is 10 % a year: 1.1^5 = 1.61051.
    annual = {2020: D("1.00"), 2021: D("1.10"), 2022: D("1.21"), 2023: D("1.331"), 2024: D("1.4641"), 2025: D("1.61051")}  # fmt: skip
    cagr, problem = dps_cagr(annual, 5, TODAY)
    assert problem is None and cagr is not None
    assert abs(cagr - D("0.10")) < D("0.000001")
    assert years_without_cut(annual, TODAY) == 5
    assert dps_cagr({2024: D(1), 2025: D(1)}, 5, TODAY) == (
        None,
        "needs dividends in 2020 and 2025",
    )


def test_accuracy_matches_each_payment_once_and_to_the_closest_row() -> None:
    from steward.dividends.accuracy import forecast_accuracy
    from steward.dividends.model import ReceivedPayment

    # Two forecast rows 11 days apart, both inside the 21-day window, one payment 2 days after the second: one hit (the second
    # row, error (0.50 - 0.40) / 0.40 = 0.25), not two hits off the same payment.
    body = {
        "rows": [
            {"pay_date": "2025-03-10", "per_share": "0.30"},
            {"pay_date": "2025-03-21", "per_share": "0.50"},
        ]
    }
    paid = [ReceivedPayment("p1", "A", day("2025-03-23"), D("4"), "EUR", D("10"), D("0.40"))]
    a = forecast_accuracy({"A": body}, day("2025-01-01"), day("2025-01-01"), paid, TODAY)
    assert (a.rows, a.hits, a.hit_rate, a.mean_error) == (2, 1, D("0.5"), D("0.25"))


def test_single_source_history_counts_only_when_the_rule_allows_it_and_is_labelled() -> None:
    from steward.dividends.forecast import history_points
    from steward.dividends.reconcile import Reconciliation

    def yahoo(i: int, ex: str) -> EventGroup:
        return EventGroup(DeclaredEvent(
            id=i, ticker="ACME", symbol="ACME", ex_date=day(ex), payment_date=None,
            record_date=None, amount=D("0.50"), currency="USD", source="yahoo",
            source_url="", status="suggested",
        ))  # fmt: skip

    groups = [yahoo(1, "2025-01-15"), yahoo(2, "2025-04-15"), yahoo(3, "2025-07-15")]
    rec = Reconciliation(matches=[], unmatched_events=groups, disagreements=[])
    # Rule off: one source is not enough, so there is no history at all.
    assert history_points("ACME", rec, groups, "USD") == []
    # Rule on: all three count, each labelled single_source, dated by the ex-date (no pay date).
    lone = history_points("ACME", rec, groups, "USD", single_source=True)
    assert [(p.pay_date, p.origin) for p in lone] == [
        (day("2025-01-15"), "single_source"),
        (day("2025-04-15"), "single_source"),
        (day("2025-07-15"), "single_source"),
    ]
    # History is not a declaration: the schedule estimates from it and declares nothing.
    s = schedule(lone, groups, min_payments=3)
    assert s.rows and "declared" not in {r.kind for r in s.rows}
