"""Audit fixes for phases 7-8 (R13, R31-R36, R47-R51): pure and fake-driven cases."""

import asyncio
import datetime as dt
from contextlib import asynccontextmanager
from typing import Any

import pytest
from pydantic import ValidationError

from api.release_routes import UsageIn
from steward.budget_guard import BudgetExceeded
from steward.changes.feed import graph_item
from steward.journal.entries import (
    JournalEntry,
    JournalEntryIn,
    JournalError,
    last_close_on_or_before,
    record,
)
from steward.journal.export import _cell
from steward.journal.scorecard import DecisionOutcome
from steward.journal.service import build_outcome
from steward.quality.cockpit import QualityIssue
from steward.quality.snooze import SnoozeRow, split_snoozed
from steward.release.defects import weight_for
from steward.release.readiness import STORE_COUNT_CAP, _store_input
from steward.thesis import draft as thesis_draft

D = dt.date


def entry(**kw: Any) -> JournalEntry:
    base: dict[str, Any] = {
        "id": 1, "scope": "security", "ticker": "AAPL", "decision": "buy", "parent_id": None,
        "decided_on": D(2026, 3, 1), "hypothesis": "h", "valuation_assumptions": None,
        "catalysts": [], "disconfirming": [], "review_date": None, "conviction": None,
        "main_reason": None, "price_at_decision": 100.0, "price_currency": "USD",
        "snapshot": {"thesis": {"price_target": {"sell_target": 150, "bear_case": 80,
                                                  "currency": "USD"}}},
        "created_at": dt.datetime(2026, 3, 1, 9, tzinfo=dt.UTC),
    }  # fmt: skip
    return JournalEntry(**{**base, **kw})


SERIES = [(D(2026, 3, 2), 110.0), (D(2026, 3, 5), 155.0), (D(2026, 3, 9), 120.0)]


class TestScorecardCurrency:  # R31
    def test_unknown_series_currency_gives_no_change_or_hit(self) -> None:
        o = build_outcome(entry(), SERIES, None)
        assert o.change is None and o.hit_on is None and o.low_since is None

    def test_different_currency_gives_no_change(self) -> None:
        o = build_outcome(entry(), SERIES, "EUR")
        assert o.change is None and o.hit_on is None

    def test_same_currency_computes(self) -> None:
        o = build_outcome(entry(), SERIES, "USD")
        assert o.change == pytest.approx(0.2) and o.hit_on == D(2026, 3, 5)

    def test_pence_and_pounds_are_not_the_same_unit(self) -> None:
        assert build_outcome(entry(price_currency="GBP"), SERIES, "GBp").change is None
        assert build_outcome(entry(price_currency="GBX"), SERIES, "GBp").change is not None


class TestBackDated:  # R32
    def test_last_close_on_or_before(self) -> None:
        assert last_close_on_or_before(SERIES, D(2026, 3, 5)) == 155.0
        assert last_close_on_or_before(SERIES, D(2026, 3, 4)) == 110.0
        assert last_close_on_or_before(SERIES, D(2026, 3, 1)) is None

    def test_future_decision_date_is_refused(self) -> None:
        body = JournalEntryIn(
            decision="buy", ticker="AAPL", hypothesis="x",
            decided_on=dt.date.today() + dt.timedelta(days=1),
        )  # fmt: skip
        with pytest.raises(JournalError, match="future"):
            asyncio.run(record(None, "owner", body))  # type: ignore[arg-type]

    def test_target_hit_before_the_entry_was_written_is_not_counted(self) -> None:
        # Written on 2026-03-10 for a decision on 03-01: the 03-05 close of 155 predates it.
        late = entry(created_at=dt.datetime(2026, 3, 10, 9, tzinfo=dt.UTC))
        assert build_outcome(late, SERIES, "USD").hit_on is None
        assert build_outcome(late, SERIES, "USD").low_since is None  # no close after 03-10


class TestChangesAndSnooze:
    def test_graph_item_without_earlier_record_is_new(self) -> None:  # R34
        cut = dt.datetime(2026, 3, 1, tzinfo=dt.UTC)
        row: dict[str, Any] = {
            "id": 7, "ticker": "AAPL", "type": "supplier", "object_name": "TSMC",
            "status": "confirmed", "created_at": cut - dt.timedelta(days=5),
            "updated_at": cut + dt.timedelta(days=1), "prev_id": None, "prev_status": None,
            "prev_at": None,
        }  # fmt: skip
        assert graph_item(row, cut, {}).kind == "new"
        item = graph_item({**row, "prev_id": 3, "prev_status": "proposed", "prev_at": cut}, cut, {})
        assert item.kind == "revised" and item.before == "proposed"
        assert item.before_ref is not None and item.before_ref.id == "3"

    def test_snooze_returns_on_its_date(self) -> None:  # R36
        issue = QualityIssue.model_construct(kind="stale_price", subject="AAPL")
        rows = [SnoozeRow(id=1, kind="stale_price", subject="AAPL", action="snooze",
                          until=D(2026, 6, 10), note=None)]  # fmt: skip
        assert split_snoozed([issue], rows, D(2026, 6, 9))[1] != []
        active, snoozed = split_snoozed([issue], rows, D(2026, 6, 10))
        assert active == [issue] and snoozed == []


class TestRelease:
    def test_usage_path_accepts_mixed_case_only_plain_paths(self) -> None:  # R35
        assert UsageIn(path="/companies/AAPL").path == "/companies/AAPL"
        assert UsageIn(path="/funds/IE00B5BMR087")
        for bad in ("/x?a=1", "x", "/" + "a" * 121, "/a b"):
            with pytest.raises(ValidationError):
                UsageIn(path=bad)

    def test_store_counts_are_shown_capped(self) -> None:  # R47
        assert _store_input("holdings", {"holdings": 42}).detail == "42 rows"
        capped = _store_input("holdings", {"holdings": STORE_COUNT_CAP + 1}).detail
        assert capped == "1,000+ rows"
        assert _store_input("holdings", {}).detail == "no data stored"

    def test_csv_cell_neutralises_padded_formulas(self) -> None:  # R48
        for text in ("=1+1", "  =1+1", " +x", "\t-1", " @SUM(A1)", "\r=1"):
            assert _cell(text).startswith("'"), text
        assert _cell("plain") == "plain" and _cell("a = b") == "a = b"

    def test_defect_ticker_folds_to_the_held_broker_ticker(self) -> None:  # R51
        weights = {"AAPL_US_EQ": 0.2, "MSFT_US_EQ": 0.1}
        assert weight_for("AAPL", weights) == 0.2
        assert weight_for("aapl", weights) == 0.2
        assert weight_for("aapl_us_eq", weights) == 0.2
        assert weight_for("TSLA", weights) is None and weight_for(None, weights) is None


class _FakeCtx:
    @asynccontextmanager
    async def step(self, name: str):
        yield object()


def _fake_run_job(monkeypatch: pytest.MonkeyPatch, outcome: str = "succeeded") -> list[str]:
    seen: list[str] = []

    async def run_job(pool: Any, job_type: str, handler: Any, **kw: Any) -> str:
        seen.append(job_type)
        if outcome == "busy":
            return "busy"
        try:
            await handler(_FakeCtx())
        except Exception:
            return "failed"
        return "succeeded"

    monkeypatch.setattr("steward.jobs.runner.run_job", run_job)
    return seen


class TestThesisDraftBudget:  # R13
    def test_draft_runs_inside_a_job_and_returns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen = _fake_run_job(monkeypatch)

        async def fake_draft(llm: Any, model: str, report: Any) -> str:
            return "DRAFT"

        monkeypatch.setattr(thesis_draft, "draft_thesis", fake_draft)
        report = type("R", (), {"ticker": "AAPL"})()
        out = asyncio.run(thesis_draft.draft_within_budget(None, None, "m", report))  # type: ignore[arg-type]
        assert out == "DRAFT" and seen == ["thesis_draft"]

    def test_cap_reached_refuses_the_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _fake_run_job(monkeypatch)

        async def over(llm: Any, model: str, report: Any) -> str:
            raise BudgetExceeded("monthly AI budget reached ($5.00 of $5.00)")

        monkeypatch.setattr(thesis_draft, "draft_thesis", over)
        report = type("R", (), {"ticker": "AAPL"})()
        with pytest.raises(thesis_draft.DraftBudgetRefused, match="monthly AI budget"):
            asyncio.run(thesis_draft.draft_within_budget(None, None, "m", report))  # type: ignore[arg-type]

    def test_a_second_draft_for_the_company_is_refused_while_one_runs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _fake_run_job(monkeypatch, "busy")
        report = type("R", (), {"ticker": "AAPL"})()
        with pytest.raises(thesis_draft.DraftUnavailable, match="already"):
            asyncio.run(thesis_draft.draft_within_budget(None, None, "m", report))  # type: ignore[arg-type]


def test_decision_outcome_is_still_constructible() -> None:
    assert DecisionOutcome(
        id=1, ticker="A", decision="buy", decided_on=D(2026, 1, 1), price_at_decision=1.0,
        price_now=2.0,
    ).change == 1.0  # fmt: skip
