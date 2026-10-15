from types import SimpleNamespace

from steward.config.strategy import PretradeThresholds
from steward.research.plan import ResearchPlan
from steward.research.read_tools import full_data_calls, plan_calls
from steward.thesis.review import category_gaps, holding_flags, judge_buy, summarise


def flags(**over: object) -> dict | None:
    base: dict[str, object] = {
        "hard_limit": None,
        "target": None,
        "band": None,
        "sell_target": None,
        "breached": [],
        "stance": None,
        "has_thesis": True,
    }
    base.update(over)
    return holding_flags("T", 0.08, 100.0, **base)  # type: ignore[arg-type]


def test_quiet_holding_is_not_flagged() -> None:
    assert flags() is None


def test_hard_limit_target_and_sell_target_flag_a_trim() -> None:
    assert flags(hard_limit=0.05)["action"] == "trim"  # type: ignore[index]
    assert flags(target=0.04, band=0.02)["action"] == "trim"  # type: ignore[index]
    out = flags(sell_target=90.0)
    assert out is not None and "sell target" in out["reasons"][0]


def test_exit_stance_wins_over_trim() -> None:
    out = flags(stance="exit", hard_limit=0.05)
    assert out is not None and out["action"] == "exit"


def test_breached_kpi_is_a_review_and_missing_thesis_is_noted() -> None:
    assert flags(breached=["growth"])["action"] == "review"  # type: ignore[index]
    assert flags(has_thesis=False)["action"] == "no thesis"  # type: ignore[index]


def test_category_gaps_list_buckets_below_the_band() -> None:
    strategy = SimpleNamespace(
        classifications={"A": {"sector": "Tech"}, "B": {"sector": "Health"}},
        target_categories=[
            SimpleNamespace(dimension="sector", bucket="Tech", target_weight=0.3, band=0.05),
            SimpleNamespace(dimension="sector", bucket="Health", target_weight=0.2, band=0.05),
            SimpleNamespace(dimension="sector", bucket="Energy", target_weight=0.1, band=0.05),
        ],
    )
    gaps = category_gaps({"A": 0.2, "B": 0.19}, strategy)  # type: ignore[arg-type]
    assert [g["bucket"] for g in gaps] == ["Tech", "Energy"]


def memo(stance: str, price: float, sc: dict) -> dict:
    return {
        "id": 1,
        "ticker": "X",
        "stance": stance,
        "executive_summary": "s",
        "body": {"current_price": price, "thesis": {"price_target": {"scenarios": sc}}},
    }


SC = {"bear": 60, "base": 150, "bull": 200, "p_bear": 0.2, "p_base": 0.5, "p_bull": 0.3}


def strategy(hurdle: float = 0.3, gate: float = 2.0) -> SimpleNamespace:
    return SimpleNamespace(pretrade=PretradeThresholds(return_hurdle=hurdle, asymmetry_gate=gate))


def test_buy_needs_the_hurdle_and_the_gate() -> None:
    # EV = 12 + 75 + 60 = 147 against 100: +47%; upside 100 against a loss of 40: 2.5x.
    assert judge_buy(memo("buy", 100, SC), strategy())["verdict"] == "buy"  # type: ignore[arg-type]
    low = judge_buy(memo("buy", 100, SC), strategy(hurdle=0.6))  # type: ignore[arg-type]
    assert low["verdict"] == "pass" and "hurdle" in low["reason"]
    gate = judge_buy(memo("buy", 100, SC), strategy(gate=3.0))  # type: ignore[arg-type]
    assert gate["verdict"] == "pass" and "gate" in gate["reason"]


def test_pass_stance_is_a_pass_whatever_the_numbers() -> None:
    out = judge_buy(memo("pass", 100, SC), strategy())  # type: ignore[arg-type]
    assert out["verdict"] == "pass" and "itself" in out["reason"]


def test_summary_lists_actions_buys_and_passes() -> None:
    text = summarise(
        {
            "holdings": [{"ticker": "A", "weight": 0.1, "action": "trim", "reasons": ["too big"]}],
            "buys": [{"ticker": "B", "reason": "clears", "expected_return": 0.4}],
            "passes": [{"ticker": "C", "reason": "weak"}],
        }
    )
    assert "Trim A" in text and "Buy B" in text and "Pass on C" in text


def test_owner_tools_are_called_only_for_an_owner_run_and_extras_are_added() -> None:
    plan = ResearchPlan.model_construct(
        questions=[
            SimpleNamespace(section="risk", internal_tools=["stress_test", "holding"]),
        ]
    )
    quiet = {c.tool: c.skip for c in plan_calls(plan, "T")}
    assert quiet["stress_test"] is not None and quiet["holding"] is None
    owner = {c.tool: c.skip for c in plan_calls(plan, "T", owner=True)}
    assert owner["stress_test"] is None
    extra = plan_calls(plan, "T", owner=True, extra=full_data_calls("T", "Tee Inc"))
    assert {"search_documents", "transactions", "agent_notes"} <= {c.tool for c in extra}
