from steward.disclosure.ownership_read import group_summary


def holders(*flags: bool | None) -> list[dict[str, bool | None]]:
    return [{"member_of_group": f} for f in flags]


def test_a_joint_filing_without_a_ticked_box_is_not_called_a_group() -> None:
    out = group_summary(holders(None, None, None))
    assert out["status"] == "joint_filing_group_not_stated" and out["members"] == 3
    assert out["note"] and "not a sum" in out["note"]


def test_group_statuses() -> None:
    assert group_summary(holders(True, None))["status"] == "stated_group"
    assert group_summary(holders(False, False))["status"] == "stated_not_group"
    single = group_summary(holders(None))
    assert single["status"] == "single_filer" and single["note"] is None
