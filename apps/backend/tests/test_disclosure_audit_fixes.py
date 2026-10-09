"""Regression tests for docs/revision/public_disclosure_audit_2026-10-08.md (pure functions)."""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import json
from decimal import Decimal as D
from typing import Any

from steward.disclosure import asof, replay
from steward.disclosure import insiders as ins
from steward.disclosure.conditional import ConditionalFetch
from steward.disclosure.coverage_report import quality_rows
from steward.disclosure.dependencies import (
    Dependency,
    Edge,
    latest_edges,
    one_per_counterparty,
)
from steward.disclosure.estimate_read import release_for
from steward.disclosure.fetch import FetchOutcome, Validators
from steward.disclosure.financials import Repurchase, check_repurchase
from steward.disclosure.ingest import MAX_FAILED_ATTEMPTS, attempts_done_sql
from steward.disclosure.ingest_job import index_filing_items
from steward.disclosure.jobs import FILINGS_PER_ISSUER, advance_cursor, new_filings
from steward.disclosure.note_job import period_end_of
from steward.disclosure.parse import IxContext, IxFact, ParsedDocument, parse_fact_value
from steward.disclosure.pdmr_activity import dedupe
from steward.disclosure.sec_discovery import DiscoveredFiling

UTC = dt.UTC


def at(month: int, day: int) -> dt.datetime:
    return dt.datetime(2026, month, day, 12, tzinfo=UTC)


# ------------------------------------------------------------------------- 3.3 iXBRL formats
def test_hyphenated_transformation_formats_are_read() -> None:
    assert parse_fact_value("1.234,5", "ixt:num-comma-decimal", 0, "")[0] == D("1234.5")
    assert parse_fact_value("1,234.5", "ixt:num-dot-decimal", 0, "")[0] == D("1234.5")
    assert parse_fact_value("-", "ixt:fixed-zero", 0, "")[0] == D(0)
    assert parse_fact_value("—", "ixt:zero-dash", 0, "")[0] == D(0)


# ------------------------------------------------------------------- 2.3 review state at cutoff
def test_state_at_cutoff_reads_review_decisions_not_the_stored_state() -> None:
    sql = asof.state_at_sql("observation", "o", "$2")
    assert "quality_review" in sql and "$2" in sql and "o.review_state" in sql
    claim = asof.state_at_sql("claim", "c", "$3", decided_at_col=True)
    assert "c.decided_at" in claim


# --------------------------------------------------------------- 2.1 model labels never count
def test_model_labels_never_reach_the_quality_gate() -> None:
    labels = [
        {
            "dataset": "insider_class",
            "sample_key": "s",
            "item_ref": f"i{n}",
            "correct": True,
            "adjudicator_kind": "model",
        }
        for n in range(60)
    ]
    q = {r["dataset"]: r for r in quality_rows(labels)}
    assert q["insider_class"]["state"] == "not_sampled"
    accepted = [{**r, "adjudicator_kind": "accepted_suggestion"} for r in labels]
    q = {r["dataset"]: r for r in quality_rows(accepted)}
    assert q["insider_class"]["labelled"] == 60


def test_quality_reads_only_the_newest_sample() -> None:
    old = [
        {"dataset": "insider_class", "sample_key": "a", "item_ref": f"i{n}", "correct": False}
        for n in range(50)
    ]
    new = [
        {"dataset": "insider_class", "sample_key": "b", "item_ref": f"j{n}", "correct": True}
        for n in range(10)
    ]
    q = {r["dataset"]: r for r in quality_rows(old + new)}
    assert q["insider_class"]["sample_key"] == "b"
    assert q["insider_class"]["labelled"] == 10 and q["insider_class"]["errors"] == 0


# ------------------------------------------------------------------- 4.5 insider alert groups
def tx(accession: str, row: int, shares: str, *, known: dt.datetime | None = None) -> ins.InsiderTx:
    known = known or at(6, 3)
    return ins.InsiderTx(
        accession=accession,
        row_index=row,
        insider="0000000001",
        name="Person 1",
        role="officer",
        tx_date=dt.date(2026, 6, 1),
        filed=known.date(),
        known_at=known,
        economic_class="open_market_buy",
        shares=D(shares),
        price=D(10),
        plan=False,
        class_confirmed=True,
    )


def test_split_fills_are_summed_into_one_alert() -> None:
    lines = [tx("A", 0, "600"), tx("A", 1, "600")]  # 6,000 each, 12,000 together
    got = ins.alerts(lines, at(7, 1), min_value=D(10_000))
    assert len(got) == 1 and got[0].value == D(12_000) and got[0].shares == D(1200)


def test_a_later_filing_repeating_a_line_is_not_a_second_trade() -> None:
    lines = [tx("A", 0, "600"), tx("B", 0, "600", known=at(6, 5))]
    got = ins.alerts(lines, at(7, 1), min_value=D(10_000))
    assert got == []  # one 6,000 trade, below the threshold


# --------------------------------------------------------------------- 4.8 PDMR split fills
def test_pdmr_keeps_identical_lines_of_one_notice_and_drops_a_repeat() -> None:
    a0, a1 = tx("A", 0, "100"), tx("A", 1, "100")
    repeat = tx("B", 0, "100", known=at(6, 9))
    assert [t.key for t in dedupe([a0, a1, repeat])] == [("A", 0), ("A", 1)]


# --------------------------------------------------------- 4.13 one claim per counterparty
def dep(i: int, known: dt.datetime, **kw: object) -> Dependency:
    base: dict[str, object] = {
        "id": i,
        "reporter": "AAA",
        "relationship": "customer",
        "shape": "named",
        "status": "confirmed",
        "known_at": known,
        "passage": "Acme accounted for 22% of revenue.",
        "counterparty_name": "Acme Corp",
        "counterparty_key": "ACME",
        "share": D("0.22"),
        "denominator": "total_revenue",
        "period_end": dt.date(2025, 12, 31),
    }
    base.update(kw)
    return Dependency(**base)  # type: ignore[arg-type]


def test_one_customer_reported_in_three_filings_is_one_customer() -> None:
    rows = [dep(1, at(2, 1)), dep(2, at(5, 1)), dep(3, at(3, 1), share=D("0.21"))]
    kept = one_per_counterparty(rows)
    assert [d.id for d in kept] == [2]


def test_anonymous_claims_keep_their_own_label() -> None:
    a = dep(
        1,
        at(2, 1),
        shape="anonymous",
        counterparty_name=None,
        counterparty_key=None,
        label="Customer A",
    )
    b = dep(
        2,
        at(2, 1),
        shape="anonymous",
        counterparty_name=None,
        counterparty_key=None,
        label="Customer B",
    )
    assert len(one_per_counterparty([a, b])) == 2


def test_several_years_of_one_link_are_one_edge() -> None:
    def edge(i: int, year: int) -> Edge:
        return Edge(
            i, "SUP", "CUS", D("0.2"), "confirmed", at(2, 1), period_end=dt.date(year, 12, 31)
        )

    kept = latest_edges([edge(1, 2024), edge(2, 2025), edge(3, 2023)])
    assert [e.id for e in kept] == [2]


# ---------------------------------------------------------------------- 4.20 zero buyback
def test_a_reported_zero_buyback_is_a_value() -> None:
    zero = Repurchase(D(0), D(0), dt.date(2026, 1, 1), dt.date(2026, 3, 31), "common")
    assert check_repurchase(zero).status == "pass"
    blank = Repurchase(None, D(0), dt.date(2026, 1, 1), dt.date(2026, 3, 31), "common")
    assert check_repurchase(blank).status == "needs_review"


# ------------------------------------------------------------------ 4.21 reporting period end
def fact(name: str, ctx: str | None, text: str = "1") -> IxFact:
    return IxFact(name, ctx, None, None, 0, "", text, D(1), None, None, False, None, None)


def test_period_end_is_the_stated_date_else_the_most_used_end() -> None:
    q = IxContext("q", dt.date(2026, 1, 1), dt.date(2026, 3, 31), None, ())
    fwd = IxContext("f", dt.date(2027, 1, 1), dt.date(2027, 12, 31), None, ())
    doc = ParsedDocument(
        text="",
        facts=[fact("us-gaap:Revenue", "q"), fact("us-gaap:Cost", "q"), fact("x:Guide", "f")],
        contexts={"q": q, "f": fwd},
    )
    assert period_end_of(doc) == dt.date(2026, 3, 31)
    stated = dataclasses.replace(
        doc, facts=[*doc.facts, fact("dei:DocumentPeriodEndDate", None, "March 28, 2026")]
    )
    assert period_end_of(stated) == dt.date(2026, 3, 28)


# ------------------------------------------------------------------ 4.4 replay state changes
def test_replay_since_lists_a_changed_item_with_what_it_was() -> None:
    known = [replay.item("Deal X", None, "announced", 7)]
    now = [replay.item("Deal X", None, "closed", 7), replay.item("Deal Y", None, "rumored", 8)]
    since = replay.new_since(known, now)
    assert [(i["ref"], i["change"]) for i in since] == [("7", "changed"), ("8", "new")]
    assert since[0]["was"] == "announced"


# ------------------------------------------------------------ 4.29 backfill attempts recorded
def test_attempts_sql_gives_up_after_an_empty_read_or_repeated_failures() -> None:
    sql = attempts_done_sql("v.id", "sec_backfill", "$3")
    assert "disclosure_backfill_attempt" in sql and "'sec_backfill'" in sql
    assert f"a.attempts >= {MAX_FAILED_ATTEMPTS}" in sql and "a.outcome = 'empty'" in sql


# --------------------------------------------------------- 6.4 incremental, conditional fetch
def filing(acc: str, day: int) -> DiscoveredFiling:
    return DiscoveredFiling(
        "0000000001", acc, "10-Q", dt.date(2026, 5, day), None, None, "a.htm", "", at(5, day), {}
    )


def test_only_filings_after_the_cursor_are_read_and_the_cursor_keeps_validators() -> None:
    filings = [filing(f"0-{d}", d) for d in range(1, 8)]
    assert len(new_filings(filings, {})) == FILINGS_PER_ISSUER
    cursor = {"known_at": at(5, 6).isoformat(), "accession": "0-6"}
    assert [f.accession for f in new_filings(filings, cursor)] == ["0-7"]
    nxt = advance_cursor([filings[-1]], cursor, Validators('"e1"', None))
    assert nxt == {"known_at": at(5, 7).isoformat(), "accession": "0-7", "etag": '"e1"'}
    # nothing new: the position stays, the validators are refreshed
    same = advance_cursor([], {**nxt, "last_modified": "x"}, Validators('"e2"', None))
    assert same["accession"] == "0-7" and same["etag"] == '"e2"' and "last_modified" not in same


class _CursorPool:
    """`disclosure_source.cursor` of one source, in memory."""

    def __init__(self) -> None:
        self.cursor: dict[str, Any] = {}

    async def fetchval(self, sql: str, source: str, key: str) -> Any:
        return json.dumps(self.cursor[key]) if key in self.cursor else None

    async def execute(self, sql: str, source: str, key: str, value: str) -> str:
        self.cursor[key] = json.loads(value)
        return "UPDATE 1"


class _Fetcher:
    def __init__(self) -> None:
        self.sent: list[Validators | None] = []

    async def fetch(
        self, source_key: str, url: str, *, validators: Validators | None = None
    ) -> FetchOutcome:
        self.sent.append(validators)
        if validators and validators.etag == '"v1"':
            return FetchOutcome(url, 304, False)
        return FetchOutcome(url, 200, True, b"{}", validators=Validators('"v1"', None))


def test_validators_are_remembered_only_after_the_response_was_handled() -> None:
    async def run() -> None:
        pool, fetcher = _CursorPool(), _Fetcher()
        get = ConditionalFetch(pool, fetcher, "sec.submissions", "deals")  # type: ignore[arg-type]
        first = await get("https://x/a.json")
        assert first.modified and pool.cursor == {}  # not handled yet: nothing remembered
        again = await get("https://x/a.json")
        assert again.modified  # a failed run fetches the page in full again
        await get.done("https://x/a.json")
        assert pool.cursor == {"deals:https://x/a.json": {"etag": '"v1"'}}
        third = await get("https://x/a.json")
        assert not third.modified and fetcher.sent[-1] == Validators('"v1"', None)

    asyncio.run(run())


# ------------------------------------------------------------- 3.5 earnings release from 8-K
class _InsertPool:
    def __init__(self) -> None:
        self.rows: dict[str, tuple[Any, ...]] = {}

    async def execute(self, sql: str, *args: Any) -> str:
        if args[0] in self.rows:
            return "INSERT 0 0"
        self.rows[args[0]] = args
        return "INSERT 0 1"


def test_only_8k_items_are_indexed_once() -> None:
    def f(acc: str, form: str, items: str) -> DiscoveredFiling:
        return dataclasses.replace(filing(acc, 3), form=form, items=items)

    pool = _InsertPool()
    filings = [
        f("a", "8-K", "2.02,9.01"),
        f("b", "10-Q", ""),
        f("c", "8-K", ""),
        f("d", "8-K/A", "2.02"),
    ]
    assert asyncio.run(index_filing_items(pool, 7, filings)) == 2  # type: ignore[arg-type]
    assert asyncio.run(index_filing_items(pool, 7, filings)) == 0  # type: ignore[arg-type]
    assert set(pool.rows) == {"a", "d"}


def test_a_stored_item_2_02_8k_is_the_release() -> None:
    q_end = dt.date(2026, 3, 31)
    release = dt.datetime(2026, 4, 28, 20, 5, tzinfo=UTC)
    filings = [("8.01", dt.datetime(2026, 4, 10, tzinfo=UTC)), ("2.02,9.01", release)]
    assert release_for(q_end, filings) == (release, "earnings_release")
    late = [("2.02", dt.datetime(2026, 8, 1, tzinfo=UTC))]  # past the 75-day window
    assert release_for(q_end, late)[1] == "period_end_fallback"
