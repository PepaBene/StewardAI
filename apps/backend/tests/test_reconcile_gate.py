"""fact_reconcile downloads an issuer's full XBRL only after a newer periodic report, or a week
after its last check."""

import datetime as dt
import json
from typing import Any

from steward.earnings.reconcile import (
    RECHECK_DAYS,
    due_for_check,
    newest_periodic_report,
    record_check,
)
from steward.sources.edgar import Filing

TODAY = dt.date(2026, 10, 22)


def _filing(form: str, day: int, accession: str) -> Filing:
    return Filing("0001326801", form, accession, dt.date(2026, 10, day), None, "x.htm", "")


class _Edgar:
    def __init__(self, filings: list[Filing]) -> None:
        self._filings = filings

    async def filings(self, cik: str) -> list[Filing]:
        return self._filings


class _Pool:
    def __init__(self) -> None:
        self.state: dict[str, str] = {}

    async def fetchval(self, sql: str, key: str) -> str | None:
        return self.state.get(key)

    async def execute(self, sql: str, key: str, value: str) -> None:
        self.state[key] = value


async def test_newest_periodic_report_ignores_other_forms() -> None:
    edgar = _Edgar(
        [
            _filing("10-Q", 1, "q3"),
            _filing("8-K", 21, "release"),
            _filing("4", 22, "insider"),
            _filing("10-Q/A", 5, "q3a"),
        ]
    )
    assert await newest_periodic_report(edgar, "1326801") == "q3a"  # type: ignore[arg-type]
    assert await newest_periodic_report(_Edgar([]), "1") is None  # type: ignore[arg-type]


async def test_a_ticker_never_checked_is_due() -> None:
    assert await due_for_check(_Pool(), "FB_US_EQ", "q3", TODAY)


async def test_same_report_within_a_week_is_skipped_then_due_again() -> None:
    pool = _Pool()
    await record_check(pool, "FB_US_EQ", "q3", TODAY)
    assert not await due_for_check(pool, "FB_US_EQ", "q3", TODAY)
    later = TODAY + dt.timedelta(days=RECHECK_DAYS)
    assert await due_for_check(pool, "FB_US_EQ", "q3", later)


async def test_a_newer_report_is_due_at_once() -> None:
    pool = _Pool()
    await record_check(pool, "FB_US_EQ", "q2", TODAY)
    assert await due_for_check(pool, "FB_US_EQ", "q3", TODAY)


async def test_unreadable_state_counts_as_never_checked() -> None:
    pool: Any = _Pool()
    pool.state["fact_reconcile:FB_US_EQ"] = json.dumps({"accession": "q3"})
    assert await due_for_check(pool, "FB_US_EQ", "q3", TODAY)
