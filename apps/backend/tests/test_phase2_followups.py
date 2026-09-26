"""Phase 2 follow-ups: earnings-check triggers, cross-source reconciliation and restatements,
the owner review queue with overrides, and the thesis board."""

import datetime as dt
import json
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool
from api.main import create_app
from steward.config import ThesesConfig
from steward.config.store import save_config
from steward.earnings.calendar import due_for_refresh, queue_due_checks, set_report_date
from steward.earnings.check import thesis_verdicts_handler
from steward.earnings.reconcile import FactToCheck, compare, reconcile_ticker, tolerance
from steward.earnings.review import review_fact
from steward.jobs.runner import run_job
from tests.conftest import MigratedDatabase

pytestmark = pytest.mark.db

OWNER = "00000000-0000-0000-0000-00000000000a"
TICKER = "AAPL_US_EQ"
D = dt.date(2026, 7, 30)
OFFSETS = [1, 2, 4]


def needs_pgmq(db: MigratedDatabase) -> None:
    if not any("pgmq" in name for name in db.applied):
        pytest.skip("pgmq extension not available on this server")


@pytest.fixture
async def api(backend_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(OWNER, None, "aal2")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def instrument(pool: asyncpg.Pool, next_date: dt.date | None) -> None:
    await pool.execute(
        "insert into instrument (t212_ticker, name, currency, next_report_date)"
        " values ($1, 'Apple Inc.', 'USD', $2)",
        TICKER,
        next_date,
    )


async def finish_checks(pool: asyncpg.Pool, status: str) -> None:
    await pool.execute(
        "update job set status = $1 where job_type = 'earnings_check' and status = 'queued'",
        status,
    )


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------


async def attempts_over(pool: asyncpg.Pool, days: range, outcome: dict[int, str]) -> list[int]:
    """Run the due-check trigger on each day; return the offsets that queued a check."""
    queued = []
    for n in days:
        if await queue_due_checks(pool, OFFSETS, D + dt.timedelta(days=n)):
            queued.append(n)
            await finish_checks(pool, outcome.get(n, "failed"))
    return queued


async def test_a_report_date_gets_exactly_the_configured_attempts(
    backend_pool: asyncpg.Pool, migrated_db: MigratedDatabase
) -> None:
    needs_pgmq(migrated_db)
    await instrument(backend_pool, D)
    assert await attempts_over(backend_pool, range(0, 10), {}) == [1, 2, 4]


async def test_attempts_stop_after_the_first_success(
    backend_pool: asyncpg.Pool, migrated_db: MigratedDatabase
) -> None:
    needs_pgmq(migrated_db)
    await instrument(backend_pool, D)
    assert await attempts_over(backend_pool, range(0, 10), {2: "succeeded"}) == [1, 2]


async def test_a_date_change_found_on_sunday_moves_the_trigger(
    backend_pool: asyncpg.Pool, migrated_db: MigratedDatabase
) -> None:
    needs_pgmq(migrated_db)
    await instrument(backend_pool, D)
    moved = D + dt.timedelta(days=5)
    await set_report_date(
        backend_pool, TICKER, moved, "https://ir.test/cal", D - dt.timedelta(days=3), 4
    )
    assert await attempts_over(backend_pool, range(0, 12), {}) == [6, 7, 9]


async def test_retries_survive_the_calendar_moving_on(
    backend_pool: asyncpg.Pool, migrated_db: MigratedDatabase
) -> None:
    needs_pgmq(migrated_db)
    await instrument(backend_pool, D)
    assert await attempts_over(backend_pool, range(0, 2), {}) == [1]
    # Sunday, D+2: the calendar finds the next quarter's date. D's +2/+4 retries still run.
    sunday = D + dt.timedelta(days=2)
    pending = await set_report_date(
        backend_pool, TICKER, dt.date(2026, 10, 29), "finnhub", sunday, 4
    )
    assert pending == D
    assert await attempts_over(backend_pool, range(2, 10), {}) == [2, 4]
    # Once past the window, the pending date is cleared on the next refresh.
    later = await set_report_date(
        backend_pool, TICKER, dt.date(2026, 10, 29), "finnhub", D + dt.timedelta(days=9), 4
    )
    assert later is None


async def test_weekly_refresh_covers_only_holdings_reporting_soon(
    backend_pool: asyncpg.Pool,
) -> None:
    today = dt.date(2026, 9, 6)
    for ticker, date in [
        ("A_EQ", today + dt.timedelta(days=3)),
        ("B_EQ", today + dt.timedelta(days=40)),
        ("C_EQ", None),
        ("D_EQ", today - dt.timedelta(days=20)),
    ]:
        await backend_pool.execute(
            "insert into instrument (t212_ticker, next_report_date) values ($1, $2)", ticker, date
        )
    due = await due_for_refresh(backend_pool, ["A_EQ", "B_EQ", "C_EQ", "D_EQ"], today, 14)
    assert due == ["A_EQ", "C_EQ", "D_EQ"]


# ---------------------------------------------------------------------------
# Facts: seeding
# ---------------------------------------------------------------------------


async def seed_fact(
    pool: asyncpg.Pool,
    *,
    metric: str = "revenue",
    value: str = "94,036",
    number: Decimal = Decimal("94036"),
    scale: str = "millions",
    unit: str = "currency",
    fy: int = 2025,
    fp: str = "Q3",
    period_end: dt.date = dt.date(2025, 6, 28),
    status: str = "verified",
    restates: int | None = None,
) -> int:
    sha = "a" * 64
    await pool.execute(
        "insert into evidence (sha256, storage_path, source_url, content_type, byte_size)"
        " values ($1, 'evidence/aa.html', 'https://ir.test/q3', 'text/html', 10)"
        " on conflict do nothing",
        sha,
    )
    fact_id: int = await pool.fetchval(
        "insert into fact (ticker, metric, basis, fiscal_year, fiscal_period, period,"
        " period_end, value, value_as_written, scale, unit, currency, source_url, snippet,"
        " evidence_sha256, evidence_path, status, restates_fact_id, checks)"
        " values ($1, $2, 'reported', $3, $4, $5, $6, $7, $8, $9, $10, 'USD',"
        " 'https://ir.test/q3', $11, $12, 'evidence/aa.html', $13, $14, $15::jsonb) returning id",
        TICKER,
        metric,
        fy,
        fp,
        f"FY{fy} {fp}",
        period_end,
        number,
        value,
        scale,
        unit,
        f"Revenue was ${value} million",
        sha,
        status,
        restates,
        json.dumps(
            [{"check": "sanity_bound", "ok": False, "detail": "+60% against FY2025 Q2"}]
            if status == "unverified"
            else []
        ),
    )
    return fact_id


def xbrl(concept: str, rows: list[dict[str, Any]], unit: str = "USD") -> dict[str, Any]:
    return {"facts": {"us-gaap": {concept: {"units": {unit: rows}}}}}


def row(start: str, end: str, val: float, accn: str = "0000320193-25-000073", form: str = "10-Q",
        filed: str = "2025-08-01") -> dict[str, Any]:  # fmt: skip
    return {"start": start, "end": end, "val": val, "accn": accn, "form": form, "filed": filed}


THESES = ThesesConfig.model_validate(
    {
        "theses": [
            {
                "ticker": TICKER,
                "why_owned": "Services growth.",
                "kpis": [
                    {"id": "rev", "metric": "revenue", "operator": ">=", "threshold": 90,
                     "unit": "USD bn"},
                    {"id": "margin", "metric": "gross_margin", "operator": ">=", "threshold": 45,
                     "unit": "%"},
                ],
            },
            {"ticker": "MSFT_US_EQ", "why_owned": "Cloud.", "kpis": [
                {"id": "rev", "metric": "revenue", "operator": ">", "threshold": 1,
                 "unit": "USD bn"}]},
        ]
    }
)  # fmt: skip


async def save_theses(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        stored = await save_config(conn, "theses", THESES, created_by=None)
    return stored.id


async def run_verdicts_job(pool: asyncpg.Pool) -> None:
    assert await run_job(pool, "thesis_verdicts", thesis_verdicts_handler, trigger="manual") == (
        "succeeded"
    )


async def current_status(pool: asyncpg.Pool, kpi: str) -> tuple[str, list[int]]:
    r = await pool.fetchrow(
        "select status, fact_ids from verdict where ticker = $1 and kpi_id = $2"
        " order by id desc limit 1",
        TICKER,
        kpi,
    )
    return r["status"], list(r["fact_ids"])


# ---------------------------------------------------------------------------
# Reconciliation and restatements
# ---------------------------------------------------------------------------


def test_tolerance_follows_the_printed_precision() -> None:
    assert tolerance("94,036", "millions") == Decimal("500000")
    assert tolerance("143.8", "billions") == Decimal("50000000")
    assert tolerance("(1.57)", "units") == Decimal("0.005")


def test_fourth_quarter_is_derived_from_the_annual_report() -> None:
    fact = FactToCheck(1, "revenue", "Q4", dt.date(2025, 9, 27), Decimal("102466000000"),
                       "102,466", "millions", "currency", "USD")  # fmt: skip
    facts = xbrl(
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        [
            row("2024-09-29", "2025-09-27", 416161000000, "k", "10-K", "2025-10-31"),
            row("2024-09-29", "2025-06-28", 313695000000, "q3", "10-Q", "2025-08-01"),
        ],
    )
    result = compare(fact, facts, dt.date(2025, 11, 5))
    assert result.outcome == "match" and result.derivation == "derived_q4"
    assert result.xbrl_value == Decimal("102466000000")


async def test_a_mismatch_quarantines_the_fact_and_recomputes_verdicts(
    backend_pool: asyncpg.Pool,
) -> None:
    version = await save_theses(backend_pool)
    good = await seed_fact(backend_pool, metric="revenue", value="94,036",
                           number=Decimal("94036"))  # fmt: skip
    await run_verdicts_job(backend_pool)
    assert await current_status(backend_pool, "rev") == ("pass", [good])

    # The 10-Q says 84,036: the press-release figure is wrong.
    facts = xbrl("Revenues", [row("2025-03-30", "2025-06-28", 84036000000)])
    summary = await reconcile_ticker(backend_pool, TICKER, facts, dt.date(2025, 8, 5), None)
    assert summary.quarantined == [good]
    status = await backend_pool.fetchrow(
        "select status, status_reason from fact where id = $1", good
    )
    assert status["status"] == "quarantined" and "Revenues" in status["status_reason"]
    recon = await backend_pool.fetchrow(
        "select * from fact_reconciliation where fact_id = $1", good
    )
    assert recon["outcome"] == "mismatch" and recon["xbrl_value"] == Decimal("84036000000")

    from steward.earnings.verdicts import run_verdicts

    await run_verdicts(backend_pool, THESES, version, None, [TICKER])
    assert (await current_status(backend_pool, "rev"))[0] == "unknown"

    # A second run leaves the quarantined fact alone (one mismatch row, not two).
    await reconcile_ticker(backend_pool, TICKER, facts, dt.date(2025, 8, 6), None)
    assert await backend_pool.fetchval("select count(*) from fact_reconciliation") == 1


async def test_a_matching_figure_is_recorded_and_kept(backend_pool: asyncpg.Pool) -> None:
    fact = await seed_fact(backend_pool, number=Decimal("94036"))
    facts = xbrl("Revenues", [row("2025-03-30", "2025-06-28", 94036000000)])
    summary = await reconcile_ticker(backend_pool, TICKER, facts, dt.date(2025, 8, 5), None)
    assert summary.matched == 1 and summary.quarantined == []
    assert await backend_pool.fetchval("select status from fact where id = $1", fact) == "verified"


async def test_not_yet_filed_is_not_a_finding(backend_pool: asyncpg.Pool) -> None:
    await seed_fact(backend_pool, number=Decimal("94036"))
    summary = await reconcile_ticker(backend_pool, TICKER, {}, dt.date(2025, 7, 31), None)
    assert summary.pending == 1
    assert await backend_pool.fetchval("select count(*) from fact_reconciliation") == 0


async def test_a_restatement_keeps_both_values(backend_pool: asyncpg.Pool) -> None:
    first = await seed_fact(backend_pool, number=Decimal("94036"))
    second = await seed_fact(
        backend_pool, value="94,930", number=Decimal("94930"), status="unverified",
        restates=first,
    )  # fmt: skip
    rows = await backend_pool.fetch(
        "select id, value_units, status, restates_fact_id from fact order by id"
    )
    assert [(r["id"], r["value_units"], r["status"], r["restates_fact_id"]) for r in rows] == [
        (first, Decimal("94036000000"), "verified", None),
        (second, Decimal("94930000000"), "unverified", first),
    ]
    with pytest.raises(asyncpg.RaiseError, match="immutable"):
        await backend_pool.execute("update fact set value = 1 where id = $1", first)


# ---------------------------------------------------------------------------
# Review queue and overrides
# ---------------------------------------------------------------------------


async def test_a_held_fact_is_used_nowhere_until_accepted(backend_pool: asyncpg.Pool) -> None:
    await save_theses(backend_pool)
    held = await seed_fact(backend_pool, number=Decimal("94036"), status="unverified")
    await run_verdicts_job(backend_pool)
    assert (await current_status(backend_pool, "rev"))[0] == "unknown"

    outcome = await review_fact(backend_pool, held, "accept", "checked the 10-Q", owner_id=OWNER)
    assert outcome.status == "verified"
    await run_verdicts_job(backend_pool)
    assert await current_status(backend_pool, "rev") == ("pass", [held])
    log = await backend_pool.fetchrow("select * from fact_review where fact_id = $1", held)
    assert (log["action"], log["old_status"], log["new_status"], log["reason"]) == (
        "accept", "unverified", "verified", "checked the 10-Q",
    )  # fmt: skip


async def test_a_correction_wins_and_recomputes_verdicts(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool, migrated_db: MigratedDatabase
) -> None:
    needs_pgmq(migrated_db)
    await save_theses(backend_pool)
    wrong = await seed_fact(backend_pool, value="84,036", number=Decimal("84036"))
    await run_verdicts_job(backend_pool)
    assert (await current_status(backend_pool, "rev"))[0] == "breach"

    response = await api.post(
        f"/facts/{wrong}/review",
        json={"action": "correct", "value": "94036", "reason": "typo in the release"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    corrected = body["corrected_fact_id"]
    assert body["status"] == "rejected" and body["verdict_job"]["job_type"] == "thesis_verdicts"
    new = await backend_pool.fetchrow("select * from fact where id = $1", corrected)
    assert new["origin"] == "owner" and new["status"] == "verified"
    assert new["value_units"] == Decimal("94036000000") and new["restates_fact_id"] == wrong

    # One worker cycle: the queued verdict run.
    await run_verdicts_job(backend_pool)
    assert await current_status(backend_pool, "rev") == ("pass", [corrected])


async def test_the_queue_lists_every_held_fact_with_its_reason(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    await backend_pool.execute(
        "insert into instrument (t212_ticker, name) values ($1, 'Apple Inc.')", TICKER
    )
    held = await seed_fact(backend_pool, status="unverified")
    quarantined = await seed_fact(backend_pool, fp="Q2", period_end=dt.date(2025, 3, 29),
                                  number=Decimal("95359"), value="95,359")  # fmt: skip
    facts = xbrl("Revenues", [row("2024-12-29", "2025-03-29", 90000000000)])
    await reconcile_ticker(backend_pool, TICKER, facts, dt.date(2025, 5, 10), None)
    await seed_fact(backend_pool, fp="Q1", period_end=dt.date(2024, 12, 28))  # verified: not listed

    queue = (await api.get("/facts/review-queue")).json()
    assert [i["fact"]["id"] for i in queue] == [held, quarantined]
    first, second = queue
    assert first["name"] == "Apple Inc." and first["fact"]["snippet"].startswith("Revenue was")
    assert first["failed_checks"] == [{"check": "sanity_bound", "detail": "+60% against FY2025 Q2"}]
    assert second["why"].startswith("quarantined") and second["reconciliation"][0]["outcome"] == (
        "mismatch"
    )

    await review_fact(backend_pool, held, "reject", "wrong period", owner_id=OWNER)
    assert [i["fact"]["id"] for i in (await api.get("/facts/review-queue")).json()] == [quarantined]


# ---------------------------------------------------------------------------
# Thesis board
# ---------------------------------------------------------------------------


async def test_board_lists_every_thesis_once_with_unknown_never_omitted(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    await save_theses(backend_pool)
    fact = await seed_fact(backend_pool, number=Decimal("94036"))
    await run_verdicts_job(backend_pool)
    board = (await api.get("/theses/board")).json()
    assert [r["ticker"] for r in board["rows"]] == [TICKER, "MSFT_US_EQ"]
    apple = board["rows"][0]
    statuses = {k["kpi"]["id"]: k["status"] for k in apple["kpis"]}
    assert statuses == {"rev": "pass", "margin": "unknown"}
    assert apple["overall"] == "unknown" and apple["counts"] == {
        "pass": 1,
        "breach": 0,
        "unknown": 1,
    }
    rev = next(k for k in apple["kpis"] if k["kpi"]["id"] == "rev")
    assert rev["verdict"]["fact_ids"] == [fact]
    msft = board["rows"][1]
    assert [k["status"] for k in msft["kpis"]] == ["unknown"]
