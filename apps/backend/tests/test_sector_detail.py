from steward.sectors.map import (
    Detail,
    Profile,
    build_map,
    geography_mix,
    patent_areas,
    top_products,
)

PROFILES = [
    Profile("AAA_US_EQ", "Tech", "Chips", "US"),
    Profile("BBB_US_EQ", "Tech", "Chips", "US"),
]
DETAIL = Detail(
    products={
        "AAA_US_EQ": [("Compute", 0.75), ("Gaming", 0.07)],
        "BBB_US_EQ": [("Phones", 0.5)],
    },
    geography={
        "AAA_US_EQ": {"US": 0.4, "CN": 0.2},
        "BBB_US_EQ": {"US": 0.2},
    },
    patents={"AAA_US_EQ": {"H01L": 3}, "BBB_US_EQ": {"H01L": 2, "G06F": 1}},
)


def test_top_products_are_ranked_by_share_across_the_group() -> None:
    lines = top_products(["AAA_US_EQ", "BBB_US_EQ"], DETAIL.products)
    assert [(p.ticker, p.label) for p in lines[:2]] == [
        ("AAA_US_EQ", "Compute"),
        ("BBB_US_EQ", "Phones"),
    ]


def test_geography_averages_over_the_companies_that_report_it() -> None:
    mix = {g.geography: g for g in geography_mix(["AAA_US_EQ", "BBB_US_EQ"], DETAIL.geography)}
    assert mix["US"].mean_share == 0.30000000000000004 or abs(mix["US"].mean_share - 0.3) < 1e-9
    assert mix["US"].n == 2 and mix["CN"].n == 1


def test_patent_areas_add_families_across_the_group() -> None:
    areas = patent_areas(["AAA_US_EQ", "BBB_US_EQ"], DETAIL.patents)
    assert areas[0].code == "H01L" and areas[0].families == 5


def test_build_map_carries_the_detail_into_each_group() -> None:
    sector = build_map(PROFILES, {}, {}, {}, {}, set(), DETAIL).sectors[0]
    assert sector.top_products[0].label == "Compute" and sector.patent_areas[0].families == 5


def test_map_without_detail_has_empty_lists() -> None:
    sector = build_map(PROFILES, {}, {}, {}, {}, set()).sectors[0]
    assert sector.top_products == [] and sector.geography == [] and sector.patent_areas == []
