"""Phase 4: verification hardening, plus the job log, impact-note cost and schedule-aware
staleness the Phase 3 screens needed."""

import datetime as dt
import gzip
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import anthropic
import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool, get_pool_or_error
from api.main import create_app
from steward.ai import Usage
from steward.config import (
    EventRulesConfig,
    HoldingProfilesConfig,
    JobsConfig,
    NotificationsConfig,
)
from steward.config.store import save_config
from steward.config.verification import ClaimVerifierSettings, TrustSettings
from steward.earnings.golden import DEFAULT_DIR as GOLDEN_DIR
from steward.earnings.golden import OracleExtractor, evaluate, load_cases
from steward.news.corroborate import OtherStory, decide
from steward.news.digest import (
    DigestBody,
    DigestFactSource,
    DigestSection,
    DigestSource,
    render_text,
    verify_digest_claims,
)
from steward.news.digest import Sentence as DigestSentence
from steward.news.escalation import (
    EscalationBody,
    EscalationSource,
    NoteSentence,
    verify_note_claims,
)
from steward.news.eval import SET_PATH as NEWS_SET
from steward.news.eval import load_set
from steward.news.normalize import host_of
from steward.news.pipeline import load_story
from steward.reference.identity import reconcile_structure, upsert_relationship
from steward.storage import MemoryStorage
from steward.verify.claims import Claim, ClaimVerdict
from steward.verify.eval import load_cases as load_claim_cases
from steward.verify.eval import score as score_claims
from steward.verify.evals_gate import (
    COMPONENTS,
    Component,
    fingerprint,
    load_baseline,
    run_gate,
    write_baseline,
)
from steward.verify.export_evals import export_candidates
from steward.verify.trust import TrustTable, load_trust
from tests.conftest import MigratedDatabase
from tests.test_golden import _checker as golden_checker
from tests.test_phase2_followups import (
    TICKER,
    current_status,
    needs_pgmq,
    run_verdicts_job,
    save_theses,
    seed_fact,
)

pytestmark = pytest.mark.db

OWNER = "7f0c3d4e-0000-4000-8000-000000000001"


@pytest.fixture
async def api(backend_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def pool_override() -> asyncpg.Pool:
        return backend_pool

    app.dependency_overrides[get_pool] = pool_override
    app.dependency_overrides[get_pool_or_error] = pool_override
    app.dependency_overrides[require_owner] = lambda: AuthenticatedUser(OWNER, None, "aal2")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def add_job(
    pool: asyncpg.Pool,
    job_type: str,
    status: str,
    *,
    params: dict[str, Any] | None = None,
    created_at: dt.datetime | None = None,
    steps: list[tuple[str, str, float | None, str | None]] = (),  # type: ignore[assignment]
) -> int:
    created = created_at or dt.datetime.now(dt.UTC)
    job_id: int = await pool.fetchval(
        "insert into public.job (job_type, trigger, status, params, created_at, started_at,"
        " finished_at, error) values ($1, 'schedule', $2, $3::jsonb, $4, $4, $5, $6)"
        " returning id",
        job_type,
        status,
        json.dumps(params or {}),
        created,
        None if status in ("running", "queued") else created + dt.timedelta(seconds=5),
        "boom" if status == "failed" else None,
    )
    for index, (name, step_status, cost, model) in enumerate(steps):
        await pool.execute(
            "insert into public.job_step (job_id, step_index, name, status, started_at,"
            " finished_at, cost_usd, model) values ($1, $2, $3, $4, $5, $5, $6, $7)",
            job_id,
            index,
            name,
            step_status,
            created,
            cost,
            model,
        )
    return job_id


# ---------------------------------------------------------------------------
# Job log, impact-note cost, next-due times
# ---------------------------------------------------------------------------


async def test_job_log_filters_pages_and_sums_costs(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    sync = await add_job(
        backend_pool, "portfolio_sync", "failed", steps=[("fetch", "failed", None, None)]
    )
    check = await add_job(
        backend_pool,
        "earnings_check",
        "succeeded",
        params={"ticker": "AAPL_US_EQ"},
        steps=[
            ("locate", "succeeded", 0.0125, "claude-haiku-4-5"),
            ("extract", "succeeded", 0.25, "claude-sonnet-5"),
        ],
    )
    await add_job(backend_pool, "earnings_check", "succeeded", params={"ticker": "MSFT_US_EQ"})

    everything = (await api.get("/jobs")).json()
    assert [j["job_type"] for j in everything["jobs"]][:3] == [
        "earnings_check",
        "earnings_check",
        "portfolio_sync",
    ]
    by_id = {j["id"]: j for j in everything["jobs"]}
    assert by_id[check]["cost_usd"] == pytest.approx(0.2625)
    assert by_id[sync]["failed_step"] == "fetch" and by_id[sync]["cost_usd"] is None

    failed = (await api.get("/jobs", params={"status": "failed"})).json()["jobs"]
    assert [j["id"] for j in failed] == [sync]
    apple = (await api.get("/jobs", params={"ticker": "AAPL_US_EQ"})).json()["jobs"]
    assert [j["id"] for j in apple] == [check]
    typed = await api.get("/jobs", params=[("job_type", "portfolio_sync")])
    assert [j["id"] for j in typed.json()["jobs"]] == [sync]

    page = (await api.get("/jobs", params={"limit": 1})).json()
    assert len(page["jobs"]) == 1 and page["next_before_id"] == page["jobs"][0]["id"]
    rest = (await api.get("/jobs", params={"before_id": page["next_before_id"]})).json()
    assert page["jobs"][0]["id"] not in [j["id"] for j in rest["jobs"]]


async def test_impact_note_carries_its_run_status_and_cost(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    job = await add_job(
        backend_pool,
        "event_escalation",
        "succeeded",
        steps=[
            ("write", "succeeded", 0.04, "claude-sonnet-5"),
            ("search", "succeeded", 0.01, None),
        ],
    )
    body = {
        "ticker": "AAPL_US_EQ",
        "trigger": "story",
        "cluster_id": None,
        "event_type": "guidance_change",
        "severity": 3,
        "headline": "Apple cuts guidance",
        "sentences": [],
        "kpis": [],
        "exit_conditions": [],
        "none_applies": True,
        "thesis_link": "none",
        "rationale": "No KPI is affected.",
        "sources": [],
    }
    report = await backend_pool.fetchval(
        "insert into public.report (type, ticker, title, body, body_md, job_id)"
        " values ('escalation', 'AAPL_US_EQ', 'Escalation', $1::jsonb, '', $2) returning id",
        json.dumps(body),
        job,
    )
    note = (await api.get(f"/escalations/{report}")).json()
    assert note["job_id"] == job and note["job_status"] == "succeeded"
    assert note["cost_usd"] == pytest.approx(0.05)
    assert note["models"] == ["claude-sonnet-5"]
    listed = (await api.get("/escalations")).json()
    assert [n["id"] for n in listed] == [report]
    assert (await api.get(f"/escalations/{report + 1}")).status_code == 404


async def test_digest_and_feed_are_due_at_the_next_scheduled_run(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    async with backend_pool.acquire() as conn:
        # Daily digests on weekdays at 07:00 Prague, news every 2 h 06:00-22:00.
        await save_config(conn, "jobs", JobsConfig(), created_by=None)
    friday = dt.datetime(2026, 9, 25, 5, 0, 30, tzinfo=dt.UTC)  # 07:00:30 Prague
    report = await backend_pool.fetchval(
        "insert into public.report (type, period, title, body, body_md, created_at)"
        " values ('digest', 'daily', 'Daily digest', $1::jsonb, '', $2) returning id",
        json.dumps(
            {
                "kind": "daily",
                "headline": "Quiet",
                "nothing_material": True,
                "window_start": (friday - dt.timedelta(days=1)).isoformat(),
                "window_end": friday.isoformat(),
            }
        ),
        friday,
    )
    monday_7 = dt.datetime(2026, 9, 28, 5, 0, tzinfo=dt.UTC)
    summary = (await api.get("/reports")).json()[0]
    assert dt.datetime.fromisoformat(summary["next_due_at"]) == monday_7
    detail = await api.get(f"/reports/{report}")
    assert detail.status_code == 200, detail.text
    assert dt.datetime.fromisoformat(detail.json()["next_due_at"]) == monday_7

    await add_job(
        backend_pool,
        "news_ingest",
        "succeeded",
        created_at=dt.datetime(2026, 9, 25, 20, 0, tzinfo=dt.UTC),  # 22:00 Prague
    )
    feed = (await api.get("/news")).json()
    # The last run of the day: the next check is 06:00 Prague the following morning.
    assert dt.datetime.fromisoformat(feed["next_ingest_due_at"]) == dt.datetime(
        2026, 9, 26, 4, 0, tzinfo=dt.UTC
    )


# ---------------------------------------------------------------------------
# Corroboration rules and source trust
# ---------------------------------------------------------------------------


def test_corroboration_rules_come_from_config() -> None:
    wire = OtherStory(2, 2, frozenset({"reuters.com"}))
    second = OtherStory(3, 2, frozenset({"bloomberg.com"}))
    base = {"outlets": ["cnbc.com"], "rumour": False}
    assert decide(best_tier=2, others=[wire], **base).status == "confirmed"  # type: ignore[arg-type]
    three = decide(best_tier=2, others=[wire], independent_needed=3, **base)  # type: ignore[arg-type]
    assert three.status == "unconfirmed"
    assert (
        decide(
            best_tier=2,
            others=[wire, second],
            independent_needed=3,
            **base,  # type: ignore[arg-type]
        ).status
        == "confirmed"
    )
    # Tier 1 alone confirms by default; with the rule off it is one source among others.
    assert decide(best_tier=1, outlets=["apple.com"], rumour=False).status == "confirmed"
    alone_off = decide(best_tier=1, outlets=["apple.com"], rumour=False, tier1_alone=False)
    assert alone_off.status == "unconfirmed"
    assert (
        decide(
            best_tier=1, outlets=["apple.com"], rumour=False, others=[wire], tier1_alone=False
        ).status
        == "confirmed"
    )


def test_a_distrusted_source_counts_one_tier_lower() -> None:
    table = TrustTable({"apple.com": 0.4, "cnbc.com": 0.59, "ok.com": 0.6}, TrustSettings())
    assert table.effective_tier(1, "apple.com") == 2
    assert table.effective_tier(2, "cnbc.com") == 3
    assert table.effective_tier(3, "cnbc.com") == 3
    assert table.effective_tier(2, "ok.com") == 2  # at the threshold: not below it
    assert table.effective_tier(1, "never-judged.com") == 1


async def seed_story(pool: asyncpg.Pool, items: list[tuple[str, int]]) -> int:
    await pool.execute(
        "insert into source (id, name, tier, type) values ('wire', 'Wire', 2, 'rss')"
        " on conflict do nothing"
    )
    cluster: int = await pool.fetchval(
        "insert into story_cluster (headline, headline_key, best_tier, ticker, event_type)"
        " values ('Apple cuts guidance', 'apple cuts guidance', $1, 'AAPL_US_EQ',"
        " 'guidance_change') returning id",
        min(t for _, t in items),
    )
    for url, tier in items:
        await pool.execute(
            "insert into news_item (url, source_id, tier, published_at, headline, outlet,"
            " cluster_id, classified_at, event_type, affected_ticker, relevant)"
            " values ($1, 'wire', $2, now(), 'Apple cuts guidance', $3, $4, now(),"
            " 'guidance_change', 'AAPL_US_EQ', true)",
            url,
            tier,
            host_of(url),
            cluster,
        )
    return cluster


async def test_wrong_marks_lower_trust_until_the_source_is_demoted(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    story = await seed_story(
        backend_pool, [(f"https://investor.apple.com/pr/{n}", 1) for n in range(3)]
    )
    items = [
        r["id"]
        for r in await backend_pool.fetch(
            "select id from news_item where cluster_id = $1 order by id", story
        )
    ]
    before = await load_story(
        backend_pool, story, EventRulesConfig(), await trust_now(backend_pool)
    )
    assert before is not None and before.best_tier == 1

    # Marking the same item wrong twice moves trust once.
    for item, expected in ((items[0], 0.8), (items[0], 0.8), (items[1], 0.6), (items[2], 0.4)):
        response = await api.post(
            "/feedback", json={"target_kind": "news_item", "target_id": item, "rating": "wrong"}
        )
        assert response.status_code == 201, response.text
        score = await backend_pool.fetchval(
            "select score from source_trust where host = 'investor.apple.com'"
        )
        assert float(score) == pytest.approx(expected)
    assert response.json()["effects"]["trust"] == {"investor.apple.com": pytest.approx(0.4)}
    after = await load_story(backend_pool, story, EventRulesConfig(), await trust_now(backend_pool))
    assert after is not None and after.best_tier == 2  # a tier-1 source no longer confirms alone

    trust = (await api.get("/sources/trust")).json()
    assert trust[0]["host"] == "investor.apple.com" and trust[0]["demoted"] is True
    assert trust[0]["wrong_count"] == 3

    # Useful raises it; irrelevant leaves it alone.
    useful = await api.post(
        "/feedback", json={"target_kind": "story", "target_id": story, "rating": "useful"}
    )
    assert useful.json()["effects"]["trust"] == {"investor.apple.com": pytest.approx(0.45)}
    irrelevant = await api.post(
        "/feedback", json={"target_kind": "story", "target_id": story, "rating": "irrelevant"}
    )
    assert irrelevant.json()["effects"] == {} and irrelevant.json()["source_hosts"] == []
    marks = (await api.get("/feedback", params={"target_kind": "story"})).json()
    assert [m["rating"] for m in marks] == ["irrelevant", "useful"]


async def trust_now(pool: asyncpg.Pool) -> TrustTable:
    return await load_trust(pool, TrustSettings())


async def test_feedback_targets_must_exist(api: httpx.AsyncClient) -> None:
    for kind in ("fact", "verdict", "alert", "news_item", "story"):
        response = await api.post(
            "/feedback", json={"target_kind": kind, "target_id": 999, "rating": "wrong"}
        )
        assert response.status_code == 404, (kind, response.text)
    no_path = await api.post(
        "/feedback", json={"target_kind": "report_sentence", "target_id": 1, "rating": "wrong"}
    )
    assert no_path.status_code == 422
    eval_without_value = await api.post(
        "/feedback",
        json={"target_kind": "fact", "target_id": 1, "rating": "wrong", "add_to_eval": True},
    )
    assert eval_without_value.status_code == 422


async def test_a_report_sentence_is_found_by_its_path(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    report = await backend_pool.fetchval(
        "insert into public.report (type, title, body, body_md) values ('escalation', 't',"
        " $1::jsonb, '') returning id",
        json.dumps({"sentences": [{"text": "Apple cut guidance.", "source_urls": []}]}),
    )
    ok = await api.post(
        "/feedback",
        json={
            "target_kind": "report_sentence",
            "target_id": report,
            "sentence_path": "sentences/0",
            "rating": "wrong",
            "note": "It raised guidance.",
        },
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["effects"] == {}  # a model sentence: stored, no trust change
    missing = await api.post(
        "/feedback",
        json={
            "target_kind": "report_sentence",
            "target_id": report,
            "sentence_path": "sentences/1",
            "rating": "wrong",
        },
    )
    assert missing.status_code == 404


async def test_a_fact_marked_wrong_is_quarantined_and_verdicts_recomputed(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool, migrated_db: MigratedDatabase
) -> None:
    needs_pgmq(migrated_db)
    await save_theses(backend_pool)
    fact = await seed_fact(backend_pool)
    await run_verdicts_job(backend_pool)
    assert await current_status(backend_pool, "rev") == ("pass", [fact])

    response = await api.post(
        "/feedback",
        json={"target_kind": "fact", "target_id": fact, "rating": "wrong", "note": "Typo."},
    )
    assert response.status_code == 201, response.text
    effects = response.json()["effects"]
    assert effects["quarantined"] is True
    assert effects["trust"] == {"ir.test": pytest.approx(0.8)}
    row = await backend_pool.fetchrow("select status, status_reason from fact where id = $1", fact)
    assert row["status"] == "quarantined" and row["status_reason"].endswith("Typo.")

    # The queued re-judgement: one worker cycle later the KPI no longer rests on the fact.
    job = await backend_pool.fetchrow(
        "select job_type, params from job where id = $1", effects["verdict_job_id"]
    )
    assert job["job_type"] == "thesis_verdicts"
    assert json.loads(job["params"]) == {"ticker": TICKER}
    await run_verdicts_job(backend_pool)
    assert (await current_status(backend_pool, "rev"))[0] == "unknown"


# ---------------------------------------------------------------------------
# Claim verifier
# ---------------------------------------------------------------------------


class FakeClaimVerifier:
    """Flags any sentence containing one of the given words; records what it was asked."""

    def __init__(self, flag: tuple[str, ...] = (), fail: bool = False) -> None:
        self.flag = flag
        self.fail = fail
        self.asked: list[Claim] = []

    async def check(self, claims: Any, today: dt.date) -> tuple[list[ClaimVerdict], Usage]:
        if self.fail:
            raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://x.test"))
        self.asked.extend(claims)
        return [
            ClaimVerdict(
                index=c.index,
                supported=not any(w in c.text for w in self.flag),
                reason="checked",
            )
            for c in claims
        ], Usage(model="fake")


async def test_the_code_check_catches_every_injected_figure() -> None:
    cases = load_claim_cases()
    assert len(cases) == 20
    result = await score_claims(cases, None, ClaimVerifierSettings())
    numeric = [c.id for c in cases if c.kind == "numeric"]
    assert sorted(result.injected_flagged) == sorted(numeric)
    assert result.faithful_flagged == []
    # With a verifier, every sentence the code check passes is sent to it, and its answer
    # decides; the injected semantic sentences are the ones this fake flags.
    words = ("raised its 2026", "step down", "has agreed", "weigh on", "in China", "approved",
             "beating", "upgraded")  # fmt: skip
    live_like = await score_claims(cases, FakeClaimVerifier(words), ClaimVerifierSettings())
    assert live_like.catch_rate == 1.0 and live_like.faithful_flagged == []


def digest_body() -> DigestBody:
    now = dt.datetime(2026, 9, 26, 5, tzinfo=dt.UTC)
    return DigestBody(
        kind="daily",
        window_start=now - dt.timedelta(days=1),
        window_end=now,
        nothing_material=False,
        headline="Apple cut guidance.",
        overview=[
            DigestSentence(text="Apple cut its outlook.", news_item_ids=[1]),
            DigestSentence(text="Apple beat estimates.", news_item_ids=[1]),
        ],
        sections=[
            DigestSection(
                ticker="AAPL_US_EQ",
                name="Apple",
                summary=[
                    DigestSentence(text="Revenue was $94.0 billion.", fact_ids=[7]),
                    DigestSentence(text="Revenue was $97.0 billion.", fact_ids=[7]),
                ],
            )
        ],
        sources=[
            DigestSource(
                news_item_id=1,
                url="https://investor.apple.com/1",
                outlet="investor.apple.com",
                tier=1,
                headline="Apple lowers its outlook",
                published_at=now,
                summary="Apple cut its full-year outlook.",
            )
        ],
        fact_sources=[
            DigestFactSource(
                fact_id=7,
                ticker="AAPL_US_EQ",
                metric="revenue",
                period="FY2025 Q3",
                value_as_written="94,036",
                snippet="Total net sales | 94,036",
                source_url="https://ir.test/q3",
                status="verified",
                origin="extraction",
                relation="supports",
            )
        ],
    )


async def test_the_digest_strips_or_marks_unsupported_sentences() -> None:
    body = digest_body()
    verifier = FakeClaimVerifier(("beat",))
    await verify_digest_claims(body, ClaimVerifierSettings(), verifier, dt.date(2026, 9, 26))
    assert [s.text for s in body.overview] == ["Apple cut its outlook."]
    assert [s.text for s in body.sections[0].summary] == ["Revenue was $94.0 billion."]
    assert body.claims is not None and body.claims.removed == 2
    assert (body.claims.supported, body.claims.unsupported) == (2, 2)
    # The $97.0 bn sentence failed the code check: only three sentences reached the model,
    # each with the text it cites.
    assert len(verifier.asked) == 3
    assert verifier.asked[0].evidence == (
        "Apple lowers its outlook Apple cut its full-year outlook.",
    )
    assert verifier.asked[2].evidence == ("revenue FY2025 Q3: 94,036. Total net sales | 94,036",)

    marked = digest_body()
    settings = ClaimVerifierSettings(mode="mark")
    await verify_digest_claims(marked, settings, FakeClaimVerifier(("beat",)), dt.date(2026, 9, 26))
    assert [s.check for s in marked.overview] == ["supported", "unsupported"]
    assert marked.sections[0].summary[1].check_reason == "figure not in the cited snippets: 97.0"
    assert "Apple beat estimates. (" in render_text(marked, None, markdown=True)
    assert "[not supported by its sources]" in render_text(marked, None, markdown=True)


async def test_a_verifier_that_cannot_run_marks_sentences_unchecked() -> None:
    body = digest_body()
    outcome = await verify_digest_claims(
        body, ClaimVerifierSettings(), FakeClaimVerifier(fail=True), dt.date(2026, 9, 26)
    )
    assert outcome.note is not None and outcome.note.startswith("claim verifier failed")
    # Nothing unverified passes as supported; the figure the code check caught is removed.
    assert [s.check for s in body.overview] == ["unchecked", "unchecked"]
    assert [s.text for s in body.sections[0].summary] == ["Revenue was $94.0 billion."]
    assert body.claims is not None and body.claims.unchecked == 3


async def test_impact_note_sentences_are_checked_against_their_sources() -> None:
    body = EscalationBody(
        ticker="AAPL_US_EQ",
        trigger="story",
        cluster_id=1,
        event_type="guidance_change",
        severity=3,
        headline="Apple cut guidance",
        sentences=[
            NoteSentence(text="Apple cut its outlook by 5%.", source_urls=["https://a.test/1"]),
            NoteSentence(text="Apple cut its outlook by 9%.", source_urls=["https://a.test/1"]),
            NoteSentence(text="Peers followed.", source_urls=["https://search.test/2"]),
        ],
        kpis=[],
        exit_conditions=[],
        none_applies=True,
        thesis_link="none",
        rationale="",
        sources=[
            EscalationSource(
                url="https://a.test/1",
                headline="Apple lowers outlook",
                snippet="Apple expects revenue 5% lower than planned.",
            ),
            EscalationSource(url="https://search.test/2", kind="search"),
        ],
    )
    verifier = FakeClaimVerifier()
    await verify_note_claims(body, ClaimVerifierSettings(), verifier, dt.date(2026, 9, 26))
    assert [s.text for s in body.sentences] == ["Apple cut its outlook by 5%.", "Peers followed."]
    # A search result whose page was never stored cannot be checked: kept, marked unchecked.
    assert body.sentences[1].check == "unchecked"
    assert body.claims is not None and body.claims.removed == 1
    assert [c.text for c in verifier.asked] == ["Apple cut its outlook by 5%."]


# ---------------------------------------------------------------------------
# Replay preview
# ---------------------------------------------------------------------------


async def seed_classified(
    pool: asyncpg.Pool,
    event_type: str,
    *,
    hours_ago: float,
    ticker: str = "AAPL_US_EQ",
    status: str = "confirmed",
    rumour: bool = False,
) -> int:
    await pool.execute(
        "insert into source (id, name, tier, type) values ('wire', 'Wire', 2, 'rss')"
        " on conflict do nothing"
    )
    seen = dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours_ago)
    cluster: int = await pool.fetchval(
        "insert into story_cluster (headline, headline_key, best_tier, ticker, event_type,"
        " severity, status, rumour, first_seen, last_seen) values ($1, $1, 2, $2, $3, 1, $4,"
        " $5, $6, $6) returning id",
        f"{ticker} {event_type} {hours_ago}",
        ticker,
        event_type,
        status,
        rumour,
        seen,
    )
    await pool.execute(
        "insert into news_item (url, source_id, tier, published_at, headline, cluster_id,"
        " classified_at, event_type, affected_ticker, relevant) values ($1, 'wire', 2, $2,"
        " 'h', $3, $2, $4, $5, true)",
        f"https://wire.test/{cluster}",
        seen,
        cluster,
        event_type,
        ticker,
    )
    return cluster


async def test_replay_preview_diffs_a_draft_against_the_active_rules(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    async with backend_pool.acquire() as conn:
        # No quiet hours, so the outcome does not depend on the time the test runs.
        await save_config(
            conn,
            "notifications",
            NotificationsConfig.model_validate(
                {"quiet_hours": {"enabled": False}, "quiet_all_day_on": []}
            ),
            created_by=None,
        )
    product = await seed_classified(backend_pool, "product_business", hours_ago=30)
    guidance = await seed_classified(backend_pool, "guidance_change", hours_ago=20)
    await seed_classified(backend_pool, "m_and_a", hours_ago=10, rumour=True)
    await seed_classified(backend_pool, "m_and_a", hours_ago=24 * 9)  # outside the window

    draft = EventRulesConfig().model_dump(mode="json")
    draft["severities"]["product_business"] = 3
    response = await api.post("/config/event-rules/preview", json=draft)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["events"] == 3
    assert (preview["alerts_before"], preview["alerts_after"]) == (1, 2)
    [change] = preview["changes"]
    assert change["cluster_id"] == product
    assert change["before"]["channels"] == [] and change["before"]["severity"] == 1
    assert change["after"]["channels"] == ["telegram"] and change["after"]["severity"] == 3

    muted = NotificationsConfig.model_validate(
        {"quiet_hours": {"enabled": False}, "quiet_all_day_on": [], "muted_tickers": ["AAPL_US_EQ"]}
    )
    response = await api.post("/config/notifications/preview", json=muted.model_dump(mode="json"))
    [change] = response.json()["changes"]
    assert change["cluster_id"] == guidance
    assert change["after"]["suppressed"] == "AAPL_US_EQ is muted"
    # A preview writes nothing.
    assert await backend_pool.fetchval("select count(*) from alert") == 0


async def test_replay_preview_answers_in_under_five_seconds(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    await backend_pool.execute(
        "insert into source (id, name, tier, type) values ('wire', 'Wire', 2, 'rss')"
    )
    # A heavy week: 1,500 classified stories over 30 holdings.
    await backend_pool.execute(
        "insert into story_cluster (headline, headline_key, best_tier, ticker, event_type,"
        " severity, status, first_seen, last_seen)"
        " select 's' || g, 's' || g, 2, 'T' || (g % 30) || '_EQ',"
        " (array['guidance_change','earnings_release','product_business',"
        " 'insider_transaction'])[1 + g % 4], 2, 'confirmed',"
        " now() - make_interval(mins => g * 6), now() - make_interval(mins => g * 6)"
        " from generate_series(1, 1500) g"
    )
    await backend_pool.execute(
        "insert into news_item (url, source_id, tier, published_at, headline, cluster_id,"
        " classified_at, event_type, affected_ticker, relevant)"
        " select 'https://wire.test/' || c.id, 'wire', 2, c.first_seen, c.headline, c.id,"
        " c.first_seen, c.event_type, c.ticker, true from story_cluster c"
    )
    started = time.monotonic()
    response = await api.post(
        "/config/notifications/preview",
        json=NotificationsConfig(daily_alert_cap=None).model_dump(mode="json"),
    )
    elapsed = time.monotonic() - started
    assert response.status_code == 200, response.text
    assert response.json()["events"] == 1500
    assert elapsed < 5, elapsed


# ---------------------------------------------------------------------------
# Trust tiers for reference data
# ---------------------------------------------------------------------------


async def test_a_gleif_parent_contradicting_wikidata_is_disputed_with_both_sources(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    await backend_pool.execute(
        "insert into instrument (t212_ticker, name, isin) values ('SUB_EQ', 'Sub', 'DE0000000001')"
    )
    common = {"ticker": "SUB_EQ", "subject": "LEI-SUB", "subject_name": "Sub AG"}
    await upsert_relationship(
        backend_pool, **common, type="parent", object_name="Holding SE",
        object_lei="LEI-HOLD", source="gleif", confirmed=True,
        source_ref="https://search.gleif.org/#/record/LEI-HOLD",
    )  # fmt: skip
    await upsert_relationship(
        backend_pool, **common, type="parent", object_name="Other Group plc", qid="Q1",
        source="wikidata", confirmed=False, source_ref="https://www.wikidata.org/wiki/Q9",
    )  # fmt: skip
    # Wikidata naming GLEIF's own parent agrees with it: not a dispute (and stays unconfirmed
    # until GLEIF lists it under that key, since Wikidata alone never confirms).
    await upsert_relationship(
        backend_pool, **common, type="parent", object_name="Holding SE", qid="Q2",
        source="wikidata", confirmed=False,
    )  # fmt: skip
    # A Wikidata-only competitor: a suggestion, never confirmed.
    await upsert_relationship(
        backend_pool, **common, type="competitor", object_name="Rival AG", qid="Q3",
        source="wikidata", confirmed=False,
    )  # fmt: skip
    disputed = await reconcile_structure(backend_pool, "SUB_EQ")
    assert len(disputed) == 1

    identity = (await api.get("/holdings/SUB_EQ/identity")).json()
    [dispute] = identity["disputed"]
    assert dispute["object_name"] == "Other Group plc" and dispute["tier"] == 3
    assert dispute["source_ref"] == "https://www.wikidata.org/wiki/Q9"
    assert dispute["conflict"]["object_name"] == "Holding SE"
    assert dispute["conflict"]["tier"] == 1 and dispute["conflict"]["source"] == "gleif"
    assert [p["object_name"] for p in identity["parents"]] == ["Holding SE"]
    assert {s["object_name"] for s in identity["suggested"]} == {"Holding SE", "Rival AG"}
    assert all(r["sources"] != ["wikidata"] for r in identity["parents"])

    # A re-run of Wikidata keeps the dispute; the owner can decide it.
    await upsert_relationship(
        backend_pool, **common, type="parent", object_name="Other Group plc", qid="Q1",
        source="wikidata", confirmed=False,
    )  # fmt: skip
    await reconcile_structure(backend_pool, "SUB_EQ")
    assert (await api.get("/holdings/SUB_EQ/identity")).json()["disputed"][0]["id"] == dispute["id"]
    rejected = await api.post(f"/relationships/{dispute['id']}/reject")
    assert rejected.status_code == 200, rejected.text
    assert (await api.get("/holdings/SUB_EQ/identity")).json()["disputed"] == []


# ---------------------------------------------------------------------------
# Eval sets grow from feedback
# ---------------------------------------------------------------------------


async def test_a_flagged_fact_and_item_join_the_next_eval_run(
    api: httpx.AsyncClient,
    backend_pool: asyncpg.Pool,
    migrated_db: MigratedDatabase,
    tmp_path: Path,
) -> None:
    needs_pgmq(migrated_db)
    # The fact: Apple's June 2025 quarter revenue, stored with the prior-year column's value.
    source = GOLDEN_DIR / "aapl-fy2025-q3"
    case = json.loads((source / "case.json").read_text())
    raw = gzip.decompress((source / case["document"]).read_bytes())
    storage = MemoryStorage()
    await storage.put("ab/aapl.html", raw, "text/html")
    async with backend_pool.acquire() as conn:
        await save_config(
            conn,
            "holding_profiles",
            HoldingProfilesConfig.model_validate(
                {"profiles": [{"ticker": "AAPL_US_EQ", "fiscal_year_end_month": 9}]}
            ),
            created_by=None,
        )
    await backend_pool.execute(
        "insert into instrument (t212_ticker, name, cik) values ('AAPL_US_EQ', 'Apple Inc.',"
        " '0000320193')"
    )
    job = await add_job(
        backend_pool, "earnings_check", "succeeded", params={"report_date": "2025-07-31"}
    )
    await backend_pool.execute(
        "insert into evidence (sha256, storage_path, source_url, content_type, byte_size)"
        " values ($1, 'ab/aapl.html', $2, 'text/html', $3)",
        "b" * 64,
        case["source_url"],
        len(raw),
    )
    fact = await backend_pool.fetchval(
        "insert into fact (ticker, metric, basis, fiscal_year, fiscal_period, period,"
        " period_end, value, value_as_written, scale, unit, currency, source_url, snippet,"
        " evidence_sha256, evidence_path, status, job_id, unit_snippet) values ('AAPL_US_EQ',"
        " 'revenue', 'reported', 2025, 'Q3', 'FY2025 Q3', '2025-06-28', 85777, '85,777',"
        " 'millions', 'currency', 'USD', $1, $2, $3, 'ab/aapl.html', 'verified', $4, $5)"
        " returning id",
        case["source_url"],
        "Total net sales (1) | 94,036 | 85,777 | 313,695 | 296,105",
        "b" * 64,
        job,
        case["figures"][0]["unit_snippet"],
    )
    flagged = await api.post(
        "/feedback",
        json={
            "target_kind": "fact",
            "target_id": fact,
            "rating": "wrong",
            "note": "Prior-year column.",
            "add_to_eval": True,
            "corrected_value": "94036",
            "corrected_value_as_written": "94,036",
        },
    )
    assert flagged.status_code == 201, flagged.text
    assert flagged.json()["effects"]["eval_candidate_id"] is not None

    # The news item: classified as a product story, really a guidance change.
    story = await seed_classified(backend_pool, "product_business", hours_ago=2)
    item = await backend_pool.fetchval("select id from news_item where cluster_id = $1", story)
    labelled = await api.post(
        "/feedback",
        json={
            "target_kind": "news_item",
            "target_id": item,
            "rating": "wrong",
            "add_to_eval": True,
            "corrected_label": {
                "event_type": "guidance_change",
                "affected_ticker": "AAPL_US_EQ",
                "relevant": True,
                "rumour": False,
            },
        },
    )
    assert labelled.status_code == 201, labelled.text

    golden_dir = tmp_path / "golden"
    news_set = tmp_path / "news.json"
    news_set.write_text(NEWS_SET.read_text())
    report = await export_candidates(backend_pool, storage, golden_dir, news_set)
    assert report.skipped == [] and len(report.written) == 2

    # The next eval run: the golden case is loaded, passes CI's checks and scores the label.
    [golden] = load_cases(golden_dir)
    assert golden.figures[0]["value"] == 94036 and golden.data["origin"] == "feedback"
    scores = await evaluate([golden], OracleExtractor([golden]))
    assert scores[0].accuracy == 1.0, scores[0]
    assert golden_checker().check_case(golden_dir / golden.id) == []
    items = {x.id: x for x in load_set(news_set)}
    assert items[f"fb-{item}"].expected["event_type"] == "guidance_change"
    assert len(items) == len(load_set()) + 1

    listed = (await api.get("/eval-candidates")).json()
    assert {c["kind"] for c in listed} == {"earnings_figure", "news_label"}
    assert all(c["exported_at"] for c in listed)

    # Exported once: a second export writes nothing.
    again = await export_candidates(backend_pool, storage, golden_dir, news_set)
    assert again.written == [] and again.skipped == []


# ---------------------------------------------------------------------------
# The evals gate
# ---------------------------------------------------------------------------


async def test_a_broken_prompt_fails_the_evals_gate_and_reverting_passes(tmp_path: Path) -> None:
    prompt = tmp_path / "classify.py"
    data = tmp_path / "set.json"
    good = 'SYSTEM_PROMPT = "Classify each item by event type."\n'
    prompt.write_text(good)
    data.write_text("{}")
    component = Component("classification", (prompt,), (data,), lambda: "claude-x", 0.85)
    calls: list[str] = []

    def scorer_for(value: float) -> Any:
        async def scorer(name: str) -> float:
            calls.append(name)
            return value

        return scorer

    # A passing live run records the baseline.
    first = await run_gate(None, True, scorer_for(0.91), (component,))
    assert first.ok and first.scores["classification"]["score"] == 0.91
    baseline_path = tmp_path / "baseline.json"
    write_baseline(first, baseline_path)
    baseline = load_baseline(baseline_path)

    # Nothing the model sees changed: no live call.
    unchanged = await run_gate(baseline, False, scorer_for(0.0), (component,))
    assert unchanged.ok and calls == ["classification"]

    # A deliberately broken prompt: without a key CI refuses it unmeasured, with a key the
    # live eval fails it.
    prompt.write_text('SYSTEM_PROMPT = "Answer anything."\n')
    assert not (await run_gate(baseline, False, scorer_for(0.9), (component,))).ok
    broken = await run_gate(baseline, True, scorer_for(0.41), (component,))
    assert not broken.ok and "FAIL" in broken.lines[0]
    # A regression below the recorded score fails even above the target.
    assert not (await run_gate(baseline, True, scorer_for(0.88), (component,))).ok

    # Reverting it passes again with no live run.
    prompt.write_text(good)
    calls.clear()
    reverted = await run_gate(baseline, False, scorer_for(0.0), (component,))
    assert reverted.ok and calls == []


def test_the_repository_fingerprints_cover_prompt_model_and_set() -> None:
    names = {c.name for c in COMPONENTS}
    assert names == {"earnings", "classification", "claims"}
    for component in COMPONENTS:
        assert all(p.exists() for p in component.code), component.name
        assert len(fingerprint(component)) == 64


# ---------------------------------------------------------------------------
# Activity page: coverage and system alerts
# ---------------------------------------------------------------------------


async def hold(pool: asyncpg.Pool, ticker: str, since: dt.datetime) -> None:
    job = await add_job(pool, "portfolio_sync", "succeeded", created_at=since)
    await pool.execute(
        "insert into instrument (t212_ticker, name) values ($1, $1) on conflict do nothing",
        ticker,
    )
    await pool.execute(
        "insert into position_snapshot (snapshot_date, ticker, job_id, captured_at, quantity,"
        " avg_price, current_price, instrument_currency, value, account_currency, value_account,"
        " cost_account, ppl, base_currency, fx_source, fx_rate, value_base) values"
        " ($1, $2, $3, $4, 1, 1, 1, 'USD', 1, 'USD', 1, 1, 0, 'USD', 'ecb', 1, 1)",
        since.date(),
        ticker,
        job,
        since,
    )


async def test_a_holding_no_news_run_covered_for_48_hours_shows_a_gap(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    now = dt.datetime.now(dt.UTC)
    for ticker in ("AAPL_US_EQ", "MSFT_US_EQ"):
        await hold(backend_pool, ticker, now - dt.timedelta(days=5))
    # News runs kept covering Apple; Microsoft was dropped from them three days ago.
    for hours, holdings in ((80, ["AAPL_US_EQ", "MSFT_US_EQ"]), (2, ["AAPL_US_EQ"])):
        job = await add_job(
            backend_pool,
            "news_ingest",
            "succeeded",
            created_at=now - dt.timedelta(hours=hours),
            steps=[("load_config", "succeeded", None, None)],
        )
        await backend_pool.execute(
            "update job_step set detail = $2::jsonb where job_id = $1",
            job,
            json.dumps({"holdings": holdings}),
        )
    view = (await api.get("/activity/coverage")).json()
    by_ticker = {h["ticker"]: h for h in view["holdings"]}
    assert by_ticker["AAPL_US_EQ"]["news_gap"] is False
    assert by_ticker["MSFT_US_EQ"]["news_gap"] is True
    covered = dt.datetime.fromisoformat(by_ticker["MSFT_US_EQ"]["news_last_covered"])
    assert now - covered > dt.timedelta(hours=79)
    assert view["gaps"] == 1 and view["news_window_hours"] == 48

    await backend_pool.execute(
        "insert into alert (kind, severity, channel, message, status, send_after, dedupe_key)"
        " values ('system', 2, 'telegram', 'no news coverage for MSFT_US_EQ', 'pending', now(),"
        " 'coverage:news:MSFT_US_EQ:x'), ('news', 3, 'telegram', 'Apple', 'sent', now(), null)"
    )
    system = (await api.get("/alerts", params={"kind": "system"})).json()
    assert [a["message"] for a in system] == ["no news coverage for MSFT_US_EQ"]
    assert len((await api.get("/alerts")).json()) == 2
