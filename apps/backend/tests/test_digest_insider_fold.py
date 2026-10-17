"""A digest folds a company's insider trades into one line with counts and totals."""

import datetime as dt

from steward.news.digest import fold_insider_trades

AT = dt.datetime(2026, 10, 16, tzinfo=dt.UTC)


def alert(ticker: str, headline: str, value: str | None, kind: str = "insider_trade") -> dict:
    return {
        "ticker": ticker,
        "kind": kind,
        "headline": headline,
        "source_url": "https://www.sec.gov/x",
        "known_at": AT,
        "value": value,
    }


def test_a_companys_insider_trades_read_as_one_line_with_buys_first() -> None:
    lines = fold_insider_trades(
        [
            alert("GOOGL", "Brin Sergey reported an open-market sale of 33,332 shares", "25270611"),
            alert("BA", "Boeing holder crossed 5%", None, kind="major_holder"),
            alert("GOOGL", "Pichai reported an open-market sale of 10,000 shares", "2000000"),
            alert("GOOGL", "Doe reported an open-market purchase of 1,000 shares", "150000"),
            alert("CELH", "Fieldly reported an open-market purchase of 5,000 shares", "200000"),
        ]
    )
    folded = "Insiders filed 1 open-market purchase ($150,000) and 2 open-market sales ($27.3M)"
    assert [(line.ticker, line.headline) for line in lines] == [
        ("GOOGL", folded),
        ("BA", "Boeing holder crossed 5%"),
        ("CELH", "Fieldly reported an open-market purchase of 5,000 shares"),
    ]
    assert lines[0].source_url is None
    assert lines[2].source_url == "https://www.sec.gov/x"
