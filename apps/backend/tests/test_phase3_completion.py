"""Phase 3 completion: regulatory news (RNS, EQS), EDGAR full-text search, macro series and
surprises, event escalation, portfolio breaches, the price-move trigger, digests, known
events, disputed stories, whole-day quiet, and monitoring alerts."""

import datetime as dt
import json
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest

from steward.ai import Usage
from steward.config import (
    EventRulesConfig,
    HoldingProfilesConfig,
    JobsConfig,
    NewsSourcesConfig,
    NotificationsConfig,
    StrategyConfig,
    ThesesConfig,
)
from steward.config.jobs import EurostatSeries
from steward.config.store import save_config
from steward.earnings.verdicts import run_verdicts
from steward.jobs.runner import JobContext, run_job
from steward.macro.collect import (
    MacroDeps,
    Observation,
    parse_jsonstat,
    run_macro_collect,
    store_and_evaluate,
)
from steward.monitor import run_monitor
from steward.news.digest import build_digest
from steward.news.escalation import (
    DraftResult,
    EscalationDeps,
    ImpactDraft,
    NoteSentence,
    run_escalation,
)
from steward.news.feeds import HoldingFeedInfo
from steward.news.pipeline import NewsDeps, run_news_pipeline
from steward.news.read import disputed_stories, recent_alerts, resolve_story
from steward.news.regulatory import (
    collect_regulatory,
    parse_efts,
    parse_eqs,
    parse_investegate,
)
from steward.notify.route import quiet_day_end
from steward.portfolio.breaches import evaluate, record_breaches
from steward.portfolio.drift import HoldingValue, compute_drift
from steward.reference.prices import PriceDeps, run_price_history
from tests.conftest import MigratedDatabase
from tests.test_news_pipeline import Clock, FakeChannel, FakeCorroborator, KeywordClassifier

FIXTURES = Path(__file__).parent / "fixtures" / "reference"
NOON = dt.datetime(2026, 9, 25, 10, 0, tzinfo=dt.UTC)


def load(name: str) -> Any:
    text = (FIXTURES / name).read_text()
    return json.loads(text) if name.endswith(".json") else text


def needs_pgmq(db: MigratedDatabase) -> None:
    if not any("pgmq" in name for name in db.applied):
        pytest.skip("pgmq extension not available on this server")


# ---------------------------------------------------------------------------
# Parsers on recorded pages
# ---------------------------------------------------------------------------


def test_investegate_rows() -> None:
    rows = parse_investegate(
        load("investegate_vod.html"), "https://www.investegate.co.uk/company/VOD"
    )
    assert rows and rows[0].headline == "Director/PDMR Shareholding"
    assert rows[0].url.startswith("https://www.investegate.co.uk/announcement/rns/")
    # 21 Sep 2026 10:26 AM London (BST) is 09:26 UTC.
    assert rows[0].published_at == dt.datetime(2026, 9, 21, 9, 26, tzinfo=dt.UTC)
    assert rows[0].service == "RNS"
    rules = EventRulesConfig()
    assert rules.announcement_event("rns", rows[0].announcement_type) == "insider_transaction"


def test_eqs_items() -> None:
    items = parse_eqs(load("eqs_company.html"))
    assert items and all(i.url.startswith("https://www.eqs-news.com/news/") for i in items)
    first = items[0]
    assert first.announcement_type == "Voting rights" and first.isin == "LU2290523658"
    # 25 September 2026 21:00 Frankfurt (CEST) is 19:00 UTC.
    assert first.published_at == dt.datetime(2026, 9, 25, 19, 0, tzinfo=dt.UTC)
    assert "HomeToGo" in first.headline or "Hometogo" in first.headline
    assert EventRulesConfig().announcement_event("eqs", "Ad-hoc") is None  # left to the model


def test_efts_hits() -> None:
    hits = parse_efts(load("efts_supplier.json"))
    assert [h.filer_name for h in hits] == ["KLA CORP", "LAM RESEARCH CORP"]
    assert hits[0].url == (
        "https://www.sec.gov/Archives/edgar/data/319201/000031920126000027/klac-20260630.htm"
    )
    assert hits[0].form == "10-K" and hits[0].filed_at == dt.date(2026, 8, 6)


def test_eurostat_jsonstat() -> None:
    series = EurostatSeries(
        key="hicp",
        label="HICP inflation, annual rate",
        dataset="prc_hicp_minr",
        filters={"unit": "RCH_A"},
    )
    obs = {
        (o.series_id, o.period_label): o for o in parse_jsonstat(load("eurostat_hicp.json"), series)
    }
    assert obs[("hicp:EA21", "2026-08")].value == Decimal("3.2")
    assert obs[("hicp:CZ", "2026-06")].value == Decimal("1.1")
    assert obs[("hicp:DE", "2026-07")].value == Decimal("2.8")
    assert obs[("hicp:EA21", "2026-08")].release_date == dt.date(2026, 9, 17)
    assert obs[("hicp:EA21", "2026-08")].label == "Euro area HICP inflation, annual rate"


def test_quiet_all_day_on_weekends_and_holidays() -> None:
    config = NotificationsConfig.model_validate({"quiet_all_day_on": ["weekends", "holidays"]})
    saturday = dt.datetime(2026, 9, 26, 10, tzinfo=dt.UTC)
    holiday = dt.date(2026, 9, 28)  # Monday, St Wenceslas Day
    end = quiet_day_end(config, saturday, {holiday})
    assert end is not None and end.date() == dt.date(2026, 9, 29) and end.hour == 7
    assert quiet_day_end(config, dt.datetime(2026, 9, 29, 10, tzinfo=dt.UTC), {holiday}) is None
    assert quiet_day_end(NotificationsConfig(), saturday, {holiday}) is None


# ---------------------------------------------------------------------------
# Against Postgres
# ---------------------------------------------------------------------------

db = pytest.mark.db


class Pages:
    def __init__(self) -> None:
        self.docs: dict[str, tuple[int, str, str]] = {}
        self.json: dict[str, Any] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.url.host == "efts.sec.gov":
            key = request.url.params.get("q", "")
            return httpx.Response(200, json=self.json.get(key, {"hits": {"hits": []}}))
        base = url.split("?")[0]
        if base in self.docs:
            status, body, ctype = self.docs[base]
            return httpx.Response(status, text=body, headers={"content-type": ctype})
        for prefix, body in self.json.items():
            if base.startswith(prefix):
                return httpx.Response(200, json=body)
        return httpx.Response(404)


@pytest.fixture
def pages() -> Pages:
    return Pages()


@pytest.fixture
async def http(pages: Pages) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.MockTransport(pages.handler)) as client:
        yield client


INVESTEGATE = "https://www.investegate.co.uk/company/VOD"


def investegate_page(rows: list[tuple[str, str, str, str]]) -> str:
    body = "".join(
        f'<tr><td>{day}</td><td>{clock}</td><td><div><a class="regulatory">RNS</a></div></td>'
        f'<td><a class="announcement-link" href="{href}">{title}</a></td></tr>'
        for day, clock, title, href in rows
    )
    return f'<table class="table-investegate"><tbody>{body}</tbody></table>'


async def setup_uk_holding(pool: asyncpg.Pool) -> None:
    await pool.execute(
        "insert into instrument (t212_ticker, name, isin, currency, home_country) values"
        " ('VODl_EQ', 'Vodafone Group', 'GB00BH4HKS39', 'GBX', 'GB'),"
        " ('SAPd_EQ', 'SAP SE', 'DE0007164600', 'EUR', 'DE')"
    )
    sources = [
        {"id": "rns-investegate", "name": "RNS", "tier": 1, "type": "rns"},
        {"id": "edgar-filings", "name": "EDGAR", "tier": 1, "type": "edgar", "enabled": False},
    ]
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
                        {
                            "ticker": "VODl_EQ",
                            "company_name": "Vodafone Group Plc",
                            "regulatory_news_urls": [INVESTEGATE],
                        },
                        {"ticker": "SAPd_EQ", "company_name": "SAP SE"},
                    ]
                }
            ),
            created_by=None,
        )


async def ingest(pool: asyncpg.Pool, http: httpx.AsyncClient, now: dt.datetime) -> str:
    deps = NewsDeps(
        http=http,
        classifier=KeywordClassifier(),
        corroborator=FakeCorroborator(),
        channels={"telegram": FakeChannel()},
        now=Clock(now),
    )

    async def handler(ctx: JobContext) -> None:
        await run_news_pipeline(ctx, deps)

    return await run_job(pool, "news_ingest", handler, trigger="manual")


@db
async def test_rns_results_start_the_earnings_check_and_ingest_once(
    backend_pool: asyncpg.Pool, http: httpx.AsyncClient, pages: Pages, migrated_db: MigratedDatabase
) -> None:
    needs_pgmq(migrated_db)
    await setup_uk_holding(backend_pool)
    pages.docs[INVESTEGATE] = (
        200,
        investegate_page(
            [
                (
                    "25 Sep 2026",
                    "07:00 AM",
                    "Half-year Results",
                    "https://www.investegate.co.uk/announcement/rns/vodafone--vod/half-year-results/1",
                ),
                (
                    "24 Sep 2026",
                    "10:26 AM",
                    "Director/PDMR Shareholding",
                    "https://www.investegate.co.uk/announcement/rns/vodafone--vod/pdmr/2",
                ),
            ]
        ),
        "text/html",
    )
    for hours in (0, 2):
        assert await ingest(backend_pool, http, NOON + dt.timedelta(hours=hours)) == "succeeded"
    items = await backend_pool.fetch(
        "select url, event_type, tier, tickers from news_item order by id"
    )
    assert len(items) == 2 and len({i["url"] for i in items}) == 2  # each exactly once
    assert [i["event_type"] for i in items] == ["insider_transaction", "earnings_release"]
    assert all(i["tier"] == 1 and i["tickers"] == ["VODl_EQ"] for i in items)
    check = await backend_pool.fetchval("select params from job where job_type = 'earnings_check'")
    assert json.loads(check)["ticker"] == "VODl_EQ"
    # SAP's home market is Germany: RNS was not read for it.
    assert (
        await backend_pool.fetchval("select count(*) from news_item where 'SAPd_EQ' = any(tickers)")
        == 0
    )


async def test_regulatory_service_follows_the_home_market(
    http: httpx.AsyncClient, pages: Pages
) -> None:
    pages.docs[INVESTEGATE] = (
        200,
        investegate_page(
            [("25 Sep 2026", "07:00 AM", "Trading Statement", "/announcement/rns/vod/ts/3")]
        ),
        "text/html",
    )
    holdings = [
        HoldingFeedInfo(
            "VODl_EQ", ("Vodafone",), None, "VOD", home_country="GB", regulatory_urls=(INVESTEGATE,)
        ),
        HoldingFeedInfo("SAPd_EQ", ("SAP",), None, "SAP", home_country="DE"),
    ]
    rns = await collect_regulatory(http, "rns", "rns", holdings, EventRulesConfig(), "GB")
    assert [(i.tickers, i.event_type) for i in rns.items] == [(("VODl_EQ",), "earnings_release")]
    eqs = await collect_regulatory(http, "eqs", "eqs", holdings, EventRulesConfig(), "DE")
    assert eqs.items == [] and "EQS News company page" in eqs.errors[0]


@db
async def test_edgar_fts_topics_become_items_and_filers_become_suggestions(
    backend_pool: asyncpg.Pool, http: httpx.AsyncClient, pages: Pages
) -> None:
    await backend_pool.execute(
        "insert into instrument (t212_ticker, name, currency, cik) values"
        " ('TSM_US_EQ', 'Taiwan Semiconductor', 'USD', '0001046179')"
    )
    async with backend_pool.acquire() as conn:
        await save_config(
            conn,
            "news_sources",
            NewsSourcesConfig.model_validate(
                {"sources": [{"id": "fts", "name": "EDGAR FTS", "tier": 1, "type": "edgar_fts"}]}
            ),
            created_by=None,
        )
        await save_config(
            conn,
            "holding_profiles",
            HoldingProfilesConfig.model_validate(
                {
                    "profiles": [
                        {
                            "ticker": "TSM_US_EQ",
                            "company_name": "Taiwan Semiconductor",
                            "watch_topics": ["chip export controls"],
                        }
                    ]
                }
            ),
            created_by=None,
        )
    topic_hit = load("efts_supplier.json")
    for hit in topic_hit["hits"]["hits"]:
        hit["_source"]["file_date"] = "2026-09-24"
    pages.json['"chip export controls"'] = topic_hit
    pages.json['"Taiwan Semiconductor" customer'] = load("efts_supplier.json")

    deps = NewsDeps(
        http=http, classifier=KeywordClassifier(), corroborator=FakeCorroborator(), now=Clock(NOON)
    )
    from steward.sources.edgar import EdgarClient

    deps.edgar = EdgarClient(http, "StewardAI test test@example.com")

    async def handler(ctx: JobContext) -> None:
        await run_news_pipeline(ctx, deps)

    assert await run_job(backend_pool, "news_ingest", handler, trigger="manual") == "succeeded"
    items = await backend_pool.fetch("select headline, links from news_item order by id")
    assert len(items) == 2
    assert all(json.loads(i["links"])[0]["via"] == "topic" for i in items)
    hits = await backend_pool.fetch("select kind, news_item_id, relationship_id from filing_hit")
    # The keyword search no longer tags a filer "supplier"/"customer" (D5.8): the hit is a
    # review candidate with no role until a fetched passage is read (steward.graph.passages).
    assert {h["kind"] for h in hits} == {"topic", "relationship_candidate"}
    assert all(h["news_item_id"] for h in hits if h["kind"] == "topic")
    # A filer near "customer"/"supplier" proves no relationship (D5.8): the hits stay unlinked
    # review candidates until a fetched passage is resolved.
    assert await backend_pool.fetchval("select count(*) from entity_relationship") == 0
    assert all(h["relationship_id"] is None for h in hits)


# ---------------------------------------------------------------------------
# Macro
# ---------------------------------------------------------------------------


def obs(
    series: str, period: dt.date, value: str, release: dt.date, policy: bool = False
) -> Observation:
    return Observation(
        series,
        series.split(":")[0],
        "eurostat" if not policy else "ecb",
        period,
        period.strftime("%Y-%m"),
        Decimal(value),
        f"Label {series}",
        "%",
        release,
        "https://ec.europa.eu/eurostat/databrowser/view/x/default/table",
        policy,
    )


@db
async def test_macro_surprise_only_on_release_day(backend_pool: asyncpg.Pool) -> None:
    bands = EventRulesConfig().macro_surprise_bands
    july, aug, sep = dt.date(2026, 7, 1), dt.date(2026, 8, 1), dt.date(2026, 9, 1)
    count, events = await store_and_evaluate(
        backend_pool, [obs("hicp:EA21", july, "2.4", dt.date(2026, 8, 18))], bands
    )
    assert (count, events) == (1, [])  # nothing to compare with
    # A revision of July is stored but never evaluated.
    count, events = await store_and_evaluate(
        backend_pool, [obs("hicp:EA21", july, "3.9", dt.date(2026, 9, 17))], bands
    )
    assert (count, events) == (0, [])
    # August, released 17 Sep: +0.2 pp against July's stored 3.9 is inside the 0.3 band.
    _, events = await store_and_evaluate(
        backend_pool, [obs("hicp:EA21", aug, "4.1", dt.date(2026, 9, 17))], bands
    )
    assert events == []
    # September: +0.5 pp is a surprise, citing its release date and source URL.
    _, events = await store_and_evaluate(
        backend_pool, [obs("hicp:EA21", sep, "4.6", dt.date(2026, 10, 1))], bands
    )
    assert len(events) == 1 and events[0].event_type == "macro_surprise"
    assert "2026-10-01" in events[0].summary and "eurostat" in events[0].url
    # A policy rate: any change is a central-bank decision.
    await store_and_evaluate(
        backend_pool,
        [obs("ecb_deposit_rate", dt.date(2026, 9, 24), "2.5", dt.date(2026, 9, 24), True)],
        bands,
    )
    _, events = await store_and_evaluate(
        backend_pool,
        [obs("ecb_deposit_rate", dt.date(2026, 9, 25), "2.25", dt.date(2026, 9, 25), True)],
        bands,
    )
    assert [e.event_type for e in events] == ["central_bank"]


@db
async def test_a_rate_decision_reaches_the_next_digest_with_cited_figures(
    backend_pool: asyncpg.Pool, http: httpx.AsyncClient, pages: Pages
) -> None:
    csv_rows = "KEY,FREQ,TIME_PERIOD,OBS_VALUE\n" + "\n".join(
        f"FM.D.U2.EUR.4F.KR.DFR.LEV,D,2026-09-{d},{v}" for d, v in ((23, "2.5"), (24, "2.5"))
    )
    pages.docs["https://data-api.ecb.europa.eu/service/data/FM/D.U2.EUR.4F.KR.DFR.LEV"] = (
        200,
        csv_rows,
        "text/csv",
    )
    async with backend_pool.acquire() as conn:
        await save_config(
            conn,
            "jobs",
            JobsConfig.model_validate({"macro": {"eurostat": [], "fred": []}}),
            created_by=None,
        )

    async def collect(now: dt.datetime) -> None:
        async def handler(ctx: JobContext) -> None:
            await run_macro_collect(ctx, MacroDeps(http=http, today=now.date(), now=now))

        assert (
            await run_job(backend_pool, "macro_collect", handler, trigger="manual") == "succeeded"
        )

    await collect(NOON - dt.timedelta(days=1))
    pages.docs["https://data-api.ecb.europa.eu/service/data/FM/D.U2.EUR.4F.KR.DFR.LEV"] = (
        200,
        csv_rows + "\nFM.D.U2.EUR.4F.KR.DFR.LEV,D,2026-09-25,2.25",
        "text/csv",
    )
    await collect(NOON)
    # The news run assesses the macro story (tier 1: confirmed); the digest lists it.
    async with backend_pool.acquire() as conn:
        await save_config(
            conn,
            "news_sources",
            NewsSourcesConfig.model_validate(
                {
                    "sources": [
                        {
                            "id": "edgar-filings",
                            "name": "EDGAR",
                            "tier": 1,
                            "type": "edgar",
                            "enabled": False,
                        }
                    ]
                }
            ),
            created_by=None,
        )
    assert await ingest(backend_pool, http, NOON) == "succeeded"
    story = await backend_pool.fetchrow(
        "select status, event_type, severity, ticker from story_cluster"
    )
    assert (story["status"], story["event_type"], story["ticker"]) == (
        "confirmed",
        "central_bank",
        None,
    )

    from steward.config.jobs import DigestSettings

    body, _, _ = await build_digest(
        backend_pool,
        "daily",
        NOON + dt.timedelta(hours=12),
        HoldingProfilesConfig(),
        DigestSettings(),
        None,
    )
    macro_section = next(s for s in body.sections if s.ticker is None)
    assert any(st.event_type == "central_bank" for st in macro_section.stories)
    figure = next(m for m in body.macro if m.series_id == "ecb_deposit_rate")
    assert figure.value == 2.25 and figure.release_date == NOON.date() and figure.new
    assert figure.url.startswith("https://data.ecb.europa.eu/")


# ---------------------------------------------------------------------------
# Escalation and price moves
# ---------------------------------------------------------------------------


class FakeEscalator:
    def __init__(self, draft: ImpactDraft, search_urls: set[str] | None = None) -> None:
        self.draft = draft
        self.search_urls = set(search_urls or ())
        self.requests: list[Any] = []

    async def write(self, request: Any, today: dt.date) -> DraftResult:
        self.requests.append(request)
        return DraftResult(
            self.draft, Usage(model="claude-sonnet-5", input_tokens=1000), self.search_urls
        )


async def seed_story(pool: asyncpg.Pool, severity: int = 3, status: str = "confirmed") -> int:
    await pool.execute(
        "insert into instrument (t212_ticker, name) values ('AAPL_US_EQ', 'Apple')"
        " on conflict do nothing"
    )
    await pool.execute(
        "insert into source (id, name, tier, type) values ('wire', 'Wire', 2, 'rss')"
        " on conflict do nothing"
    )
    cluster = await pool.fetchval(
        "insert into story_cluster (ticker, event_type, severity, status, confidence, headline,"
        " headline_key, best_tier) values ('AAPL_US_EQ', 'guidance_change', $1, $2,"
        " 'corroborated', 'Apple cuts guidance', 'apple cuts guidance', 2) returning id",
        severity,
        status,
    )
    await pool.execute(
        "insert into news_item (url, source_id, outlet, tier, published_at, headline,"
        " cluster_id, tickers) values ('https://www.reuters.com/apple-guidance', 'wire',"
        " 'reuters.com', 2, now(), 'Apple cuts guidance', $1, '{AAPL_US_EQ}')",
        cluster,
    )
    return int(cluster)


THESES = ThesesConfig.model_validate(
    {
        "theses": [
            {
                "ticker": "AAPL_US_EQ",
                "why_owned": "Services growth.",
                "kpis": [
                    {
                        "id": "rev",
                        "metric": "revenue",
                        "operator": ">",
                        "threshold": 1,
                        "unit": "USD bn",
                        "description": "Revenue",
                    }
                ],
                "exit_conditions": ["Services growth below 5 %"],
            }
        ]
    }
)


@db
async def test_escalation_cites_sources_and_names_the_thesis_link(
    backend_pool: asyncpg.Pool,
) -> None:
    cluster = await seed_story(backend_pool)
    async with backend_pool.acquire() as conn:
        await save_config(conn, "theses", THESES, created_by=None)
    draft = ImpactDraft(
        headline="Guidance cut puts the revenue KPI at risk",
        sentences=[
            NoteSentence(
                text="Apple cut its full-year guidance.",
                source_urls=["https://www.reuters.com/apple-guidance"],
            ),
            NoteSentence(text="Analysts expect more cuts.", source_urls=["https://evil.test/x"]),
            NoteSentence(text="Services grew 12 %.", source_urls=["https://apnews.com/found"]),
        ],
        kpi_ids=["rev", "nope"],
        exit_conditions=[1, 9],
        none_applies=False,
        rationale="Lower revenue guidance bears on the revenue KPI.",
    )
    writer = FakeEscalator(draft, {"https://apnews.com/found"})

    async def handler(ctx: JobContext) -> None:
        await run_escalation(ctx, EscalationDeps(writer=writer, now=NOON))  # type: ignore[arg-type]

    assert (
        await run_job(
            backend_pool,
            "event_escalation",
            handler,
            trigger="event",
            params={"cluster_id": cluster, "ticker": "AAPL_US_EQ"},
        )
        == "succeeded"
    )
    report = await backend_pool.fetchrow(
        "select ticker, body from report where type = 'escalation'"
    )
    body = json.loads(report["body"])
    assert report["ticker"] == "AAPL_US_EQ" and body["dropped_sentences"] == 1
    assert [s["text"] for s in body["sentences"]] == [
        "Apple cut its full-year guidance.",
        "Services grew 12 %.",
    ]
    assert body["kpis"] == [{"id": "rev", "description": "Revenue"}]
    assert body["exit_conditions"] == ["Services growth below 5 %"] and not body["none_applies"]
    urls = [s["url"] for s in body["sources"]]
    assert (
        urls[0] == "https://www.reuters.com/apple-guidance" and "https://apnews.com/found" in urls
    )

    # With nothing named, the note states that none applies.
    writer.draft = draft.model_copy(update={"kpi_ids": [], "exit_conditions": []})
    await run_job(
        backend_pool,
        "event_escalation",
        handler,
        trigger="event",
        params={"cluster_id": cluster, "ticker": "AAPL_US_EQ"},
    )
    latest = json.loads(
        await backend_pool.fetchval(
            "select body from report where type = 'escalation' order by id desc limit 1"
        )
    )
    assert latest["none_applies"] and latest["thesis_link"].startswith("none")


async def seed_prices(pool: asyncpg.Pool, move_pct: float) -> dt.date:
    await pool.execute(
        "insert into instrument (t212_ticker, name, price_symbol) values"
        " ('AAPL_US_EQ', 'Apple', 'aapl.us') on conflict (t212_ticker) do update"
        " set price_symbol = 'aapl.us'"
    )
    job = await pool.fetchval(
        "insert into job (job_type, trigger, status) values ('portfolio_sync', 'schedule',"
        " 'succeeded') returning id"
    )
    today = dt.date(2026, 9, 25)
    await pool.execute(
        "insert into position_snapshot (snapshot_date, ticker, job_id, captured_at, quantity,"
        " avg_price, current_price, instrument_currency, value, account_currency, value_account,"
        " cost_account, ppl, base_currency, fx_source, fx_rate, value_base) values"
        " ($1, 'AAPL_US_EQ', $2, now(), 1, 1, 1, 'USD', 1, 'USD', 1, 1, 0, 'USD', 'ecb', 1, 1)"
        " on conflict do nothing",
        today,
        job,
    )
    close = 100 * (1 + move_pct / 100)
    await pool.executemany(
        "insert into price_bar (symbol, date, close, source) values ('aapl.us', $1, $2, 'stooq')"
        " on conflict (symbol, date, source) do update set close = excluded.close",
        [(today - dt.timedelta(days=1), Decimal(100)), (today, Decimal(str(close)))],
    )
    return today


@db
async def test_price_move_trigger(
    backend_pool: asyncpg.Pool, http: httpx.AsyncClient, pages: Pages, migrated_db: MigratedDatabase
) -> None:
    needs_pgmq(migrated_db)
    pages.docs["https://stooq.com/q/d/l/"] = (200, "No data", "text/plain")
    pages.json["https://api.frankfurter.dev/"] = {"base": "EUR", "rates": {}}

    async def prices(today: dt.date) -> None:
        async def handler(ctx: JobContext) -> None:
            await run_price_history(ctx, PriceDeps(http=http, today=today))

        assert (
            await run_job(backend_pool, "price_history", handler, trigger="manual") == "succeeded"
        )

    async def escalations() -> int:
        return int(
            await backend_pool.fetchval(
                "select count(*) from job where job_type = 'event_escalation'"
            )
        )

    today = await seed_prices(backend_pool, 7.0)
    await prices(today)
    assert await escalations() == 0  # 7 % is inside the 8 % default
    await seed_prices(backend_pool, 9.0)
    await prices(today)
    await prices(today)  # the same move never queues twice
    assert await escalations() == 1
    params = json.loads(
        await backend_pool.fetchval("select params from job where job_type = 'event_escalation'")
    )
    assert params["trigger"] == "price_move" and round(params["move_pct"]) == 9

    # Raising the threshold in config changes it, no deploy: the next day's 9 % move is
    # inside a 10 % threshold.
    async with backend_pool.acquire() as conn:
        await save_config(
            conn, "event_rules", EventRulesConfig(price_move_threshold_pct=10), created_by=None
        )
    tomorrow = today + dt.timedelta(days=1)
    await backend_pool.execute(
        "insert into price_bar (symbol, date, close, source) values ('aapl.us', $1, $2, 'stooq')",
        tomorrow,
        Decimal("118.81"),  # +9 % on 109
    )
    await prices(tomorrow)
    assert await escalations() == 1


# ---------------------------------------------------------------------------
# Portfolio breaches
# ---------------------------------------------------------------------------


STRATEGY = StrategyConfig.model_validate(
    {
        "base_currency": "EUR",
        "target_positions": [
            {"ticker": "A_EQ", "target_weight": 0.3, "band": 0.05, "hard_limit": 0.4}
        ],
        "rules": [
            {
                "id": "max-single",
                "type": "position_weight",
                "operator": "<=",
                "value": 0.45,
                "on_breach": "severity_2",
            }
        ],
    }
)


def drift(weight_a: float) -> Any:
    total = Decimal(1000)
    a = Decimal(str(weight_a)) * total
    rest = (total - a) / 2
    return compute_drift(
        [
            HoldingValue("A_EQ", "EUR", a),
            HoldingValue("B_EQ", "EUR", rest),
            HoldingValue("C_EQ", "EUR", rest),
        ],
        Decimal(0),
        STRATEGY,
    )


@db
async def test_a_breach_alerts_once_and_clears_when_back_in_band(
    backend_pool: asyncpg.Pool,
) -> None:
    async with backend_pool.acquire() as conn:
        await save_config(
            conn,
            "notifications",
            NotificationsConfig.model_validate(
                {
                    "quiet_hours": {"enabled": False},
                    "rules": [
                        {"id": "all", "match": {"severities": [2, 3]}, "channels": ["telegram"]}
                    ],
                    "repeat_throttle_hours": 0,
                }
            ),
            created_by=None,
        )
    job = await backend_pool.fetchval(
        "insert into job (job_type, trigger) values ('portfolio_sync', 'manual') returning id"
    )

    async def sync(weight: float, hours: int) -> Any:
        return await record_breaches(
            backend_pool,
            evaluate(drift(weight), STRATEGY),
            job_id=job,
            config_version_id=None,
            now=NOON + dt.timedelta(hours=hours),
        )

    first = await sync(0.5, 0)  # above band, above hard limit, rule breached
    assert set(first.opened) == {"hard_limit:A_EQ", "band:position:A_EQ", "rule:max-single:A_EQ"}
    await sync(0.5, 24)
    await sync(0.48, 48)
    alerts = await backend_pool.fetch("select severity, message from alert order by id")
    assert [a["severity"] for a in alerts] == [3, 2]  # hard limit and rule, once each
    assert {a.kind for a in await recent_alerts(backend_pool, 50)} == {"portfolio"}
    back = await sync(0.3, 72)
    assert set(back.cleared) == set(first.opened)
    reopened = await sync(0.5, 96)
    assert "hard_limit:A_EQ" in reopened.opened
    assert await backend_pool.fetchval("select count(*) from alert") == 4


# ---------------------------------------------------------------------------
# Disputed stories and verdict freeze
# ---------------------------------------------------------------------------


@db
async def test_a_disputed_story_freezes_verdicts_until_resolved(
    backend_pool: asyncpg.Pool,
) -> None:
    async with backend_pool.acquire() as conn:
        stored = await save_config(conn, "theses", THESES, created_by=None)
    cluster = await seed_story(backend_pool, severity=3, status="disputed")
    await backend_pool.execute(
        "update story_cluster set corroboration = $2::jsonb where id = $1",
        cluster,
        json.dumps(
            {
                "contradicting": [["https://apnews.com/denial", 2]],
                "supporting": [],
                "note": "Apple denies",
            }
        ),
    )
    summary = await run_verdicts(backend_pool, THESES, stored.id, None)
    assert summary.written == 0 and summary.frozen == [f"AAPL_US_EQ: disputed story #{cluster}"]
    shown = await disputed_stories(backend_pool, False)
    assert shown[0].claim[0].url == "https://www.reuters.com/apple-guidance"
    assert shown[0].contradicting[0].url == "https://apnews.com/denial"
    assert shown[0].verdicts_frozen

    assert await resolve_story(backend_pool, cluster, "false", "denied by Apple")
    summary = await run_verdicts(backend_pool, THESES, stored.id, None)
    assert summary.frozen == [] and summary.written == 1
    assert await disputed_stories(backend_pool, False) == []


# ---------------------------------------------------------------------------
# Monitoring
# ---------------------------------------------------------------------------


async def monitor(pool: asyncpg.Pool, now: dt.datetime) -> None:
    async def handler(ctx: JobContext) -> None:
        await run_monitor(ctx, now)

    assert await run_job(pool, "monitor", handler, trigger="schedule") == "succeeded"


@db
async def test_monitoring_alerts_once_per_failure_missed_run_and_gap(
    backend_pool: asyncpg.Pool,
) -> None:
    async with backend_pool.acquire() as conn:
        await save_config(
            conn,
            "notifications",
            NotificationsConfig.model_validate(
                {
                    "quiet_hours": {"enabled": False},
                    "daily_alert_cap": None,
                    "repeat_throttle_hours": 0,
                }
            ),
            created_by=None,
        )
        # Only the sync and the news run are scheduled.
        schedules = {
            k: {"cron": s.cron, "enabled": k in ("portfolio_sync", "news_ingest")}
            for k, s in JobsConfig().schedules().items()
        }
        await save_config(conn, "jobs", JobsConfig.model_validate(schedules), created_by=None)
    now = dt.datetime.now(dt.UTC)
    # A Trading212 authentication failure.
    await backend_pool.execute(
        "insert into job (job_type, trigger, status, error, created_at, finished_at) values"
        " ('portfolio_sync', 'schedule', 'failed', 'Trading212 answered 401: bad API key',"
        " $1, $1)",
        now - dt.timedelta(hours=1),
    )
    # A holding held for days with no news run since.
    snap_job = await backend_pool.fetchval(
        "insert into job (job_type, trigger, status, created_at) values ('portfolio_sync',"
        " 'schedule', 'succeeded', $1) returning id",
        now - dt.timedelta(days=5),
    )
    await backend_pool.execute("insert into instrument (t212_ticker) values ('AAPL_US_EQ')")
    await backend_pool.execute(
        "insert into position_snapshot (snapshot_date, ticker, job_id, captured_at, quantity,"
        " avg_price, current_price, instrument_currency, value, account_currency, value_account,"
        " cost_account, ppl, base_currency, fx_source, fx_rate, value_base) values"
        " ($1, 'AAPL_US_EQ', $2, $3, 1, 1, 1, 'USD', 1, 'USD', 1, 1, 0, 'USD', 'ecb', 1, 1)",
        (now - dt.timedelta(days=3)).date(),
        snap_job,
        now - dt.timedelta(days=3),
    )
    for _ in range(3):
        await monitor(backend_pool, now)
    alerts = await backend_pool.fetch(
        "select dedupe_key, message from alert where kind = 'system' order by id"
    )
    keys = [a["dedupe_key"] for a in alerts]
    assert len(keys) == len(set(keys))
    assert any(k.startswith("job:") for k in keys)
    assert any("401" in a["message"] for a in alerts)
    assert any(k.startswith("coverage:news:AAPL_US_EQ") for k in keys)
    # The news run never ran in the last slots: a missed run, alerted once.
    assert sum(k.startswith("missed:news_ingest:") for k in keys) == 1
    # GET /alerts reads system alerts (its response model once rejected the new kinds).
    listed = await recent_alerts(backend_pool, 50)
    assert {a.kind for a in listed} == {"system"}
