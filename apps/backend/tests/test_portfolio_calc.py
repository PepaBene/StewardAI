"""Snapshot arithmetic and drift: pure functions, no database.

Phase 1 ACs covered here:
- a snapshot's FX-adjusted value matches a manual calculation for at least 3 currency pairs;
- changing a target weight in config changes the reported drift with no code change.
"""

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from steward.config.strategy import StrategyConfig
from steward.fx import FxTable, MissingRateError, parse_cnb
from steward.portfolio.drift import HoldingValue, compute_drift
from steward.portfolio.snapshot import build_snapshot, snapshot_date_for
from steward.trading212.models import AccountSummary, Position
from tests.test_fx import CNB_TEXT

SUMMARY = AccountSummary.model_validate(
    {
        "id": 51320716,
        "currency": "CZK",
        "totalValue": "20000",
        "cash": {"availableToTrade": "1000", "inPies": "200", "reservedForOrders": "300"},
        "investments": {
            "currentValue": "18500",
            "realizedProfitLoss": "0",
            "totalCost": "17000",
            "unrealizedProfitLoss": "1500",
        },
    }
)


def position(ticker: str, currency: str, quantity: str, price: str, **extra: Any) -> Position:
    return Position.model_validate(
        {
            "instrument": {"ticker": ticker, "name": ticker, "currency": currency},
            "quantity": quantity,
            "averagePricePaid": price,
            "currentPrice": price,
            "walletImpact": {
                "currency": "CZK",
                "currentValue": "1",
                "totalCost": "1",
                "unrealizedProfitLoss": "0",
            },
            **extra,
        }
    )


POSITIONS = [
    position("AAPL_US_EQ", "USD", "3.5", "228.37"),  # USD -> CZK
    position("ULVRl_EQ", "GBX", "10", "4312.5"),  # GBX (pence) -> CZK
    position("ASMLa_EQ", "EUR", "2", "610.2"),  # EUR -> CZK
    position("CEZ_CZ_EQ", "CZK", "5", "1000"),  # CZK, no conversion
]
CAPTURED = dt.datetime(2026, 9, 25, 20, 31, tzinfo=dt.UTC)  # 22:31 in Prague


def close(a: Decimal, b: Decimal) -> bool:
    """Equal within Decimal's 28-significant-digit rounding."""
    return abs(a - b) <= abs(b) * Decimal("1e-24")


def snapshot(strategy: StrategyConfig | None = None) -> Any:
    fx = FxTable("cnb", parse_cnb(CNB_TEXT))
    return build_snapshot(
        SUMMARY, POSITIONS, fx, strategy or StrategyConfig(base_currency="CZK"), CAPTURED
    )


def test_fx_adjusted_values_match_manual_calculation() -> None:
    snap = snapshot()
    by_ticker = {p.ticker: p for p in snap.positions}
    # Manual: quantity x price x ČNB rate (CZK per unit), GBX scaled to GBP first.
    assert by_ticker["AAPL_US_EQ"].value_base == Decimal("3.5") * Decimal("228.37") * Decimal(
        "21.359"
    )
    assert by_ticker["ULVRl_EQ"].value_base == Decimal("10") * Decimal("4312.5") * Decimal(
        "0.01"
    ) * Decimal("28.299")
    assert by_ticker["ASMLa_EQ"].value_base == Decimal("2") * Decimal("610.2") * Decimal("24.350")
    assert by_ticker["CEZ_CZ_EQ"].value_base == Decimal("5000")
    assert by_ticker["ULVRl_EQ"].fx_rate == Decimal("0.28299")
    assert by_ticker["CEZ_CZ_EQ"].fx_rate == 1 and by_ticker["CEZ_CZ_EQ"].fx_rate_date is None


def test_totals_cash_and_weights() -> None:
    snap = snapshot()
    a = snap.account
    assert a.cash_base == Decimal("1500")  # available + in pies + reserved, CZK -> CZK
    assert a.invested_base == sum(p.value_base for p in snap.positions)
    assert a.total_base == a.invested_base + a.cash_base
    assert close(sum(p.weight for p in snap.positions) + a.cash_base / a.total_base, Decimal(1))
    assert snap.snapshot_date == dt.date(2026, 9, 25)


def test_base_currency_other_than_account_currency() -> None:
    snap = snapshot(StrategyConfig(base_currency="EUR", fx_source="cnb"))
    by_ticker = {p.ticker: p for p in snap.positions}
    assert by_ticker["ASMLa_EQ"].value_base == Decimal("1220.4")  # EUR stays EUR
    usd = by_ticker["AAPL_US_EQ"]
    assert close(
        usd.value_base, Decimal("3.5") * Decimal("228.37") * Decimal("21.359") / Decimal("24.350")
    )
    assert close(snap.account.cash_base, Decimal("1500") / Decimal("24.350"))


def test_missing_rate_fails_the_snapshot() -> None:
    fx = FxTable("cnb", parse_cnb(CNB_TEXT))
    with pytest.raises(MissingRateError):
        build_snapshot(
            SUMMARY,
            [position("NESN_EQ", "CHF", "1", "100")],
            fx,
            StrategyConfig(base_currency="CZK"),
            CAPTURED,
        )


def test_snapshot_day_is_the_prague_calendar_day() -> None:
    assert snapshot_date_for(dt.datetime(2026, 9, 25, 22, 30, tzinfo=dt.UTC)) == dt.date(
        2026, 9, 26
    )


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------

HOLDINGS = [
    HoldingValue("AAA_EQ", "USD", Decimal("5000")),
    HoldingValue("BBB_EQ", "EUR", Decimal("3000")),
    HoldingValue("CCC_EQ", "GBX", Decimal("1000")),
]
CASH = Decimal("1000")  # total 10 000


def strategy(**overrides: Any) -> StrategyConfig:
    body: dict[str, Any] = {
        "base_currency": "CZK",
        "target_positions": [
            {"ticker": "AAA_EQ", "target_weight": 0.40, "band": 0.05, "hard_limit": 0.55},
            {"ticker": "BBB_EQ", "target_weight": 0.30, "band": 0.02},
            {"ticker": "DDD_EQ", "target_weight": 0.10, "band": 0.02},
        ],
        "cash_target": {"target_weight": 0.10, "band": 0.02},
        **overrides,
    }
    return StrategyConfig.model_validate(body)


def test_position_drift_and_statuses() -> None:
    report = compute_drift(HOLDINGS, CASH, strategy())
    by_ticker = {p.ticker: p for p in report.positions}
    assert by_ticker["AAA_EQ"].weight == pytest.approx(0.5)
    assert by_ticker["AAA_EQ"].drift == pytest.approx(0.10)
    assert by_ticker["AAA_EQ"].status == "above_band"
    assert by_ticker["BBB_EQ"].status == "in_band"
    assert by_ticker["CCC_EQ"].status == "no_target" and by_ticker["CCC_EQ"].drift is None
    # A target for a position not held yet: weight 0, below its band.
    assert by_ticker["DDD_EQ"].held is False and by_ticker["DDD_EQ"].status == "below_band"
    assert report.cash.status == "in_band" and report.cash.weight == pytest.approx(0.1)
    assert report.out_of_band == 2


def test_changing_a_target_in_config_changes_the_drift() -> None:
    """Phase 1 AC: the same holdings, a different target weight in config, different drift."""
    before = compute_drift(HOLDINGS, CASH, strategy())
    config = strategy().model_dump()
    config["target_positions"][0]["target_weight"] = 0.50
    after = compute_drift(HOLDINGS, CASH, StrategyConfig.model_validate(config))
    a_before = next(p for p in before.positions if p.ticker == "AAA_EQ")
    a_after = next(p for p in after.positions if p.ticker == "AAA_EQ")
    assert a_before.drift == pytest.approx(0.10) and a_before.status == "above_band"
    assert a_after.drift == pytest.approx(0.0) and a_after.status == "in_band"


def test_hard_limit_outranks_band() -> None:
    report = compute_drift(
        [HoldingValue("AAA_EQ", "USD", Decimal("6000"))], Decimal("4000"), strategy()
    )
    assert next(p for p in report.positions if p.ticker == "AAA_EQ").status == "above_hard_limit"


def test_invested_basis_ignores_cash() -> None:
    report = compute_drift(HOLDINGS, CASH, strategy(weight_basis="invested", cash_target=None))
    by_ticker = {p.ticker: p for p in report.positions}
    assert by_ticker["AAA_EQ"].weight == pytest.approx(5000 / 9000)
    assert report.cash.weight is None and report.cash.status == "no_target"


def test_categories_use_classifications_and_derive_currency() -> None:
    report = compute_drift(
        HOLDINGS,
        CASH,
        strategy(
            classifications={"AAA_EQ": {"sector": "Tech"}, "BBB_EQ": {"sector": "Tech"}},
            target_categories=[{"dimension": "sector", "bucket": "Tech", "target_weight": 0.7}],
        ),
    )
    sector = next(c for c in report.categories if c.dimension == "sector")
    tech = next(b for b in sector.buckets if b.bucket == "Tech")
    assert tech.weight == pytest.approx(0.8) and tech.status == "above_band"
    assert {b.bucket for b in sector.buckets} == {"Tech", "Unclassified"}
    currency = next(c for c in report.categories if c.dimension == "currency")
    # GBX is reported as GBP.
    assert {b.bucket: b.weight for b in currency.buckets} == pytest.approx(
        {"USD": 0.5, "EUR": 0.3, "GBP": 0.1}
    )


def test_empty_portfolio_has_no_division_by_zero() -> None:
    report = compute_drift([], Decimal(0), StrategyConfig(base_currency="CZK"))
    assert report.positions == [] and report.cash.weight == 0.0
