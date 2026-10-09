"""Audit regressions for screening, look-through, estimate reads and estimate evidence (pure)."""

from __future__ import annotations

import asyncio
import datetime as dt
import itertools
from decimal import Decimal as D
from typing import Any

from steward.disclosure import coverage_report as cr
from steward.disclosure import estimate_extract as ex
from steward.disclosure import estimate_read as er
from steward.disclosure import estimate_store as store
from steward.disclosure import estimates as est
from steward.disclosure import look_through as lt
from steward.disclosure import screening as sc


def at(m: int, d: int = 1, y: int = 2026) -> dt.datetime:
    return dt.datetime(y, m, d, tzinfo=dt.UTC)


_ids = itertools.count(1)


def val(known: dt.datetime, mean: str = "100", **kw: Any) -> est.Value:
    n = next(_ids)
    base: dict[str, Any] = dict(
        id=n,
        snapshot_id=n,
        series="published_analyst_consensus",
        published_at=known,
        known_at=known,
        metric="Revenue",
        fiscal_period="FY2026Q2",
        period_end=dt.date(2026, 6, 30),
        period_start=dt.date(2026, 4, 1),
        unit="amount",
        scale=0,
        basis="GAAP",
        currency="USD",
        mean=D(mean),
        analyst_count=10,
        panel_method="compiled",
    )
    base.update(kw)
    return est.Value(**base)


# --------------------------------------------------------------------------- 4.25 segment surprise
def test_segment_events_feed_the_screening_field_as_a_fraction() -> None:
    estimates = [
        val(at(6), "100", segment="Cloud"),
        val(at(6), "50", segment="Devices"),
    ]
    actuals = [
        est.Actual(
            1, "Revenue", dt.date(2026, 6, 30), D("110"), "amount", 0, "USD", "GAAP", "Cloud"
        ),
        est.Actual(
            2, "Revenue", dt.date(2026, 6, 30), D("49"), "amount", 0, "USD", "GAAP", "Devices"
        ),
    ]
    bridge = est.segment_bridge(
        estimates, actuals, metric="Revenue", period_end=dt.date(2026, 6, 30)
    )
    events = er.segment_events(
        bridge,
        release_at=at(7, 15),
        release_basis="period_end_fallback",
        known_at_by_value={v.id: v.known_at for v in estimates},
    )
    assert {e["segment"] for e in events} == {"Cloud", "Devices"}
    assert all(e["release_basis"] == "period_end_fallback" for e in events)
    field = sc.segment_surprise_field(events)
    assert field is not None
    assert field.value == D("0.1")  # Cloud +10 % beats Devices -2 %; a fraction, not 10
    assert "Cloud" in field.source


def test_segment_surprise_ignores_total_level_and_unscored_events() -> None:
    total = {"segment": None, "status": "scored", "surprise_pct": "5", "period_end": None}
    unscored = {
        "segment": "A",
        "status": "not_comparable",
        "surprise_pct": None,
        "period_end": None,
    }
    assert sc.segment_surprise_field([total, unscored]) is None


# ---------------------------------------------------------------------------- 4.26 ratio fields
def _row(
    metric: str, end: dt.date, value: str, known: dt.datetime, scale: int = 0
) -> dict[str, Any]:
    return {
        "metric": metric,
        "period_start": dt.date(end.year, 1, 1),
        "period_end": end,
        "value": D(value),
        "scale": scale,
        "known_at": known,
    }


def test_ratio_field_never_falls_back_to_an_older_period() -> None:
    rows = [
        _row("operating_cash_flow", dt.date(2025, 12, 31), "120", at(2)),
        _row("net_income", dt.date(2025, 12, 31), "100", at(2)),
        _row("operating_cash_flow", dt.date(2026, 6, 30), "50", at(8)),
        _row("net_income", dt.date(2026, 6, 30), "-10", at(8)),
    ]
    got = sc.ratio_field(rows, "operating_cash_flow", "net_income", "obs")
    assert isinstance(got, sc.Missing) and "2026-06-30" in got.reason
    no_den = sc.ratio_field(rows[:1] + rows[2:3], "operating_cash_flow", "net_income", "obs")
    assert isinstance(no_den, sc.Missing)


def test_ratio_field_applies_scale() -> None:
    rows = [
        _row("operating_cash_flow", dt.date(2026, 6, 30), "3", at(8), scale=6),
        _row("net_income", dt.date(2026, 6, 30), "2000000", at(8)),
    ]
    got = sc.ratio_field(rows, "operating_cash_flow", "net_income", "obs")
    assert isinstance(got, sc.FieldValue) and got.value == D("1.5000")


def _note(acc: str, known: dt.datetime, value: str | None, **kw: Any) -> dict[str, Any]:
    return {
        "accession": acc,
        "known_at": known,
        "period_end": dt.date(2026, 6, 30),
        "value": None if value is None else D(value),
        "status": None,
        "dash": None,
        **kw,
    }


def test_debt_maturity_coverage_reads_the_year_one_note_bucket() -> None:
    cash = [_row("cash", dt.date(2026, 6, 30), "5", at(8), scale=6)]
    got = sc.debt_maturity_field(cash, [_note("a", at(8), "2500000")])
    assert isinstance(got, sc.FieldValue) and got.value == D("2.0000")


def test_zero_debt_due_in_year_one_is_missing_with_a_reason_not_zero() -> None:
    cash = [_row("cash", dt.date(2026, 6, 30), "5", at(8), scale=6)]
    for note in (_note("a", at(8), "0"), _note("a", at(8), None, dash="true")):
        got = sc.debt_maturity_field(cash, [note])
        assert got == sc.Missing("no debt due in year 1")


def test_debt_maturity_review_bucket_in_latest_filing_does_not_fall_back() -> None:
    cash = [_row("cash", dt.date(2026, 6, 30), "5", at(8), scale=6)]
    notes = [_note("old", at(2), "1000000"), _note("new", at(8), "2000000", status="review")]
    got = sc.debt_maturity_field(cash, notes)
    assert isinstance(got, sc.Missing) and "review" in got.reason


def test_missing_reason_is_shown_in_the_exclusion() -> None:
    u = sc.Universe(
        "AAA", D("1"), D("100"), {"debt_maturity_coverage": sc.Missing("no debt due in year 1")}
    )
    out = sc.screen([u], {"min_debt_maturity_coverage": D("1")})
    assert out["excluded"][0]["kind"] == "missing"
    assert "no debt due in year 1" in out["excluded"][0]["reasons"][0]["reason"]


# --------------------------------------------------------------------------- 4.27 look-through
def _fund_row(ticker: str, isin: str | None, line_isin: str | None = None) -> dict[str, Any]:
    return {
        "fund_ticker": "FUND",
        "held_ticker": ticker,
        "issuer_name": "Alpha",
        "pct_value": D("5"),
        "report_period_end": dt.date(2026, 3, 31),
        "known_at": at(5),
        "accession": "acc",
        "row_index": 1,
        "line_isin": line_isin,
        "instrument_isin": isin,
    }


def test_fund_line_shared_by_two_share_classes_is_counted_once() -> None:
    rows = [_fund_row("ALPHA_B", "US0000000002"), _fund_row("ALPHA_A", "US0000000019")]
    lines = lt.assign_fund_lines(rows, {"ALPHA_A", "ALPHA_B", "FUND"})
    assert len(lines) == 1
    assert lines[0].held_ticker == "ALPHA_A"  # lowest ticker, deterministic
    assert lines[0].note == "shared by 2 held share classes; not split"
    holdings = [
        cr.Holding("FUND", D("1000"), "US", None),
        cr.Holding("ALPHA_A", D("500"), "US", None),
        cr.Holding("ALPHA_B", D("500"), "US", None),
    ]
    out = lt.build(holdings, lines, [], [], at(9), None)
    reached = sum(D(e["fund_weight"]) for e in out["exposures"])
    assert reached == D("0.5") * D("0.05")  # fund weight 0.5 x 5 %, not doubled
    assert out["funds"][0]["notes"] == ["shared by 2 held share classes; not split"]


def test_exact_isin_match_picks_the_share_class_without_a_note() -> None:
    rows = [
        _fund_row("ALPHA_A", "US0000000019", line_isin="US0000000002"),
        _fund_row("ALPHA_B", "US0000000002", line_isin="US0000000002"),
    ]
    lines = lt.assign_fund_lines(rows, {"ALPHA_A", "ALPHA_B"})
    assert [(ln.held_ticker, ln.note) for ln in lines] == [("ALPHA_B", None)]


def _rel(
    share: str, period: str, end: dt.date, kind: str | None, basis: str | None, **kw: Any
) -> lt.Relationship:
    return lt.Relationship(
        "AAA",
        "customer",
        kw.get("key", "acme"),
        "Acme",
        False,
        D(share),
        basis,
        period,
        at(3),
        None,
        kind,
        end,
    )


def test_concentrated_customer_uses_latest_period_and_revenue_denominator_only() -> None:
    holdings = [cr.Holding("AAA", D("100"), "US", None)]
    old_big = _rel("0.30", "FY2024", dt.date(2024, 12, 31), "total_revenue", None)
    new_small = _rel("0.05", "FY2025", dt.date(2025, 12, 31), "total_revenue", None)
    out = lt.build(holdings, [], [old_big, new_small], [], at(9), None)
    assert out["totals"]["concentrated_customer_holdings"] == []
    assert [r["fiscal_period"] for r in out["relationships"]] == ["FY2025"]

    purchases = _rel("0.40", "FY2025", dt.date(2025, 12, 31), "purchases", None, key="other")
    out = lt.build(holdings, [], [purchases], [], at(9), None)
    assert out["totals"]["concentrated_customer_holdings"] == []
    legacy = _rel("0.40", "FY2025", dt.date(2025, 12, 31), None, "issuer_revenue", key="third")
    out = lt.build(holdings, [], [legacy], [], at(9), None)
    assert out["totals"]["concentrated_customer_holdings"] == ["AAA"]


# ----------------------------------------------------------------------------- 2.6 pair review
def test_pair_review_accepts_only_pairs_the_revision_view_produces() -> None:
    a, b, c = val(at(5), "100"), val(at(6), "105"), val(at(7), "110")
    values = [a, b, c]
    now = at(9)
    assert er.is_revision_pair(values, now, a.id, b.id)
    assert er.is_revision_pair(values, now, b.id, c.id)
    assert not er.is_revision_pair(values, now, a.id, c.id)  # not adjacent
    assert not er.is_revision_pair(values, now, b.id, a.id)  # reversed
    assert issubclass(er.PairRejected, ValueError)


# ------------------------------------------------------------------------ 3.5 release basis
def test_release_uses_an_item_202_filing_within_75_days_else_the_fallback() -> None:
    end = dt.date(2026, 6, 30)
    got, basis = er.release_for(end, [("2.02, 9.01", at(7, 25)), ("1.01", at(7, 1))])
    assert (got, basis) == (at(7, 25), "earnings_release")
    too_late = [("2.02", at(10, 1))]
    before = [("2.02", at(6, 1))]
    for filings in (too_late, before, []):
        got, basis = er.release_for(end, filings)
        assert basis == "period_end_fallback"
        assert got == dt.datetime.combine(end, dt.time.max, dt.UTC)


# ---------------------------------------------------------------------------- 6.5 evidence
class _Tx:
    async def start(self) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


class _Conn:
    def __init__(self) -> None:
        self.evidence = itertools.count(100)
        self.value_args: list[tuple[Any, ...]] = []
        self.evidence_args: list[tuple[Any, ...]] = []

    def transaction(self) -> _Tx:
        return _Tx()

    async def fetchrow(self, sql: str, *args: Any) -> None:
        return None

    async def fetchval(self, sql: str, *args: Any) -> int | None:
        if "insert into public.source_evidence" in sql:
            self.evidence_args.append(args)
            return next(self.evidence)
        if "insert into public.source_document_version" in sql:
            return 10
        if "insert into public.source_document(" in sql:
            return 1
        if "insert into public.public_estimate_snapshot" in sql:
            return 5
        return None

    async def execute(self, sql: str, *args: Any) -> None:
        if "insert into public.estimate_value" in sql:
            self.value_args.append(args)


def _extracted(original: str, start: int) -> ex.ExtractedValue:
    return ex.ExtractedValue(
        metric="Revenue",
        segment=None,
        segment_taxonomy=None,
        non_additive=False,
        restated_prior=False,
        fiscal_period="FY2026Q2",
        period_start=dt.date(2026, 4, 1),
        period_end=dt.date(2026, 6, 30),
        fiscal_calendar=None,
        currency="USD",
        unit="amount",
        scale=6,
        basis="GAAP",
        basis_definition=None,
        mean=D("100"),
        median=None,
        high=None,
        low=None,
        analyst_count=10,
        original_string=original,
        table_index=0,
        row_index=3,
        start=start,
        end=start + len(original),
        issues=(),
    )


def test_stored_estimate_values_carry_evidence_from_the_stored_file() -> None:
    extraction = ex.Extraction(
        kind="html",
        content_hash="h",
        headings=(),
        footnotes=(),
        panel_description=None,
        panel_size=None,
        panel_method=None,
        fiscal_calendar=None,
        published_on=None,
        values=(_extracted("Revenue | 100", 10), _extracted("EBITDA | 20", 40)),
        issues=(),
    )
    conn = _Conn()
    out = asyncio.run(
        store.store_extraction(
            conn,  # type: ignore[arg-type]
            issuer_id=1,
            source_id=2,
            extraction=extraction,
            fetched_at=at(7),
            document=store.SourceFile("ir", "https://example.com/consensus", b"<html/>"),
        )
    )
    assert out.snapshot_id == 5 and out.stored_values == 2
    assert [a[0] for a in conn.evidence_args] == [10, 10]  # the new document version
    assert [a[2] for a in conn.evidence_args] == ["Revenue | 100", "EBITDA | 20"]
    assert [a[3] for a in conn.evidence_args] == [10, 40]  # span offsets
    assert [a[-1] for a in conn.value_args] == [100, 101]  # evidence_id on each value
