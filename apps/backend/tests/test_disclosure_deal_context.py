"""P6.6: deal impacts read share counts and net debt from stored observations, and stay null with a
reason when a number is not stored."""

import datetime as dt
from decimal import Decimal as D

from steward.disclosure import deal_read


def row(who: str, metric: str, value: str, scale: int = 0) -> dict:
    return {"who": who, "metric": metric, "value": D(value), "scale": scale}


def test_share_counts_are_scaled_to_base_units_and_split_by_role() -> None:
    ctx = deal_read.context_from_facts(
        [
            row("acquirer", "shares_outstanding", "500", 6),
            row("target", "shares_outstanding", "120", 6),
        ],
        dt.date(2026, 6, 1),
    )
    assert ctx.acquirer_shares == D(500_000_000)
    assert ctx.target_shares == D(120_000_000)
    assert ctx.announced_on == dt.date(2026, 6, 1)


def test_the_latest_row_per_participant_and_metric_wins() -> None:
    ctx = deal_read.context_from_facts(
        [row("target", "shares_outstanding", "100"), row("target", "shares_outstanding", "110")],
        None,
    )
    assert ctx.target_shares == D(110)


def test_net_debt_needs_both_debt_and_cash_never_one_alone() -> None:
    only_debt = deal_read.context_from_facts([row("held", "total_debt", "900")], None)
    assert only_debt.net_debt_before is None
    both = deal_read.context_from_facts(
        [row("held", "total_debt", "900"), row("held", "cash", "250")], None
    )
    assert both.net_debt_before == D(650)


def test_nothing_stored_leaves_every_input_unknown_not_zero() -> None:
    ctx = deal_read.context_from_facts([], None)
    assert ctx.acquirer_shares is None and ctx.target_shares is None
    assert ctx.parent_shares is None and ctx.net_debt_before is None
    assert ctx.enterprise_value_before is None and ctx.acquirer_segments is None


def test_a_spin_off_parent_uses_the_issuer_then_the_held_company() -> None:
    held = deal_read.context_from_facts([row("held", "shares_outstanding", "80")], None)
    assert held.parent_shares == D(80)
    named = deal_read.context_from_facts(
        [row("held", "shares_outstanding", "80"), row("issuer", "shares_outstanding", "90")], None
    )
    assert named.parent_shares == D(90)


def held_facts(debt_cur: str = "USD", cash_cur: str = "USD") -> list[dict]:
    return [
        {**row("held", "shares_outstanding", "10"), "currency": None},
        {**row("held", "total_debt", "500"), "currency": debt_cur},
        {**row("held", "cash", "100"), "currency": cash_cur},
    ]


def test_enterprise_value_is_market_value_plus_net_debt_in_one_currency() -> None:
    ctx = deal_read.context_from_facts(held_facts(), None, {"close": D("20"), "currency": "usd"})
    assert ctx.enterprise_value_before == D(600)  # 20 x 10 + (500 - 100)


def test_enterprise_value_stays_unknown_when_currencies_differ_or_price_is_missing() -> None:
    price = {"close": D("20"), "currency": "USD"}
    assert (
        deal_read.context_from_facts(held_facts("EUR"), None, price).enterprise_value_before is None
    )
    assert (
        deal_read.context_from_facts(held_facts(cash_cur=""), None, price).enterprise_value_before
        is None
    )
    assert deal_read.context_from_facts(held_facts(), None, None).enterprise_value_before is None


def seg(who: str, name: str, value: str, end: dt.date) -> dict:
    return {**row(who, "revenue", value), "segment": name, "period_end": end}


def test_segment_revenue_uses_the_latest_period_and_feeds_the_mix() -> None:
    old, new = dt.date(2025, 12, 31), dt.date(2026, 12, 31)
    ctx = deal_read.context_from_facts(
        [
            seg("acquirer", "cloud", "10", old),
            seg("acquirer", "cloud", "60", new),
            seg("acquirer", "devices", "40", new),
            seg("target", "cloud", "20", new),
            {**row("held", "shares_outstanding", "5"), "segment": "cloud"},  # not the firm's count
        ],
        None,
    )
    assert ctx.acquirer_segments == {"cloud": D(60), "devices": D(40)}
    assert ctx.target_segments == {"cloud": D(20)}
    assert ctx.parent_shares is None
