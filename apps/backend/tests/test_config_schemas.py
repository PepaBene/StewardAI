import pytest
from pydantic import ValidationError

from steward.config import StrategyConfig, ThesesConfig

SPEC_EXAMPLE_RULE = {
    # Verbatim from the product description's config example.
    "id": "max-single-position",
    "type": "position_weight",
    "operator": "<=",
    "value": 0.10,
    "on_breach": "severity_2",
}


def _strategy(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "base_currency": "CZK",
        "classifications": {"ASML_EQ": {"sector": "Semiconductors", "geography": "EU"}},
        "cash_target": {"target_weight": 0.05},
        "target_positions": [
            {"ticker": "ASML_EQ", "target_weight": 0.08, "band": 0.02, "hard_limit": 0.12},
            {"ticker": "AAPL_US_EQ", "target_weight": 0.07},
        ],
        "target_categories": [
            {"dimension": "geography", "bucket": "EU", "target_weight": 0.4, "band": 0.05},
            {"dimension": "geography", "bucket": "US", "target_weight": 0.5},
        ],
        "rules": [SPEC_EXAMPLE_RULE],
    }
    body.update(overrides)
    return body


def test_valid_strategy_with_spec_example_rule() -> None:
    config = StrategyConfig.model_validate(_strategy())
    assert config.rules[0].id == "max-single-position"
    assert config.target_positions[1].band == 0.02  # default band


@pytest.mark.parametrize(("base", "expected"), [("CZK", "cnb"), ("EUR", "ecb"), ("USD", "ecb")])
def test_fx_source_defaults_from_base_currency(base: str, expected: str) -> None:
    assert StrategyConfig.model_validate(_strategy(base_currency=base)).fx_source == expected


def test_explicit_fx_source_is_kept() -> None:
    config = StrategyConfig.model_validate(_strategy(base_currency="CZK", fx_source="ecb"))
    assert config.fx_source == "ecb"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"base_currency": "czk"}, "base_currency"),
        ({"unexpected_key": 1}, "Extra inputs are not permitted"),
        (
            {
                "target_positions": [
                    {"ticker": "A_EQ", "target_weight": 0.6},
                    {"ticker": "B_EQ", "target_weight": 0.5},
                ]
            },
            "sum to 1.1500",
        ),
        (
            {
                "target_positions": [
                    {"ticker": "A_EQ", "target_weight": 0.1},
                    {"ticker": "A_EQ", "target_weight": 0.1},
                ]
            },
            "duplicate target_positions ticker",
        ),
        (
            {"target_positions": [{"ticker": "A_EQ", "target_weight": 0.1, "hard_limit": 0.05}]},
            "hard_limit must not be below target_weight",
        ),
        ({"classifications": {"A_EQ": {"style": "value"}}}, "undeclared dimensions"),
        (
            {
                "target_categories": [
                    {"dimension": "style", "bucket": "value", "target_weight": 0.1}
                ]
            },
            "undeclared dimension",
        ),
        (
            {
                "target_categories": [
                    {"dimension": "sector", "bucket": "Tech", "target_weight": 0.7},
                    {"dimension": "sector", "bucket": "Health", "target_weight": 0.4},
                ]
            },
            "dimension 'sector' sum to 1.1000",
        ),
        ({"rules": [SPEC_EXAMPLE_RULE, SPEC_EXAMPLE_RULE]}, "duplicate rules id"),
        ({"rules": [{**SPEC_EXAMPLE_RULE, "value": 1.5}]}, "less than or equal to 1"),
        (
            {"rules": [{**SPEC_EXAMPLE_RULE, "type": "moon_phase"}]},
            "does not match any of the expected tags",
        ),
        ({"rules": [{**SPEC_EXAMPLE_RULE, "id": "Max Single"}]}, "should match pattern"),
        (
            {
                "rules": [
                    {
                        "id": "sector-cap",
                        "type": "category_weight",
                        "operator": "<=",
                        "value": 0.3,
                        "dimension": "style",
                    }
                ]
            },
            "undeclared dimension",
        ),
        ({"cash_target": {"target_weight": 0.9}}, "sum to 1.0500"),
    ],
)
def test_invalid_strategy_is_rejected(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        StrategyConfig.model_validate(_strategy(**overrides))


def _theses(**thesis_overrides: object) -> dict[str, object]:
    thesis: dict[str, object] = {
        "ticker": "ASML_EQ",
        "why_owned": "Monopoly on EUV lithography.",
        "kpis": [
            {
                "id": "revenue-growth",
                "metric": "revenue_growth_yoy",
                "operator": ">=",
                "threshold": 10,
                "unit": "%",
            },
            {
                "id": "gross-margin",
                "metric": "gross_margin",
                "operator": ">=",
                "threshold": 50,
                "unit": "%",
                "basis": "reported",
                "period": "annual",
            },
        ],
        "exit_conditions": ["A competitor ships EUV at volume"],
        "review_date": "2027-03-31",
    }
    thesis.update(thesis_overrides)
    return {"theses": [thesis]}


def test_valid_theses() -> None:
    config = ThesesConfig.model_validate(_theses())
    assert config.theses[0].kpis[0].period == "quarter"  # default


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (_theses(why_owned=""), "at least 1 character"),
        (
            _theses(
                kpis=[{"id": "x", "metric": "m", "operator": "~", "threshold": 1, "unit": "%"}]
            ),
            "operator",
        ),
        (
            _theses(
                kpis=[{"id": "x", "metric": "m", "operator": ">", "threshold": 1, "unit": "%"}] * 2
            ),
            "duplicate kpi id",
        ),
        ({"theses": _theses()["theses"] * 2}, "duplicate thesis ticker"),  # type: ignore[operator]
        (_theses(review_date="next spring"), "review_date"),
    ],
)
def test_invalid_theses_are_rejected(body: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        ThesesConfig.model_validate(body)
