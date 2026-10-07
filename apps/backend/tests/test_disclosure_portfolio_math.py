"""P9.5: portfolio look-through arithmetic for share classes, funds that are not held, mixed
currencies already converted to the account currency, and the invariants the totals must keep."""

import datetime as dt
from decimal import Decimal as D

from steward.disclosure import coverage_report as cr
from steward.disclosure import look_through as lt

WHEN = dt.datetime(2026, 10, 7, 12, tzinfo=dt.UTC)
PERIOD = dt.date(2026, 6, 30)
KNOWN = dt.datetime(2026, 8, 1, tzinfo=dt.UTC)


def fund_line(fund: str, held: str | None, pct: str | None) -> lt.FundLine:
    return lt.FundLine(fund, held, None, None if pct is None else D(pct), PERIOD, KNOWN)


def build(holdings: list[cr.Holding], lines: list[lt.FundLine]) -> dict:
    # The view lists only companies with a fund line, a relationship or a deal, so each holding
    # gets a small customer record to appear in `exposures`.
    rels = [
        lt.Relationship(
            h.ticker, "customer", "k", "Big Co", False, D("0.01"), None, "FY2025", KNOWN
        )
        for h in holdings
    ]
    return lt.build(holdings, lines, rels, [], WHEN, dt.date(2026, 10, 6))


def exposure(out: dict, ticker: str) -> dict:
    return next(e for e in out["exposures"] if e["ticker"] == ticker)


def test_two_share_classes_of_one_company_stay_separate_tickers() -> None:
    holdings = [cr.Holding("GOOG", D(300), "US", 1), cr.Holding("GOOGL", D(200), "US", 1)]
    out = build(holdings, [])
    assert D(exposure(out, "GOOG")["direct_weight"]) == D("0.6")
    assert D(exposure(out, "GOOGL")["direct_weight"]) == D("0.4")


def test_a_fund_reaches_each_class_only_through_the_class_it_reports() -> None:
    holdings = [
        cr.Holding("GOOG", D(300), "US", 1),
        cr.Holding("GOOGL", D(200), "US", 1),
        cr.Holding("FUND", D(500), "US", None),
    ]
    out = build(holdings, [fund_line("FUND", "GOOGL", "0.1")])
    assert D(exposure(out, "GOOGL")["fund_weight"]) == D("0.05")
    assert D(exposure(out, "GOOGL")["total_weight"]) == D("0.25")
    assert exposure(out, "GOOG")["fund_weight"] == "0"


def test_a_fund_line_for_a_fund_that_is_not_held_adds_no_weight() -> None:
    holdings = [cr.Holding("AAA", D(100), "US", 1)]
    out = build(holdings, [fund_line("NOTHELD", "AAA", "0.5")])
    row = exposure(out, "AAA")
    assert row["fund_weight"] == "0" and row["total_weight"] == row["direct_weight"]
    assert row["funds"][0]["contribution"] is None  # unknown, not zero


def test_a_company_reached_only_through_a_fund_is_listed_as_not_held_directly() -> None:
    holdings = [cr.Holding("FUND", D(400), "US", None), cr.Holding("AAA", D(600), "US", 1)]
    out = build(holdings, [fund_line("FUND", "ZZZ", "0.25")])
    zzz = exposure(out, "ZZZ")
    assert zzz["held_directly"] is False and D(zzz["total_weight"]) == D("0.1")


def test_values_already_in_the_account_currency_give_weights_that_sum_to_one() -> None:
    # 100 USD at 23.5 CZK, 50 EUR at 25.0 CZK and 12 000 CZK: converted upstream to CZK values.
    holdings = [
        cr.Holding("USDCO", D(100) * D("23.5"), "US", 1),
        cr.Holding("EURCO", D(50) * D("25.0"), "DE", 2),
        cr.Holding("CZKCO", D(12000), "CZ", 3),
    ]
    out = build(holdings, [])
    total = sum(D(e["direct_weight"]) for e in out["exposures"])
    assert abs(total - 1) < D("1e-20")  # decimal division rounds at 28 digits
    assert D(out["denominator_value"]) == D(2350 + 1250 + 12000)


def test_a_fund_cannot_make_total_exposure_exceed_the_weight_it_was_given() -> None:
    holdings = [cr.Holding("FUND", D(500), "US", None), cr.Holding("AAA", D(500), "US", 1)]
    lines = [fund_line("FUND", "AAA", "0.6"), fund_line("FUND", "BBB", "0.4")]
    out = build(holdings, lines)
    via = sum(D(e["fund_weight"]) for e in out["exposures"])
    assert via == D("0.5")  # exactly the fund's own weight when its lines add to 100 %
    assert D(exposure(out, "AAA")["total_weight"]) == D("0.5") + D("0.3")


def test_unknown_percentages_are_counted_as_unknown_lines_and_never_as_zero_weight() -> None:
    holdings = [cr.Holding("FUND", D(100), "US", None)]
    out = build(holdings, [fund_line("FUND", "AAA", None), fund_line("FUND", "BBB", "0.1")])
    assert out["funds"][0]["unknown_lines"] == 1
    assert all(e["ticker"] != "AAA" for e in out["exposures"])


def test_a_zero_value_watchlist_ticker_gets_zero_direct_weight_not_a_division_error() -> None:
    holdings = [cr.Holding("AAA", D(100), "US", 1), cr.Holding("WATCH", D(0), "US", None)]
    out = build(holdings, [])
    assert D(exposure(out, "WATCH")["direct_weight"]) == 0
    assert D(exposure(out, "AAA")["direct_weight"]) == 1
