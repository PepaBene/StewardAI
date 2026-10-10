"""Deep-audit fixes from the second backend pass (B2-B8)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal as D

from steward.analytics import risk
from steward.disclosure import ownership as own
from steward.sources.schedule13 import parse_exhibits


def test_a_cited_exhibit_is_not_listed_and_a_lettered_one_is() -> None:
    text = (
        "Exhibit 99.1 - Joint Filing Agreement. The agreement is incorporated by reference "
        "to Exhibit 99.1 of the prior filing. Exhibit A: Power of attorney."
    )
    got = parse_exhibits(text)
    assert [label for label, _ in got] == ["Exhibit 99.1", "Exhibit A"]
    assert got[1][1] == "Power of attorney"


def test_exhibit_a_lowercase_article_is_not_a_label() -> None:
    assert parse_exhibits("See Exhibit a copy of the agreement.") == ()


def test_a_report_with_a_missing_share_count_publishes_no_total() -> None:
    report = own.EffectiveReport(
        cik="1",
        period=dt.date(2026, 3, 31),
        accessions=("a", "b"),
        known_at=dt.datetime(2026, 5, 1, tzinfo=dt.UTC),
        amended=False,
    )
    lines = own.effective_positions(
        report,
        [
            own.Holding13F("a", "037833100", D(10), D(100)),
            own.Holding13F("b", "037833100", None, D(50)),
        ],
    )
    assert lines[0].shares is None and lines[0].value_usd == D(150)


def test_an_unknown_frequency_is_unavailable_not_a_key_error() -> None:
    returns = [D("0.01")] * 40
    got = risk.volatility(returns, "hourly")
    assert got.value is None and "unknown frequency" in " ".join(got.warnings)


def test_all_gains_gives_a_negative_var_with_a_note() -> None:
    var, _ = risk.var_es([D("0.01")] * 300)
    assert var.value is not None and var.value < 0
    assert any("no losing period" in w for w in var.warnings)


def test_a_negative_weight_is_not_blended() -> None:
    series = [[D("0.01"), D("-0.02")] * 40, [D("0.02"), D("0.01")] * 40]
    got = risk.diversification_benefit([D("1.5"), D("-0.5")], series)
    assert got.value is None
