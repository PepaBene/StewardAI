"""The research data catalog, the benchmark's case set and the shared figure envelope.

No database: these hold the catalog to the real tool list, the benchmark to the catalog, and
the code-owned helpers (envelope, price thinning, drawdown) to their contracts.
"""

import datetime as dt
import inspect
from decimal import Decimal
from pathlib import Path

import pytest

from steward.chat import benchmark, catalog, envelope
from steward.chat import research_tools as rt
from steward.chat.tools import TOOL_DEFINITIONS, ChatTools

TOOL_NAMES = {t["name"] for t in TOOL_DEFINITIONS}
DOC = Path(__file__).resolve().parents[3] / "docs" / "plans" / "ai_research_capability_map.md"
START, END = "<!-- capability-map:start -->", "<!-- capability-map:end -->"


def test_catalog_matches_the_real_tools() -> None:
    assert catalog.problems(TOOL_NAMES) == []


def test_every_tool_has_a_handler_and_a_closed_schema() -> None:
    for tool in TOOL_DEFINITIONS:
        assert callable(getattr(ChatTools, f"_tool_{tool['name']}", None)), tool["name"]
        schema = tool["input_schema"]
        assert schema["type"] == "object" and schema["additionalProperties"] is False, tool["name"]
        assert tool["description"].strip(), tool["name"]


def test_tool_schema_properties_match_handler_parameters() -> None:
    """A property the schema offers but the handler cannot take would fail at call time."""
    for tool in TOOL_DEFINITIONS:
        handler = getattr(ChatTools, f"_tool_{tool['name']}")
        params = set(inspect.signature(handler).parameters) - {"self"}
        props = set(tool["input_schema"].get("properties", {}))
        required = set(tool["input_schema"].get("required", []))
        assert props <= params, (tool["name"], props - params)
        assert required <= props, tool["name"]


def test_capability_map_document_is_current() -> None:
    text = DOC.read_text(encoding="utf-8")
    block = text.split(START, 1)[1].split(END, 1)[0].strip()
    assert block == catalog.capability_map().strip(), (
        "docs/plans/ai_research_capability_map.md is out of date: regenerate the block between "
        "the capability-map markers from steward.chat.catalog.capability_map()"
    )


def test_benchmark_cases_are_valid_and_cover_the_catalog() -> None:
    cases = benchmark.load_cases()
    assert len(cases) >= 30
    assert benchmark.problems(cases, TOOL_NAMES) == []


def test_score_trace_reports_missed_groups_failed_calls_and_actions() -> None:
    case = next(c for c in benchmark.load_cases() if c.id == "thesis-04")
    perfect = [{"tool": "strategy_rules", "ok": True}, {"tool": "over_cap_positions", "ok": True}]
    assert benchmark.score_trace(case, perfect).failure is None
    assert benchmark.score_trace(case, perfect).tool_recall == 1.0
    partial = benchmark.score_trace(case, [{"tool": "strategy_rules", "ok": True}])
    assert partial.tool_recall == 0.5 and partial.failure == "retrieval"
    assert partial.missed_groups == [["portfolio_overview", "over_cap_positions"]]
    broke = benchmark.score_trace(case, [*perfect, {"tool": "holding", "ok": False}])
    assert broke.failed_calls == ["holding"] and broke.failure == "retrieval"
    acted = benchmark.score_trace(case, [*perfect, {"tool": "propose_job", "ok": True}])
    assert acted.unexpected_actions == ["propose_job"]


def test_summarize_groups_recall_by_category() -> None:
    cases = benchmark.load_cases()
    quick = next(c for c in cases if c.id == "quick-fact-01")
    scores = [benchmark.score_trace(quick, [{"tool": "portfolio_overview", "ok": True}])]
    summary = benchmark.summarize(scores, cases)
    assert summary["tool_recall"] == 1.0 and summary["by_category"] == {"quick_fact": 1.0}


# ----------------------------------------------------------------------------- envelope


def test_absent_figures_carry_no_value_and_a_reason() -> None:
    for status in ("missing", "not_applicable", "failed"):
        d = envelope.absent(status, "no filing", unit="money")
        assert d["value"] is None and d["status"] == status and d["reason"] == "no filing"


def test_datum_never_turns_absence_into_zero() -> None:
    assert envelope.datum(0, status="missing")["value"] is None
    assert envelope.datum(0)["value"] == 0
    stale = envelope.datum(Decimal("1.5"), status="stale", as_of=dt.date(2026, 1, 2))
    assert stale["value"] == 1.5 and stale["as_of"] == "2026-01-02"


def test_age_status() -> None:
    today = dt.date(2026, 10, 10)
    assert envelope.age_status(None, today, 7) == "missing"
    assert envelope.age_status(dt.date(2026, 10, 5), today, 7) == "ok"
    assert envelope.age_status(dt.date(2026, 10, 1), today, 7) == "stale"
    assert envelope.age_status(dt.datetime(2026, 10, 9, tzinfo=dt.UTC), today, 3) == "ok"


def test_clamp_limit_rejects_junk() -> None:
    assert envelope.clamp_limit(None, 10, 25) == 10
    assert envelope.clamp_limit(True, 10, 25) == 10
    assert envelope.clamp_limit(0, 10, 25) == 10
    assert envelope.clamp_limit(-3, 10, 25) == 10
    assert envelope.clamp_limit("7", 10, 25) == 10
    assert envelope.clamp_limit(1000, 10, 25) == 25
    assert envelope.clamp_limit(7, 10, 25) == 7


def test_stamp_adds_provenance_but_not_to_errors() -> None:
    now = dt.datetime(2026, 10, 10, 12, 0, tzinfo=dt.UTC)
    assert envelope.stamp({"a": 1}, "t", now) == {
        "a": 1,
        "tool": "t",
        "retrieved_at": "2026-10-10T12:00:00+00:00",
    }
    assert envelope.stamp({"error": "x"}, "t", now) == {"error": "x"}


# ----------------------------------------------------------------------------- helpers


def test_max_drawdown_is_the_deepest_peak_to_trough_fall() -> None:
    d = dt.date(2026, 1, 1)
    closes = [(d + dt.timedelta(days=i), Decimal(c)) for i, c in enumerate([100, 120, 90, 110, 60])]
    assert rt.max_drawdown(closes) == pytest.approx(-0.5)
    assert rt.max_drawdown(closes[:1]) is None
    assert rt.max_drawdown([(d, Decimal(1)), (d, Decimal(2))]) == 0.0


def test_thin_keeps_short_series_and_caps_long_ones() -> None:
    start = dt.date(2020, 1, 1)
    short = [(start + dt.timedelta(days=i), Decimal(i + 1)) for i in range(100)]
    assert rt._thin(short) == (short, "daily")
    long = [(start + dt.timedelta(days=i), Decimal(i + 1)) for i in range(1500)]
    points, interval = rt._thin(long)
    assert interval in ("weekly", "monthly") and len(points) <= rt.MAX_PRICE_POINTS
    assert points[-1] == long[-1]  # the newest close always survives


def test_shrink_cuts_lists_and_long_text() -> None:
    value, cut = rt._shrink({"rows": list(range(30)), "text": "x" * 2000}, 5)
    assert value["rows"] == [0, 1, 2, 3, 4] and cut
    assert value["text"].endswith("…") and len(value["text"]) == rt.TEXT_CHARS + 1
    assert rt._shrink({"rows": [1, 2]}, 5) == ({"rows": [1, 2]}, False)


def test_parse_cutoff() -> None:
    assert rt.parse_cutoff(None) is None
    assert rt.parse_cutoff("2026-03-01") == dt.datetime(2026, 3, 1, 23, 59, 59, 999999, dt.UTC)
    with pytest.raises(ValueError):
        rt.parse_cutoff("last week")
