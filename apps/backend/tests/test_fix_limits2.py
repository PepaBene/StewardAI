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
    holding = HoldingFeedInfo("A_US_EQ", (), None, None, topics=("widget",))
    result = await collect_edgar_fts(
        http, {}, "fts", [holding], dt.date(2026, 1, 1), dt.date(2026, 3, 31), max_hits=150
    )
    assert len(result.topic_hits) == 150 and not result.errors
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
        return [{"url": u} for u in self.loaded]


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

    async def fake_store(*_: Any) -> tuple[int, int]:
        return 0, 0

    monkeypatch.setattr(ownership_collect, "portfolio_cusips", no_cusips)
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
