"""M8.1: the golden-coverage map in docs/analytics/golden_coverage.md names real tests, and every
calculation family the plan lists has at least one."""

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
MAP = BACKEND.parents[1] / "docs" / "analytics" / "golden_coverage.md"

# The families M8.1 names, plus fund look-through and overlap.
REQUIRED = {
    "accounting identity",
    "return reconciliation",
    "x-ray bucket totals",
    "valuation sensitivity",
    "fx inversion",
    "peer-universe membership",
    "point-in-time screen",
    "risk statistics",
    "shock reconciliation",
    "theme exposure",
    "rebalance",
    "pre-trade checks",
}

ROW = re.compile(r"^\|\s*(?P<family>[^|]+?)\s*\|\s*(?P<scope>[^|]+?)\s*\|\s*(?P<test>[^|]+?)\s*\|$")


def rows() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for line in MAP.read_text(encoding="utf-8").splitlines():
        m = ROW.match(line.strip())
        if m and "::" in m["test"]:
            out.append((m["family"], m["test"]))
    return out


def test_every_required_family_has_a_mapped_test() -> None:
    assert REQUIRED <= {family for family, _ in rows()}


def test_every_mapped_test_exists() -> None:
    missing: list[str] = []
    for _, ref in rows():
        file, name = ref.split("::")
        path = BACKEND / file
        if not path.is_file() or not re.search(
            rf"^(async )?def {re.escape(name)}\b", path.read_text(encoding="utf-8"), re.M
        ):
            missing.append(ref)
    assert missing == []


def test_no_test_is_mapped_twice() -> None:
    refs = [ref for _, ref in rows()]
    assert len(refs) == len(set(refs))
