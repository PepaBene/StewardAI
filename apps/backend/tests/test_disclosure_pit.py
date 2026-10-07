"""P9.4 / P9.5 (offline part): point-in-time ordering, replay separation, alert rules and failure
behaviour that need no database. DB-bound cases (document amendment, deal reversal, insider
amendment, issuer rename, restatement through the SQL reads) are not covered here."""

from __future__ import annotations

import asyncio
import datetime as dt
from decimal import Decimal
from typing import Any

import asyncpg

from steward.disclosure import alerts, answer, replay
from steward.disclosure import estimates as est


def at(day: int) -> dt.datetime:
    return dt.datetime(2026, 3, day, tzinfo=dt.UTC)


def value(
    vid: int, snap: int, day: int, mean: str, *, supersedes: int | None = None, **kw: Any
) -> est.Value:
    return est.Value(
        id=vid,
        snapshot_id=snap,
        series="published_analyst_consensus",
        published_at=at(day),
        known_at=at(day),
        metric="revenue",
        fiscal_period="FY2026",
        period_end=dt.date(2026, 12, 31),
        unit="amount",
        basis="GAAP",
        currency="USD",
        mean=Decimal(mean),
        analyst_count=10,
        supersedes_snapshot_id=supersedes,
        **kw,
    )


# ---------------------------------------------------------- consensus correction ordering
def test_a_correction_hides_the_original_only_once_it_is_known() -> None:
    original = value(1, 10, 1, "100")
    correction = value(2, 11, 10, "90", supersedes=10)
    before = est.visible([original, correction], at(5))
    after = est.visible([original, correction], at(12))
    assert [v.id for v in before] == [1]  # the correction is not known yet
    assert [v.id for v in after] == [2]  # the original is replaced from the moment it is known


def test_no_visible_value_predates_its_known_at() -> None:
    vals = [value(1, 10, 1, "100"), value(2, 11, 10, "90", supersedes=10)]
    for day in (1, 5, 10, 20):
        assert all(v.known_at <= at(day) for v in est.visible(vals, at(day)))


def test_revision_pairs_compare_only_consecutive_snapshots() -> None:
    pairs = est.revision_pairs([value(1, 10, 1, "100"), value(2, 11, 10, "110")])
    assert len(pairs) == 1 and pairs[0].change_pct == Decimal(10)


# ------------------------------------------------------------------- replay separation
def _item(ref: str, day: int | None = None) -> dict[str, Any]:
    return replay.item(f"x{ref}", None if day is None else at(day), "", ref)


def test_future_items_are_dropped_from_the_known_half_and_counted() -> None:
    kept, leaked = replay.drop_future([_item("a", 1), _item("b", 20), _item("c")], at(10))
    assert [i["ref"] for i in kept] == ["a", "c"] and leaked == 1


def test_since_lists_only_what_became_known_afterwards() -> None:
    known = [_item("a"), _item("b")]
    now = [_item("a"), _item("b"), _item("c")]
    assert [i["ref"] for i in replay.new_since(known, now)] == ["c"]
    assert replay.new_since(known, known) == []


class _Pool:
    """A pool with one held ticker and a reader that returns a future-stamped row."""

    async def fetchval(self, sql: str, *args: Any) -> Any:
        return dt.date(2026, 3, 1)

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        return [{"ticker": "AAA", "quantity": 1, "value": 10, "instrument_currency": "USD"}]

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        return None


def test_replay_never_leaks_a_future_row_and_returns_it_as_later() -> None:
    async def reader(
        _pool: Any, name: str, _t: str, _i: Any, when: dt.datetime
    ) -> list[dict[str, Any]]:
        rows = [_item("old", 1), _item("late", 20)]
        return [r for r in rows if r["known_at"] <= when] if when < at(25) else rows

    out = asyncio.run(replay.replay(_Pool(), at(10), now=at(30), reader=reader))  # type: ignore[arg-type]
    d = out["holdings"][0]["datasets"]["filings"]
    assert [i["ref"] for i in d["known"]] == ["old"]
    assert [i["ref"] for i in d["since"]] == ["late"]
    assert out["snapshot_date"] == dt.date(2026, 3, 1)


def test_replay_marks_a_failed_dataset_instead_of_showing_it_empty() -> None:
    async def reader(*_a: Any) -> list[dict[str, Any]]:
        raise asyncpg.PostgresError("down")

    out = asyncio.run(replay.replay(_Pool(), at(10), now=at(30), reader=reader))  # type: ignore[arg-type]
    assert all(d["error"] for d in out["holdings"][0]["datasets"].values())


# ------------------------------------------------------------------------- alert rules
def test_control_crossing_needs_a_stated_stake_that_was_not_already_there() -> None:
    t = Decimal(25)
    assert alerts.crossed_control(None, Decimal(30), t)
    assert alerts.crossed_control(Decimal(20), Decimal(25), t)
    assert not alerts.crossed_control(Decimal(30), Decimal(35), t)
    assert not alerts.crossed_control(None, None, t)
    assert not alerts.crossed_control(Decimal(5), Decimal(24), t)


def test_non_comparable_or_ungated_revisions_review_instead_of_alert() -> None:
    a, b = value(1, 10, 1, "100"), value(2, 11, 10, "120")
    ok = est.revision(a, b)
    assert alerts.revision_decision(ok, gate_met=True, series_ok=True).action == "alert"
    assert alerts.revision_decision(ok, gate_met=False, series_ok=True).action == "review"
    broken = est.revision(a, value(3, 12, 11, "120", fiscal_calendar="other"))
    if not broken.comparable:
        assert alerts.revision_decision(broken, gate_met=True, series_ok=True).action == "review"
    assert alerts.revision_decision(ok, gate_met=True, series_ok=False).action == "skip"


def test_debt_maturity_shift_uses_the_year_buckets_and_ignores_the_stated_total() -> None:
    prior = {
        "year_1": Decimal(10),
        "year_2": Decimal(40),
        "thereafter": Decimal(50),
        "total": Decimal(100),
    }
    now = {
        "year_1": Decimal(40),
        "year_2": Decimal(30),
        "thereafter": Decimal(30),
        "total": Decimal(100),
    }
    shift = alerts.maturity_shift(prior, now, Decimal(10))
    assert shift is not None and shift[2] == Decimal(30)
    assert alerts.maturity_shift(prior, prior, Decimal(10)) is None
    assert alerts.near_term_share({"year_2": Decimal(5)}) is None  # no year 1: no share


def test_why_carries_rule_threshold_and_inputs() -> None:
    w = alerts.why("r", "t", {"a": 1})
    assert w == {"rule": "r", "threshold": "t", "inputs": {"a": 1}}


class _FailingPool:
    """Held tickers read fine; every collection read fails. Nothing may be recorded."""

    def __init__(self) -> None:
        self.writes = 0

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        if "position_snapshot" in sql:
            return [{"ticker": "AAA"}]
        raise asyncpg.PostgresError("collection unavailable")

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.writes += 1
        return 1


def test_a_collection_failure_counts_as_failed_and_never_alerts() -> None:
    pool = _FailingPool()
    totals = asyncio.run(alerts.scan_all(pool, at(10)))  # type: ignore[arg-type]
    assert totals["failed"] == 1
    assert pool.writes == 0
    assert sum(v for k, v in totals.items() if k != "failed") == 0


class _FullThrottlePool:
    def __init__(self) -> None:
        self.inserted = False

    async def fetchval(self, sql: str, *args: Any) -> Any:
        if sql.lstrip().startswith("select count"):
            return alerts.THROTTLE_PER_DAY
        self.inserted = True
        return 1


def test_a_full_throttle_stores_nothing() -> None:
    pool = _FullThrottlePool()
    stored = asyncio.run(
        alerts.record(
            pool,  # type: ignore[arg-type]
            kind="deal_state",
            ticker="AAA",
            dedupe_key="k",
            headline="h",
            detail={},
            known_at=at(1),
        )
    )
    assert stored is False and pool.inserted is False


# ---------------------------------------------------------------------- answer guard
def test_numbers_must_appear_in_the_retrieved_evidence() -> None:
    assert answer.numbers_grounded("revenue was 1,200 in 2025", ["revenue 1,200", "2025"])
    assert not answer.numbers_grounded("revenue was 1,300", ["revenue 1,200"])


def test_an_answer_with_no_evidence_says_so_and_invents_nothing() -> None:
    out = answer.compose("who are the customers", "AAA", at(10), [])
    assert out["grounded"] and "No verified stored evidence" in out["text"]


def test_an_anonymous_counterparty_is_never_named() -> None:
    ev = [
        {
            "kind": "claim",
            "id": 1,
            "label": "relationship_customer",
            "counterparty": "Customer A",
            "value": None,
            "quote": "Customer A accounted for 12% of revenue",
            "anonymous": True,
            "source": "SEC 10-K",
            "known_at": at(3),
            "period_end": None,
            "section": None,
            "document_url": None,
        }
    ]
    out = answer.compose("customer revenue", "AAA", at(10), ev)
    assert "undisclosed counterparty" in (out["text"] or "")
    assert "Customer A" not in (out["text"] or "")
