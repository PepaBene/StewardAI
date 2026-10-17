import datetime as dt

import pytest

from steward.thesis import author

TODAY = dt.date(2026, 10, 13)


def draft(**over: object) -> author.ThesisDraft:
    base: dict[str, object] = {
        "stance": "keep",
        "stance_reason": "Cloud growth carries the case.",
        "executive_summary": "Keep. Cloud growth carries it. [S1]",
        "memo": [
            {"title": f"Section {i}", "body": "Revenue grew 14% [S1, S99]."} for i in range(6)
        ],
        "why_owned": "A durable search and cloud franchise at a fair price.",
        "main_reason": "quality",
        "conviction": 4,
        "kpis": [
            {
                "id": "revenue-growth",
                "metric": "revenue_growth_yoy",
                "operator": ">=",
                "threshold": 10,
                "unit": "%",
                "description": "Revenue still grows in double digits.",
            }
        ],
        "exit_conditions": ["Cloud growth falls below 15% for two quarters"],
        "pre_mortem": ["Search share loss", "Antitrust remedy", "AI capex without return"],
        "scenarios": {
            "bear": 120,
            "base": 200,
            "bull": 260,
            "p_bear": 0.25,
            "p_base": 0.5,
            "p_bull": 0.25,
        },
        "sell_target": 260,
        "buy_target": 150,
        "valuation_method": "20x 2027 EPS",
        "review_in_months": 6,
    }
    base.update(over)
    return author.ThesisDraft.model_validate(base)


def test_valid_draft_becomes_a_thesis() -> None:
    thesis = author.build_thesis(draft(), "GOOGL_US_EQ", "USD", TODAY, today_price=180.0)
    assert thesis.ticker == "GOOGL_US_EQ"
    assert thesis.review_date == TODAY + dt.timedelta(days=180)
    assert thesis.price_target is not None
    assert thesis.price_target.bear_case == 120
    assert thesis.conviction == 4 and len(thesis.pre_mortem) == 3


def test_unknown_metric_is_refused() -> None:
    bad = draft(
        kpis=[
            {
                "id": "vibes",
                "metric": "brand_love",
                "operator": ">",
                "threshold": 1,
                "unit": "x",
                "description": "x",
            }
        ]
    )
    with pytest.raises(author.AuthorError, match="brand_love"):
        author.build_thesis(bad, "T", "USD", TODAY, today_price=None)


def test_unordered_scenarios_are_refused() -> None:
    bad = draft(
        scenarios={
            "bear": 300,
            "base": 200,
            "bull": 260,
            "p_bear": 0.25,
            "p_base": 0.5,
            "p_bull": 0.25,
        }
    )
    with pytest.raises(author.AuthorError):
        author.build_thesis(bad, "T", "USD", TODAY, today_price=None)


def test_buy_target_above_sell_target_is_refused() -> None:
    with pytest.raises(author.AuthorError):
        author.build_thesis(
            draft(sell_target=100, buy_target=150), "T", "USD", TODAY, today_price=None
        )


def test_scenarios_in_the_wrong_unit_are_refused() -> None:
    # A price of 18000 (pence) against dollar scenarios of 120-260.
    with pytest.raises(author.AuthorError, match="currency or unit"):
        author.build_thesis(draft(), "T", "USD", TODAY, today_price=18000.0)


def test_buy_stance_does_not_fit_a_held_thesis_and_back() -> None:
    with pytest.raises(author.AuthorError, match="does not fit"):
        author.build_thesis(draft(stance="buy"), "T", "USD", TODAY, today_price=None)
    with pytest.raises(author.AuthorError, match="does not fit"):
        author.build_thesis(draft(), "T", "USD", TODAY, today_price=None, mode="buy")
    ok = author.build_thesis(draft(stance="buy"), "T", "USD", TODAY, today_price=None, mode="buy")
    assert ok.ticker == "T"


def test_memo_citations_unknown_to_the_report_are_removed() -> None:
    cleaned = author.clean_memo(draft(), {"S1"})
    assert cleaned[0]["body"] == "Revenue grew 14% [S1]."
    assert author.clean_memo(draft(), set())[0]["body"] == "Revenue grew 14% ."


def test_buy_system_prompt_differs_from_the_held_one() -> None:
    assert "does not hold" in author.system_for("buy")
    assert "does not hold" not in author.system_for("held")


def test_allowed_metrics_include_growth_and_margins() -> None:
    metrics = author.allowed_metrics()
    assert {"revenue", "gross_margin", "revenue_growth_yoy", "free_cash_flow"} <= set(metrics)


def test_exit_stance_is_flagged_for_the_owner() -> None:
    assert "exit" in author.STANCES_NEEDING_OWNER
    assert "keep" not in author.STANCES_NEEDING_OWNER


def test_report_text_drops_unsupported_sentences() -> None:
    body = {
        "sections": [
            {
                "id": "business",
                "title": "Business",
                "sentences": [
                    {"text": "Revenue grew 14%.", "source_ids": ["S1"], "check": "supported"},
                    {"text": "It will win AI.", "source_ids": ["S2"], "check": "unsupported"},
                ],
            }
        ],
        "valuation": [{"method": "pe_ttm", "display": "24x", "formula": "price / eps"}],
    }
    text, figures = author.report_text(body)
    assert "Revenue grew 14%. [S1]" in text
    assert "win AI" not in text
    assert figures == ["pe_ttm: 24x (price / eps)"]


def test_only_passing_provider_errors_are_waited_out() -> None:
    from steward.llm.types import LLMError
    from steward.thesis.job import transient

    assert transient(LLMError("gateway", status=520))
    assert transient(LLMError("rate limited", status=429))
    assert transient(LLMError("connection reset"))
    assert not transient(LLMError("bad request", status=400))
    assert not transient(LLMError("no key", status=401))
