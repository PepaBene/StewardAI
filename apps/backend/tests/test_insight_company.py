# ruff: noqa: E501
"""Insight I13: ownership mix split, CEO pay and tenure helpers, the cited earnings note's
verification, the insider monthly strip and the proxy pay concepts."""

import datetime as dt
from decimal import Decimal

from steward.company.pay_tenure import Insider, average, is_ceo, tenure_sets, years_between
from steward.earnings.summary import ModelLine, ModelNote, fragment_url, verify
from steward.ownership.insider_read import monthly
from steward.ownership.mix import split
from steward.sources.xbrl_concepts import classify

D = Decimal
TODAY = dt.date(2026, 10, 1)


def test_split_states_the_rest_only_when_both_parts_are_known_and_do_not_overlap() -> None:
    assert split(D("0.6"), D("0.1")) == (D("0.3"), None)
    rest, why = split(D("0.95"), D("0.1"))
    assert rest is None and why and "overlap" in why
    assert split(None, D("0.1"))[0] is None
    assert split(D("0.6"), None)[0] is None


def test_ceo_titles() -> None:
    assert is_ceo("Chief Executive Officer")
    assert is_ceo("President and CEO")
    assert is_ceo("Principal Executive Officer")
    assert not is_ceo("Chief Financial Officer")
    assert not is_ceo("Executive Vice President")
    assert not is_ceo(None)


def _insider(
    name: str, *, officer: dt.date | None, director: dt.date | None, last: dt.date
) -> Insider:
    return Insider(
        cik=name, name=name, title=None, first_officer=officer, first_director=director,
        first_ceo=None, last_filed=last, officer_now=officer is not None,
        director_now=director is not None,
    )  # fmt: skip


def test_tenure_counts_current_filers_from_their_first_form_4_in_the_role() -> None:
    insiders = [
        _insider("A", officer=dt.date(2016, 10, 1), director=None, last=dt.date(2026, 3, 1)),
        _insider("B", officer=dt.date(2021, 10, 1), director=dt.date(2021, 10, 1), last=dt.date(2026, 5, 1)),
        _insider("C", officer=None, director=dt.date(2011, 10, 1), last=dt.date(2026, 2, 1)),
        # left: last filed more than two years ago
        _insider("D", officer=dt.date(2001, 1, 1), director=None, last=dt.date(2022, 1, 1)),
    ]  # fmt: skip
    management, board = tenure_sets(insiders, TODAY)
    assert dict(management) == {"A": 10.0, "B": 5.0}
    assert dict(board) == {"B": 5.0, "C": 15.0}
    assert average([y for _n, y in management]) == 7.5
    assert average([]) is None
    assert years_between(dt.date(2020, 4, 1), TODAY) == 6.5


RELEASE = (
    "Revenue for the quarter was $12.4 billion, up 8% from a year earlier. "
    "Operating margin narrowed to 21.3% because of higher freight costs. "
    "The company raised its full-year outlook for earnings per share to $5.10 to $5.30. "
    "Inventory rose 14% as the company built stock ahead of the new product launch."
)


def test_verify_keeps_only_lines_whose_quote_is_in_the_release() -> None:
    raw = ModelNote(
        summary=[
            ModelLine(text="Revenue grew 8%.", quote="Revenue for the quarter was $12.4 billion, up 8%"),
            ModelLine(text="Margins fell on freight.", quote="Operating margin narrowed to 21.3%"),
            ModelLine(text="Made-up claim.", quote="record cash flow of $9 billion"),
            ModelLine(text="Too short a quote.", quote="up 8%"),
        ],
        questions=[
            ModelLine(text="How long will freight costs weigh on margin?", quote="because of higher freight costs"),
            ModelLine(text="Why build inventory now?", quote="Inventory rose 14% as the company built stock"),
            ModelLine(text="What drives the raised outlook?", quote="raised its full-year outlook for earnings per share"),
            ModelLine(text="Duplicate?", quote="raised its full-year outlook for earnings per share"),
            ModelLine(text="duplicate?", quote="raised its full-year outlook for earnings per share"),
        ],
    )  # fmt: skip
    summary, questions, dropped = verify(raw, RELEASE, "https://ir.example.com/q3")
    assert [s.text for s in summary] == ["Revenue grew 8%.", "Margins fell on freight."]
    assert len(questions) == 4
    assert dropped == 3  # an unfound quote, a too-short quote and a repeated question
    assert summary[0].quote_url and summary[0].quote_url.startswith(
        "https://ir.example.com/q3#:~:text="
    )


def test_fragment_links_only_for_web_pages() -> None:
    assert fragment_url("https://x.com/a.pdf", "some long passage here") is None
    assert fragment_url(None, "passage") is None
    long = fragment_url("https://x.com/r#top", "one two three four five six seven eight nine ten")
    assert long == "https://x.com/r#:~:text=one%20two%20three%20four,seven%20eight%20nine%20ten"


def _row(day: dt.date, cls: str, shares: str, price: str | None) -> dict[str, object]:
    return {
        "transaction_date": day, "filed": day, "economic_class": cls, "shares": D(shares),
        "price_per_share": None if price is None else D(price),
    }  # fmt: skip


def test_monthly_strip_has_every_month_and_counts_only_counted_rows() -> None:
    rows = [
        _row(dt.date(2026, 1, 5), "open_market_buy", "100", "10"),
        _row(dt.date(2026, 1, 20), "open_market_sell", "50", "12"),
        _row(dt.date(2026, 3, 2), "open_market_sell", "10", None),
        _row(dt.date(2026, 3, 9), "open_market_buy", "999", "1"),  # excluded below
    ]
    months = monthly(rows, [True, True, True, False], dt.date(2026, 1, 15), dt.date(2026, 4, 2))
    assert [m.month for m in months] == [dt.date(2026, m, 1) for m in (1, 2, 3, 4)]
    jan, feb, mar, _apr = months
    assert (jan.bought_value, jan.sold_value, jan.net_value) == (D(1000), D(600), D(400))
    assert (jan.buys, jan.sells) == (1, 1)
    assert feb.net_value == 0 and feb.buys == feb.sells == 0
    assert mar.sold_shares == 10 and mar.bought_shares == 0 and not mar.value_complete


def test_proxy_pay_concepts_are_notes_with_line_items() -> None:
    assert classify("ecd:PeoTotalCompAmt") == ("note", "peo_total_comp")
    assert classify("ecd:PeoActuallyPaidCompAmt", dimensional=True) == (
        "segment",
        "peo_actually_paid_comp",
    )
