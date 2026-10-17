"""Monthly and quarterly digests, and the strategy deviations every digest carries."""

import datetime as dt
from decimal import Decimal

import pytest

from steward.config.jobs import DigestSettings, JobsConfig
from steward.config.strategy import (
    CashWeightRule,
    CategoryTarget,
    PositionCountRule,
    PositionWeightRule,
    StrategyConfig,
    TargetBand,
)
from steward.config.verification import ALL_CLAIM_TARGETS
from steward.jobs.registry import JOB_HANDLERS
from steward.news.digest import (
    DEFAULT_PERIOD,
    DigestBody,
    Material,
    digest_title,
    headline_for,
    period_text,
    render_text,
)
from steward.news.digest_strategy import (
    Reading,
    classify_change,
    strategy_deviations,
)
from steward.portfolio.drift import DriftReport, HoldingValue, compute_drift

END = dt.datetime(2026, 10, 1, 7, 30, tzinfo=dt.UTC)


def test_the_four_kinds_cover_their_windows() -> None:
    s = DigestSettings()
    assert s.window("daily") == dt.timedelta(hours=24)
    assert s.window("weekly") == dt.timedelta(days=7)
    assert s.window("monthly") == dt.timedelta(days=30)
    assert s.window("quarterly") == dt.timedelta(days=91)


def test_a_window_is_named_in_days_or_hours() -> None:
    s = DigestSettings()
    assert period_text(s.window("daily")) == "24 hours"
    assert period_text(s.window("monthly")) == "30 days"
    assert period_text(s.window("quarterly")) == "91 days"
    assert DEFAULT_PERIOD == {
        kind: period_text(s.window(kind)) for kind in ("daily", "weekly", "monthly", "quarterly")
    }


def test_the_long_digests_raise_the_summary_floor_and_cap_the_material() -> None:
    s = DigestSettings()
    assert [s.summarised_floor(k) for k in ("daily", "weekly", "monthly", "quarterly")] == [
        2,
        2,
        3,
        3,
    ]
    assert DigestSettings(min_severity_summarised=3).summarised_floor("monthly") == 3
    assert s.material_cap("weekly") is None
    assert s.material_cap("monthly") == 40
    assert s.material_cap("quarterly") == 60


def test_titles_and_headlines_name_the_period() -> None:
    assert digest_title("monthly", END.date()) == "Monthly digest, 2026-10-01"
    assert digest_title("quarterly", END.date()) == "Quarterly digest, 2026-10-01"
    empty = Material({}, {}, {}, set(), set())
    text, nothing = headline_for("monthly", empty)
    assert nothing
    assert text.endswith("in the last 30 days.")
    assert headline_for("quarterly", empty)[0].endswith("in the last 91 days.")
    body = DigestBody(
        kind="quarterly",
        window_start=END - dt.timedelta(days=91),
        window_end=END,
        nothing_material=True,
        headline=text,
    )
    assert render_text(body, None, markdown=True).startswith("# Quarterly digest, 2026-10-01")


def test_the_new_digests_are_scheduled_run_and_checked() -> None:
    jobs = JobsConfig()
    schedules = jobs.schedules()
    assert schedules["monthly_digest"].cron == "30 7 1 * *"
    assert schedules["quarterly_digest"].cron == "0 8 1 1,4,7,10 *"
    assert "monthly_digest" in JOB_HANDLERS
    assert "quarterly_digest" in JOB_HANDLERS
    assert {"monthly_digest", "quarterly_digest"} <= set(ALL_CLAIM_TARGETS)


# ---------------------------------------------------------------------------
# Strategy deviations
# ---------------------------------------------------------------------------


def strategy() -> StrategyConfig:
    return StrategyConfig(
        base_currency="USD",
        categories=["tier"],
        classifications={"AAA": {"tier": "core"}, "BBB": {"tier": "growth"}},
        cash_target=TargetBand(target_weight=0.05, band=0.05),
        target_categories=[CategoryTarget(dimension="tier", bucket="core", target_weight=0.6)],
        rules=[
            CashWeightRule(
                id="2c",
                type="cash_weight",
                operator="<=",
                value=0.10,
                description="Cash above 10% goes to the best bench idea or the core ETFs",
            ),
            PositionWeightRule(
                id="max-position", type="position_weight", operator="<=", value=0.35
            ),
            PositionCountRule(id="count", type="position_count", operator="<=", value=2),
        ],
    )


def drift(cash: int, aaa: int, bbb: int, ccc: int = 0) -> DriftReport:
    holdings = [
        HoldingValue(t, "USD", Decimal(v))
        for t, v in (("AAA", aaa), ("BBB", bbb), ("CCC", ccc))
        if v
    ]
    return compute_drift(holdings, Decimal(cash), strategy())


def by_key(rows: list) -> dict:
    return {r.key: r for r in rows}


def test_deviations_compare_the_start_and_end_of_the_period() -> None:
    start = drift(cash=22, aaa=40, bbb=38)  # cash 22%, core 40%, largest 38%
    end = drift(cash=27, aaa=40, bbb=33)  # cash 27%, core 40%, largest 40%
    rows = by_key(strategy_deviations(start, end, strategy()))

    cash = rows["cash"]
    assert (cash.status, cash.change) == ("outside", "worse")
    assert cash.start_value == pytest.approx(0.22)
    assert cash.end_value == pytest.approx(0.27)
    assert cash.target == "5% (band +/-5%)"
    assert cash.implication.startswith("Cash is 27% against 5% (band +/-5%), up from 22%.")
    assert "outside the band set in your strategy" in cash.implication

    rule = rows["2c"]
    assert (rule.status, rule.change) == ("outside", "worse")
    assert rule.target == "at most 10%"
    assert "Your rule 2c: Cash above 10% goes to the best bench idea or the core ETFs." in (
        rule.implication
    )

    core = rows["category:tier:core"]
    assert core.label == "Core tier"
    assert core.status == "outside"  # 40% against 60% +/- 2 pp


def test_a_rule_over_many_positions_shows_its_worst_subject() -> None:
    rows = by_key(strategy_deviations(drift(5, 30, 35, 30), drift(10, 45, 25, 20), strategy()))
    largest = rows["max-position:AAA"]
    assert largest.label == "Largest position (AAA)"
    assert largest.subject == "AAA"
    assert largest.end_value == pytest.approx(0.45)
    assert largest.start_value == pytest.approx(0.3)
    assert (largest.status, largest.change) == ("outside", "new")


def test_a_count_rule_reads_in_positions() -> None:
    rows = by_key(strategy_deviations(drift(5, 40, 40), drift(5, 30, 30, 30), strategy()))
    count = rows["count"]
    assert count.unit == "count"
    assert (count.start_value, count.end_value) == (2.0, 3.0)
    assert (count.status, count.change) == ("outside", "new")
    assert count.target == "at most 2"


def test_outside_items_come_first_the_furthest_first() -> None:
    rows = strategy_deviations(drift(20, 40, 40), drift(30, 35, 35), strategy())
    statuses = [r.status for r in rows]
    assert statuses == sorted(statuses, key={"outside": 0, "within": 1, "unknown": 2}.get)
    outside = [r for r in rows if r.status == "outside"]
    assert outside[0].key == "category:tier:core"  # 35% against 60%: 23 pp past its band


def test_no_start_snapshot_leaves_the_change_unknown() -> None:
    rows = strategy_deviations(None, drift(27, 40, 33), strategy())
    cash = by_key(rows)["cash"]
    assert cash.start_value is None
    assert cash.change == "unknown"
    assert cash.status == "outside"
    assert "could not be read" in cash.implication


def test_a_window_before_the_first_snapshot_names_the_date_it_reads() -> None:
    first = dt.date(2026, 9, 27)
    rows = strategy_deviations(drift(20, 40, 40), drift(27, 40, 33), strategy(), None, first)
    cash = by_key(rows)["cash"]
    assert cash.start_value == pytest.approx(0.2)
    assert cash.change == "worse"
    assert "up from 20% on 27 Sep, the first portfolio snapshot." in cash.implication


def test_no_snapshot_at_all_gives_unknown_rows() -> None:
    rows = strategy_deviations(None, None, strategy())
    assert rows
    assert {r.status for r in rows} == {"unknown"}
    assert {r.change for r in rows} == {"unknown"}


def reading(value: float, outside: float) -> Reading:
    return Reading(value, outside, outside == 0)


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        (reading(0.05, 0), reading(0.20, 0.10), "new"),
        (reading(0.20, 0.10), reading(0.05, 0), "cleared"),
        (reading(0.20, 0.10), reading(0.25, 0.15), "worse"),
        (reading(0.25, 0.15), reading(0.20, 0.10), "better"),
        (reading(0.20, 0.10), reading(0.202, 0.102), "unchanged"),
        (reading(0.05, 0), reading(0.06, 0), "unchanged"),
        (Reading(None, 0, False), reading(0.2, 0.1), "unknown"),
        (reading(0.2, 0.1), Reading(None, 0, False), "unknown"),
    ],
)
def test_change_classification(before: Reading, after: Reading, expected: str) -> None:
    assert classify_change(before, after, 0.005) == expected


def test_the_strategy_section_is_rendered() -> None:
    rows = strategy_deviations(drift(22, 40, 38), drift(27, 40, 33), strategy())
    body = DigestBody(
        kind="monthly",
        window_start=END - dt.timedelta(days=30),
        window_end=END,
        nothing_material=True,
        headline="Nothing material.",
        strategy=rows,
    )
    md = render_text(body, None, markdown=True)
    assert "## Strategy" in md
    assert "[outside, worse] Cash is 27% against 5% (band +/-5%), up from 22%." in md
    assert "\nSTRATEGY\n" in render_text(body, None, markdown=False)
