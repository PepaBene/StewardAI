"""N8: the segment release gate, the golden cases for the code techniques, and the ops report."""

import datetime as dt
import tempfile
from pathlib import Path

import asyncpg
import pytest

from steward.news.eval_analysis import load_golden, run_all, run_diffs, run_novelty, run_spans
from steward.news.gate import (
    Baseline,
    check,
    load_baseline,
    make_baseline,
    save_baseline,
    wilson_interval,
)
from steward.news.ops import ops_report

NOW = dt.datetime(2026, 10, 10, 12, 0, tzinfo=dt.UTC)


def baseline(**segments: tuple[int, int]) -> Baseline:
    return Baseline(
        "m", "2026-10-01", (90, 100), {k.replace("_", ":", 1): v for k, v in segments.items()}
    )


def test_golden_cases_all_pass() -> None:
    total, failures = run_all()
    assert total >= 20
    assert failures == []


def test_golden_harness_reports_a_wrong_expectation() -> None:
    golden = load_golden()
    golden["spans"][0]["ok"] = False
    golden["diffs"][0]["new"] = 5
    golden["novelty"][0]["expect"] = "repeat"
    assert len(run_spans(golden["spans"])) == 1
    assert len(run_diffs(golden["diffs"])) == 1
    assert len(run_novelty(golden["novelty"])) == 1


def test_gate_passes_without_a_regression() -> None:
    b = baseline(language_de=(18, 20))
    assert check((88, 100), {"language:de": [17, 20]}, b) == []


def test_gate_fails_on_a_clear_segment_drop() -> None:
    b = baseline(language_de=(19, 20))
    failures = check((88, 100), {"language:de": [8, 20]}, b)
    assert len(failures) == 1
    assert "language:de" in failures[0]


def test_gate_ignores_a_drop_inside_the_noise() -> None:
    # 18/20 against a 19/20 baseline: the interval still reaches the baseline rate.
    assert wilson_interval(18, 20)[1] >= 0.95
    assert check((88, 100), {"language:de": [18, 20]}, baseline(language_de=(19, 20))) == []


def test_gate_fails_below_the_pooled_target() -> None:
    assert "pooled" in check((80, 100), {}, None)[0]


def test_gate_fails_when_a_baseline_segment_is_missing_from_the_run() -> None:
    failures = check((90, 100), {}, baseline(region_JP=(12, 12)))
    assert failures and "missing" in failures[0]


def test_gate_does_not_gate_small_segments() -> None:
    assert check((90, 100), {"region:JP": [0, 5]}, baseline(region_JP=(5, 5))) == []


def test_baseline_round_trips() -> None:
    saved = make_baseline("luna", (90, 100), {"region:JP": [11, 12]}, dt.date(2026, 10, 10))
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "baseline.json"
        save_baseline(path, saved)
        assert load_baseline(path) == saved


async def story(pool: asyncpg.Pool, key: str, severity: int, ago_days: int = 1) -> int:
    row = await pool.fetchrow(
        "insert into public.story_cluster (ticker, headline, headline_key, best_tier, severity,"
        " status, first_seen, last_seen) values ('AAA_US_EQ', $1, $1, 1, $2, 'confirmed', $3, $3)"
        " returning id",
        key,
        severity,
        dt.datetime.now(dt.UTC) - dt.timedelta(days=ago_days),
    )
    assert row is not None
    return int(row["id"])


async def alert(pool: asyncpg.Pool, cluster_id: int, status: str) -> None:
    await pool.execute(
        "insert into public.alert (kind, severity, cluster_id, channel, message, status)"
        " values ('news', 3, $1, 'email', 'm', $2)",
        cluster_id,
        status,
    )


async def mark(pool: asyncpg.Pool, cluster_id: int, kind: str) -> None:
    await pool.execute(
        "insert into public.news_feedback (target, target_id, kind) values ('story', $1, $2)",
        str(cluster_id),
        kind,
    )


@pytest.mark.db
async def test_ops_report_costs_useful_events_and_incidents(backend_pool: asyncpg.Pool) -> None:
    pool = backend_pool
    for job_type, cost in (("news_analysis", 0.30), ("news_brief", 0.10), ("portfolio_sync", 9.0)):
        job_id = await pool.fetchval(
            "insert into public.job (job_type, trigger, status) values ($1, 'schedule',"
            " 'succeeded') returning id",
            job_type,
        )
        await pool.execute(
            "insert into public.job_step (job_id, step_index, name, status, cost_usd)"
            " values ($1, 0, 'step', 'succeeded', $2)",
            job_id,
            cost,
        )
    alerted = await story(pool, "alerted and fine", 3)
    await alert(pool, alerted, "sent")
    rejected = await story(pool, "alerted but noise", 3)
    await alert(pool, rejected, "sent")
    await mark(pool, rejected, "not_useful")
    missed = await story(pool, "severe and silent", 3)
    liked = await story(pool, "marked useful", 1)
    await mark(pool, liked, "useful")
    await story(pool, "old and silent", 3, ago_days=90)

    report = await ops_report(pool, days=30)

    assert {j.job_type for j in report.by_job} == {"news_analysis", "news_brief"}
    assert report.cost_usd == pytest.approx(0.40)
    assert report.useful_events == 2  # the alert nobody objected to, and the marked one
    assert report.cost_per_useful_event == pytest.approx(0.20)
    assert report.marks == {"not_useful": 1, "useful": 1}
    by_kind = {i.kind: i.story_id for i in report.incidents}
    assert by_kind == {"unalerted_severe": missed, "alert_not_useful": rejected}


@pytest.mark.db
async def test_ops_report_with_nothing_has_no_cost_per_event(backend_pool: asyncpg.Pool) -> None:
    report = await ops_report(backend_pool, days=1)
    assert report.useful_events == 0
    assert report.cost_per_useful_event is None
