"""Data for the UI redesign: thesis scores, fact check scores and source tiers, review and
news stats, news source health, and per-holder 13F changes. Every figure is a deterministic
function of stored rows; these tests pin the formulas."""

import datetime as dt
import json
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool
from api.main import create_app
from steward.config import ThesesConfig
from steward.config.holding_profiles import HoldingProfilesConfig
from steward.config.news_sources import NewsSourcesConfig
from steward.config.verification import TrustSettings
from steward.earnings import read as earnings_read
from steward.earnings.read import FactCheck, check_score, thesis_board
from steward.earnings.review_stats import golden_accuracy, review_stats
from steward.earnings.scores import (
    Judgement,
    VerdictPoint,
    current_as_of,
    round_half_up,
    thesis_score,
    thesis_state,
    weighted_score,
)
from steward.earnings.source_tier import SourceTiers
from steward.news.stats import news_stats, sources_health
from steward.ownership.read import holding_ownership, share_change
from steward.verify.trust import TrustTable

OWNER = "00000000-0000-0000-0000-00000000000a"
AAPL, MSFT, NVDA = "AAPL_US_EQ", "MSFT_US_EQ", "NVDA_US_EQ"
B = dt.date(2026, 9, 1)
NOW = dt.datetime(2026, 9, 20, 12, tzinfo=dt.UTC)

pytestmark_db = pytest.mark.db


def at(days: int, hour: int = 10) -> dt.datetime:
    return dt.datetime.combine(B + dt.timedelta(days=days), dt.time(hour), tzinfo=dt.UTC)


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


# ---------------------------------------------------------------------------
# Thesis score: pure functions
# ---------------------------------------------------------------------------


def test_score_is_the_mean_of_kpi_points_and_ignores_never_judged_kpis() -> None:
    pass_, breach, unknown = (Judgement(s, False) for s in ("pass", "breach", "unknown"))
    # (1 + 0 + 0.5) / 3 = 0.5; the never-judged KPI (None) is left out.
    assert thesis_score([pass_, breach, unknown, None]) == 50
    assert thesis_score([pass_, pass_]) == 100
    assert thesis_score([breach]) == 0
    # A stale verdict counts 0.5 whatever it says.
    assert thesis_score([Judgement("breach", True), pass_]) == 75
    assert thesis_score([Judgement("pass", True)]) == 50
    assert thesis_score([None, None]) is None
    assert thesis_score([]) is None
    # 2 of 3 pass: 66.67 -> 67; halves round up (as JavaScript's Math.round does).
    assert thesis_score([pass_, pass_, breach]) == 67
    assert round_half_up(37.5) == 38 and round_half_up(62.5) == 63


def test_state_follows_the_breach_count() -> None:
    assert thesis_state(None, 0) == "unjudged"
    assert thesis_state(100, 0) == "intact"
    assert thesis_state(50, 1) == "watch"
    assert thesis_state(0, 2) == "at_risk" and thesis_state(0, 5) == "at_risk"


def test_portfolio_score_weights_by_position_weight() -> None:
    assert weighted_score([(75, 0.75), (50, 0.25)]) == 69  # 68.75
    # A row without a score or a weight is left out.
    assert weighted_score([(80, 0.5), (None, 0.4), (10, None), (20, 0.0)]) == 80
    assert weighted_score([(None, 0.5)]) is None
    assert weighted_score([]) is None


def point(i: int, kpi: str, fy: int | None, fp: str | None, status: str, day: int) -> VerdictPoint:
    return VerdictPoint(i, kpi, fy, fp, status, 1, at(day))  # type: ignore[arg-type]


def test_current_verdict_as_of_a_date_is_the_latest_judged_period() -> None:
    rows = [
        point(1, "rev", None, None, "unknown", 0),
        point(2, "rev", 2025, "Q2", "pass", 1),
        point(3, "rev", 2025, "Q3", "breach", 2),
        point(4, "rev", 2025, "Q2", "breach", 3),  # an older period re-judged: not current
        point(5, "rev", None, None, "unknown", 4),  # undated counts only with no period
    ]
    assert current_as_of(rows, at(0, 23))["rev"].id == 1
    assert current_as_of(rows, at(1, 23))["rev"].id == 2
    assert current_as_of(rows, at(2, 23))["rev"].id == 3
    assert current_as_of(rows, at(4, 23))["rev"].id == 3
    assert current_as_of(rows, at(0, 0)) == {}


# ---------------------------------------------------------------------------
# Thesis score: the board
# ---------------------------------------------------------------------------

THESES = {
    "theses": [
        {"ticker": AAPL, "why_owned": "Services.", "kpis": [
            {"id": "rev", "metric": "revenue", "operator": ">=", "threshold": 90,
             "unit": "USD bn"},
            {"id": "margin", "metric": "gross_margin", "operator": ">=", "threshold": 45,
             "unit": "%"},
        ]},
        {"ticker": MSFT, "why_owned": "Cloud.", "kpis": [
            {"id": "rev", "metric": "revenue", "operator": ">", "threshold": 1,
             "unit": "USD bn"}]},
        {"ticker": NVDA, "why_owned": "GPUs.", "kpis": [
            {"id": "rev", "metric": "revenue", "operator": ">", "threshold": 1,
             "unit": "USD bn"}]},
    ]
}  # fmt: skip


async def theses_version(pool: asyncpg.Pool, created_at: dt.datetime, active: bool) -> int:
    body = ThesesConfig.model_validate(THESES).model_dump(mode="json")
    version_id: int = await pool.fetchval(
        "insert into config_version (domain, body, schema_version, created_at, active)"
        " values ('theses', $1::jsonb, 1, $2, $3) returning id",
        json.dumps(body),
        created_at,
        active,
    )
    return version_id


async def verdict(
    pool: asyncpg.Pool,
    ticker: str,
    kpi: str,
    status: str,
    version: int,
    created_at: dt.datetime,
    fact_ids: list[int],
    fp: str = "Q3",
) -> None:
    await pool.execute(
        "insert into verdict (ticker, kpi_id, metric, basis, kpi_period, fiscal_year,"
        " fiscal_period, period, status, operator, threshold, threshold_unit, rationale,"
        " fact_ids, config_version_id, created_at) values ($1, $2, 'revenue', 'reported',"
        " 'quarter', 2025, $3, $4, $5, '>=', 1, 'USD bn', 'test', $6, $7, $8)",
        ticker,
        kpi,
        fp,
        f"FY2025 {fp}",
        status,
        fact_ids,
        version,
        created_at,
    )


@pytestmark_db
async def test_board_scores_history_state_and_portfolio_score(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    v1 = await theses_version(backend_pool, at(0, 0), active=False)
    v2 = await theses_version(backend_pool, at(10), active=True)
    await verdict(backend_pool, AAPL, "rev", "pass", v1, at(1), [1])
    await verdict(backend_pool, AAPL, "margin", "pass", v1, at(1), [1, 2])
    await verdict(backend_pool, MSFT, "rev", "breach", v1, at(2), [])
    await verdict(backend_pool, AAPL, "rev", "breach", v1, at(3), [3])
    await verdict(backend_pool, AAPL, "rev", "pass", v2, at(12), [4])

    async def holdings(_pool: asyncpg.Pool) -> Any:
        return SimpleNamespace(
            holdings=[
                SimpleNamespace(ticker=AAPL, weight=0.75),
                SimpleNamespace(ticker=MSFT, weight=0.25),
            ]
        )

    monkeypatch.setattr(earnings_read, "portfolio_holdings", holdings)
    board = await thesis_board(backend_pool, NOW.date(), NOW)
    rows = {r.ticker: r for r in board.rows}

    # AAPL now: rev pass under v2 (1.0), margin pass under v1, now stale (0.5) -> 75.
    aapl = rows[AAPL]
    assert aapl.score == 75 and aapl.state == "intact"
    assert [(p.date, p.score) for p in aapl.score_history] == [
        (B + dt.timedelta(days=1), 100),  # both pass
        (B + dt.timedelta(days=3), 50),  # rev breach, margin pass
        (B + dt.timedelta(days=12), 75),  # rev pass under v2, margin stale
    ]
    # MSFT: a breach judged under v1; v2 is active now, so it counts 0.5 but still breaches.
    msft = rows[MSFT]
    assert msft.score == 50 and msft.state == "watch"
    assert [(p.date, p.score) for p in msft.score_history] == [(B + dt.timedelta(days=2), 0)]
    # NVDA: never judged.
    nvda = rows[NVDA]
    assert nvda.score is None and nvda.state == "unjudged" and nvda.score_history == []
    assert not nvda.held

    assert board.portfolio_score == 69  # 75 x 0.75 + 50 x 0.25 = 68.75
    assert [(p.date, p.score) for p in board.portfolio_score_history] == [
        (B + dt.timedelta(days=1), 100),  # only AAPL has a score
        (B + dt.timedelta(days=2), 75),  # 100 x 0.75 + 0 x 0.25
        (B + dt.timedelta(days=3), 38),  # 50 x 0.75 + 0 x 0.25 = 37.5
        (B + dt.timedelta(days=12), 69),  # 75 x 0.75 + 50 (MSFT stale) x 0.25
    ]

    activity = board.verdicts_30d
    assert activity.count == 5 and activity.fact_count == 4
    assert len(activity.per_day) == 30 and activity.per_day[-1].date == NOW.date()
    counts = {d.date: d.count for d in activity.per_day if d.count}
    assert counts == {
        B + dt.timedelta(days=1): 2,
        B + dt.timedelta(days=2): 1,
        B + dt.timedelta(days=3): 1,
        B + dt.timedelta(days=12): 1,
    }

    body = (await api.get("/theses/board")).json()
    assert "round_half_up" in body["score_method"] and "s_k" in body["score_method"]
    assert {r["ticker"]: r["score"] for r in body["rows"]} == {AAPL: 75, MSFT: 50, NVDA: None}
    assert body["portfolio_score"] == 69 and "verdicts_30d" in body


@pytestmark_db
async def test_board_without_theses_still_reports_verdict_activity(
    backend_pool: asyncpg.Pool,
) -> None:
    board = await thesis_board(backend_pool, NOW.date(), NOW)
    assert board.rows == [] and board.portfolio_score is None
    assert board.verdicts_30d.count == 0 and len(board.verdicts_30d.per_day) == 30


# ---------------------------------------------------------------------------
# Facts: check score and source tier
# ---------------------------------------------------------------------------


def test_check_score_is_the_share_of_passed_checks() -> None:
    ok, bad = FactCheck(check="a", ok=True), FactCheck(check="b", ok=False)
    assert check_score([ok, bad, ok, ok]) == 0.75
    assert check_score([bad]) == 0.0
    assert check_score([]) is None


def test_source_tier_uses_ir_domains_the_registry_and_trust() -> None:
    profiles = HoldingProfilesConfig.model_validate(
        {"profiles": [{"ticker": AAPL, "ir_url": "https://investor.apple.com/"}]}
    )
    tiers = SourceTiers(
        registry=NewsSourcesConfig(),
        profiles=profiles,
        trust=TrustTable({"marketwatch.com": 0.2}, TrustSettings()),
    )
    assert tiers.tier(AAPL, "https://investor.apple.com/q3.html") == 1
    assert tiers.tier(MSFT, "https://investor.apple.com/q3.html") is None  # not MSFT's IR
    assert tiers.tier(AAPL, "https://www.sec.gov/Archives/x.htm") == 1
    assert tiers.tier(AAPL, "https://www.reuters.com/markets/x") == 2
    # Trust below demote_below: one tier lower.
    assert tiers.tier(AAPL, "https://www.marketwatch.com/story/x") == 3
    assert tiers.tier(AAPL, "https://unknown.example/x") is None
    assert tiers.tier(AAPL, None) is None


async def seed_fact(
    pool: asyncpg.Pool, url: str, status: str, checks: list[dict[str, Any]], metric: str = "revenue"
) -> int:
    await pool.execute(
        "insert into evidence (sha256, storage_path, source_url, content_type, byte_size)"
        " values ($1, 'evidence/aa.html', $2, 'text/html', 10) on conflict do nothing",
        "a" * 64,
        url,
    )
    fact_id: int = await pool.fetchval(
        "insert into fact (ticker, metric, basis, fiscal_year, fiscal_period, period,"
        " period_end, value, value_as_written, scale, unit, currency, source_url, snippet,"
        " evidence_sha256, evidence_path, status, checks) values ($1, $2, 'reported', 2025,"
        " 'Q3', 'FY2025 Q3', '2025-06-28', 94036, '94,036', 'millions', 'currency', 'USD', $3,"
        " 'Revenue was $94,036 million', $4, 'evidence/aa.html', $5, $6::jsonb) returning id",
        AAPL,
        metric,
        url,
        "a" * 64,
        status,
        json.dumps(checks),
    )
    return fact_id


@pytestmark_db
async def test_facts_carry_check_score_and_source_tier(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    await backend_pool.execute(
        "insert into instrument (t212_ticker, name) values ($1, 'Apple')", AAPL
    )
    checks = [
        {"check": "sanity_bound", "ok": False, "detail": "+60%"},
        {"check": "unit", "ok": True, "detail": ""},
    ]
    held = await seed_fact(backend_pool, "https://www.sec.gov/x.htm", "unverified", checks)
    clean = await seed_fact(backend_pool, "https://unknown.example/pr", "verified", [])
    await backend_pool.execute(
        "insert into source_trust (host, score, wrong_count, useful_count)"
        " values ('sec.gov', 0.1, 5, 0)"
    )
    detail = (await api.get(f"/holdings/{AAPL}")).json()
    facts = {f["id"]: f for f in detail["facts"]}
    assert facts[held]["check_score"] == 0.5
    assert facts[held]["source_tier"] == 2  # tier 1, demoted by low trust
    assert facts[held]["confidence"] == "unconfirmed"  # unchanged
    assert facts[clean]["check_score"] is None and facts[clean]["source_tier"] is None

    queue = (await api.get("/facts/review-queue")).json()
    assert [
        (i["fact"]["id"], i["fact"]["check_score"], i["fact"]["source_tier"]) for i in queue
    ] == [(held, 0.5, 2)]


# ---------------------------------------------------------------------------
# Review stats
# ---------------------------------------------------------------------------


def test_golden_accuracy_reads_the_recorded_earnings_run(tmp_path: Path) -> None:
    assert golden_accuracy(tmp_path / "missing.json") is None
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"recorded_at": "2026-09-01T10:00:00+00:00", "components": {
        "claims": {"score": 1.0}}}))  # fmt: skip
    assert golden_accuracy(path) is None  # no earnings entry
    path.write_text(json.dumps({"recorded_at": "2026-09-01T10:00:00+00:00", "components": {
        "earnings": {"fingerprint": "x", "score": 0.9912, "model": "m"}}}))  # fmt: skip
    found = golden_accuracy(path)
    assert found is not None
    assert (found.value, found.target, found.model) == (0.9912, 0.98, "m")
    assert found.evaluated_at == dt.datetime(2026, 9, 1, 10, tzinfo=dt.UTC)


@pytestmark_db
async def test_review_stats_count_pending_decisions_and_wrong_marks(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient, tmp_path: Path
) -> None:
    await theses_version(backend_pool, at(0), active=True)
    # Pending: revenue feeds AAPL's rev KPI and its gross-margin KPI; eps feeds none.
    feeding = await seed_fact(backend_pool, "https://www.sec.gov/x", "unverified", [])
    await seed_fact(backend_pool, "https://www.sec.gov/x", "quarantined", [], metric="eps_diluted")
    done = await seed_fact(backend_pool, "https://www.sec.gov/x", "verified", [])
    for fact_id, created in ((done, at(18)), (done, at(18, 11)), (done, at(2))):
        await backend_pool.execute(
            "insert into fact_review (fact_id, action, old_status, new_status, reason,"
            " created_at) values ($1, 'accept', 'unverified', 'verified', 'ok', $2)",
            fact_id,
            created,
        )
    for target, created in ((done, at(15)), (done, at(16)), (feeding, at(17)), (done, at(1))):
        await backend_pool.execute(
            "insert into feedback (target_kind, target_id, rating, created_at)"
            " values ('fact', $1, 'wrong', $2)",
            target,
            created,
        )
    await backend_pool.execute(
        "insert into feedback (target_kind, target_id, rating, created_at)"
        " values ('fact', $1, 'useful', $2)",
        feeding,
        at(17),
    )

    stats = await review_stats(backend_pool, 7, NOW, tmp_path / "none.json")
    assert stats.pending == 2 and stats.blocking_verdict == 1
    assert stats.reviewed_count == 2  # the review on day 2 is outside the 7 days
    assert [(d.date, d.count) for d in stats.reviewed_per_day if d.count] == [
        (B + dt.timedelta(days=18), 2)
    ]
    assert len(stats.reviewed_per_day) == 7
    assert stats.marked_wrong == 2  # distinct facts marked wrong in the window
    assert stats.golden_accuracy is None

    body = (await api.get("/facts/review-stats", params={"days": 30})).json()
    assert body["days"] == 30 and len(body["reviewed_per_day"]) == 30
    assert body["pending"] == 2 and body["blocking_verdict"] == 1 and "golden_accuracy" in body
    assert (await api.get("/facts/review-stats", params={"days": 0})).status_code == 422


# ---------------------------------------------------------------------------
# News stats and source health
# ---------------------------------------------------------------------------


async def story(
    pool: asyncpg.Pool,
    published: list[dt.datetime],
    severity: int | None,
    event_type: str | None,
    status: str = "confirmed",
    duplicate_of: int | None = None,
) -> int:
    cluster: int = await pool.fetchval(
        "insert into story_cluster (ticker, event_type, severity, status, headline,"
        " headline_key, best_tier, duplicate_of) values ($1, $2, $3, $4, 'h', 'h', 1, $5)"
        " returning id",
        AAPL,
        event_type,
        severity,
        status,
        duplicate_of,
    )
    for when in published:
        await pool.execute(
            "insert into news_item (url, source_id, tier, published_at, headline, cluster_id)"
            " values ($1, 'wire', 1, $2, 'h', $3)",
            f"https://wire.example/{cluster}/{when.isoformat()}",
            when,
            cluster,
        )
    return cluster


@pytestmark_db
async def test_news_stats_count_stories_by_first_publication(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    await backend_pool.execute(
        "insert into source (id, name, tier, type) values ('wire', 'Wire', 1, 'rss')"
    )
    # Dated by its earliest item (day 18), not its later copy (day 19).
    first = await story(backend_pool, [at(19), at(18)], 2, "guidance_change")
    await story(backend_pool, [at(18, 11)], 3, "guidance_change", status="disputed")
    await story(backend_pool, [at(19)], 0, "other")
    await story(backend_pool, [at(19, 8)], None, None, status="pending")  # unclassified
    await story(backend_pool, [at(19)], 2, "guidance_change", duplicate_of=first)  # a repeat
    await story(backend_pool, [at(1)], 1, "other")  # outside a 14-day window

    stats = await news_stats(backend_pool, 14, NOW)
    assert stats.total == 4 and stats.unclassified == 1 and stats.disputed_count == 1
    assert len(stats.per_day) == 14 and stats.per_day[-1].date == NOW.date()
    by_day = {d.date: (d.by_severity, d.unclassified) for d in stats.per_day}
    assert by_day[B + dt.timedelta(days=18)] == ({"0": 0, "1": 0, "2": 1, "3": 1}, 0)
    assert by_day[B + dt.timedelta(days=19)] == ({"0": 1, "1": 0, "2": 0, "3": 0}, 1)
    assert by_day[B + dt.timedelta(days=10)] == ({"0": 0, "1": 0, "2": 0, "3": 0}, 0)
    assert [(e.event_type, e.count) for e in stats.by_event_type] == [
        ("guidance_change", 2),
        ("other", 1),
    ]

    assert (await api.get("/news/stats", params={"days": 90})).status_code == 200
    assert (await api.get("/news/stats", params={"days": 91})).status_code == 422
    assert (await api.get("/news/stats")).json()["days"] == 14


@pytestmark_db
async def test_source_health_reports_last_fetch_and_the_rule(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    now = dt.datetime.now(dt.UTC)
    rows = [
        ("edgar-filings", now - dt.timedelta(hours=2), 12, None),
        ("finnhub-company-news", now - dt.timedelta(hours=3), 0, "HTTP 429"),
        ("fed-press", now - dt.timedelta(hours=100), 3, None),
    ]
    for source_id, fetched, count, error in rows:
        await backend_pool.execute(
            "insert into source (id, name, tier, type, last_fetched_at, last_item_count,"
            " last_error) values ($1, $1, 1, 'rss', $2, $3, $4)",
            source_id,
            fetched,
            count,
            error,
        )
    health = await sources_health(backend_pool, now)
    by_id = {s.id: s for s in health.sources}
    assert health.window_hours == 48 and "news_coverage_hours" in health.rule
    assert by_id["edgar-filings"].healthy and by_id["edgar-filings"].last_item_count == 12
    assert by_id["edgar-filings"].host is None and by_id["edgar-filings"].tier == 1
    assert not by_id["finnhub-company-news"].healthy  # the fetch failed
    assert not by_id["fed-press"].healthy  # older than the window
    assert by_id["fed-press"].host == "federalreserve.gov"
    assert not by_id["google-news"].healthy  # disabled by default, never fetched
    assert by_id["ecb-press"].last_fetched_at is None and not by_id["ecb-press"].healthy

    body = (await api.get("/news/sources/health")).json()
    assert [s["id"] for s in body["sources"]] == [s.id for s in NewsSourcesConfig().sources]


# ---------------------------------------------------------------------------
# 13F per-holder change
# ---------------------------------------------------------------------------


def test_share_change_is_a_fraction_of_the_previous_position() -> None:
    assert share_change(Decimal(150), Decimal(100)) == 0.5
    assert share_change(Decimal(50), Decimal(100)) == -0.5
    assert share_change(Decimal(50), None) is None
    assert share_change(Decimal(50), Decimal(0)) is None


@pytestmark_db
async def test_top_holders_carry_their_change_against_the_previous_period(
    backend_pool: asyncpg.Pool,
) -> None:
    await backend_pool.execute("insert into form13f_dataset (url) values ('u')")
    filings = [
        ("0000000001-26-000001", "0000000001", "Alpha", "2025-12-31", 100),
        ("0000000002-26-000001", "0000000002", "Beta", "2025-12-31", 40),
        ("0000000001-26-000002", "0000000001", "Alpha", "2026-03-31", 150),
        ("0000000002-26-000002", "0000000002", "Beta", "2026-03-31", 30),
        ("0000000003-26-000002", "0000000003", "Gamma", "2026-03-31", 80),
    ]
    for accession, cik, name, period, shares in filings:
        await backend_pool.execute(
            "insert into form13f_filing (accession, cik, manager_name, period, filed,"
            " submission_type, dataset_url) values ($1, $2, $3, $4, $4, '13F-HR', 'u')",
            accession,
            cik,
            name,
            dt.date.fromisoformat(period),
        )
        await backend_pool.execute(
            "insert into institutional_holding (accession, infotable_sk, cusip, ticker,"
            " shares, shares_type) values ($1, 1, '037833100', $2, $3, 'SH')",
            accession,
            AAPL,
            shares,
        )
    ours = await holding_ownership(backend_pool, AAPL)
    assert ours.top_holders_previous_period == dt.date(2025, 12, 31)
    top = {h.manager_name: (h.shares, h.prev_shares, h.shares_change_pct) for h in ours.top_holders}
    assert top == {
        "Alpha": (Decimal(150), Decimal(100), 0.5),
        "Gamma": (Decimal(80), None, None),  # new this period
        "Beta": (Decimal(30), Decimal(40), -0.25),
    }


@pytestmark_db
async def test_a_single_period_has_no_previous_positions(backend_pool: asyncpg.Pool) -> None:
    await backend_pool.execute("insert into form13f_dataset (url) values ('u')")
    await backend_pool.execute(
        "insert into form13f_filing (accession, cik, manager_name, period, filed,"
        " submission_type, dataset_url) values ('0000000001-26-000001', '0000000001', 'A',"
        " '2026-03-31', '2026-05-01', '13F-HR', 'u')"
    )
    await backend_pool.execute(
        "insert into institutional_holding (accession, infotable_sk, cusip, ticker, shares,"
        " shares_type) values ('0000000001-26-000001', 1, '037833100', $1, 10, 'SH')",
        AAPL,
    )
    ours = await holding_ownership(backend_pool, AAPL)
    assert ours.top_holders_previous_period is None
    assert [(h.prev_shares, h.shares_change_pct) for h in ours.top_holders] == [(None, None)]
