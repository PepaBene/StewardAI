"""iShares securities lending and benchmark series readers: no network or database."""

import datetime as dt
from decimal import Decimal

from steward.fundresearch import issuer_sync as sync

TODAY = dt.date(2026, 10, 7)
NASDAQ = sync.IssuerSource("IE00B53SZB19", "ishares", "253741")
SENTENCE = "62.5% of all securities lending income is paid directly into the fund, with BlackRock"


def dp(value, as_of=None):
    return {"formattedValue": value, "value": value, "formattedAsOfDate": as_of}


def lending(as_of="06/Aug/2026", one_year=("0.00", "1.58", "2.63", "110.44"), text=SENTENCE):
    return {
        "componentLabels": {"info": text},
        "componentsByNameMap": {
            "secLending": {
                "containersByNameMap": {
                    "download": {"dataPointsByNameMap": {"secLendingAsOfDate": dp(as_of)}},
                    "summary": {
                        "dataPointsByNameMap": {
                            "returnTypes": dp(
                                [
                                    "netSecLendingPerf",
                                    "avgOnLoan",
                                    "maxOnLoan",
                                    "avgCollateralisation",
                                ]
                            ),
                            "oneYear": dp(list(one_year)),
                        }
                    },
                }
            }
        },
    }


def performance(
    dates=(20100126, 20100127, 20100128),
    levels=("10000.0", "10289.4", "10022.3"),
    name="NASDAQ 100 Index",
):
    return {
        "componentsByNameMap": {
            "performance": {
                "containersByNameMap": {
                    "chart": {
                        "dataPointsByNameMap": {
                            "benchmarkData": {"value": list(levels), "asOfDate": list(dates)},
                            "benchmarkName": dp(name),
                            "benchmarkCurrencySymbol": dp("USD"),
                        }
                    }
                }
            }
        }
    }


def test_current_lending_statistics_give_yes_and_the_stated_income_share():
    r = sync.parse_ishares_lending(lending(), TODAY)
    assert r.facts["securities_lending"].value == "yes"
    assert r.facts["lending_revenue_share"].value == "0.625"
    assert r.skipped == {}


def test_stale_or_zero_lending_statistics_are_unknown_not_no():
    old = sync.parse_ishares_lending(lending(as_of="10/Oct/2023"), TODAY)
    assert (
        "securities_lending" not in old.facts and "not current" in old.skipped["securities_lending"]
    )
    zero = sync.parse_ishares_lending(lending(one_year=("0.00", "0.00", "0.00", "")), TODAY)
    assert "securities_lending" not in zero.facts
    nosentence = sync.parse_ishares_lending(lending(text="nothing"), TODAY)
    assert "lending_revenue_share" in nosentence.skipped


def test_benchmark_series_is_read_with_its_dates_and_basis_only_when_named():
    s = sync.parse_ishares_benchmark(performance(), "253741")
    assert s is not None and s.symbol == "ishares-bm-253741" and s.currency == "USD"
    assert s.points[0] == (dt.date(2010, 1, 26), Decimal("10000.0")) and s.basis is None
    total = sync.parse_ishares_benchmark(
        performance(name="Bloomberg Commodity Index Total Return (Official)"), "287254"
    )
    assert total is not None and total.basis == "total"


def test_a_malformed_benchmark_series_is_refused_whole():
    assert sync.parse_ishares_benchmark(performance(levels=("1", "x", "3")), "1") is None
    assert (
        sync.parse_ishares_benchmark(performance(dates=(20100127, 20100126, 20100128)), "1") is None
    )
    assert sync.parse_ishares_benchmark(performance(levels=("1", "2")), "1") is None


def test_extras_fold_into_the_reading_and_name_what_is_left_out():
    reading = sync.IssuerReading(isin=NASDAQ.isin, source_url=NASDAQ.url, as_of=TODAY)
    sync.add_ishares_extras(reading, NASDAQ, lending(), performance(), TODAY)
    assert reading.benchmark is not None
    assert reading.facts["benchmark_symbol"].value == "ishares-bm-253741"
    assert reading.facts["benchmark_currency"].value == "USD"
    assert "benchmark_basis" not in reading.facts and "benchmark_basis" in reading.skipped
    broken = sync.IssuerReading(isin=NASDAQ.isin, source_url=NASDAQ.url, as_of=TODAY)
    sync.add_ishares_extras(broken, NASDAQ, sync.IssuerReadError("down"), {"x": 1}, TODAY)
    assert "securities_lending" in broken.skipped and "benchmark" in broken.skipped
    assert broken.benchmark is None
