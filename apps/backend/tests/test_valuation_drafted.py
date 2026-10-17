"""A valuation set drafted for the owner is not read as the owner's own valuation."""

from steward.valuation.drafted import (
    DRAFTED_PREFIX,
    OWNED_POINT_SQL,
    OWNED_SET_SQL,
    is_drafted,
)


def test_a_drafted_starting_set_is_not_the_owners_and_an_edited_one_is() -> None:
    # Production, 17 Oct 2026: all 22 saved DCF sets were drafted starting sets, yet fair values
    # headlined them as "Your DCF scenario" and theses cited them as the owner's valuation.
    assert is_drafted("Starting set drafted by Claude: compound revenue growth over 2 years")
    assert not is_drafted("My own view after the Q3 call")
    assert not is_drafted(None)
    # The SQL filters test the same prefix the Python check does.
    assert f"'{DRAFTED_PREFIX}%'" in OWNED_SET_SQL and "not like" in OWNED_SET_SQL
    assert f"'{DRAFTED_PREFIX}%'" in OWNED_POINT_SQL and "scenario_id is null" in OWNED_POINT_SQL
