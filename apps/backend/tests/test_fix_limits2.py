"""Per-request caps of SEC EDGAR, EFTS, 13F data sets, OpenFIGI, GLEIF, ESMA and Google News:
every collector pages or splits until it has what it intends to read, and a deliberate cap
is visible in the job step (mocked transports, no network)."""

import contextlib
import datetime as dt
from collections.abc import AsyncIterator, Callable
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from steward.config.jobs import JobsConfig, OwnershipSettings
from steward.jobs.runner import StepHandle
from steward.news import feeds as news_feeds
from steward.news.feeds import HoldingFeedInfo, collect_google_news
from steward.news.regulatory import collect_edgar_fts, efts_search
from steward.ownership import collect as ownership_collect
from steward.sources import esma, openfigi
from steward.sources.edgar import EdgarClient
from steward.sources.form13f import Dataset
from steward.sources.gleif import GleifClient

UA = "StewardAI test test@example.com"


def client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _cols(rows: list[tuple[str, str, str, str]]) -> dict[str, list[str]]:
    return {
        "form": [r[0] for r in rows],
        "accessionNumber": [r[1] for r in rows],
        "filingDate": [r[2] for r in rows],
        "items": [r[3] for r in rows],
        "reportDate": ["" for _ in rows],
        "primaryDocument": ["d.htm" for _ in rows],
    }


# --- EDGAR submissions: older filings live in filings.files pages --------------------------

SUBMISSIONS = {
    "filings": {
        "recent": _cols([("10-Q", "r-1", "2026-08-01", ""), ("8-K", "r-2", "2025-07-01", "2.02")]),
        "files": [
            {"name": "CIK0000320193-submissions-002.json", "filingFrom": "2021-01-01",
             "filingTo": "2025-06-30"},
            {"name": "CIK0000320193-submissions-001.json", "filingFrom": "1994-01-01",
             "filingTo": "2020-12-31"},
        ],
    }
}  # fmt: skip
PAGE_002 = _cols([("8-K", "0000320193-23-000070", "2023-08-03", "2.02,9.01")])
INDEX = (
    '<table><tr><td>1</td><td>EX-99.1</td><td><a href="y">pr.htm</a></td>'
    "<td>EX-99.1</td><td>100</td></tr></table>"
)


async def test_edgar_reads_older_submission_pages_for_an_old_report_date() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("CIK0000320193.json"):
            return httpx.Response(200, json=SUBMISSIONS)
        if request.url.path.endswith("submissions-002.json"):
            return httpx.Response(200, json=PAGE_002)
        if request.url.path.endswith("-index.html"):
            return httpx.Response(200, text=INDEX)
        return httpx.Response(404)

    edgar = EdgarClient(client(handler), UA)
    found = await edgar.find_earnings_release("320193", dt.date(2023, 8, 3))
    assert found is not None and found[0].accession == "0000320193-23-000070"
    # Only the page covering the date was read, never the 1994-2020 one.
    assert not any(p.endswith("submissions-001.json") for p in seen)

    # A date inside `recent` reads no older page.
    seen.clear()
    await edgar.filings("320193", since=dt.date(2025, 7, 1))
    assert not any("submissions-0" in p for p in seen)


# --- EDGAR full-text search: 100 per page, `from` offset, 10,000 window ----------------------


def efts_handler(total: int, calls: list[int]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        start = int(parse_qs(request.url.query.decode()).get("from", ["0"])[0])
        calls.append(start)
        n = max(0, min(100, total - start))
        hits = [
            {
                "_id": f"0000000000-26-{start + i:06d}:doc.htm",
                "_source": {"ciks": ["0000000001"], "display_names": ["Filer (CIK 1)"],
                            "form": "10-K", "file_date": "2026-03-01"},
            }
            for i in range(n)
        ]  # fmt: skip
        return httpx.Response(200, json={"hits": {"total": {"value": total}, "hits": hits}})

    return handler


async def test_efts_pages_until_every_hit_is_read() -> None:
    calls: list[int] = []
    http = client(efts_handler(250, calls))
    hits = await efts_search(
        http, {}, '"x"', "10-K", dt.date(2026, 1, 1), dt.date(2026, 3, 31), 1000
    )
    assert len(hits) == 250 and calls == [0, 100, 200]
    assert len({h.accession for h in hits}) == 250


async def test_efts_cap_is_reported_not_silent() -> None:
    calls: list[int] = []
    notes: list[str] = []
    http = client(efts_handler(5000, calls))
    hits = await efts_search(
        http, {}, '"x"', "10-K", dt.date(2026, 1, 1), dt.date(2026, 3, 31), 200, notes
    )
    assert len(hits) == 200 and calls == [0, 100]
    assert notes and "200 of 5000" in notes[0]


async def test_collect_edgar_fts_uses_the_configured_cap() -> None:
    calls: list[int] = []
    http = client(efts_handler(150, calls))
    holding = HoldingFeedInfo("A_US_EQ", ("Acme Corp",), None, None, topics=("widget",))
    result = await collect_edgar_fts(
        http, {}, "fts", [holding], dt.date(2026, 1, 1), dt.date(2026, 3, 31), max_hits=150
    )
    assert len(result.topic_hits) == 150 and not result.errors


async def test_a_topic_hit_must_name_the_holding() -> None:
    # "tariffs" alone matched every filer that mentions tariffs, and each became an update
    # for the holding; the search now asks for the topic and the holding's name together.
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        queries.append(request.url.params["q"])
        return httpx.Response(200, json={"hits": {"total": {"value": 0}, "hits": []}})

    named = HoldingFeedInfo("A_US_EQ", ("Acme Corp",), None, None, topics=("tariffs",))
    unnamed = HoldingFeedInfo("B_US_EQ", (), None, None, topics=("tariffs",))
    await collect_edgar_fts(
        client(handler), {}, "fts", [named, unnamed], dt.date(2026, 1, 1), dt.date(2026, 3, 31)
    )
    assert '"tariffs" "Acme Corp"' in queries
    assert '"tariffs"' not in queries
    assert JobsConfig().news.edgar_fts_max_hits == 100


# --- GLEIF children: page[size] max 200, every page read ------------------------------------


def _entity(lei: str) -> dict[str, Any]:
    return {"attributes": {"lei": lei, "entity": {"legalName": {"name": lei}}}}


async def test_gleif_reads_every_page_of_children() -> None:
    sizes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        sizes.append(params["page[size]"])
        page = int(params["page[number]"])
        data = [_entity(f"L{page}-{i}") for i in range(200 if page < 3 else 59)]
        pagination = {"currentPage": page, "lastPage": 3, "total": 459}
        return httpx.Response(200, json={"data": data, "meta": {"pagination": pagination}})

    gleif = GleifClient(client(handler))
    children = await gleif.children("W38RGI023J3WT1HWRP32")
    assert len(children) == 459 and gleif.children_total == 459
    assert sizes == ["200", "200", "200"]


# --- ESMA FIRDS: Solr paged with `start` until numFound -------------------------------------


async def test_esma_pages_until_num_found() -> None:
    starts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params["start"])
        rows = int(request.url.params["rows"])
        starts.append(start)
        docs = [
            {"isin": "US0378331005", "mic": f"M{i:04d}", "status": "UNCH"}
            for i in range(start, min(start + rows, 1200))
        ]
        return httpx.Response(200, json={"response": {"numFound": 1200, "docs": docs}})

    records = await esma.firds(client(handler), "US0378331005")
    assert len(records) == 1200 and starts == [0, 500, 1000]


# --- OpenFIGI: a 429 is waited out, not a failure past the 25th holding ---------------------


async def test_openfigi_waits_out_the_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(openfigi, "_sleep", fake_sleep)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"ratelimit-reset": "12"})
        return httpx.Response(200, json=[{"data": [{"figi": "BBG000B9XRY4"}]}])

    listings = await openfigi.map_isin(client(handler), "US0378331005")
    assert [x.figi for x in listings] == ["BBG000B9XRY4"] and waits == [12.0]

    def always(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    with pytest.raises(openfigi.OpenFigiError, match="rate limit"):
        await openfigi.map_isin(client(always), "US0378331005")


# --- 13F: how far back, and what is left, is in the step detail -----------------------------


class _Pool:
    def __init__(self, loaded: list[str]) -> None:
        self.loaded = loaded

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        return [{"url": u, "rows_kept": 5} for u in self.loaded]


class _Ctx:
    def __init__(self, pool: _Pool) -> None:
        self.pool = pool
        self.job_id = 1
        self.steps: dict[str, dict[str, Any]] = {}

    @contextlib.asynccontextmanager
    async def step(self, name: str) -> AsyncIterator[StepHandle]:
        handle = StepHandle()
        yield handle
        self.steps[name] = handle.detail


async def test_13f_loads_within_history_and_reports_remaining(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names = [f"{2026 - i // 4}q{4 - i % 4}_form13f.zip" for i in range(20)]
    html = "".join(f'<a href="/files/{n}">x</a>' for n in names)
    base = "https://www.sec.gov/13f"
    urls = [f"https://www.sec.gov/files/{n}" for n in names]
    loaded_now: list[str] = []

    async def no_cusips(_: Any) -> dict[str, str]:
        return {}

    async def fake_download(_: Any, url: str, target: Any) -> None:
        loaded_now.append(url)
        target.write_bytes(b"zip")

    async def fake_store(*_: Any, **__: Any) -> tuple[int, int]:
        return 0, 0

    async def no_names(*_: Any) -> dict[str, str]:
        return {}

    monkeypatch.setattr(ownership_collect, "portfolio_cusips", no_cusips)
    monkeypatch.setattr(ownership_collect, "depositary_names", no_names)
    monkeypatch.setattr(ownership_collect, "_download", fake_download)
    monkeypatch.setattr(ownership_collect, "parse_dataset", lambda *_: Dataset([], []))
    monkeypatch.setattr(ownership_collect, "store_dataset", fake_store)

    http = client(lambda r: httpx.Response(200, text=html))
    ctx = _Ctx(_Pool(loaded=[urls[0]]))
    settings = OwnershipSettings(index_url=base, datasets_per_run=2, history_datasets=6)
    await ownership_collect.collect(ctx, http, settings)  # type: ignore[arg-type]
    detail = ctx.steps["datasets"]
    assert loaded_now == urls[1:3]
    assert detail["wanted"] == 6 and detail["remaining"] == 3
    assert detail["older_not_loaded"] == 14


# --- Google News: its 100-item cap is noted -------------------------------------------------


async def test_google_news_cap_is_noted(monkeypatch: pytest.MonkeyPatch) -> None:
    async def full_feed(*_: Any) -> list[news_feeds.RawItem]:
        return [
            news_feeds.RawItem("g", f"https://x.test/{i}", "headline", None) for i in range(100)
        ]

    monkeypatch.setattr(news_feeds, "collect_rss", full_feed)
    holding = HoldingFeedInfo("A_US_EQ", ("Acme Corp",), None, None)
    result = await collect_google_news(client(lambda r: httpx.Response(500)), "g", [holding], 3)
    assert len(result.items) == 100
    assert result.errors and "cap" in result.errors[0]


def _lei_entity(lei: str, name: str, country: str) -> Any:
    from steward.sources.gleif import LegalEntity

    return LegalEntity(
        lei=lei,
        legal_name=name,
        other_names=[],
        jurisdiction=country,
        legal_form=None,
        category=None,
        hq_address={"country": country},
        legal_address={"country": country},
        status="ACTIVE",
        successor_lei=None,
        registration_status="ISSUED",
        next_renewal=None,
        raw={},
    )


def test_a_venue_lei_from_firds_is_not_taken_for_a_foreign_issuer() -> None:
    from steward.reference.identity import plausible_issuer

    venue = _lei_entity("213800R54EFFINMY1P02", "TP ICAP (EUROPE)", "FR")
    assert not plausible_issuer(venue, "US15118V2079", "Celsius")
    assert plausible_issuer(
        _lei_entity("X", "Celsius Holdings, Inc.", "US"), "US15118V2079", "Celsius"
    )
    # a holding company registered abroad still fits by its name
    assert plausible_issuer(
        _lei_entity("Y", "Nu Holdings Ltd.", "KY"), "US67066G1040", "Nu Holdings"
    )


def test_places_and_common_nouns_are_not_linked_as_customers_or_rivals() -> None:
    from steward.news.link import names_a_company

    assert not names_a_company("Ukraine")
    assert not names_a_company("Germany government")
    assert not names_a_company("Europe")
    assert not names_a_company("bottlers")
    assert not names_a_company("United")  # "Man United"; "United Airlines" links
    for company in (
        "United Airlines",
        "Rheinmetall",
        "NATO governments",
        "Czech Army",
        "TSMC",
        "McDonald's",
    ):
        assert names_a_company(company), company


async def test_a_tokyo_home_listing_is_in_yen_not_the_brokers_euro_line() -> None:
    from steward.reference.listings import derive_candidate
    from steward.reference.price_series import PriceSymbols

    instrument = {
        "t212_ticker": "D4Sd_EQ",
        "isin": "JP3475350009",
        "name": "Daiichi Sankyo",
        "currency": "EUR",
        "home_mic": "XTKS",
        "price_symbol": "4568.jp",
        "issuer_lei": None,
        "security_type": None,
        "share_class_figi": None,
    }
    answers = iter([instrument, None, {"figi": "BBG000BQWLX5", "listing_ticker": "4568"}])

    class FakePool:
        async def fetchrow(self, _sql: str, *_args: Any) -> Any:
            return next(answers)

    c = await derive_candidate(FakePool(), "D4Sd_EQ", PriceSymbols(stooq="4568.jp", yahoo=None))  # type: ignore[arg-type]
    assert c is not None and c.currency == "JPY"
    assert c.evidence["currency"]["market"] == "JPY" and not c.confirmable


def test_a_wikidata_competitor_says_what_the_inference_rests_on() -> None:
    from steward.sources.wikidata import parse_competitors

    body = {
        "results": {
            "bindings": [
                {
                    "c": {"value": "http://www.wikidata.org/entity/Q248"},
                    "cLabel": {"value": "Intel"},
                    "industry": {"value": "semiconductor industry"},
                    "index": {"value": "NASDAQ-100"},
                }
            ]
        }
    }
    (link,) = parse_competitors(body)
    assert link.basis is not None and "Inferred, not stated" in link.basis
    assert "the industry semiconductor industry and the index NASDAQ-100" in link.basis


def test_an_event_quote_is_whole_sentences() -> None:
    from steward.graph.deal_extract import _quote

    text = "Intro. The Company agreed with Acme Corp to buy it. Then more."
    at = text.index("Acme")
    quote, (lo, hi) = _quote(text, at, at + 4)
    assert quote == "The Company agreed with Acme Corp to buy it."
    assert " ".join(text[lo:hi].split()) == quote


def test_a_thesis_run_resumes_after_another_holdings_thesis_is_saved() -> None:
    from types import SimpleNamespace

    from pydantic import BaseModel

    from steward.research.run import run_inputs, same_run_inputs

    class T(BaseModel):
        ticker: str
        stance: str

    def theses(*items: T) -> Any:
        return SimpleNamespace(body=SimpleNamespace(theses=list(items)))

    profiles = SimpleNamespace(body=SimpleNamespace(for_ticker=lambda t: None))
    own = run_inputs("A", theses(T(ticker="A", stance="keep")), profiles)
    first = {"ids": [1, 10, 2, 20, 3, 4], "own": own}
    # Another holding's thesis saved: the theses version moved, this holding's entry did not.
    later = run_inputs(
        "A", theses(T(ticker="A", stance="keep"), T(ticker="B", stance="add")), profiles
    )
    assert same_run_inputs(first, [1, 11, 2, 21, 3, 4], later)
    # This holding's own thesis changed, or a shared config (the research profile) did.
    changed = run_inputs("A", theses(T(ticker="A", stance="exit")), profiles)
    assert not same_run_inputs(first, [1, 11, 2, 20, 3, 4], changed)
    assert not same_run_inputs(first, [9, 10, 2, 20, 3, 4], own)
    # A run checkpointed before the fingerprint existed is judged on the shared ids.
    assert same_run_inputs({"ids": [1, 10, 2, 20, 3, 4]}, [1, 12, 2, 22, 3, 4], own)


def test_a_thesis_run_resumes_after_another_research_profile_is_edited() -> None:
    from types import SimpleNamespace

    from steward.config.research import ResearchProfilesConfig
    from steward.research.run import run_inputs, same_run_inputs

    def research(**changes: Any) -> Any:
        body = ResearchProfilesConfig()
        profiles = [p.model_copy(update=changes.get(p.id, {})) for p in body.profiles]
        return SimpleNamespace(body=body.model_copy(update={"profiles": profiles}))

    none = SimpleNamespace(body=SimpleNamespace(theses=[], for_ticker=lambda t: None))
    own = run_inputs("A", none, none, research(), "deep-thesis")
    first = {"ids": [94, 10, 2, 20, 3, 4], "own": own}
    # The quick profile was edited: a new research version, but not the profile this run uses.
    other = run_inputs("A", none, none, research(quick={"max_searches": 5}), "deep-thesis")
    assert same_run_inputs(first, [95, 10, 2, 20, 3, 4], other)
    # The profile this run uses changed: the resume stops.
    mine = run_inputs(
        "A", none, none, research(**{"deep-thesis": {"max_searches": 5}}), "deep-thesis"
    )
    assert not same_run_inputs(first, [95, 10, 2, 20, 3, 4], mine)
    # A checkpoint with only the two older fingerprints still compares the research id.
    assert not same_run_inputs(
        {"ids": [94, 10, 2, 20, 3, 4], "own": own[:2]}, [95, 10, 2, 20, 3, 4], other
    )


class _ConsensusPool:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        return self.row


async def test_the_thesis_author_sees_the_analyst_consensus_as_an_anchor() -> None:
    from decimal import Decimal

    from steward.research.gather import consensus_anchor
    from steward.research.sources import Registry

    row = {
        "id": 82, "as_of": dt.date(2026, 10, 16), "source": "fmp", "analyst_count": 28,
        "target_mean": Decimal("104.72"), "target_median": Decimal("100"),
        "target_high": Decimal("150"), "target_low": Decimal("74"), "target_currency": "USD",
        "eps_next_fy": Decimal("3.36"), "fiscal_period": "FY2026", "estimate_currency": None,
    }  # fmt: skip
    pool: Any = _ConsensusPool(row)
    price = Decimal("71.26")
    sentences, vals = await consensus_anchor(pool, Registry(), "UBER_US_EQ", price, "USD")
    assert "28 analysts is 104.72 USD" in sentences[0].text
    assert "+47% against the price" in sentences[0].text
    assert "not a fair value" in sentences[0].text
    # The EPS estimate has no currency (an ADR's may be in the home currency): no forward P/E.
    assert [v.method for v in vals] == ["consensus_target"]
    row["estimate_currency"] = "USD"
    sentences, vals = await consensus_anchor(pool, Registry(), "UBER_US_EQ", price, "USD")
    assert vals[-1].method == "forward_pe" and vals[-1].display == "21.2x"
    # A target in another currency gives no gap against the price.
    row["target_currency"] = "TWD"
    sentences, _ = await consensus_anchor(pool, Registry(), "TSM_US_EQ", Decimal("420"), "USD")
    assert "against the price" not in sentences[0].text
    empty: Any = _ConsensusPool(None)
    assert await consensus_anchor(empty, Registry(), "X", None, None) == ([], [])


async def test_a_thesis_save_builds_on_a_version_another_job_saved_meanwhile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from steward.config.store import ConfigConflictError
    from steward.thesis import job

    loads: list[int] = []
    saves: list[int | None] = []

    async def load_active(pool: Any, domain: str, model: Any) -> Any:
        loads.append(1)
        return SimpleNamespace(id=100 + len(loads), body=SimpleNamespace())

    async def save_config(conn: Any, domain: str, body: Any, **kw: Any) -> Any:
        saves.append(kw["expected_active_id"])
        if len(saves) == 1:  # another thesis job saved version 102 in between
            raise ConfigConflictError("theses", 101, 102)
        return SimpleNamespace(id=200)

    async def not_overruled(*_: Any) -> bool:
        return False

    class Pool:
        @contextlib.asynccontextmanager
        async def acquire(self) -> AsyncIterator[None]:
            yield None

    monkeypatch.setattr(job, "load_active", load_active)
    monkeypatch.setattr(job, "save_config", save_config)
    monkeypatch.setattr(job, "replace_thesis", lambda config, thesis: config)
    monkeypatch.setattr(job.overrides, "was_overruled", not_overruled)
    thesis: Any = SimpleNamespace(ticker="A")
    draft: Any = SimpleNamespace(stance="keep", stance_reason="fine")
    pool: Any = Pool()
    assert await job._save_held(pool, thesis, draft, 7) == 200
    assert saves == [101, 102]  # the second try builds on the newer version
