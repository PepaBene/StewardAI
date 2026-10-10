"""The news pipeline, notifier and digest against a real Postgres, with feeds served by a mock
transport and the model calls replaced by fakes.

Phase 3 ACs covered here:
- adding a source to config and saving makes it appear in the next ingest run, no deploy;
- repeated runs over the same feeds produce no duplicate news_item rows for a URL;
- a single tier-2-only item is never confirmed; a tier-1 item is confirmed with no search call;
- a severity-3 alert is sent right away outside quiet hours and held until they end inside;
- changing a severity's channel in config changes where the next test alert goes, no restart;
- the digest for a day with no severity-2+ events still generates and sends, saying so;
- every digest sentence cites a news item or fact it was generated from;
- every feed filter combination returns what a direct SQL query with the same filters returns.
"""

import datetime as dt
import itertools
import json
from collections.abc import AsyncIterator, Sequence
from email.utils import format_datetime
from typing import Any

import asyncpg
import httpx
import pytest

from api.auth import AuthenticatedUser, require_owner
from api.db import get_pool
from api.main import create_app
from steward.ai import Usage
from steward.config import (
    HoldingProfilesConfig,
    JobsConfig,
    NewsSourcesConfig,
    NotificationsConfig,
    ThesesConfig,
)
from steward.config.store import save_config
from steward.jobs.runner import JobContext, run_job
from steward.news.classify import ClassifyItem, ClassifyResult, HoldingContext, Label
from steward.news.corroborate import SearchFinding, SearchOutcome, SearchRequest
from steward.news.digest import DigestDraft, DraftSection, Sentence, run_digest
from steward.news.pipeline import NewsDeps, run_news_pipeline
from steward.notify.alerts import dispatch_due
from tests.conftest import MigratedDatabase

pytestmark = pytest.mark.db

OWNER = "00000000-0000-0000-0000-00000000000a"
NOON = dt.datetime(2026, 9, 25, 10, 0, tzinfo=dt.UTC)  # 12:00 in Prague
NIGHT = dt.datetime(2026, 9, 25, 21, 30, tzinfo=dt.UTC)  # 23:30 in Prague

IR_FEED = "https://ir.apple.test/rss"
WIRE_FEED = "https://wire.test/rss"
EXTRA_FEED = "https://extra.test/rss"


def rss(items: Sequence[tuple[str, str, dt.datetime]]) -> bytes:
    entries = "".join(
        f"<item><title>{title}</title><link>{link}</link>"
        f"<pubDate>{format_datetime(when)}</pubDate></item>"
        for title, link, when in items
    )
    return (
        f'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>{entries}'
        "</channel></rss>"
    ).encode()


class Feeds:
    def __init__(self) -> None:
        self.docs: dict[str, bytes] = {}
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        if url in self.docs:
            return httpx.Response(
                200, content=self.docs[url], headers={"content-type": "application/rss+xml"}
            )
        return httpx.Response(404)


class KeywordClassifier:
    """Stands in for the model: labels by keyword, affected holding = first candidate."""

    RULES = (
        ("guidance", "guidance_change"),
        ("acquire", "m_and_a"),
        ("buy", "m_and_a"),
        ("deal", "m_and_a"),
        ("unveils", "product_business"),
    )

    def __init__(self) -> None:
        self.calls = 0

    async def classify(
        self, items: Sequence[ClassifyItem], holdings: Sequence[HoldingContext], today: dt.date
    ) -> tuple[ClassifyResult, Usage]:
        self.calls += 1
        result = ClassifyResult()
        for item in items:
            text = item.headline.lower()
            event = next((e for k, e in self.RULES if k in text), "other")
            result.labels[item.index] = Label(
                event_type=event,
                affected_ticker=item.candidates[0].ticker if item.candidates else None,
                relevant=True,
                thesis_link=None,
                rumour="reportedly" in text,
                event_date=None,
            )
        return result, Usage(model="fake")


class FakeCorroborator:
    def __init__(self, finding: SearchFinding | None = None) -> None:
        self.requests: list[SearchRequest] = []
        self.finding = finding or SearchFinding(note="nothing found")

    async def search(self, request: SearchRequest, tier_of: Any) -> tuple[SearchOutcome, Usage]:
        self.requests.append(request)
        return SearchOutcome(self.finding), Usage(model="fake")


class FakeChannel:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, subject: str, text: str) -> None:
        self.sent.append((subject, text))


class Clock:
    def __init__(self, now: dt.datetime) -> None:
        self.now = now

    def __call__(self) -> dt.datetime:
        return self.now


async def setup_config(pool: asyncpg.Pool, extra_sources: Sequence[dict[str, Any]] = ()) -> None:
    await pool.execute(
        "insert into instrument (t212_ticker, name, currency) values"
        " ('AAPL_US_EQ', 'Apple', 'USD'), ('MSFT_US_EQ', 'Microsoft', 'USD')"
    )
    sources = [
        {"id": "edgar-filings", "name": "EDGAR", "tier": 1, "type": "edgar", "enabled": False},
        {"id": "apple-ir", "name": "Apple IR", "tier": 1, "type": "rss", "url": IR_FEED,
         "tickers": ["AAPL_US_EQ"]},
        {"id": "wire", "name": "Wire", "tier": 2, "type": "rss", "url": WIRE_FEED},
        *extra_sources,
    ]  # fmt: skip
    async with pool.acquire() as conn:
        await save_config(
            conn,
            "news_sources",
            NewsSourcesConfig.model_validate({"sources": sources}),
            created_by=None,
        )
        await save_config(
            conn,
            "holding_profiles",
            HoldingProfilesConfig.model_validate(
                {
                    "profiles": [
                        {"ticker": "AAPL_US_EQ", "company_name": "Apple Inc."},
                        {"ticker": "MSFT_US_EQ", "company_name": "Microsoft Corporation"},
                    ]
                }
            ),
            created_by=None,
        )
        await save_config(
            conn,
            "theses",
            ThesesConfig.model_validate(
                {"theses": [{"ticker": "AAPL_US_EQ", "why_owned": "Services growth."}]}
            ),
            created_by=None,
        )
        if not await conn.fetchval("select to_regnamespace('pgmq') is not null"):
            # Escalation reports are queued through pgmq; without it (plain local Postgres)
            # the route step would fail. test_phase3_completion covers escalation.
            jobs = JobsConfig.model_validate({"escalation": {"enabled": False}})
            await save_config(conn, "jobs", jobs, created_by=None)


@pytest.fixture
async def feeds() -> Feeds:
    return Feeds()


@pytest.fixture
async def http(feeds: Feeds) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.MockTransport(feeds.handler)) as client:
        yield client


def make_deps(
    http: httpx.AsyncClient,
    clock: Clock,
    corroborator: FakeCorroborator,
    channels: dict[str, FakeChannel],
) -> NewsDeps:
    return NewsDeps(
        http=http,
        classifier=KeywordClassifier(),
        corroborator=corroborator,
        channels=dict(channels),
        now=clock,
    )


async def ingest(pool: asyncpg.Pool, deps: NewsDeps) -> str:
    async def handler(ctx: JobContext) -> None:
        await run_news_pipeline(ctx, deps)

    return await run_job(pool, "news_ingest", handler, trigger="manual")


def first_run_feeds(
    feeds: Feeds, t: dt.datetime, extra: Sequence[tuple[str, str, dt.datetime]] = ()
) -> None:
    feeds.docs[IR_FEED] = rss(
        [("Apple cuts full-year guidance on weak iPhone demand", "https://ir.apple.test/n/1",
          t - dt.timedelta(hours=1))]
    )  # fmt: skip
    feeds.docs[WIRE_FEED] = rss(
        [
            ("Microsoft to acquire Contoso for $5 billion",
             "https://www.reuters.com/tech/msft-contoso", t - dt.timedelta(hours=3)),
            ("UPDATE 1-Microsoft to acquire Contoso for $5 bln",
             "https://www.cnbc.com/msft-contoso?utm_source=rss", t - dt.timedelta(hours=2)),
            ("Apple unveils a thinner watch", "https://www.reuters.com/tech/apple-watch",
             t - dt.timedelta(hours=2)),
            ("Bananas are cheap this year", "https://www.reuters.com/food/bananas",
             t - dt.timedelta(hours=2)),
            *extra,
        ]
    )  # fmt: skip


async def test_pipeline_end_to_end(backend_pool: asyncpg.Pool, feeds: Feeds, http: Any) -> None:
    await setup_config(backend_pool)
    clock = Clock(NOON)
    corroborator = FakeCorroborator()
    telegram = FakeChannel()
    deps = make_deps(http, clock, corroborator, {"telegram": telegram})
    first_run_feeds(feeds, NOON)

    assert await ingest(backend_pool, deps) == "succeeded"

    items = await backend_pool.fetch("select url, tickers, tier, cluster_id from news_item")
    urls = {r["url"] for r in items}
    assert "https://www.reuters.com/food/bananas" not in urls  # matched no holding
    assert "https://www.cnbc.com/msft-contoso" in urls  # tracking parameter removed
    assert len(items) == 4

    stories = {
        r["headline"]: r
        for r in await backend_pool.fetch(
            "select id, headline, status, confidence, severity, search_rounds, best_tier,"
            " source_count from story_cluster"
        )
    }
    assert len(stories) == 3  # the CNBC copy joined the Reuters story
    guidance = stories["Apple cuts full-year guidance on weak iPhone demand"]
    assert (guidance["status"], guidance["confidence"], guidance["severity"]) == (
        "confirmed", "verified", 3,
    )  # fmt: skip
    assert guidance["search_rounds"] == 0  # tier 1: confirmed with no search call
    deal = stories["Microsoft to acquire Contoso for $5 billion"]
    assert deal["source_count"] == 2
    assert (deal["status"], deal["severity"]) == ("unconfirmed", 3)  # one wire story, two copies
    assert deal["search_rounds"] == 1
    assert [r.headline for r in corroborator.requests] == [
        "Microsoft to acquire Contoso for $5 billion"
    ]
    watch = stories["Apple unveils a thinner watch"]
    assert (watch["status"], watch["severity"], watch["search_rounds"]) == ("unconfirmed", 1, 0)

    # The confirmed severity-3 story was sent to Telegram in the same run.
    assert len(telegram.sent) == 1
    assert "Apple" in telegram.sent[0][0] and "guidance" in telegram.sent[0][1].lower()
    alert = await backend_pool.fetchrow("select status, channel, sent_at from alert")
    assert alert["status"] == "sent" and alert["channel"] == "telegram"

    # Two hours later an independent outlet reports the deal (a different story).
    clock.now = NOON + dt.timedelta(hours=2)
    first_run_feeds(
        feeds,
        NOON,
        [("Microsoft agrees Contoso deal worth $5 billion in cash and stock",
          "https://apnews.com/article/msft", NOON + dt.timedelta(hours=1))],
    )  # fmt: skip
    assert await ingest(backend_pool, deps) == "succeeded"
    statuses = {
        r["headline"]: (r["status"], r["confidence"])
        for r in await backend_pool.fetch("select headline, status, confidence from story_cluster")
    }
    assert statuses["Microsoft to acquire Contoso for $5 billion"] == ("confirmed", "corroborated")
    alerts = await backend_pool.fetch(
        "select cluster_id, status, suppressed_reason from alert where kind = 'news' order by id"
    )
    # Guidance alert, then the deal: one sent. Its twin story (another outlet's report of
    # the same event) is a known event: no alert at all.
    assert [a["status"] for a in alerts] == ["sent", "sent"]
    twin = await backend_pool.fetchrow(
        "select duplicate_of, status_reason from story_cluster where headline like"
        " 'Microsoft agrees%'"
    )
    deal_id = await backend_pool.fetchval(
        "select id from story_cluster"
        " where headline = 'Microsoft to acquire Contoso for $5 billion'"
    )
    assert twin["duplicate_of"] == deal_id and "known event" in twin["status_reason"]
    assert len(telegram.sent) == 2

    # More runs over the same feeds (every 2 h): no new rows, never a duplicate URL.
    for hours in (4, 6, 8, 10):
        clock.now = NOON + dt.timedelta(hours=hours)
        assert await ingest(backend_pool, deps) == "succeeded"
    counts = await backend_pool.fetchrow(
        "select count(*) as n, count(distinct url) as urls from news_item"
    )
    assert counts["n"] == counts["urls"] == 5
    assert len(telegram.sent) == 2


async def test_quiet_hours_hold_the_alert(
    backend_pool: asyncpg.Pool, feeds: Feeds, http: Any
) -> None:
    await setup_config(backend_pool)
    clock = Clock(NIGHT)
    telegram = FakeChannel()
    deps = make_deps(http, clock, FakeCorroborator(), {"telegram": telegram})
    first_run_feeds(feeds, NIGHT)
    assert await ingest(backend_pool, deps) == "succeeded"

    alert = await backend_pool.fetchrow("select status, send_after from alert")
    assert alert["status"] == "pending"
    assert alert["send_after"] == dt.datetime(2026, 9, 26, 5, 0, tzinfo=dt.UTC)  # 07:00 Prague
    assert telegram.sent == []

    early = await dispatch_due(backend_pool, {"telegram": telegram}, now=NIGHT)
    assert early.sent == [] and telegram.sent == []
    later = await dispatch_due(
        backend_pool, {"telegram": telegram}, now=dt.datetime(2026, 9, 26, 5, 0, 30, tzinfo=dt.UTC)
    )
    assert len(later.sent) == 1 and len(telegram.sent) == 1


async def test_a_source_added_to_config_is_read_on_the_next_run(
    backend_pool: asyncpg.Pool, feeds: Feeds, http: Any
) -> None:
    await setup_config(backend_pool)
    clock = Clock(NOON)
    deps = make_deps(http, clock, FakeCorroborator(), {})
    feeds.docs[IR_FEED] = rss([])
    feeds.docs[WIRE_FEED] = rss([])
    feeds.docs[EXTRA_FEED] = rss(
        [("Microsoft unveils new Surface", "https://www.cnbc.com/surface", NOON)]
    )
    await ingest(backend_pool, deps)
    assert EXTRA_FEED not in feeds.requests

    stored = NewsSourcesConfig.model_validate(
        json.loads(
            await backend_pool.fetchval(
                "select body from config_version where domain = 'news_sources' and active"
            )
        )
    )
    body = stored.model_dump(mode="json")
    body["sources"].append(
        {"id": "extra", "name": "Extra", "tier": 2, "type": "rss", "url": EXTRA_FEED}
    )
    async with backend_pool.acquire() as conn:
        await save_config(
            conn, "news_sources", NewsSourcesConfig.model_validate(body), created_by=None
        )
    clock.now = NOON + dt.timedelta(hours=2)
    await ingest(backend_pool, deps)
    assert EXTRA_FEED in feeds.requests
    assert (
        await backend_pool.fetchval("select count(*) from news_item where source_id = 'extra'") == 1
    )
    source = await backend_pool.fetchrow(
        "select enabled, last_item_count from source where id = 'extra'"
    )
    assert source["enabled"] and source["last_item_count"] == 1


# ---------------------------------------------------------------------------
# API: feed filters, test alerts, digests
# ---------------------------------------------------------------------------


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


async def test_every_feed_filter_combination_matches_sql(
    backend_pool: asyncpg.Pool, feeds: Feeds, http: Any, api: httpx.AsyncClient
) -> None:
    await setup_config(backend_pool)
    deps = make_deps(http, Clock(NOON), FakeCorroborator(), {})
    first_run_feeds(feeds, NOON)
    await ingest(backend_pool, deps)

    tickers = [None, "AAPL_US_EQ", "MSFT_US_EQ"]
    severities: list[list[int]] = [[], [3], [1, 3], [2]]
    confidences: list[list[str]] = [[], ["verified"], ["unconfirmed"], ["verified", "unconfirmed"]]
    tiers: list[list[int]] = [[], [1], [2], [1, 2]]
    checked = 0
    for ticker, sev, conf, tier in itertools.product(tickers, severities, confidences, tiers):
        params: list[tuple[str, str | int]] = []
        if ticker:
            params.append(("ticker", ticker))
        params += [("severity", s) for s in sev] + [("confidence", c) for c in conf]
        params += [("tier", t) for t in tier]
        response = await api.get("/news", params=[*params, ("limit", 200)])
        assert response.status_code == 200, response.text
        got = [item["id"] for item in response.json()["items"]]

        sql = (
            "select i.id from news_item i join story_cluster c on c.id = i.cluster_id"
            " where ($1::text is null or $1 = any(i.tickers))"
            " and (cardinality($2::int[]) = 0 or c.severity = any($2))"
            " and (cardinality($3::text[]) = 0 or c.confidence = any($3))"
            " and (cardinality($4::int[]) = 0 or i.tier = any($4))"
            " order by i.id desc"
        )
        expected = [r["id"] for r in await backend_pool.fetch(sql, ticker, sev, conf, tier)]
        assert got == expected, (ticker, sev, conf, tier)
        checked += 1
    assert checked == 192


async def test_test_alert_follows_the_saved_channel(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    response = await api.post("/alerts/test", json={"severity": 3})
    assert response.status_code == 202
    first = response.json()
    assert [a["channel"] for a in first["alerts"]] == ["telegram"]

    config = NotificationsConfig().model_dump(mode="json")
    config["rules"][0]["channels"] = ["email"]
    config["quiet_hours"]["enabled"] = False
    saved = await api.put("/config/notifications", json=config)
    assert saved.status_code == 200

    second = (await api.post("/alerts/test", json={"severity": 3})).json()
    assert [a["channel"] for a in second["alerts"]] == ["email"]
    assert second["held_until"] is None

    email = FakeChannel()
    result = await dispatch_due(backend_pool, {"email": email})
    assert second["alerts"][0]["id"] in result.sent and len(email.sent) == 1
    # Telegram is not configured in this test: the first alert fails with a clear reason.
    failed = await backend_pool.fetchrow("select status, error from alert where id = $1",
                                         first["alerts"][0]["id"])  # fmt: skip
    assert failed["status"] in ("failed", "pending")

    severity_two = (await api.post("/alerts/test", json={"severity": 2})).json()
    assert severity_two["alerts"] == [] and "digest only" in severity_two["explanation"]


class FakeWriter:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def write(self, material: str, today: dt.date) -> tuple[DigestDraft, Usage]:
        self.prompts.append(material)
        groups = json.loads(material)
        sections = []
        for group in groups:
            item_id = group["stories"][0]["items"][0]["id"]
            sections.append(
                DraftSection(
                    ticker=group["ticker"],
                    sentences=[
                        Sentence(text=f"{group['name']} news.", news_item_ids=[item_id]),
                        Sentence(text="An uncited claim."),
                        Sentence(text="A made-up citation.", news_item_ids=[999999]),
                    ],
                )
            )
        overview = [
            Sentence(text="A busy day.", news_item_ids=[groups[0]["stories"][0]["items"][0]["id"]])
        ]
        return DigestDraft(overview=overview, sections=sections), Usage(model="fake")


async def _digest(
    pool: asyncpg.Pool, writer: FakeWriter, email: FakeChannel, now: dt.datetime
) -> int:
    report_id = 0

    async def handler(ctx: JobContext) -> None:
        nonlocal report_id
        report_id = await run_digest(
            ctx, "daily", writer=writer, channels={"email": email}, now=now, app_url=None
        )

    assert await run_job(pool, "daily_digest", handler, trigger="manual") == "succeeded"
    return report_id


async def test_a_quiet_day_still_sends_a_digest(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    writer, email = FakeWriter(), FakeChannel()
    report_id = await _digest(backend_pool, writer, email, NOON)
    assert writer.prompts == []  # nothing to summarise, no model call
    assert len(email.sent) == 1 and "Nothing material" in email.sent[0][1]

    report = (await api.get(f"/reports/{report_id}")).json()
    assert report["body"]["nothing_material"] is True
    assert report["delivery"]["email"]["status"] == "sent"
    listed = (await api.get("/reports", params={"period": "daily"})).json()
    assert listed[0]["id"] == report_id and listed[0]["nothing_material"] is True


async def test_digest_sentences_all_cite_their_sources(
    backend_pool: asyncpg.Pool, feeds: Feeds, http: Any, api: httpx.AsyncClient
) -> None:
    await setup_config(backend_pool)
    deps = make_deps(http, Clock(NOON), FakeCorroborator(), {})
    first_run_feeds(feeds, NOON)
    await ingest(backend_pool, deps)

    writer, email = FakeWriter(), FakeChannel()
    report_id = await _digest(backend_pool, writer, email, NOON + dt.timedelta(hours=1))
    body = (await api.get(f"/reports/{report_id}")).json()["body"]
    assert body["nothing_material"] is False
    assert body["dropped_sentences"] == 4  # two per section, uncited or miscited

    item_ids = {s["news_item_id"] for s in body["sources"]}
    sentences = body["overview"] + [s for sec in body["sections"] for s in sec["summary"]]
    assert sentences
    for s in sentences:
        assert s["news_item_ids"] or s["fact_ids"]
        assert set(s["news_item_ids"]) <= item_ids
    for section in body["sections"]:
        for story in section["stories"]:
            assert story["news_item_ids"] and set(story["news_item_ids"]) <= item_ids
    assert len(email.sent) == 1


async def test_phase3_tables_follow_the_access_model(migrated_db: MigratedDatabase) -> None:
    conn = await asyncpg.connect(migrated_db.admin_url)
    try:
        for table in ("source", "story_cluster", "news_item", "alert", "report"):
            assert await conn.fetchval(
                "select relrowsecurity from pg_class where oid = $1::regclass", f"public.{table}"
            )
            for role, privilege, allowed in (
                ("anon", "select", False),
                ("authenticated", "select", True),
                ("authenticated", "insert", False),
                ("app_backend", "select", True),
                ("app_backend", "update", True),
                # N3 lets the dedupe step remove the empty story it merges into an older one.
                ("app_backend", "delete", table == "story_cluster"),
            ):
                got = await conn.fetchval(
                    "select has_table_privilege($1, $2, $3)", role, f"public.{table}", privilege
                )
                assert got is allowed, (table, role, privilege)
    finally:
        await conn.close()
