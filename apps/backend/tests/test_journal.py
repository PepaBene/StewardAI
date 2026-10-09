"""Phase 7 (decision journal, scorecard, thesis fields, data-quality cockpit): hand-worked cases
for the pure parts. Expected figures are written out in the comments, not taken from the code."""

import datetime as dt

import pytest
from pydantic import ValidationError

from steward.config.thesis import (
    ThesesConfig,
    ThesisBaseRate,
    ThesisConfig,
    ThesisVariantPerception,
)
from steward.journal.entries import JournalEntry, JournalEntryIn
from steward.journal.export import journal_csv
from steward.journal.scorecard import (
    MIN_DECISIONS,
    DecisionOutcome,
    build_scorecard,
)
from steward.journal.service import first_hit, sell_target_of
from steward.quality.cockpit import (
    QualityIssue,
    affected_weight,
    fund_weight_problem,
    rank,
)

D = dt.date


def outcome(i: int, decision: str, now: float | None, **kw: object) -> DecisionOutcome:
    base: dict[str, object] = {
        "id": i,
        "ticker": f"T{i}",
        "decision": decision,
        "decided_on": D(2026, 3, 1),
        "price_at_decision": 100.0,
        "price_now": now,
    }
    return DecisionOutcome(**{**base, **kw})  # type: ignore[arg-type]


def card(outcomes: list[DecisionOutcome]):
    return build_scorecard(outcomes, since=D(2026, 1, 1), until=D(2026, 12, 31))


def section(sc, id_: str):
    return next(s for s in sc.sections if s.id == id_)


class TestScorecard:
    def test_the_scorecard_can_be_limited_to_one_security_and_one_reason(self) -> None:
        rows = [
            outcome(1, "buy", 110.0, ticker="AAA", main_reason="valuation"),
            outcome(2, "buy", 120.0, ticker="AAA", main_reason="quality"),
            outcome(3, "buy", 90.0, ticker="BBB", main_reason="valuation"),
        ]
        both = build_scorecard(
            rows, since=D(2026, 1, 1), until=D(2026, 12, 31), ticker="AAA", reason="valuation"
        )
        assert both.entries == 1
        assert both.filtered_by == ["security AAA", "main reason valuation"]
        only_ticker = build_scorecard(
            rows, since=D(2026, 1, 1), until=D(2026, 12, 31), ticker="AAA"
        )
        assert only_ticker.entries == 2
        assert card(rows).filtered_by == []

    def test_returns_by_conviction_median_and_win_share(self) -> None:
        # Five buys at 100 now 110/120/90/100/130 -> changes +10%, +20%, -10%, 0, +30%.
        # Sorted -10, 0, 10, 20, 30 -> median +10%. Wins are the strictly positive: 3 of 5.
        buys = [
            outcome(i, "buy", now, conviction=4)
            for i, now in enumerate([110.0, 120.0, 90.0, 100.0, 130.0], start=1)
        ]
        s = section(card(buys), "by_conviction")
        assert s.status == "ok" and s.n == 5
        (row,) = s.rows
        assert row.label == "4" and row.n == 5
        assert row.median_change == pytest.approx(0.10)
        assert row.share == pytest.approx(0.6)

    def test_below_the_minimum_a_section_says_too_few_and_gives_no_figure(self) -> None:
        sells = [outcome(i, "sell", 90.0) for i in range(1, MIN_DECISIONS)]
        s = section(card(sells), "after_sell")
        assert s.status == "too_few" and s.n == MIN_DECISIONS - 1
        assert s.rows == [] and "Too few decisions" in s.note

    def test_after_a_sale_a_fall_counts_as_right(self) -> None:
        # Sold at 100; now 90/80/110/100/95 -> -10, -20, +10, 0, -5 %. Median -5 %.
        # The sale was right where the price fell: 3 of 5.
        sells = [
            outcome(i, "sell", now)
            for i, now in enumerate([90.0, 80.0, 110.0, 100.0, 95.0], start=1)
        ]
        (row,) = section(card(sells), "after_sell").rows
        assert row.median_change == pytest.approx(-0.05)
        assert row.share == pytest.approx(0.6)

    def test_sell_targets_reached_and_median_days(self) -> None:
        # Five buys with a target of 150; three reached it, after 10, 30 and 50 days.
        decided = D(2026, 3, 1)
        hits = [10, 30, 50, None, None]
        buys = [
            outcome(
                i,
                "buy",
                120.0,
                sell_target=150.0,
                hit_on=decided + dt.timedelta(days=d) if d else None,
            )
            for i, d in enumerate(hits, start=1)
        ]
        s = section(card(buys), "sell_targets")
        assert s.status == "ok"
        assert s.rows[0].share == pytest.approx(0.6)
        assert "median time to reach one was 30 days" in s.note

    def test_a_price_in_another_currency_is_not_compared(self) -> None:
        o = outcome(1, "buy", 120.0, currency_at_decision="USD", currency_now="EUR")
        assert o.change is None

    def test_reviews_and_decisions_outside_the_window_are_left_out(self) -> None:
        old = outcome(1, "buy", 110.0, decided_on=D(2025, 1, 1))
        review = outcome(2, "review", 110.0)
        assert card([old, review]).entries == 0

    def test_switch_section_says_why_it_is_unavailable(self) -> None:
        sc = card([])
        assert section(sc, "switches").status == "unavailable"
        assert "switch log" in section(sc, "switches").note
        assert section(sc, "risk_held").status == "too_few"

    def test_risk_held_counts_buys_that_reached_their_bear_case(self) -> None:
        # Bought at 100 with a bear case of 70; lows of 60, 65, 80, 90, 95: two reached it.
        lows = [60.0, 65.0, 80.0, 90.0, 95.0]
        buys = [
            outcome(i, "buy", 100.0, bear_case=70.0, low_since=low)
            for i, low in enumerate(lows, start=1)
        ]
        s = section(card(buys), "risk_held")
        assert s.status == "ok" and s.n == 5
        assert s.rows[0].share == pytest.approx(0.4)
        assert s.rows[1].median_change == pytest.approx(-0.2)  # median of -40,-35,-20,-10,-5 %

    def test_passed_ideas_beating_the_buys_suggests_a_look_at_the_entry_bar(self) -> None:
        bought = [outcome(i, "buy", 105.0) for i in range(1, 6)]
        passed = [outcome(i, "pass", 130.0) for i in range(6, 11)]
        sc = card([*bought, *passed])
        assert section(sc, "bought_vs_passed").status == "ok"
        assert any("passed on did better" in s for s in sc.suggestions)


class TestSellTargetHelpers:
    def test_first_hit_is_strictly_after_the_decision_day(self) -> None:
        series = [(D(2026, 3, 1), 160.0), (D(2026, 3, 2), 140.0), (D(2026, 3, 3), 151.0)]
        assert first_hit(series, D(2026, 3, 1), 150.0) == D(2026, 3, 3)
        assert first_hit(series, D(2026, 3, 3), 150.0) is None

    def test_target_is_read_from_the_thesis_as_it_stood(self) -> None:
        entry = JournalEntry(
            id=1, scope="security", ticker="AAPL", decision="buy", parent_id=None,
            decided_on=D(2026, 3, 1), hypothesis="h", valuation_assumptions=None,
            catalysts=[], disconfirming=[], review_date=None, conviction=None, main_reason=None,
            price_at_decision=100.0, price_currency="USD",
            snapshot={"thesis": {"price_target": {"sell_target": 150, "currency": "USD"}}},
            created_at=dt.datetime(2026, 3, 1, tzinfo=dt.UTC),
        )  # fmt: skip
        assert sell_target_of(entry) == (150.0, "USD")
        bare = entry.model_copy(update={"snapshot": {"thesis": None}})
        assert sell_target_of(bare) == (None, None)


class TestJournalInput:
    def test_blank_lines_are_dropped_and_conviction_is_bounded(self) -> None:
        body = JournalEntryIn(
            decision="buy", ticker=" AAPL ", hypothesis="x", catalysts=[" a ", " "]
        )
        assert body.catalysts == ["a"] and body.ticker == "AAPL"
        with pytest.raises(ValidationError):
            JournalEntryIn(decision="buy", ticker="A", hypothesis="x", conviction=6)

    def test_unknown_decision_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            JournalEntryIn(decision="yolo", ticker="A", hypothesis="x")  # type: ignore[arg-type]


class TestThesisFields:
    def full(self) -> ThesisConfig:
        return ThesisConfig(
            ticker="AAPL",
            why_owned="Services mix",
            implied_growth=0.08,
            base_rate=ThesisBaseRate(share_achieved=0.2, comparison="US large caps", source="own"),
            pre_mortem=["a", "b", "c"],
            variant_perception=ThesisVariantPerception(belief="b", disproof="d"),
            conviction=4,
            main_reason="quality",
        )

    def test_old_theses_still_load_and_list_every_missing_requirement(self) -> None:
        thesis = ThesisConfig(ticker="AAPL", why_owned="x")
        assert len(thesis.missing_requirements()) == 6

    def test_a_complete_thesis_has_no_gaps(self) -> None:
        assert self.full().missing_requirements() == []

    def test_pre_mortem_has_at_most_three_non_blank_reasons(self) -> None:
        with pytest.raises(ValidationError):
            ThesisConfig(ticker="A", why_owned="x", pre_mortem=["a", "b", "c", "d"])
        with pytest.raises(ValidationError):
            ThesisConfig(ticker="A", why_owned="x", pre_mortem=["a", " "])

    def test_conviction_and_share_are_bounded(self) -> None:
        with pytest.raises(ValidationError):
            ThesisConfig(ticker="A", why_owned="x", conviction=0)
        with pytest.raises(ValidationError):
            ThesisBaseRate(share_achieved=1.2, comparison="c", source="s")

    def test_round_trip_through_the_stored_body(self) -> None:
        body = ThesesConfig(theses=[self.full()])
        assert ThesesConfig.model_validate(body.model_dump(mode="json")) == body


class TestChatTools:
    def test_each_declared_analytics_tool_has_a_handler(self) -> None:
        from steward.chat.tools import TOOL_DEFINITIONS, ChatTools

        declared = {t["name"] for t in TOOL_DEFINITIONS}
        wanted = {
            "stress_test", "theme_exposure", "decision_scorecard", "fund_overlap",
            "performance_explain", "event_paths", "risk_forecast", "past_episodes",
            "factor_tilts", "idea_factor_fit",
        }  # fmt: skip
        assert wanted <= declared
        assert all(hasattr(ChatTools, f"_tool_{name}") for name in wanted)

    def test_theme_tool_offers_only_kinds_the_engine_understands(self) -> None:
        from steward.chat.analytics_tools import SELECTOR_KINDS
        from steward.decision.themes import SelectorKind

        assert set(SELECTOR_KINDS) == set(SelectorKind.__args__)  # type: ignore[attr-defined]


class TestExport:
    def entry(self, **kw: object) -> JournalEntry:
        base: dict[str, object] = dict(
            id=1, scope="security", ticker="AAPL", decision="buy", parent_id=None,
            decided_on=D(2026, 3, 1), hypothesis="Services mix", valuation_assumptions=None,
            catalysts=["Q4", "launch"], disconfirming=[], review_date=None, conviction=4,
            main_reason="quality", price_at_decision=100.0, price_currency="USD", snapshot={},
            created_at=dt.datetime(2026, 3, 1, tzinfo=dt.UTC),
        )  # fmt: skip
        return JournalEntry(**{**base, **kw})  # type: ignore[arg-type]

    def test_csv_has_a_header_one_line_per_entry_and_joined_lists(self) -> None:
        text = journal_csv([self.entry()])
        header, line = text.strip().split("\n")
        assert header.startswith("id,decided_on,scope,ticker,decision")
        assert "Q4 | launch" in line and line.startswith("1,2026-03-01,security,AAPL,buy,")

    def test_missing_values_are_empty_not_filled(self) -> None:
        line = journal_csv([self.entry(price_at_decision=None, price_currency=None)]).split("\n")[1]
        assert ",,," in line

    def test_a_formula_typed_into_the_journal_is_neutralised(self) -> None:
        text = journal_csv([self.entry(hypothesis="=HYPERLINK(1)")])
        assert "'=HYPERLINK(1)" in text


    def _report(self):  # type: ignore[no-untyped-def]
        from steward.journal.export import Manifest, OwnerReport, figure_sources_for
        from steward.quality.cockpit import Check, Cockpit

        entries = [self.entry(hypothesis="Cesky č ✓", main_reason="quality")]
        card = build_scorecard(
            [outcome(i, "buy", 110.0, conviction=3) for i in range(1, 6)],
            since=D(2026, 1, 1),
            until=D(2026, 12, 31),
        )
        cockpit = Cockpit(
            as_of=D(2026, 10, 8), portfolio_value_base=None, issues=[], affected_weight=0.0,
            checks=[Check(kind="stale_price", title="Stale", ran=True, denominator="3 holdings")],
            not_covered=[],
        )  # fmt: skip
        sources = figure_sources_for(entries, card, cockpit, D(2026, 10, 7))
        manifest = Manifest(
            generated_at=dt.datetime(2026, 10, 8, tzinfo=dt.UTC), sources=["s"],
            coverage={"a": "b"}, figure_sources=sources, method_versions={}, notes=["n"],
        )  # fmt: skip
        return OwnerReport(manifest=manifest, journal=entries, scorecard=card, cockpit=cockpit)

    def test_every_scorecard_figure_and_entry_has_its_own_source_line(self) -> None:
        sources = self._report().manifest.figure_sources
        assert "journal.entry.1" in sources
        assert "Journal entries 1, 2, 3, 4, 5" in sources["scorecard.by_conviction.3"]
        assert "cockpit.stale_price" in sources

    def test_the_pdf_is_a_pdf_and_odd_characters_do_not_break_it(self) -> None:
        from steward.journal.export import report_pdf

        data = report_pdf(self._report())
        assert data[:5] == b"%PDF-" and len(data) > 1000

    def test_the_html_escapes_what_was_typed(self) -> None:
        from steward.journal.export import report_html

        r = self._report()
        r.journal[0].main_reason = "<script>x</script>"  # type: ignore[assignment]
        assert "<script>x" not in report_html(r)


class TestCockpit:
    def issue(self, subject: str, weight: float | None, kind: str = "stale_price") -> QualityIssue:
        return QualityIssue(kind=kind, subject=subject, detail="d", weight=weight, fix="f")  # type: ignore[arg-type]

    def test_issues_are_ranked_by_affected_weight_with_unweighted_last(self) -> None:
        ranked = rank(
            [
                self.issue("B", 0.05),
                self.issue("src", None, "source_conflict"),
                self.issue("A", 0.30),
            ]
        )
        assert [i.subject for i in ranked] == ["A", "B", "src"]

    def test_a_holding_with_two_issues_is_counted_once(self) -> None:
        issues = [self.issue("A", 0.30), self.issue("A", 0.30, "thesis_gap"), self.issue("B", 0.05)]
        assert affected_weight(issues, {"A": 0.30, "B": 0.05, "C": 0.65}) == pytest.approx(0.35)

    def test_fund_weights_must_add_to_about_one(self) -> None:
        assert fund_weight_problem(0.99) is None
        assert fund_weight_problem(1.02) is None
        assert "add to 105.0%" in (fund_weight_problem(1.05) or "")
        assert "add to 88.0%" in (fund_weight_problem(0.88) or "")
