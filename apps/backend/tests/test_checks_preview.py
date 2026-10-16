"""The pure part of the Checks live preview: which checks flip and how a holding is tallied."""

from api.checks_preview_routes import diff_checks, preview_holding, tally
from steward.checks.contract import Check, State


def _c(id_: str, state: State, *, informational: bool = False) -> Check:
    return Check(
        id=id_, axis="value", label=id_.upper(), state=state, sentence="", method_version="1",
        informational=informational,
    )  # fmt: skip


def test_diff_lists_only_flipped_checks() -> None:
    cur = [_c("a", "pass"), _c("b", "fail"), _c("c", "unknown")]
    new = [_c("a", "fail"), _c("b", "fail"), _c("c", "unknown")]
    changes = diff_checks(cur, new)
    assert [(x.id, x.was, x.now) for x in changes] == [("a", "pass", "fail")]


def test_tally_ignores_informational_checks() -> None:
    t = tally(
        [_c("a", "pass"), _c("b", "fail"), _c("e", "fail", informational=True)],
        [_c("r", "fail"), _c("i", "fail", informational=True)],
    )
    assert (t.passes, t.fails, t.risk_flags) == (1, 1, 1)


def test_holding_changed_flag_covers_risk_checks() -> None:
    cur = ([_c("a", "pass")], [_c("r", "pass")])
    same = preview_holding("X", None, cur, cur)
    assert not same.changed and same.changes == []
    flipped = preview_holding("X", "Ex", cur, ([_c("a", "pass")], [_c("r", "fail")]))
    assert flipped.changed
    assert flipped.current.risk_flags == 0 and flipped.would_be.risk_flags == 1
