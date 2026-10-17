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


def test_kpis_set_at_or_past_the_latest_level_are_tight() -> None:
    from decimal import Decimal

    from steward.config.thesis import ThesisConfig, ThesisKPI
    from steward.earnings.verdicts import FactRow

    facts = [  # FY2025 Q3 revenue growth: 94.036 / 85.777 - 1 = 9.628%
        FactRow(1, "revenue", "reported", 2025, "Q3", Decimal("94036000000"), "currency", "USD"),
        FactRow(2, "revenue", "reported", 2024, "Q3", Decimal("85777000000"), "currency", "USD"),
    ]

    def kpi(i: str, op: str, threshold: float) -> ThesisKPI:
        return ThesisKPI(
            id=i, metric="revenue_growth_yoy", operator=op, threshold=threshold, unit="%"
        )

    thesis = ThesisConfig(
        ticker="AAPL_US_EQ",
        why_owned="x",
        kpis=[
            kpi("at-level", ">=", 9.6),
            kpi("failed", ">=", 12),
            kpi("room", ">=", 6),
            kpi("cap", "<=", 9.7),
        ],
    )
    tight = author.tight_kpis(thesis, facts)
    assert [t.split(" ")[0] for t in tight] == ["at-level", "failed", "cap"]
    assert "FY2025 Q3" in tight[0]


def _revenue_facts() -> list:
    from decimal import Decimal

    from steward.earnings.verdicts import FactRow

    return [  # FY2025 Q3 revenue 94.036 bn, growth 9.628% on FY2024 Q3
        FactRow(1, "revenue", "reported", 2025, "Q3", Decimal("94036000000"), "currency", "USD"),
        FactRow(2, "revenue", "reported", 2024, "Q3", Decimal("85777000000"), "currency", "USD"),
    ]


def test_kpis_no_plausible_quarter_fails_are_loose() -> None:
    from steward.config.thesis import ThesisConfig, ThesisKPI

    def kpi(i: str, metric: str, threshold: float, unit: str) -> ThesisKPI:
        return ThesisKPI(id=i, metric=metric, operator=">=", threshold=threshold, unit=unit)

    thesis = ThesisConfig(
        ticker="AAPL_US_EQ",
        why_owned="x",
        kpis=[
            kpi("far", "revenue_growth_yoy", -15, "%"),
            kpi("fair", "revenue_growth_yoy", 5, "%"),
            kpi("floor", "revenue", 30, "USD bn"),
        ],
    )
    loose = author.loose_kpis(thesis, _revenue_facts())
    assert [t.split(" ")[0] for t in loose] == ["far", "floor"]


def test_scenarios_that_restate_the_price_or_contradict_the_stance_are_named() -> None:
    thesis = author.build_thesis(draft(), "GOOGL_US_EQ", "USD", TODAY, today_price=180.0)
    # Expected value 0.25 * 120 + 0.5 * 200 + 0.25 * 260 = 195.
    assert author.scenario_issues(thesis, "keep", 160.0, 0.3) == []
    near = author.scenario_issues(thesis, "keep", 195.0, 0.3)
    assert len(near) == 1 and "within 3%" in near[0]
    assert author.scenario_issues(thesis, "keep", 195.0, 0.3, method="fairly valued at 22x") == []
    loss = author.scenario_issues(thesis, "keep", 260.0, 0.3)
    assert any("a keep whose own scenarios expect -25.0%" in x for x in loss)
    add_loss = author.scenario_issues(thesis, "add", 260.0, 0.3)
    assert any("add whose own scenarios expect -25.0%" in x for x in add_loss)
    add_thin = author.scenario_issues(thesis, "add", 170.0, 0.3)
    assert any("under the owner's 30%" in x for x in add_thin)


def test_strategy_text_carries_rules_cash_and_limits() -> None:
    from steward.config.strategy import StrategyConfig

    strategy = StrategyConfig.model_validate(
        {
            "base_currency": "CZK",
            "cash_target": {"target_weight": 0.05, "band": 0.05},
            "rules": [
                {
                    "id": "max-cash",
                    "type": "cash_weight",
                    "operator": "<=",
                    "value": 0.2,
                    "description": "Cash at most 20%.",
                }
            ],
            "risk": {"max_drawdown": 0.2},
        }
    )
    text = author.strategy_text(strategy, "X")
    assert "Cash target 5% (band 5%)" in text
    assert "Rule max-cash: Cash at most 20%. <= 0.2" in text
    assert "max drawdown 20%" in text
    assert "Return hurdle 30% a year" in text


def test_prompt_puts_the_report_before_the_app_records() -> None:
    prompt = author.author_prompt(
        ticker="X",
        name="X",
        currency="USD",
        price=10.0,
        weight=0.05,
        report_text="R",
        valuation=[],
        strategy_text="S",
        latest=["revenue: 1"],
        app_context="A",
    )
    order = [prompt.index(t) for t in ("<latest_verified_figures>", "<report>", "<app_records>")]
    assert order == sorted(order)


def test_context_keeps_facts_and_the_newest_figure_of_each_metric() -> None:
    from steward.thesis.context import compact, latest_figures, tool_block

    raw = {"a": 1, "source_id": "S1", "b": None, "c": [{"sources": [1], "d": 2}]}
    assert compact(raw) == {"a": 1, "c": [{"d": 2}]}
    assert tool_block("holding", {"error": "no owner"}) == "### holding\nnot available: no owner"
    assert latest_figures(_revenue_facts()) == ["revenue (reported): 94036000000 USD in FY2025 Q3"]


def test_context_leaves_out_a_default_dcf_but_keeps_the_owners() -> None:
    from steward.thesis.context import tool_block

    default = {
        "assumptions_basis": "default_placeholder",
        "range_low": {"value": "41.41"},
        "scenarios": [{"name": "base", "value_per_share": {"value": "57.44"}}],
        "filed_inputs": [{"label": "Free cash flow", "value": "13.9e9"}],
    }
    block = tool_block("valuation_scenarios", default)
    assert "57.44" not in block and "41.41" not in block
    assert "placeholder" in block and "Free cash flow" in block
    saved = {**default, "assumptions_basis": "owner_saved"}
    assert "57.44" in tool_block("valuation_scenarios", saved)
