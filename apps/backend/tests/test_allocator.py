"""The deposit allocator (strategy principle 8c; plan Q5.4), on a small synthetic book."""

import pytest

from steward.config.strategy import (
    CategoryTarget,
    PositionWeightRule,
    StrategyConfig,
    TargetBand,
)
from steward.decision import allocator as al

CAP = 0.10


def _strategy(
    core_target: float | None = 0.5, growth_target: float | None = None
) -> StrategyConfig:
    targets = []
    if core_target is not None:
        targets.append(
            CategoryTarget(dimension="risk", bucket="core", target_weight=core_target, band=0.05)
        )
    if growth_target is not None:
        targets.append(
            CategoryTarget(
                dimension="risk", bucket="growth", target_weight=growth_target, band=0.05
            )
        )
    return StrategyConfig(
        base_currency="CZK",
        categories=["risk"],
        classifications={
            "ETF": {"risk": "core"},
            "A": {"risk": "growth"},
            "B": {"risk": "growth"},
            "C": {"risk": "growth"},
        },
        target_categories=targets,
        # The core ETF and the large "OTHER" holding are exempt, as the strategy exempts core ETFs.
        rules=[
            PositionWeightRule(
                id=f"max-{t.lower()}", type="position_weight", operator="<=", value=CAP, ticker=t
            )
            for t in ("A", "B", "C")
        ],
    )


def _cand(
    ticker: str, price: float, ret: float | None, etf: bool = False, held: float = 0.0
) -> al.Candidate:
    return al.Candidate(ticker, ticker, "CZK", price, etf, held, ret)


def _run(
    strategy: StrategyConfig,
    held: dict[str, float],
    cands: list[al.Candidate],
    amount: float = 100.0,
    cash: float = 100.0,
    exante: al.ExAnte | None = None,
) -> al.AllocationPlan:
    tiers = {t: b["risk"] for t, b in strategy.classifications.items()}
    return al.allocate(
        amount, "CZK", cash, held, {t: "CZK" for t in held}, cands, strategy, tiers, 0.30, exante
    )


# Total before the deposit: 300 + 90 + 120 + 390 (other) + 100 cash = 1000.
HELD = {"ETF": 300.0, "A": 90.0, "B": 120.0, "OTHER": 390.0}


def test_a_deposit_keeps_back_what_cash_needs_to_reach_the_lower_edge_of_its_band() -> None:
    strategy = _strategy(core_target=0.6)
    strategy.cash_target = TargetBand(target_weight=0.10, band=0.02)
    # 900 invested + 20 cash + 100 deposit = 1020; the 8 % floor is 81.6, so 61.6 stays back.
    assert al.cash_reserve(100.0, 20.0, {"ETF": 900.0}, strategy) == pytest.approx(61.6)
    assert al.cash_reserve(100.0, 200.0, {"ETF": 900.0}, strategy) == 0.0
    strategy.cash_target = None
    assert al.cash_reserve(100.0, 20.0, {"ETF": 900.0}, strategy) == 0.0

    strategy.cash_target = TargetBand(target_weight=0.10, band=0.02)
    plan = _run(strategy, {"ETF": 900.0}, [_cand("ETF", 10.0, None, etf=True)], cash=20.0)
    assert plan.spent <= 100.0 - 61.6 + 1e-9
    assert plan.left_over >= 61.6 - 1e-9
    assert any("stays in cash" in n for n in plan.notes)


def test_the_core_gets_the_deposit_first_while_it_is_below_its_target_and_in_whole_shares() -> None:
    plan = _run(
        _strategy(core_target=0.6, growth_target=0.3),
        HELD,
        [_cand("ETF", 10.0, None, etf=True, held=300.0), _cand("A", 30.0, 0.8, held=90.0)],
    )
    assert plan.lines[0].ticker == "ETF" and plan.lines[0].stage == "core_below_target"
    assert plan.lines[0].shares * 10.0 == pytest.approx(plan.lines[0].value)
    assert plan.spent <= 100.0 + 1e-9 and plan.left_over == pytest.approx(100.0 - plan.spent)
    assert "core" in plan.lines[0].principle


def test_nothing_is_bought_into_a_position_already_above_its_cap() -> None:
    # The core target is met, a growth tier is short; B has the best return but sits at 12 %.
    plan = _run(
        _strategy(core_target=0.3, growth_target=0.4),
        HELD,
        [_cand("B", 10.0, 0.9, held=120.0), _cand("A", 5.0, 0.4, held=90.0)],
    )
    bought = {line.ticker for line in plan.lines}
    assert "B" not in bought
    assert any(s.ticker == "B" for s in plan.set_aside)
    a = next(line for line in plan.lines if line.ticker == "A")
    assert a.weight_after <= CAP + 1e-9  # the purchase was shrunk to fit under the cap


def test_a_stock_without_a_thesis_or_under_the_hurdle_is_set_aside_with_the_reason() -> None:
    plan = _run(
        _strategy(core_target=0.3, growth_target=0.4),
        HELD,
        [_cand("A", 5.0, None, held=90.0), _cand("C", 5.0, 0.10)],
    )
    reasons = {s.ticker: s.reason for s in plan.set_aside}
    assert "thesis" in reasons["A"] and "8a" in reasons["A"]
    assert "hurdle" in reasons["C"]
    assert plan.spent == 0 and plan.notes


def test_an_ex_ante_limit_halves_the_purchase_until_it_fits() -> None:
    def exante(values: dict[str, float]) -> al.Check:
        bad = ["volatility_forecast"] if values.get("A", 0.0) > 100.0 else []
        return al.Check(bad, 0.15 + values.get("A", 0.0) * 1e-4, 5.0)

    plan = _run(
        _strategy(core_target=0.3, growth_target=0.4),
        {**HELD, "B": 100.0},
        [_cand("A", 1.0, 0.5, held=90.0)],
        exante=exante,
    )
    a = next(line for line in plan.lines if line.ticker == "A")
    assert 90.0 + a.value <= 100.0 + 1e-9
    assert plan.volatility_after is not None and plan.volatility_after > plan.volatility_before


def test_with_no_gap_and_no_idea_ready_the_deposit_goes_to_the_core_etf() -> None:
    plan = _run(
        _strategy(core_target=0.27, growth_target=None),  # 300 of 1100 is already at target
        HELD,
        [_cand("ETF", 10.0, None, etf=True, held=300.0), _cand("C", 5.0, 0.05)],
    )
    assert [line.stage for line in plan.lines] == ["core_fallback"]
    assert plan.lines[0].ticker == "ETF"
