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
