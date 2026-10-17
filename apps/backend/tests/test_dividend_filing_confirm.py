# ruff: noqa: E501
"""Suggested dividend events confirmed from the issuer's filed per-share dividends.

Provider history from before the holding was bought has no Trading212 payment to match, so it
is checked against the quarterly per-share dividend the issuer filed (XBRL)."""

import datetime as dt
from decimal import Decimal

from steward.dividends.model import DeclaredEvent
from steward.dividends.reconcile import FiledDividend, filing_confirmable, group_events

D = Decimal


def event(
    i: int, ex: str, amount: str, status: str = "suggested", currency: str | None = "USD"
) -> DeclaredEvent:
    return DeclaredEvent(
        id=i, ticker="KO_US_EQ", symbol="KO", ex_date=dt.date.fromisoformat(ex), payment_date=None,
        record_date=None, amount=D(amount), currency=currency, source="yahoo", source_url="yahoo:KO",
        status=status,
    )  # fmt: skip


def paid(start: str, end: str, amount: str, currency: str | None = "USD") -> FiledDividend:
    return FiledDividend(
        ticker="KO_US_EQ", period_start=dt.date.fromisoformat(start), period_end=dt.date.fromisoformat(end),
        per_share=D(amount), currency=currency, concept="us-gaap:CommonStockDividendsPerShareCashPaid",
        form="10-Q", url="https://www.sec.gov/x",
    )  # fmt: skip


FILED = [
    paid("2023-01-01", "2023-03-31", "0.46"),
    paid("2023-04-01", "2023-06-30", "0.46"),
    paid("2023-07-01", "2023-09-30", "0.46"),
    paid("2024-01-01", "2024-03-31", "0.485"),
]
INSIDE = [
    event(1, "2023-03-16", "0.46"),
    event(2, "2023-06-15", "0.46"),
    event(3, "2023-09-14", "0.46"),
    event(4, "2024-03-14", "0.485"),
]


def ids(checks) -> dict[int, bool]:
    return {c.group.event.id: c.filed is not None for c in checks}


def test_events_the_filings_state_are_confirmed_and_older_history_follows() -> None:
    older = event(9, "2019-03-14", "0.40")
    checks = filing_confirmable(group_events([*INSIDE, older]), FILED)
    assert ids(checks) == {1: True, 2: True, 3: True, 4: True, 9: False}
    assert next(c for c in checks if c.group.event.id == 9).corroborated == 4


def test_a_contradiction_keeps_the_older_history_with_the_owner() -> None:
    wrong = event(5, "2023-06-20", "0.60")  # not within 4 days of event 2, so its own payment
    checks = filing_confirmable(
        group_events([*INSIDE, wrong, event(9, "2019-03-14", "0.40")]), FILED
    )
    assert ids(checks) == {1: True, 2: True, 3: True, 4: True}


def test_too_few_matches_do_not_vouch_for_older_history() -> None:
    checks = filing_confirmable(group_events([*INSIDE[:3], event(9, "2019-03-14", "0.40")]), FILED)
    assert 9 not in ids(checks)


def test_a_filing_in_another_or_unstated_currency_is_not_compared() -> None:
    twd = [paid(f.period_start.isoformat(), f.period_end.isoformat(), "2.75", "TWD") for f in FILED]
    blank = [
        paid(f.period_start.isoformat(), f.period_end.isoformat(), "0.46", None) for f in FILED
    ]
    assert filing_confirmable(group_events(INSIDE), twd) == []
    assert filing_confirmable(group_events(INSIDE), blank) == []
    assert (
        filing_confirmable(group_events([event(1, "2023-03-16", "0.46", currency=None)]), FILED)
        == []
    )


def test_split_adjusted_provider_history_agrees_with_the_unadjusted_filing() -> None:
    checks = filing_confirmable(group_events([event(1, "2023-03-16", "0.046")]), FILED)
    assert [c.split for c in checks] == [10]


def test_a_quarter_paid_before_the_filed_window_is_not_compared_with_it() -> None:
    # Ex-date 30 Nov is too early to be paid in the January-March quarter (which raised to 0.46).
    assert filing_confirmable(group_events([event(1, "2022-11-30", "0.44")]), FILED) == []


def test_confirmed_events_count_but_are_not_returned() -> None:
    done = [event(e.id, e.ex_date.isoformat(), str(e.amount), status="confirmed") for e in INSIDE]
    checks = filing_confirmable(group_events([*done, event(9, "2019-03-14", "0.40")]), FILED)
    assert ids(checks) == {9: False}
