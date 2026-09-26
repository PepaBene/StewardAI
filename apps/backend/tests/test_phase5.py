"""Phase 5: research, the monthly review, chat, Form 13F ownership and report search."""

import csv
import datetime as dt
import io
import re
import zipfile
from collections import defaultdict
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool, get_pool_or_error
from api.main import create_app
from steward.ai import Usage
from steward.chat.assistant import (
    AnswerSentence,
    AssistantResult,
    ChatAnswerDraft,
    ChatDeps,
    run_chat,
)
from steward.chat.tools import ChatTools
from steward.config import HoldingProfilesConfig, StrategyConfig, ThesesConfig
from steward.config.jobs import DigestSettings
from steward.config.store import save_config
from steward.jobs.runner import JobContext, run_job
from steward.news.digest import build_digest, render_text
from steward.ownership.collect import portfolio_cusips, store_dataset
from steward.ownership.read import holding_ownership, tracked_manager_changes
from steward.research.run import ResearchDeps, run_research
from steward.research.writer import DraftSentence, SectionDraft, SectionRequest, SectionResult
from steward.review.monthly import ReviewDeps, ReviewDraft, first_weekday, run_review
from steward.sources.form13f import cusip_of_isin, dataset_links, parse_dataset
from tests.conftest import MigratedDatabase
from tests.test_phase1b import seed_snapshot
from tests.test_phase2_followups import needs_pgmq, seed_fact

pytestmark = pytest.mark.db

OWNER = "7f0c3d4e-0000-4000-8000-000000000001"
FIXTURES = Path(__file__).parent / "fixtures" / "ownership"
SAMPLE = FIXTURES / "form13f_sample.zip"
RULE = {"id": "max-single", "type": "position_weight", "operator": "<=", "value": 0.1}
US = {
    "AAPL_US_EQ": "US0378331005",
    "MSFT_US_EQ": "US5949181045",
    "NVDA_US_EQ": "US67066G1040",
}


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


# ---------------------------------------------------------------------------
# Form 13F
# ---------------------------------------------------------------------------


def test_the_index_page_lists_every_data_set() -> None:
    html = (FIXTURES / "form13f_index.html").read_text()
    links = dataset_links(
        html, "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets"
    )
    assert links[0].startswith("https://www.sec.gov/files/")
    assert all(u.endswith("_form13f.zip") for u in links)
    assert any("01mar2026-31may2026" in u for u in links)
    assert len(links) == len(set(links)) >= 8
    assert cusip_of_isin("US0378331005") == "037833100"
    assert cusip_of_isin("DE0007164600") is None


def independent_aggregation(path: Path, cusip: str) -> dict[dt.date, tuple[int, Decimal]]:
    """Holders and shares per period, straight from the data set's files: the latest of
    each manager's original and restatements, plus its NEW HOLDINGS amendments; share
    positions only (SH, no puts or calls)."""

    def table(name: str) -> list[dict[str, str]]:
        with zipfile.ZipFile(path) as z, z.open(name) as f:
            text = io.TextIOWrapper(f, encoding="utf-8")
            return list(csv.DictReader(text, delimiter="\t", quoting=csv.QUOTE_NONE))

    subs = {r["ACCESSION_NUMBER"]: r for r in table("SUBMISSION.tsv")}
    cover = {r["ACCESSION_NUMBER"]: r for r in table("COVERPAGE.tsv")}

    def when(r: dict[str, str]) -> dt.date:
        return dt.datetime.strptime(r["FILING_DATE"], "%d-%b-%Y").date()

    by_manager: dict[tuple[str, str], list[str]] = defaultdict(list)
    for acc, r in subs.items():
        if r["SUBMISSIONTYPE"] in ("13F-HR", "13F-HR/A"):
            by_manager[(r["CIK"], r["PERIODOFREPORT"])].append(acc)
    effective: dict[str, tuple[str, dt.date]] = {}
    for (cik, period), accs in by_manager.items():
        adds = [a for a in accs if cover.get(a, {}).get("AMENDMENTTYPE") == "NEW HOLDINGS"]
        bases = [a for a in accs if a not in adds]
        if bases:
            latest = max(bases, key=lambda a: (when(subs[a]), a))
            effective[latest] = (cik, dt.datetime.strptime(period, "%d-%b-%Y").date())
        for a in adds:
            effective[a] = (cik, dt.datetime.strptime(period, "%d-%b-%Y").date())
    holders: dict[dt.date, set[str]] = defaultdict(set)
    shares: dict[dt.date, Decimal] = defaultdict(Decimal)
    for row in table("INFOTABLE.tsv"):
        acc = row["ACCESSION_NUMBER"]
        # CUSIPs are case-insensitive; some filers write the letters in lower case.
        if row["CUSIP"].upper() != cusip or acc not in effective:
            continue
        if row["SSHPRNAMTTYPE"] != "SH" or row["PUTCALL"]:
            continue
        cik, period = effective[acc]
        holders[period].add(cik)
        shares[period] += Decimal(row["SSHPRNAMT"])
    return {p: (len(holders[p]), shares[p]) for p in holders}


async def load_sample(pool: asyncpg.Pool) -> None:
    for ticker, isin in US.items():
        await pool.execute(
            "insert into instrument (t212_ticker, isin) values ($1, $2)", ticker, isin
        )
    tickers = await portfolio_cusips(pool)
    data = parse_dataset(SAMPLE, set(tickers))
    await store_dataset(pool, "https://sec.test/sample_form13f.zip", data, tickers, None)


async def test_holder_counts_and_shares_equal_an_independent_aggregation(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    await load_sample(backend_pool)
    # The sample holds real rows: restatements, NEW HOLDINGS amendments, puts and calls,
    # principal-amount rows, notice filings and other issuers' rows.
    kinds = await backend_pool.fetchrow(
        "select count(*) filter (where amendment_type = 'RESTATEMENT') as restated,"
        " count(*) filter (where amendment_type = 'NEW HOLDINGS') as added"
        " from form13f_filing"
    )
    assert kinds["restated"] > 0 and kinds["added"] > 0
    assert await backend_pool.fetchval(
        "select count(*) from institutional_holding where put_call is not null"
    )
    assert (
        await backend_pool.fetchval(
            "select count(*) from institutional_holding where ticker is null"
        )
        == 0
    )  # other issuers' rows were not kept

    for ticker, isin in US.items():
        expected = independent_aggregation(SAMPLE, cusip_of_isin(isin) or "")
        ours = await holding_ownership(backend_pool, ticker, periods=10)
        got = {p.period: (p.holders, p.shares) for p in ours.periods}
        assert got == expected, ticker
        assert ours.top_holders and ours.top_holders[0].shares == max(
            h.shares for h in ours.top_holders
        )

    response = await api.get("/holdings/AAPL_US_EQ/ownership")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["periods"][0]["holders"] > 0 and body["source"] == "SEC Form 13F data sets"


async def test_tracked_manager_new_and_exited_positions(backend_pool: asyncpg.Pool) -> None:
    for ticker, isin in US.items():
        await backend_pool.execute(
            "insert into instrument (t212_ticker, isin) values ($1, $2)", ticker, isin
        )
    await backend_pool.execute("insert into form13f_dataset (url) values ('u')")
    rows = [
        ("0000000001-26-000001", "2025-12-31", "2026-02-10", [("037833100", "AAPL_US_EQ")]),
        ("0000000001-26-000002", "2026-03-31", "2026-05-10", [("594918104", "MSFT_US_EQ")]),
    ]
    for accession, period, filed, positions in rows:
        await backend_pool.execute(
            "insert into form13f_filing (accession, cik, manager_name, period, filed,"
            " submission_type, dataset_url) values ($1, '0000999999', 'Tracked LP', $2, $3,"
            " '13F-HR', 'u')",
            accession,
            dt.date.fromisoformat(period),
            dt.date.fromisoformat(filed),
        )
        for sk, (cusip, ticker) in enumerate(positions):
            await backend_pool.execute(
                "insert into institutional_holding (accession, infotable_sk, cusip, ticker,"
                " shares, shares_type) values ($1, $2, $3, $4, 100, 'SH')",
                accession,
                sk,
                cusip,
                ticker,
            )
    # The digest of the days the newer filing arrived lists both changes.
    body, _, _ = await build_digest(
        backend_pool,
        "weekly",
        dt.datetime(2026, 5, 12, tzinfo=dt.UTC),
        HoldingProfilesConfig(),
        DigestSettings(),
        None,
        None,
        ["999999"],
    )
    assert {(c.ticker, c.change) for c in body.ownership_changes} == {
        ("MSFT_US_EQ", "new"),
        ("AAPL_US_EQ", "exited"),
    }
    text = render_text(body, None, markdown=True)
    assert "Tracked LP opened a position in MSFT_US_EQ" in text
    assert "Tracked LP exited AAPL_US_EQ" in text

    changes = await tracked_manager_changes(backend_pool, ["999999"])
    assert {(c.ticker, c.change) for c in changes} == {
        ("MSFT_US_EQ", "new"),
        ("AAPL_US_EQ", "exited"),
    }
    # Already reported before this digest window: nothing.
    later = dt.datetime(2026, 6, 1, tzinfo=dt.UTC)
    assert await tracked_manager_changes(backend_pool, ["999999"], since=later) == []


# ---------------------------------------------------------------------------
# Deep research
# ---------------------------------------------------------------------------


class KillWorker(BaseException):
    """Stands in for the worker process dying: not an Exception, so nothing records it."""


class FakeResearchWriter:
    def __init__(self, die_on_call: int | None = None) -> None:
        self.calls: list[str] = []
        self.die_on_call = die_on_call

    async def write(self, request: SectionRequest) -> SectionResult:
        if self.die_on_call is not None and len(self.calls) + 1 == self.die_on_call:
            raise KillWorker()
        self.calls.append(request.section)
        ids = re.findall(r"\[(S\d+)\]", request.material)
        search = f"https://www.reuters.com/{request.section}"
        sentences = [
            DraftSentence(text=f"{request.section} rests on {ids[0]}.", source_ids=[ids[0]]),
            DraftSentence(text="Uncited claim.", source_ids=["S999"]),
            DraftSentence(text=f"From the web on {request.section}.", urls=[search]),
            DraftSentence(text="Not a search result.", urls=["https://www.reuters.com/unseen"]),
        ][: request.max_sentences + 1]
        usage = Usage(model="fake")
        usage.searches = 1 if request.max_searches else 0
        return SectionResult(
            SectionDraft(sentences=sentences), usage, {search: f"Reuters: {request.section}"}
        )


async def seed_holding(pool: asyncpg.Pool) -> None:
    await pool.execute(
        "insert into instrument (t212_ticker, name, isin, currency) values"
        " ('AAPL_US_EQ', 'Apple Inc.', 'US0378331005', 'USD')"
    )
    job = await pool.fetchval(
        "insert into job (job_type, trigger, status) values ('portfolio_sync', 'schedule',"
        " 'succeeded') returning id"
    )
    await pool.execute(
        "insert into position_snapshot (snapshot_date, ticker, job_id, captured_at, quantity,"
        " avg_price, current_price, instrument_currency, value, account_currency, value_account,"
        " cost_account, ppl, base_currency, fx_source, fx_rate, value_base, weight) values"
        " (current_date, 'AAPL_US_EQ', $1, now(), 10, 150, 240, 'USD', 2400, 'USD', 2400, 1500,"
        " 900, 'USD', 'ecb', 1, 2400, 0.12)",
        job,
    )
    # Four consecutive quarters of diluted EPS (TTM 6.00) and revenue a year apart.
    for fy, fp, eps in ((2025, "Q2", "1.40"), (2025, "Q3", "1.50"), (2025, "Q4", "1.60"),
                        (2026, "Q1", "1.50")):  # fmt: skip
        await seed_fact(pool, metric="eps_diluted", value=eps, number=Decimal(eps),
                        scale="units", unit="per_share", fy=fy, fp=fp)  # fmt: skip
    await seed_fact(pool, metric="revenue", value="100,000", number=Decimal(100000), fy=2025,
                    fp="Q1")  # fmt: skip
    await seed_fact(pool, metric="revenue", value="110,000", number=Decimal(110000), fy=2026,
                    fp="Q1")  # fmt: skip
    async with pool.acquire() as conn:
        await save_config(
            conn,
            "theses",
            ThesesConfig.model_validate(
                {"theses": [{"ticker": "AAPL_US_EQ", "why_owned": "Services growth."}]}
            ),
            created_by=None,
        )
        await save_config(
            conn,
            "strategy",
            StrategyConfig.model_validate(
                {
                    "base_currency": "USD",
                    "goals": "Long-term growth.",
                    "rules": [RULE],
                }
            ),
            created_by=None,
        )  # fmt: skip


def research_handler_with(writer: FakeResearchWriter) -> Any:
    async def handler(ctx: JobContext) -> None:
        await run_research(
            ctx, ResearchDeps(writer=writer, verifier=None, now=dt.datetime.now(dt.UTC))
        )

    return handler


async def test_research_killed_mid_run_resumes_from_the_last_completed_step(
    backend_pool: asyncpg.Pool,
) -> None:
    await seed_holding(backend_pool)
    job_id = await backend_pool.fetchval(
        "insert into job (job_type, trigger, params) values ('research', 'manual',"
        ' \'{"ticker": "AAPL_US_EQ", "profile": "standard"}\') returning id'
    )
    dying = FakeResearchWriter(die_on_call=3)
    with pytest.raises(KillWorker):
        await run_job(backend_pool, "research", research_handler_with(dying), trigger="manual",
                      job_id=job_id)  # fmt: skip
    assert dying.calls == ["business", "moat_risks"]
    assert await backend_pool.fetchval("select status from job where id = $1", job_id) == "running"

    # The queue redelivers the job to a new worker process.
    fresh = FakeResearchWriter()
    result = await run_job(backend_pool, "research", research_handler_with(fresh),
                           trigger="manual", job_id=job_id)  # fmt: skip
    assert result == "succeeded"
    assert fresh.calls == ["strategy_fit", "open_questions"]  # not business or moat again
    steps = await backend_pool.fetch(
        "select name, status, error from job_step where job_id = $1 order by step_index", job_id
    )
    assert [s["name"] for s in steps if s["name"] == "gather"] == ["gather"]  # gathered once
    interrupted = [s for s in steps if s["status"] == "failed"]
    assert [s["name"] for s in interrupted] == ["section:strategy_fit"]
    # A real kill leaves the step 'running' and the resume closes it as interrupted; the
    # simulated one unwinds through the step recorder, which names the exception.
    assert interrupted[0]["error"] in ("KillWorker",) or interrupted[0]["error"].startswith(
        "interrupted"
    )
    assert (
        await backend_pool.fetchval(
            "select count(*) from report where type = 'research' and job_id = $1", job_id
        )
        == 1
    )


async def test_a_research_report_cites_only_its_sources(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    await seed_holding(backend_pool)
    writer = FakeResearchWriter()
    assert await run_job(
        backend_pool, "research", research_handler_with(writer), trigger="manual",
        params={"ticker": "AAPL_US_EQ", "profile": "standard"},
    ) == "succeeded"  # fmt: skip
    listed = (await api.get("/research", params={"ticker": "AAPL_US_EQ"})).json()
    report = (await api.get(f"/research/{listed[0]['id']}")).json()
    body = report["body"]
    ids = {s["id"] for s in body["sources"]}
    for section in body["sections"]:
        for sentence in section["sentences"]:
            assert sentence["source_ids"] and set(sentence["source_ids"]) <= ids, sentence
    # Per model section: the cited sentence and the search sentence kept; the one citing an
    # unknown id and the one citing a URL that was not a search result dropped.
    business = next(s for s in body["sections"] if s["id"] == "business")
    assert [s["written_by"] for s in business["sentences"]] == ["model", "model"]
    assert body["dropped_sentences"] == 8
    search = [s for s in body["sources"] if s["kind"] == "search"]
    assert {s["tier"] for s in search} == {2}  # reuters.com, a configured tier-2 outlet
    # Figures are computed in code and cite the stored facts and the price.
    [pe] = [v for v in body["valuation"] if v["method"] == "pe_ttm"]
    assert pe["value"] == pytest.approx(240 / 6.0)
    kinds = {next(s["kind"] for s in body["sources"] if s["id"] == i) for i in pe["source_ids"]}
    assert kinds == {"position", "fact"}
    trend = next(s for s in body["sections"] if s["id"] == "financial_trend")
    assert any("+10.0% on FY2025 Q1" in s["text"] for s in trend["sentences"])
    # Unverified here (no API key): the model sentences are marked, never passed silently.
    assert body["claims"]["unchecked"] > 0
    assert all(
        s["check"] == "unchecked"
        for sec in body["sections"] if sec["written_by"] == "model"
        for s in sec["sentences"]
    )  # fmt: skip


async def test_two_profiles_give_visibly_different_reports(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    await seed_holding(backend_pool)
    for profile in ("standard", "quick"):
        assert await run_job(
            backend_pool, "research", research_handler_with(FakeResearchWriter()),
            trigger="manual", params={"ticker": "AAPL_US_EQ", "profile": profile},
        ) == "succeeded"  # fmt: skip
    reports = (await api.get("/research", params={"ticker": "AAPL_US_EQ"})).json()
    bodies = {r["profile"]: (await api.get(f"/research/{r['id']}")).json()["body"] for r in reports}
    assert len(bodies["standard"]["sections"]) == 10
    assert len(bodies["quick"]["sections"]) == 4
    assert bodies["quick"]["depth"] == "brief" and bodies["standard"]["depth"] == "standard"
    # A brief profile caps model sections at three sentences before checking citations.
    queued = await api.post("/jobs/research", json={"ticker": "AAPL_US_EQ", "profile": "nope"})
    assert queued.status_code == 422


async def test_report_search_finds_a_ticker_or_phrase_newest_first(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    for title, md in (
        ("Daily digest", "Apple raised its dividend. AAPL_US_EQ."),
        ("Research: Microsoft", "Azure growth slowed in the quarter."),
        ("Weekly digest", "Nothing on Apple this week; MSFT_US_EQ guidance."),
    ):
        await backend_pool.execute(
            "insert into report (type, title, body, body_md) values ('digest', $1, '{}', $2)",
            title,
            md,
        )
    hits = (await api.get("/reports/search", params={"q": "Apple"})).json()
    assert [h["title"] for h in hits] == ["Weekly digest", "Daily digest"]
    assert "<b>Apple</b>" in hits[0]["excerpt"]
    phrase = (await api.get("/reports/search", params={"q": '"growth slowed"'})).json()
    assert [h["title"] for h in phrase] == ["Research: Microsoft"]
    ticker = (await api.get("/reports/search", params={"q": "MSFT_US_EQ"})).json()
    assert [h["title"] for h in ticker] == ["Weekly digest"]


async def test_a_step_left_running_by_a_dead_worker_is_closed_on_resume(
    backend_pool: asyncpg.Pool,
) -> None:
    job_id = await backend_pool.fetchval(
        "insert into job (job_type, trigger, status) values ('research', 'manual', 'running')"
        " returning id"
    )
    await backend_pool.execute(
        "insert into job_step (job_id, step_index, name, status) values ($1, 0, 'gather',"
        " 'succeeded'), ($1, 1, 'section:business', 'running')",
        job_id,
    )
    await backend_pool.execute(
        "insert into job_checkpoint (job_id, key, output) values ($1, 'done', '{\"n\": 1}')",
        job_id,
    )
    seen: list[Any] = []

    async def handler(ctx: JobContext) -> None:
        seen.append(ctx.resumed)
        seen.append(await ctx.checkpoint("done", lambda: _never()))
        async with ctx.step("section:moat_risks"):
            pass

    assert await run_job(backend_pool, "research", handler, trigger="manual",
                         job_id=job_id) == "succeeded"  # fmt: skip
    assert seen == [True, {"n": 1}]
    steps = await backend_pool.fetch(
        "select step_index, name, status, error from job_step where job_id = $1"
        " order by step_index",
        job_id,
    )
    assert [(s["step_index"], s["status"]) for s in steps] == [
        (0, "succeeded"), (1, "failed"), (2, "succeeded")
    ]  # fmt: skip
    assert steps[1]["error"].startswith("interrupted")
    # Delivered again after it finished: not run a third time.
    assert await run_job(backend_pool, "research", handler, trigger="manual",
                         job_id=job_id) == "succeeded"  # fmt: skip
    assert len(seen) == 2


async def _never() -> Any:
    raise AssertionError("a completed step was recomputed")


# ---------------------------------------------------------------------------
# Monthly review
# ---------------------------------------------------------------------------


class FakeReviewWriter:
    def __init__(self, actions: list[dict[str, Any]]) -> None:
        self.actions = actions
        self.material = ""

    async def propose(
        self, material: str, today: dt.date
    ) -> tuple[ReviewDraft | None, Usage, str | None]:
        self.material = material
        return ReviewDraft.model_validate({"actions": self.actions}), Usage(model="fake"), None


REVIEW_STRATEGY = {
    "base_currency": "CZK",
    "target_positions": [
        {"ticker": "AAPL_US_EQ", "target_weight": 0.3, "band": 0.05, "hard_limit": 0.4}
    ],
    "rules": [
        {"id": "max-single", "type": "position_weight", "operator": "<=", "value": 0.4,
         "description": "No position above 40 %"},
        {"id": "min-cash", "type": "cash_weight", "operator": ">=", "value": 0.05},
        {"id": "max-count", "type": "position_count", "operator": "<=", "value": 10},
    ],
}  # fmt: skip


async def seed_review(pool: asyncpg.Pool) -> None:
    await seed_snapshot(pool, dt.date.today())
    async with pool.acquire() as conn:
        await save_config(
            conn, "strategy", StrategyConfig.model_validate(REVIEW_STRATEGY), created_by=None
        )
        await save_config(
            conn,
            "theses",
            ThesesConfig.model_validate(
                {"theses": [
                    {"ticker": "AAPL_US_EQ", "why_owned": "Services.", "kpis": [
                        {"id": "rev", "metric": "revenue", "operator": ">=", "threshold": 90,
                         "unit": "USD bn"}]},
                    {"ticker": "SAP_EQ", "why_owned": "Cloud.", "review_date": "2026-01-01",
                     "kpis": [{"id": "margin", "metric": "gross_margin", "operator": ">=",
                               "threshold": 70, "unit": "%"}]},
                ]}
            ),
            created_by=None,
        )  # fmt: skip
    await pool.execute(
        "insert into verdict (ticker, kpi_id, metric, basis, kpi_period, status, period,"
        " operator, threshold, threshold_unit, rationale, config_version_id) values"
        " ('AAPL_US_EQ', 'rev', 'revenue', 'reported', 'quarter', 'pass', 'FY2026 Q1', '>=',"
        " 90, 'USD bn', 'Revenue 95 >= 90.', (select max(id) from config_version))"
    )


async def test_the_review_lists_every_rule_and_only_cited_actions(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool
) -> None:
    await seed_review(backend_pool)
    writer = FakeReviewWriter(
        [
            {"action": "Trim Apple towards 30 %", "ticker": "AAPL_US_EQ",
             "rationale": "It breaches max-single.", "rule_ids": ["max-single"]},
            {"action": "Check SAP's margin data", "ticker": "SAP_EQ",
             "rationale": "Unknown KPI.", "kpis": ["SAP_EQ:margin"]},
            {"action": "Buy more of everything", "rationale": "Momentum."},
            {"action": "Sell SAP", "rationale": "Made up.", "rule_ids": ["no-such-rule"]},
        ]
    )  # fmt: skip

    async def handler(ctx: JobContext) -> None:
        await run_review(ctx, ReviewDeps(writer=writer, now=dt.datetime.now(dt.UTC)))

    assert await run_job(backend_pool, "monthly_review", handler, trigger="manual") == "succeeded"
    [summary] = (await api.get("/reviews")).json()
    body = (await api.get(f"/reviews/{summary['id']}")).json()["body"]

    rows = {r["id"]: r for r in body["compliance"]}
    # Every strategy rule is in the table with a state, none omitted.
    assert {"max-single", "min-cash", "max-count"} <= set(rows)
    assert rows["max-single"]["status"] == "breach"
    assert rows["min-cash"]["status"] == "pass" and rows["max-count"]["status"] == "pass"
    assert rows["hard_limit:AAPL_US_EQ"]["status"] == "breach"
    assert rows["band:position:AAPL_US_EQ"]["status"] == "breach"
    kpis = {k["ref"]: k["status"] for k in body["kpis"]}
    assert kpis == {"AAPL_US_EQ:rev": "pass", "SAP_EQ:margin": "unknown"}
    assert body["reviews_due"] == ["SAP_EQ"]
    # Every published action cites an existing rule or KPI; the rest were dropped.
    assert [a["action"] for a in body["actions"]] == [
        "Trim Apple towards 30 %", "Check SAP's margin data"
    ]  # fmt: skip
    assert all(a["rule_ids"] or a["kpis"] for a in body["actions"])
    assert body["dropped_actions"] == 2
    assert '"rule_ids": [' in writer.material and "max-single" in writer.material


async def test_a_scheduled_review_runs_only_on_the_first_weekday(
    backend_pool: asyncpg.Pool,
) -> None:
    assert first_weekday(dt.date(2026, 11, 3)) == dt.date(2026, 11, 2)  # 1 Nov is a Sunday
    assert first_weekday(dt.date(2026, 10, 1)) == dt.date(2026, 10, 1)

    async def handler_on(day: dt.date) -> Any:
        async def handler(ctx: JobContext) -> None:
            now = dt.datetime.combine(day, dt.time(7), dt.UTC)
            await run_review(ctx, ReviewDeps(writer=None, now=now))

        return handler

    await run_job(backend_pool, "monthly_review", await handler_on(dt.date(2026, 11, 3)),
                  trigger="schedule")  # fmt: skip
    assert await backend_pool.fetchval("select count(*) from report") == 0
    await run_job(backend_pool, "monthly_review", await handler_on(dt.date(2026, 11, 2)),
                  trigger="schedule")  # fmt: skip
    assert await backend_pool.fetchval("select count(*) from report where type = 'review'") == 1


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


class FakeChatAssistant:
    """Uses the real tools as the model would, then answers."""

    async def answer(
        self, history: list[dict[str, str]], question: str, tools: ChatTools, today: dt.date
    ) -> AssistantResult:
        self.history = history
        holding = await tools.call("holding", {"ticker": "AAPL_US_EQ"})
        eps = next(f for f in holding["facts"] if f["metric"] == "eps_diluted")
        await tools.call("search_news", {"ticker": "AAPL_US_EQ"})
        await tools.call(
            "propose_job",
            {"kind": "earnings_check", "ticker": "AAPL_US_EQ", "reason": "New quarter out."},
        )
        await tools.call("propose_job", {"kind": "research", "ticker": "MSFT_US_EQ",
                                         "reason": "Owner asked."})  # fmt: skip
        await tools.call("propose_job", {"kind": "portfolio_sync", "reason": "Stale."})
        bad = await tools.call("propose_job", {"kind": "research", "ticker": "X",
                                               "profile": "nope", "reason": "?"})  # fmt: skip
        assert "error" in bad
        url = "https://www.reuters.com/apple"
        draft = ChatAnswerDraft(
            sentences=[
                AnswerSentence(text=f"Apple's latest diluted EPS was {eps['value_as_written']}.",
                               source_ids=[eps["source_id"]]),
                AnswerSentence(text="Apple's latest diluted EPS was 9.99.",
                               source_ids=[eps["source_id"]]),
                AnswerSentence(text="Analysts expect more.", urls=[url]),
                AnswerSentence(text="It will rain tomorrow.", source_ids=["S999"]),
                AnswerSentence(text="I proposed an earnings check; confirm it below."),
            ]
        )  # fmt: skip
        return AssistantResult(draft, Usage(model="fake"), {url: "Reuters: Apple"})


async def test_chat_answers_cite_sources_or_say_no_source(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool, migrated_db: MigratedDatabase
) -> None:
    needs_pgmq(migrated_db)
    await seed_holding(backend_pool)
    conv = (await api.post("/chat/conversations", json={})).json()
    posted = await api.post(
        f"/chat/conversations/{conv['id']}/messages", json={"content": "What did Apple earn?"}
    )
    assert posted.status_code == 202, posted.text
    job_id = posted.json()["job_id"]
    assistant = FakeChatAssistant()

    async def handler(ctx: JobContext) -> None:
        await run_chat(ctx, ChatDeps(assistant=assistant, now=dt.datetime.now(dt.UTC)))

    assert await run_job(backend_pool, "chat", handler, trigger="chat", job_id=job_id) == (
        "succeeded"
    )
    detail = (await api.get(f"/chat/conversations/{conv['id']}")).json()
    assert detail["conversation"]["title"] == "What did Apple earn?"
    user, reply = detail["messages"]
    assert user["status"] == "done" and reply["role"] == "assistant"
    sentences = reply["answer"]["sentences"]
    sources = {s["id"]: s for s in reply["answer"]["sources"]}
    # Sourced sentences link to the app's own pages or the outlet.
    first = sentences[0]
    assert not first["no_source"] and first["unsupported"] is None
    assert sources[first["source_ids"][0]]["app_path"].startswith("/evidence/")
    assert sentences[1]["unsupported"].startswith("figure not in the cited snippets")
    assert sources[sentences[2]["source_ids"][0]]["url"] == "https://www.reuters.com/apple"
    # Unsourced sentences are kept, marked "no source", never shown as sourced.
    assert [s["no_source"] for s in sentences[3:]] == [True, True]
    assert reply["answer"]["tools_used"][:2] == ["holding", "search_news"]
    assert [a["kind"] for a in detail["actions"]] == [
        "earnings_check",
        "research",
        "portfolio_sync",
    ]
    assert all(a["status"] == "proposed" and a["job_id"] is None for a in detail["actions"])
    assert all(a["message_id"] == reply["id"] for a in detail["actions"])

    # Nothing started until confirmed; each confirmed action is a job with trigger chat.
    before = (await api.get("/jobs", params={"status": "queued"})).json()["jobs"]
    assert {j["job_type"] for j in before} <= {"chat"}
    for action in detail["actions"]:
        confirmed = await api.post(f"/chat/actions/{action['id']}/confirm")
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["status"] == "confirmed"
    again = await api.post(f"/chat/actions/{detail['actions'][0]['id']}/confirm")
    assert again.status_code == 409
    log = (await api.get("/jobs", params=[("job_type", t) for t in (
        "earnings_check", "research", "portfolio_sync")])).json()["jobs"]  # fmt: skip
    log = [j for j in log if j["trigger"] == "chat"]  # (the seeded sync is a scheduled job)
    assert {(j["job_type"], j["trigger"]) for j in log} == {
        ("earnings_check", "chat"), ("research", "chat"), ("portfolio_sync", "chat")
    }  # fmt: skip
    research = next(j for j in log if j["job_type"] == "research")
    assert research["params"] == {"ticker": "MSFT_US_EQ", "profile": "standard"}


async def test_the_second_question_sends_the_conversation_so_far(
    api: httpx.AsyncClient, backend_pool: asyncpg.Pool, migrated_db: MigratedDatabase
) -> None:
    needs_pgmq(migrated_db)
    await seed_holding(backend_pool)
    conv = (await api.post("/chat/conversations", json={"title": "Apple"})).json()
    assistant = FakeChatAssistant()

    async def handler(ctx: JobContext) -> None:
        await run_chat(ctx, ChatDeps(assistant=assistant, now=dt.datetime.now(dt.UTC)))

    for question in ("First?", "Second?"):
        posted = (await api.post(f"/chat/conversations/{conv['id']}/messages",
                                 json={"content": question})).json()  # fmt: skip
        await run_job(backend_pool, "chat", handler, trigger="chat", job_id=posted["job_id"])
    assert [m["role"] for m in assistant.history] == ["user", "assistant"]
    assert assistant.history[0]["content"] == "First?"
    listed = (await api.get("/chat/conversations")).json()
    assert listed[0]["title"] == "Apple" and listed[0]["last_message"]
