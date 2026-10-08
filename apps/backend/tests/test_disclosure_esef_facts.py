"""P2.5: values read out of an ESEF (xBRL-JSON) report. Offline: the fixture is a trimmed,
hand-made report shaped like a filings.xbrl.org one (facts keyed by id, a `dimensions` object with
concept / entity / period / unit and one key per extra axis, midnight-exclusive period ends)."""

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest

from steward.disclosure.esef_facts import (
    CONCEPTS,
    REASONS,
    EsefParseError,
    parse_esef_facts,
)

FIXTURE = Path(__file__).parent / "fixtures" / "disclosure" / "esef_sample.json"
ENTITY = "scheme:ACMELEI0000000000001"


def report(facts: dict[str, object]) -> bytes:
    return json.dumps({"documentInfo": {}, "facts": facts}).encode()


def fact(
    concept: str, value: object, period: str, unit: str | None = "iso4217:EUR", **extra: object
):
    dims: dict[str, object] = {"concept": concept, "entity": ENTITY, "period": period}
    if unit is not None:
        dims["unit"] = unit
    dims.update(extra)
    return {"value": value, "decimals": -6, "dimensions": dims}


YEAR = "2025-01-01T00:00:00/2026-01-01T00:00:00"
CLOSE = "2026-01-01T00:00:00"


@pytest.fixture(scope="module")
def parsed():
    return parse_esef_facts(FIXTURE.read_bytes())


def by_metric(parsed, metric: str):
    return [f for f in parsed.facts if f.metric == metric]


def test_consolidated_values_carry_period_unit_and_scale(parsed) -> None:
    revenue = {f.period_end: f for f in by_metric(parsed, "revenue") if f.consolidated}
    this_year = revenue[dt.date(2025, 12, 31)]
    assert this_year.value == Decimal("37417000000")
    assert (this_year.period_start, this_year.is_instant) == (dt.date(2025, 1, 1), False)
    assert (this_year.unit, this_year.currency) == ("EUR", "EUR")
    assert (this_year.decimals, this_year.scale) == (-6, 6)
    assert this_year.entity == ENTITY and this_year.concept == "ifrs-full:Revenue"
    assert revenue[dt.date(2024, 12, 31)].value == Decimal("26617000000")


def test_an_instant_at_midnight_is_the_close_of_the_previous_day(parsed) -> None:
    assets = by_metric(parsed, "total_assets")
    assert len(assets) == 1
    assert assets[0].is_instant and assets[0].period_start is None
    assert assets[0].period_end == dt.date(2025, 12, 31)


def test_a_dimensioned_fact_is_kept_apart_from_the_consolidated_value(parsed) -> None:
    revenue = [f for f in by_metric(parsed, "revenue") if f.period_end == dt.date(2025, 12, 31)]
    assert sorted(f.consolidated for f in revenue) == [False, True]
    segment = next(f for f in revenue if not f.consolidated)
    assert segment.dimensions == {"ifrs-full:SegmentsAxis": "acme:EuropeMember"}
    assert segment.value == Decimal("20100000000")
    equity = by_metric(parsed, "total_equity")
    assert {f.value for f in equity if f.consolidated} == {Decimal("39287000000")}
    assert {f.value for f in equity if not f.consolidated} == {Decimal("35126000000")}


def test_identical_repeats_collapse_and_keep_the_most_precise(parsed) -> None:
    cash = by_metric(parsed, "cash")
    assert len(cash) == 1 and cash[0].decimals == -3 and cash[0].scale == 3
    assert parsed.duplicates_collapsed == 1


def test_duplicates_with_differing_values_are_all_skipped_not_picked(parsed) -> None:
    assert by_metric(parsed, "total_liabilities") == []
    conflicts = [s for s in parsed.skipped if s.reason == "conflicting_duplicates"]
    assert sorted(s.fact_id for s in conflicts if s.fact_id) == ["f10", "f11"]


def test_a_missing_concept_is_absent_not_zero(parsed) -> None:
    # nothing states current borrowings for the reporting entity, so there is no such fact
    assert [f.entity for f in by_metric(parsed, "borrowings_current")] == [
        "scheme:OTHERLEI000000000002"
    ]
    assert by_metric(parsed, "borrowings_noncurrent") == []  # stated, but unreadable


def test_unreadable_facts_are_skipped_with_a_named_reason(parsed) -> None:
    reasons = {s.fact_id: s.reason for s in parsed.skipped}
    assert reasons == {
        "f10": "conflicting_duplicates",
        "f11": "conflicting_duplicates",
        "f12": "unparseable_value",
        "f14": "nil_value",
        "f16": "period_kind_mismatch",
        "f17": "unit_mismatch",
        "f18": "no_unit",
        "f19": "bad_period",
        "f20": "bad_decimals",
        "f22": "malformed_fact",
    }
    assert set(reasons.values()) <= set(REASONS)
    assert parsed.skipped_counts()["conflicting_duplicates"] == 2
    assert all(s.detail for s in parsed.skipped)


def test_other_concepts_are_ignored_and_counted(parsed) -> None:
    assert parsed.ignored == 3  # a date, a name and inventories are not in the allow-list
    assert not {f.concept for f in parsed.facts} - set(CONCEPTS)


def test_shares_keep_their_unit_and_exact_decimals(parsed) -> None:
    (shares,) = by_metric(parsed, "shares_outstanding")
    assert (shares.unit, shares.currency, shares.decimals, shares.scale) == (
        "shares",
        None,
        None,
        0,
    )
    assert shares.value == Decimal("400000000")
    (borrowings,) = by_metric(parsed, "borrowings_current")
    assert borrowings.decimals is None and borrowings.value == Decimal("50")


def test_negative_and_zero_values_are_real_values() -> None:
    out = parse_esef_facts(
        report(
            {
                "a": fact("ifrs-full:ProfitLoss", "-108000000", YEAR),
                "b": fact("ifrs-full:Revenue", 0, YEAR),
                "c": fact("ifrs-full:Equity", "1.5e3", CLOSE),
            }
        )
    )
    values = {f.metric: f.value for f in out.facts}
    assert values == {
        "net_income": Decimal("-108000000"),
        "revenue": Decimal("0"),
        "total_equity": Decimal("1500"),
    }
    assert out.skipped == []


def test_values_that_are_not_numbers_are_skipped() -> None:
    out = parse_esef_facts(
        report(
            {
                "a": fact("ifrs-full:Revenue", "1,234", YEAR),
                "b": fact("ifrs-full:Revenue", True, "2024-01-01T00:00:00/2025-01-01T00:00:00"),
                "c": fact("ifrs-full:Equity", "NaN", CLOSE),
                "d": fact("ifrs-full:Assets", "", CLOSE),
            }
        )
    )
    assert out.facts == []
    assert [s.reason for s in out.skipped] == ["unparseable_value"] * 4


def test_dates_without_a_time_are_inclusive_and_odd_times_are_rejected() -> None:
    out = parse_esef_facts(
        report(
            {
                "a": fact("ifrs-full:Revenue", "5", "2025-01-01/2025-12-31"),
                "b": fact("ifrs-full:Assets", "6", "2025-12-31"),
                "c": fact("ifrs-full:Equity", "7", "2025-12-31T13:00:00"),
                "d": fact("ifrs-full:Cash", "8", CLOSE),
            }
        )
    )
    got = {f.metric: (f.period_start, f.period_end) for f in out.facts}
    assert got == {
        "revenue": (dt.date(2025, 1, 1), dt.date(2025, 12, 31)),
        "total_assets": (None, dt.date(2025, 12, 31)),
    }
    assert [(s.fact_id, s.reason) for s in out.skipped] == [("c", "bad_period")]
    assert out.ignored == 1  # ifrs-full:Cash is not a concept of the allow-list


def test_an_end_before_the_start_is_a_bad_period() -> None:
    out = parse_esef_facts(
        report({"a": fact("ifrs-full:Revenue", "1", "2026-01-01T00:00:00/2025-01-01T00:00:00")})
    )
    assert [s.reason for s in out.skipped] == ["bad_period"]


def test_a_currency_other_than_a_single_iso_unit_is_a_mismatch() -> None:
    out = parse_esef_facts(
        report(
            {
                "a": fact("ifrs-full:Revenue", "1", YEAR, unit="xbrli:shares"),
                "b": fact("ifrs-full:NumberOfSharesOutstanding", "1", CLOSE, unit="iso4217:EUR"),
            }
        )
    )
    assert [s.reason for s in out.skipped] == ["unit_mismatch", "unit_mismatch"]


def test_the_same_context_in_two_currencies_is_a_conflict() -> None:
    out = parse_esef_facts(
        report(
            {
                "a": fact("ifrs-full:Revenue", "10", YEAR, unit="iso4217:EUR"),
                "b": fact("ifrs-full:Revenue", "10", YEAR, unit="iso4217:USD"),
            }
        )
    )
    assert out.facts == []
    assert [s.reason for s in out.skipped] == ["conflicting_duplicates"] * 2


def test_the_same_concept_for_two_entities_is_not_a_duplicate() -> None:
    other = fact("ifrs-full:Revenue", "99", YEAR)
    other["dimensions"]["entity"] = "scheme:OTHERLEI000000000002"  # type: ignore[index]
    out = parse_esef_facts(report({"a": fact("ifrs-full:Revenue", "10", YEAR), "b": other}))
    assert sorted(f.value for f in out.facts) == [Decimal("10"), Decimal("99")]
    assert out.skipped == []


def test_language_and_xbrl_keys_are_not_axes() -> None:
    out = parse_esef_facts(
        report({"a": fact("ifrs-full:Revenue", "10", YEAR, language="en", **{"xbrl:noteId": "n1"})})
    )
    assert out.facts[0].dimensions == {}


@pytest.mark.parametrize("raw", [b"not json", b"[1, 2]", b'{"facts": []}', b"{}", b"\xff\xfe"])
def test_bytes_that_are_not_a_report_raise(raw: bytes) -> None:
    with pytest.raises(EsefParseError):
        parse_esef_facts(raw)


def test_an_empty_report_yields_nothing() -> None:
    out = parse_esef_facts(b'{"facts": {}}')
    assert (out.facts, out.skipped, out.ignored) == ([], [], 0)
