"""The alert digest groups open alerts by ticker without rewording them."""

import datetime as dt

from steward.disclosure.alerts import digest


def _row(i: int, ticker: str, kind: str, hour: int) -> dict[str, object]:
    when = dt.datetime(2026, 5, 1, hour, tzinfo=dt.UTC)
    return {
        "id": i,
        "ticker": ticker,
        "kind": kind,
        "headline": f"h{i}",
        "created_at": when,
        "known_at": when,
    }


def test_groups_by_ticker_most_alerts_first_newest_headline_first() -> None:
    rows = [
        _row(1, "AAA", "insider_trade", 1),
        _row(2, "BBB", "major_holder", 2),
        _row(3, "BBB", "insider_trade", 3),
    ]
    groups = digest(rows)  # type: ignore[arg-type]
    assert [g["ticker"] for g in groups] == ["BBB", "AAA"]
    assert groups[0]["headlines"] == ["h3", "h2"]
    assert groups[0]["kinds"] == ["insider_trade", "major_holder"]
    assert groups[0]["latest_known_at"] == dt.datetime(2026, 5, 1, 3, tzinfo=dt.UTC)


def test_no_alerts_gives_an_empty_digest() -> None:
    assert digest([]) == []
