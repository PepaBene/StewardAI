"""Q8.3 to Q8.9: the ranking rows, the bench review lines, the risk findings and the switch log."""

import datetime as dt

import pytest

from steward.config.strategy import RiskLimits
from steward.config.thesis import ThesesConfig, ThesisConfig, ThesisPriceTarget
from steward.decision import ranking as rk
from steward.decision import ranking_service as svc
from steward.journal.scorecard import SwitchOutcome, build_scorecard, switch_section
from steward.portfolio.risk_limits import (
    RiskInputs,
    StockRiskShare,
    ThesisVolatilityFlag,
    evaluate_risk,
)
from steward.review.monthly import bench_lines

TODAY = dt.date(2026, 10, 10)


def _walk(days: int, daily: float, start: float = 100.0) -> list[tuple[dt.date, float]]:
    out, level, day = [], start, dt.date(2025, 1, 1)
    for i in range(days):
        level *= 1 + (daily if i % 2 == 0 else -daily)
        out.append((day, level))
        day += dt.timedelta(days=1)
    return out


def _thesis(ticker: str, **target: object) -> ThesisConfig:
    body: dict[str, object] = {
        "ticker": ticker,
        "why_owned": "test",
        "price_target": {"currency": "USD", "method": "DCF", "set_on": "2026-10-01", **target}
        if target
        else None,
    }
    return ThesesConfig.model_validate({"theses": [body]}).theses[0]


def _row(thesis: ThesisConfig, **kw: object) -> rk.RankingRow:
    args: dict[str, object] = {
        "name": None, "held": False, "weight": None, "price": 50.0, "price_date": TODAY,
        "currency": "USD", "series": _walk(300, 0.01), "index": _walk(300, 0.01),
        "marginal": None, "cash_rate": 0.035, "sharpe": 0.3, "hurdle": 0.30, "gate": 2.0,
        "today": TODAY,
    }  # fmt: skip
    args.update(kw)
    return svc._row(thesis, **args)  # type: ignore[arg-type]


SCENARIOS = {"scenarios": {"bear": 40, "base": 70, "bull": 90}}


# --------------------------------------------------------------------------------- the rows


def test_a_ranked_row_carries_returns_risk_and_the_score() -> None:
    row = _row(_thesis("X", **SCENARIOS))
    assert row.reasons == []
    assert row.returns is not None and row.returns.total == pytest.approx(0.35)
    assert row.risk is not None and row.risk.stage == "A" and row.risk.volatility_1y
    assert row.score == pytest.approx(
        (row.returns.annualized - 0.035) / row.risk.volatility_1y  # type: ignore[operator]
    )
    assert row.improves_portfolio is True  # score well above 0.3 x correlation 1.0
    history = row.bear_loss_vs_history or ""
    assert "milder" in history or "deep" in history


def test_a_thesis_without_scenarios_says_why_instead_of_showing_a_number() -> None:
    row = _row(_thesis("X", sell_target=80))
    assert row.score is None and row.returns is None
    assert any("scenarios" in r for r in row.reasons)


def test_missing_inputs_are_each_named() -> None:
    row = _row(_thesis("X", **SCENARIOS), series=None, price=None, cash_rate=None)
    text = " | ".join(row.reasons)
    assert "no stored price history" in text and "no current price" in text
    assert row.score is None


def test_a_missing_cash_rate_is_named_and_leaves_no_score() -> None:
    row = _row(_thesis("X", **SCENARIOS), cash_rate=None)
    assert row.score is None and any("cash rate" in r for r in row.reasons)


def test_a_price_in_another_currency_is_not_compared() -> None:
    row = _row(_thesis("X", **SCENARIOS), currency="EUR")
    assert row.returns is None and any("EUR" in r for r in row.reasons)


def test_stage_b_marginal_risk_replaces_the_approximation_and_is_labelled() -> None:
    row = _row(_thesis("X", **SCENARIOS), marginal=0.21)
    assert row.risk is not None
    assert (row.risk.marginal_risk, row.risk.marginal_stage) == (0.21, "B")


# ------------------------------------------------------------------------- earnings moves


def test_earnings_moves_run_from_the_close_before_to_the_close_after() -> None:
    series = [(dt.date(2026, 1, d), c) for d, c in [(5, 100.0), (6, 100.0), (7, 90.0), (8, 90.0)]]
    moves = rk.earnings_moves(series, [dt.date(2026, 1, 6)])
    assert moves is not None
    assert moves.count == 1 and moves.largest_abs == pytest.approx(0.10)
    assert rk.earnings_moves(series, [dt.date(2025, 1, 1)]) is None


# ------------------------------------------------------------------------------- the bench


def _ranking(rows: list[rk.RankingRow]) -> rk.RankingResponse:
    return rk.RankingResponse(
        method_version="1", as_of=TODAY, base_currency="CZK", cash_rate=0.035,
        cash_rate_label=None, portfolio_sharpe=0.3, return_hurdle=0.30, asymmetry_gate=2.0,
        rows=rows, bench_count=sum(not r.held for r in rows),
    )  # fmt: skip


def test_the_review_counts_the_bench_against_five_to_ten() -> None:
    ideas = [_row(_thesis(f"B{i}", **SCENARIOS)) for i in range(3)]
    lines = bench_lines(_ranking(ideas))
    assert lines[0] == "Bench: 3 ideas with a thesis, below the target of 5 to 10."
    assert any(
        "Best-ranked idea B0" in line and "above the return hurdle" in line for line in lines
    )
    held = _row(_thesis("H", **SCENARIOS), held=True)
    assert "within" in bench_lines(_ranking([held, *ideas * 2]))[0]


def test_unranked_bench_ideas_are_counted_but_not_ranked() -> None:
    lines = bench_lines(_ranking([_row(_thesis("B", sell_target=80))]))
    assert lines[0].startswith("Bench: 1 idea ")
    assert any("cannot be ranked" in line for line in lines)


# --------------------------------------------------------------------------- risk findings


def test_the_volatility_band_opens_a_thesis_finding_with_its_own_key() -> None:
    inputs = RiskInputs(thesis_volatility=[ThesisVolatilityFlag("X", 0.60, 0.30, 1.5)])
    findings = evaluate_risk(inputs, RiskLimits())
    (f,) = [f for f in findings if f.key.startswith("thesis:volatility:")]
    assert (f.key, f.kind, f.severity, f.ticker) == ("thesis:volatility:X", "rule", 2, "X")
    assert "Thesis risk profile changed" in f.detail
    assert f.limit == pytest.approx(0.45)


def test_no_flag_means_no_thesis_finding() -> None:
    assert not [f for f in evaluate_risk(RiskInputs(), RiskLimits()) if f.key.startswith("thesis:")]


def test_the_default_multiple_is_one_and_a_half_and_can_be_switched_off() -> None:
    assert RiskLimits().thesis_volatility_multiple == 1.5
    assert RiskLimits(thesis_volatility_multiple=None).thesis_volatility_multiple is None
    with pytest.raises(ValueError):
        RiskLimits(thesis_volatility_multiple=1.0)


def test_a_stock_above_the_risk_share_ceiling_is_a_review_finding() -> None:
    inputs = RiskInputs(stock_risk_shares=[StockRiskShare("A", 0.12), StockRiskShare("B", 0.05)])
    keys = {f.key for f in evaluate_risk(inputs, RiskLimits())}
    assert "risk:stock_share:A" in keys and "risk:stock_share:B" not in keys
    assert RiskLimits().max_stock_risk_share == 0.08
    off = {f.key for f in evaluate_risk(inputs, RiskLimits(max_stock_risk_share=None))}
    assert not any(k.startswith("risk:stock_share") for k in off)


# -------------------------------------------------------------------------- the switch log


def _outcome(i: int, sold: tuple[float, float], bought: tuple[float, float]) -> SwitchOutcome:
    return SwitchOutcome(
        id=i, switched_on=dt.date(2026, 3, 1), sold="S", bought="B",
        sold_price=sold[0], sold_currency="USD", sold_now=sold[1], sold_currency_now="USD",
        bought_price=bought[0], bought_currency="USD", bought_now=bought[1],
        bought_currency_now="USD",
    )  # fmt: skip


def test_a_switch_outcome_is_the_bought_change_minus_the_sold_change() -> None:
    o = _outcome(1, sold=(100, 110), bought=(50, 60))
    assert o.sold_change == pytest.approx(0.10)
    assert o.bought_change == pytest.approx(0.20)
    assert o.difference == pytest.approx(0.10)


def test_a_currency_mismatch_gives_no_change() -> None:
    o = _outcome(1, sold=(100, 110), bought=(50, 60)).model_copy(
        update={"bought_currency_now": "EUR"}
    )
    assert o.bought_change is None and o.difference is None


def test_below_five_switches_the_section_says_too_few() -> None:
    section = switch_section([_outcome(i, (100, 110), (50, 60)) for i in range(4)])
    assert section.status == "too_few" and not section.rows


def test_the_section_reports_the_share_that_paid_and_the_median() -> None:
    wins = [_outcome(i, (100, 100), (50, 60)) for i in range(3)]
    losses = [_outcome(10 + i, (100, 120), (50, 50)) for i in range(2)]
    section = switch_section(wins + losses)
    assert section.status == "ok" and section.n == 5
    assert section.rows[0].share == pytest.approx(0.6)
    assert section.rows[1].median_change == pytest.approx(0.2)


def test_switches_that_did_not_beat_holding_suggest_a_higher_safety_margin() -> None:
    losers = [_outcome(i, (100, 130), (50, 50)) for i in range(5)]
    card = build_scorecard(
        [], since=dt.date(2026, 1, 1), until=dt.date(2026, 12, 31), switches=losers,
        safety_margin=0.05,
    )  # fmt: skip
    assert any("raise the switching safety margin (now 5%)" in s for s in card.suggestions)
    winners = [_outcome(i, (100, 100), (50, 80)) for i in range(5)]
    card = build_scorecard(
        [], since=dt.date(2026, 1, 1), until=dt.date(2026, 12, 31), switches=winners
    )
    assert not any("safety margin" in s for s in card.suggestions)


def test_an_unreadable_log_is_reported_not_hidden() -> None:
    card = build_scorecard([], since=dt.date(2026, 1, 1), until=dt.date(2026, 12, 31))
    section = next(s for s in card.sections if s.id == "switches")
    assert section.status == "unavailable"


def test_a_target_only_needs_a_volatility_band_to_trigger_nothing_by_itself() -> None:
    target = ThesisPriceTarget.model_validate(
        {
            "currency": "USD", "method": "m", "set_on": "2026-10-01", "volatility_band": 0.3,
            "scenarios": {"bear": 1, "base": 2, "bull": 3},
        }
    )  # fmt: skip
    assert target.volatility_band == 0.3
