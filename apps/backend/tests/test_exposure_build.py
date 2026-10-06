from decimal import Decimal

from steward.exposures.build import GeoShare, country_iso, plan_geography

D = Decimal


def test_only_single_countries_are_mapped() -> None:
    assert country_iso("UNITED STATES") == "US"
    assert country_iso("  Taiwan ") == "TW"
    assert country_iso("China (including Hong Kong)") is None
    assert country_iso("US & Canada") is None
    assert country_iso("Europe") is None


def test_plan_weights_are_shares_of_total_and_regions_stay_unmapped() -> None:
    plan = plan_geography(
        [("United States", D(60)), ("Japan", D(10)), ("Europe", D(20)), ("Other", D(10))], D(100)
    )
    assert isinstance(plan, list)
    assert [(s.geography, s.weight) for s in plan] == [("US", D("0.6")), ("JP", D("0.1"))]
    assert isinstance(plan[0], GeoShare)


def test_two_labels_for_one_country_must_agree() -> None:
    agree = plan_geography([("United States", D(60)), ("US", D(60))], D(100))
    assert isinstance(agree, list) and len(agree) == 1
    clash = plan_geography([("United States", D(60)), ("US", D(30)), ("Japan", D(10))], D(100))
    assert isinstance(clash, list) and [s.geography for s in clash] == ["JP"]


def test_refuses_when_nothing_or_too_much_is_mapped() -> None:
    assert plan_geography([("Europe", D(50))], D(100)) == "no single-country revenue labels"
    assert plan_geography([("Japan", D(150))], D(100)) == "country revenue exceeds total revenue"
    assert plan_geography([("Japan", D(1))], D(0)) == "no positive total revenue"
