"""Phase 2b: identity, reference data, structure, market history and calendars.

The source adapters are tested on responses recorded from the real services (OpenFIGI,
ESMA FIRDS/FITRS, GLEIF, Wikidata, Frankfurter, the ECB Data Portal, Nager.Date) in
tests/fixtures/reference. Stooq refused connections from the recording host, so its CSV is
synthetic in the documented format.
"""

import copy
import datetime as dt
import json
import math
import statistics
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest

from steward.config import (
    EventRulesConfig,
    HoldingProfilesConfig,
    NewsSourcesConfig,
    NotificationsConfig,
    ThesesConfig,
)
from steward.config.holding_profiles import HoldingProfile
from steward.config.jobs import CalendarSettings, ExchangeClosure, JobsConfig, NewsSettings
from steward.config.store import load_active, save_config
from steward.fx.sources import fetch_rates, parse_ecb
from steward.jobs.runner import JobContext, run_job
from steward.news.link import Linker, holding_terms
from steward.news.pipeline import NewsConfig, load_holdings
from steward.reference import risk
from steward.reference.calendar import easter, exchange_open, fx_stale, target_holidays
from steward.reference.identity import ReferenceDeps, resolve_holding, run_reference_data
from steward.reference.prices import PriceDeps, run_price_history
from steward.reference.read import decide_relationship, holding_identity, price_history
from steward.sources import esma, openfigi
from steward.sources.gleif import GleifClient, parse_record
from steward.sources.market import parse_frankfurter, parse_nager, parse_stooq
from steward.sources.wikidata import WikidataClient, parse_aliases, parse_properties

FIXTURES = Path(__file__).parent / "fixtures" / "reference"
SAP_ISIN = "DE0007164600"
SAP_LEI = "529900D6BF99LW9R2E68"
TICKER = "SAPd_EQ"


def load(name: str) -> Any:
    text = (FIXTURES / name).read_text()
    return json.loads(text) if name.endswith(".json") else text


# ---------------------------------------------------------------------------
# Adapters on recorded responses
# ---------------------------------------------------------------------------


def test_openfigi_resolves_share_class_and_home_listing() -> None:
    listings = openfigi.parse_mapping(load("openfigi_sap.json")[0])
    resolution = openfigi.resolve(listings, SAP_ISIN)
    assert resolution.share_class_figi == "BBG001S6RK27"
    assert resolution.home is not None and resolution.home.exch_code == "GR"
    assert resolution.home.ticker == "SAP" and resolution.home.figi == "BBG000BG7DY8"
    assert openfigi.stooq_symbol(resolution.home) == "sap.de"
    assert openfigi.parse_mapping({"error": "No identifier found."}) == []


def test_firds_and_fitrs() -> None:
    records = esma.parse_firds(load("firds_sap.json"))
    assert records and all(r.isin == SAP_ISIN for r in records)
    assert len({r.mic for r in records}) == len(records)  # one current record per venue
    assert {r.issuer_lei for r in records} == {SAP_LEI}
    xetra = next(r for r in records if r.mic == "XETA")
    assert xetra.cfi == "ESVUFB" and xetra.currency == "EUR" and xetra.terminated_at is None
    assert any(r.terminated_at is not None for r in records)
    liquidity = esma.parse_fitrs(load("fitrs_sap.json"))
    assert liquidity is not None and liquidity.liquid is True
    assert liquidity.most_relevant_market == "XETA" and liquidity.period_to == dt.date(2025, 12, 31)
    assert esma.home_country("XETA", SAP_ISIN) == "DE"


def test_gleif_level1_record() -> None:
    entity = parse_record(load("gleif_sap.json")["data"])
    assert (entity.lei, entity.legal_name, entity.jurisdiction) == (SAP_LEI, "SAP SE", "DE")
    assert entity.hq_country == "DE" and entity.status == "ACTIVE"
    assert entity.registration_status == "ISSUED" and entity.successor_lei is None
    apple = parse_record(load("gleif_isin_apple.json")["data"][0])
    assert apple.other_names == ["Apple Computer, Inc."]


def test_wikidata_properties_and_aliases() -> None:
    links = parse_properties(load("wikidata_sap_props.json"))
    kinds = {link.kind for link in links}
    assert {"subsidiary", "industry", "ceo", "part_of", "owned_by"} <= kinds
    concur = next(link for link in links if link.label == "SAP Concur")
    assert concur.qid == "Q5159089" and concur.lei == "OO5F1NP112HF423F0445"
    website = next(link for link in links if link.kind == "website")
    assert website.qid is None and website.label.startswith("https://www.sap.com")
    aliases = parse_aliases(load("wikidata_sap_aliases.json"))
    assert "SAP AG" in aliases and "sap.com" not in aliases


def test_frankfurter_equals_the_ecb_data_portal() -> None:
    portal = {(r.rate_date, r.currency): r for r in parse_ecb(load("ecb_range.csv"))}
    frankfurter = {
        (r.rate_date, r.currency): r for r in parse_frankfurter(load("frankfurter_range.json"))
    }
    shared = sorted(set(portal) & set(frankfurter))
    sample = shared[:: max(1, len(shared) // 10)][:10]
    assert len(sample) == 10
    for key in sample:
        assert frankfurter[key].published_rate == portal[key].published_rate, key
        assert frankfurter[key].rate == portal[key].rate, key


def test_nager_holidays() -> None:
    holidays = parse_nager("CZ", load("nager_cz_2026.json"))
    wenceslas = next(h for h in holidays if h.date == dt.date(2026, 9, 28))
    assert wenceslas.national and wenceslas.country == "CZ"


STOOQ_CSV = "Date,Open,High,Low,Close,Volume\n" + "\n".join(
    f"{(dt.date(2024, 9, 2) + dt.timedelta(days=i)).isoformat()},1,1,1,"
    f"{100 * (1 + 0.01 * math.sin(i / 5) + i / 1000):.4f},1000"
    for i in range(0, 760)
    if (dt.date(2024, 9, 2) + dt.timedelta(days=i)).weekday() < 5
)


def test_stooq_csv() -> None:
    bars = parse_stooq("sap.de", STOOQ_CSV)
    assert bars[0].date == dt.date(2024, 9, 2) and bars[0].close == Decimal("100.0000")
    assert parse_stooq("zzz.de", "No data") == []


# ---------------------------------------------------------------------------
# Risk figures against independent calculations
# ---------------------------------------------------------------------------


def series(seed: float, n: int = 400) -> list[tuple[dt.date, float]]:
    start = dt.date(2025, 1, 1)
    out, price = [], 100.0
    for i in range(n):
        price *= 1 + 0.02 * math.sin(i * seed) + 0.001
        out.append((start + dt.timedelta(days=i), price))
    return out


def test_risk_figures_match_independent_calculations() -> None:
    a, b = series(0.7), series(1.3)
    closes = [c for _, c in a]
    # Volatility: statistics.stdev of the last 252 log returns, annualised.
    logs = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - 252, len(closes))]
    assert risk.volatility(a) == pytest.approx(statistics.stdev(logs) * math.sqrt(252))
    # Drawdown: brute force over every pair.
    worst = min(closes[j] / max(closes[: j + 1]) - 1 for j in range(len(closes)))
    assert risk.max_drawdown(a) == pytest.approx(worst)
    # Beta and correlation: statistics.covariance / correlation on the same returns.
    ra = [a[i][1] / a[i - 1][1] - 1 for i in range(len(a) - 252, len(a))]
    rb = [b[i][1] / b[i - 1][1] - 1 for i in range(len(b) - 252, len(b))]
    assert risk.beta(a, b) == pytest.approx(statistics.covariance(ra, rb) / statistics.variance(rb))
    assert risk.correlation(a, b) == pytest.approx(statistics.correlation(ra, rb))
    # One-year return: the last close over the close 365 days earlier.
    assert risk.period_return(a, 365) == pytest.approx(a[-1][1] / a[-366][1] - 1)


# ---------------------------------------------------------------------------
# Calendars
# ---------------------------------------------------------------------------


def test_easter_and_target_holidays() -> None:
    assert easter(2026) == dt.date(2026, 4, 5) and easter(2027) == dt.date(2027, 3, 28)
    assert dt.date(2026, 4, 3) in target_holidays(2026)  # Good Friday
    assert dt.date(2026, 4, 6) in target_holidays(2026)  # Easter Monday


def test_a_closure_in_config_closes_the_exchange() -> None:
    day = dt.date(2026, 11, 26)  # Thanksgiving, a Thursday
    assert exchange_open(CalendarSettings(), "XNYS", day)
    settings = CalendarSettings(
        exchange_closures=[ExchangeClosure(mic="XNYS", date=day, name="Thanksgiving")]
    )
    assert not exchange_open(settings, "XNYS", day)
    assert exchange_open(settings, "XLON", day)
    assert not exchange_open(settings, "XLON", dt.date(2026, 11, 28))  # Saturday


# ---------------------------------------------------------------------------
# Against Postgres
# ---------------------------------------------------------------------------

pytestmark_db = pytest.mark.db


class Sources:
    """Serves the recorded responses; tests swap in changed ones to replay events."""

    def __init__(self) -> None:
        self.gleif_record = load("gleif_sap.json")
        children = load("gleif_sap_children.json")
        children["meta"]["pagination"]["lastPage"] = 1
        self.children = children
        self.firds = load("firds_sap.json")
        self.stooq: dict[str, str] = {}
        self.ecb_status = 200
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        self.requests.append(str(url))
        host, path = url.host, url.path
        if host == "api.openfigi.com":
            return httpx.Response(200, json=load("openfigi_sap.json"))
        if host == "registers.esma.europa.eu":
            if "firds" in path:
                return httpx.Response(200, json=self.firds)
            return httpx.Response(200, json=load("fitrs_sap.json"))
        if host == "api.gleif.org":
            if path.endswith("/lei-records") or path.endswith(f"/lei-records/{SAP_LEI}"):
                record = self.gleif_record
                body = {"data": [record["data"]]} if path.endswith("/lei-records") else record
                return httpx.Response(200, json=body)
            if path.endswith("-parent-reporting-exception"):
                body = load("gleif_sap_parent_exception.json")
                if "/ultimate-" in path:
                    body["data"]["attributes"]["category"] = (
                        "ULTIMATE_ACCOUNTING_CONSOLIDATION_PARENT"
                    )
                return httpx.Response(200, json=body)
            if path.endswith("-parent"):
                return httpx.Response(404, json={"errors": []})
            if path.endswith("/direct-children"):
                return httpx.Response(200, json=self.children)
            return httpx.Response(404, json={"errors": []})
        if host == "query.wikidata.org":
            query = url.params.get("query", "")
            if "P1278" in query and "UNION" in query and "LIMIT 1" in query:
                return httpx.Response(200, json={"results": {"bindings": [
                    {"item": {"type": "uri", "value": "http://www.wikidata.org/entity/Q552581"}}
                ]}})  # fmt: skip
            if "altLabel" in query:
                return httpx.Response(200, json=load("wikidata_sap_aliases.json"))
            if "Q223371" in query:
                return httpx.Response(200, json={"results": {"bindings": [
                    {"c": {"type": "uri", "value": "http://www.wikidata.org/entity/Q19900"},
                     "cLabel": {"value": "Oracle Corporation"}},
                ]}})  # fmt: skip
            return httpx.Response(200, json=load("wikidata_sap_props.json"))
        if host == "stooq.com":
            symbol = url.params.get("s", "")
            return httpx.Response(200, text=self.stooq.get(symbol, "No data"))
        if host == "api.frankfurter.dev":
            return httpx.Response(200, json=load("frankfurter_range.json"))
        if host == "data-api.ecb.europa.eu":
            return httpx.Response(self.ecb_status, text=load("ecb_range.csv"))
        return httpx.Response(404)


@pytest.fixture
def sources() -> Sources:
    return Sources()


@pytest.fixture
async def http(sources: Sources) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.MockTransport(sources.handler)) as client:
        yield client


def deps_for(http: httpx.AsyncClient, now: dt.datetime) -> ReferenceDeps:
    return ReferenceDeps(
        http=http,
        gleif=GleifClient(http),
        wikidata=WikidataClient(http, "StewardAI test"),
        now=lambda: now,
    )


NOW = dt.datetime(2026, 9, 25, 12, tzinfo=dt.UTC)


async def seed_holding(pool: asyncpg.Pool) -> None:
    await pool.execute(
        "insert into instrument (t212_ticker, name, isin, currency) values ($1, 'SAP SE', $2,"
        " 'EUR')",
        TICKER,
        SAP_ISIN,
    )


async def run_reference(pool: asyncpg.Pool, http: httpx.AsyncClient, now: dt.datetime) -> str:
    async def handler(ctx: JobContext) -> None:
        await run_reference_data(ctx, deps_for(http, now))

    return await run_job(pool, "reference_data", handler, trigger="manual", params={
        "ticker": TICKER, "force": True})  # fmt: skip


@pytestmark_db
async def test_identity_listings_entity_and_structure(
    backend_pool: asyncpg.Pool, http: httpx.AsyncClient
) -> None:
    await seed_holding(backend_pool)
    assert await run_reference(backend_pool, http, NOW) == "succeeded"
    identity = await holding_identity(backend_pool, TICKER)
    assert identity is not None and identity.identity_error is None
    assert identity.share_class_figi == "BBG001S6RK27"
    assert identity.home_mic == "XETA" and identity.home_country == "DE"
    assert identity.price_symbol == "sap.de"
    assert identity.legal_entity is not None and identity.legal_entity.lei == SAP_LEI
    assert any(x.mic == "XETA" and x.home for x in identity.listings)
    # GLEIF reports no parent, with the reason.
    assert identity.parents == []
    assert [(e.category, e.reason) for e in identity.parent_exceptions] == [
        ("DIRECT_ACCOUNTING_CONSOLIDATION_PARENT", "NO_KNOWN_PERSON"),
        ("ULTIMATE_ACCOUNTING_CONSOLIDATION_PARENT", "NO_KNOWN_PERSON"),
    ]
    # GLEIF children are confirmed; Wikidata-only links are suggestions, never confirmed.
    assert identity.subsidiary_count == 5
    assert all(r.source == "gleif" and r.tier == 1 for r in identity.subsidiaries)
    suggested = {(r.type, r.object_name) for r in identity.suggested}
    assert ("subsidiary", "SAP Concur") in suggested and (
        "competitor",
        "Oracle Corporation",
    ) in suggested
    assert all(r.source == "wikidata" for r in identity.suggested)
    aliases = {a.alias: a.kind for a in identity.aliases}
    assert aliases["SAP SE"] == "legal_name" and aliases["SAP NORGE AS"] == "subsidiary"
    assert aliases["SAP AG"] == "alias" and aliases["SAPGF"] == "listing_ticker"
    assert "SAPNOK" not in aliases


@pytestmark_db
async def test_aliases_reach_news_matching(
    backend_pool: asyncpg.Pool, http: httpx.AsyncClient
) -> None:
    await seed_holding(backend_pool)
    await run_reference(backend_pool, http, NOW)
    async with backend_pool.acquire() as conn:
        await save_config(
            conn,
            "holding_profiles",
            HoldingProfilesConfig(profiles=[HoldingProfile(ticker=TICKER)]),
            created_by=None,
        )
    config = NewsConfig(
        sources=NewsSourcesConfig(),
        event_rules=EventRulesConfig(),
        notifications=NotificationsConfig(),
        notifications_version_id=None,
        profiles=(await load_active(backend_pool, "holding_profiles", HoldingProfilesConfig)).body,  # type: ignore[union-attr]
        theses=ThesesConfig(),
        news=NewsSettings(),
        version_ids=(),
    )
    holdings = await load_holdings(backend_pool, config)
    linker = Linker(holdings.terms)
    # A GLEIF subsidiary's name links news about it to the holding.
    links = linker.link("Taulia LLC wins a large bank contract")
    assert [(x.ticker, x.via, x.term) for x in links] == [(TICKER, "alias", "Taulia LLC")]
    # A listing ticker from OpenFIGI links "$SAPGF".
    assert [x.via for x in linker.link("Shares of $SAPGF rose")] == ["ticker"]


@pytestmark_db
async def test_confirming_a_competitor_writes_the_profile_and_rejection_sticks(
    backend_pool: asyncpg.Pool, http: httpx.AsyncClient
) -> None:
    await seed_holding(backend_pool)
    await run_reference(backend_pool, http, NOW)
    identity = await holding_identity(backend_pool, TICKER)
    assert identity is not None
    oracle = next(r for r in identity.suggested if r.object_name == "Oracle Corporation")
    concur = next(r for r in identity.suggested if r.object_name == "SAP Concur")

    rel, version = await decide_relationship(backend_pool, oracle.id, True, None)
    assert rel.status == "confirmed" and version is not None
    profiles = await load_active(backend_pool, "holding_profiles", HoldingProfilesConfig)
    assert profiles is not None and profiles.id == version
    profile = profiles.body.for_ticker(TICKER)
    assert profile is not None and profile.competitors == ["Oracle Corporation"]
    linker = Linker([holding_terms(TICKER, profile, "SAP SE", SAP_ISIN)])
    assert [x.via for x in linker.link("Oracle Corporation raises its cloud outlook")] == [
        "competitor"
    ]

    await decide_relationship(backend_pool, concur.id, False, None)
    await run_reference(backend_pool, http, NOW + dt.timedelta(days=40))
    status = await backend_pool.fetchval(
        "select status from entity_relationship where id = $1", concur.id
    )
    assert status == "rejected"


async def lifecycle_events(pool: asyncpg.Pool) -> list[tuple[str, str]]:
    rows = await pool.fetch(
        "select c.event_type, i.url from news_item i join story_cluster c on c.id = i.cluster_id"
        " where i.source_id = 'reference-data' order by i.id"
    )
    return [(r["event_type"], r["url"]) for r in rows]


@pytestmark_db
async def test_replayed_delisting_and_merger_raise_one_event_each(
    backend_pool: asyncpg.Pool, http: httpx.AsyncClient, sources: Sources
) -> None:
    await seed_holding(backend_pool)
    await run_reference(backend_pool, http, NOW)
    assert await lifecycle_events(backend_pool) == []  # the first read is a baseline

    # A recorded delisting on the home market, and a recorded merger (successor LEI).
    firds = copy.deepcopy(sources.firds)
    for doc in firds["response"]["docs"]:
        if doc["mic"] == "XETA":
            doc["mrkt_trdng_trmination_date"] = "2026-12-31T23:59:59Z"
            doc["publication_date"] = "2026-09-30T01:00:00Z"
    sources.firds = firds
    gleif = copy.deepcopy(sources.gleif_record)
    gleif["data"]["attributes"]["entity"]["successorEntity"] = {
        "lei": "5299001XJ6XQ0L6YGB78", "name": "Successor AG"
    }  # fmt: skip
    sources.gleif_record = gleif
    for day in (1, 8, 15):  # replayed on three later runs
        await run_reference(backend_pool, http, NOW + dt.timedelta(days=day))

    events = await lifecycle_events(backend_pool)
    assert [e for e, _ in events] == ["delisting", "m_and_a"]
    delisting_url, merger_url = (u for _, u in events)
    assert "isin=DE0007164600&mic=XETA" in delisting_url
    assert merger_url.startswith(f"https://search.gleif.org/#/record/{SAP_LEI}")
    status = await backend_pool.fetch(
        "select status, confidence, ticker from story_cluster order by id"
    )
    assert all(s["ticker"] == TICKER for s in status)


@pytestmark_db
async def test_unresolvable_instrument_is_listed_not_skipped(
    backend_pool: asyncpg.Pool, http: httpx.AsyncClient
) -> None:
    await backend_pool.execute(
        "insert into instrument (t212_ticker, name) values ('NOISIN_EQ', 'Mystery plc')"
    )
    result = await resolve_holding(
        backend_pool, deps_for(http, NOW), "NOISIN_EQ", JobsConfig().reference,
        HoldingProfilesConfig(),
    )  # fmt: skip
    assert result.problems
    error = await backend_pool.fetchval(
        "select identity_error from instrument where t212_ticker = 'NOISIN_EQ'"
    )
    assert error and "ISIN" in error


@pytestmark_db
async def test_prices_risk_and_visible_gaps(
    backend_pool: asyncpg.Pool, http: httpx.AsyncClient, sources: Sources
) -> None:
    await seed_holding(backend_pool)
    await backend_pool.execute(
        "update instrument set price_symbol = 'sap.de' where t212_ticker = $1", TICKER
    )
    await backend_pool.execute(
        "insert into instrument (t212_ticker, name, price_symbol) values"
        " ('ZZZ_EQ', 'No Data Corp', 'zzz.de')"
    )
    job = await backend_pool.fetchval(
        "insert into job (job_type, trigger, status) values ('portfolio_sync', 'schedule',"
        " 'succeeded') returning id"
    )
    last = parse_stooq("sap.de", STOOQ_CSV)[-1]
    for ticker, price in ((TICKER, last.close * Decimal("1.03")), ("ZZZ_EQ", Decimal(1))):
        await backend_pool.execute(
            "insert into position_snapshot (snapshot_date, ticker, job_id, captured_at,"
            " quantity, avg_price, current_price, instrument_currency, value, account_currency,"
            " value_account, cost_account, ppl, base_currency, fx_source, fx_rate, value_base)"
            " values ($1, $2, $3, now(), 1, 1, $4, 'EUR', 1, 'EUR', 1, 1, 0, 'EUR', 'ecb', 1, 1)",
            last.date,
            ticker,
            job,
            price,
        )
    sources.stooq["sap.de"] = STOOQ_CSV
    sources.stooq["^spx"] = STOOQ_CSV.replace(",1000", ",5000")

    async def handler(ctx: JobContext) -> None:
        await run_price_history(ctx, PriceDeps(http=http, today=last.date))

    assert await run_job(backend_pool, "price_history", handler, trigger="manual") == "succeeded"
    stored = await backend_pool.fetchval("select count(*) from price_bar where symbol = 'sap.de'")
    assert stored == len(parse_stooq("sap.de", STOOQ_CSV))

    history = await price_history(backend_pool, TICKER, 3700)
    assert history.gap is None and history.as_of == last.date and history.source == "Stooq"
    closes = [(b.date, b.close) for b in history.bars]
    assert history.risk is not None
    assert history.risk.volatility == pytest.approx(risk.volatility(closes))
    assert history.risk.max_drawdown == pytest.approx(risk.max_drawdown(closes))
    assert history.risk.beta == pytest.approx(1.0)  # the same series as its benchmark
    assert history.plausibility is not None and history.plausibility.flagged is True

    gap = await price_history(backend_pool, "ZZZ_EQ", 365)
    assert gap.bars == [] and gap.gap == "Stooq has no data for zzz.de"

    # FX history: backfilled once from Frankfurter without overwriting portal rates.
    backfilled = await backend_pool.fetchval(
        "select count(*) from fx_rate where provider = 'frankfurter'"
    )
    assert backfilled > 0


async def test_ecb_falls_back_to_frankfurter(http: httpx.AsyncClient, sources: Sources) -> None:
    sources.ecb_status = 503
    rates = await fetch_rates(http, "ecb", dt.date(2026, 9, 24))
    assert rates and {r.provider for r in rates} == {"frankfurter"}
    sources.ecb_status = 200
    rates = await fetch_rates(http, "ecb", dt.date(2026, 9, 24))
    assert {r.provider for r in rates} == {"ecb-data-portal"}


@pytestmark_db
async def test_a_czech_holiday_is_not_fx_staleness(backend_pool: asyncpg.Pool) -> None:
    friday, holiday = dt.date(2026, 9, 25), dt.date(2026, 9, 28)  # St Wenceslas Day, Monday
    assert await fx_stale(backend_pool, "cnb", friday, holiday) == [holiday]
    await backend_pool.execute(
        "insert into holiday (calendar, date, name, scope, source) values"
        " ('CZ', $1, 'Statehood Day', 'national', 'nager')",
        holiday,
    )
    assert await fx_stale(backend_pool, "cnb", friday, holiday) == []
    assert await fx_stale(backend_pool, "cnb", friday, holiday + dt.timedelta(days=1)) == [
        holiday + dt.timedelta(days=1)
    ]
    # ECB: Good Friday and Easter Monday are TARGET holidays.
    assert await fx_stale(backend_pool, "ecb", dt.date(2026, 4, 2), dt.date(2026, 4, 6)) == []


def test_old_or_first_seen_terminations_are_not_delistings() -> None:
    from steward.reference.events import listing_events

    today = dt.date(2026, 9, 25)
    brexit = dt.datetime(2020, 12, 31, tzinfo=dt.UTC)
    soon = dt.datetime(2026, 12, 31, tzinfo=dt.UTC)
    args = ("VODl_EQ", "Vodafone", "GB00BH4HKS39", "XLON")
    assert listing_events(*args, False, None, soon, today) == []  # first read: baseline
    assert listing_events(*args, True, None, brexit, today) == []  # stale regulator data
    assert [e.event_type for e in listing_events(*args, True, None, soon, today)] == ["delisting"]
    assert listing_events(*args, True, soon, soon, today) == []  # already known


def test_home_listing_without_a_home_composite() -> None:
    # Vodafone: the LN listing is not a composite in the mapping; the GR one is.
    rows = [
        {"figi": "BBG000C6K6G9", "ticker": "VOD", "exchCode": "LN",
         "compositeFIGI": "BBG000C6K5W3", "shareClassFIGI": "BBG001S6PJ31"},
        {"figi": "BBG000DT2N99", "ticker": "VODI", "exchCode": "GR",
         "compositeFIGI": "BBG000DT2N99", "shareClassFIGI": "BBG001S6PJ31"},
    ]  # fmt: skip
    resolution = openfigi.resolve(openfigi.parse_mapping({"data": rows}), "GB00BH4HKS39")
    assert resolution.home is not None and resolution.home.ticker == "VOD"
    assert openfigi.stooq_symbol(resolution.home) == "vod.uk"
