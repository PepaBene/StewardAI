"""Adapter assumptions checked against REAL provider responses (recorded 2026-10-03).

The fixtures in tests/fixtures/live are scrubbed recordings of live responses (no keys, no
personal data: names of natural persons replaced, postal addresses and phone numbers removed).
They exist because every earlier adapter test used a payload written from documentation. Each
test says which assumption it pins; the ones marked REGRESSION fail on the code as it was
before the live check (wrong URL, a faulty parse, a silent empty result).

No network: requests go through httpx.MockTransport.
"""

import datetime as dt
import json
import zipfile
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import asyncpg
import httpx
import pytest

from steward.fundamentals import fsds
from steward.graph import ops
from steward.graph.diffbot import parse_diffbot_relations
from steward.graph.ted import parse_ted_response
from steward.jobs.runner import JobFailed
from steward.lookthrough.factsheet import (
    extract_issuer_facts,
    unverified_fields,
)
from steward.news.regulatory import INVESTEGATE_COMPANY
from steward.ownership import insider_collect
from steward.reference import exchange_probes
from steward.reference.exchange_probes import classify_finnhub
from steward.sources import etf_issuers, finnhub, nport, openfigi
from steward.sources.alphavantage import AlphaVantageError, company_overview
from steward.sources.base import AccessState
from steward.sources.edgar import Filing, xbrl_documents
from steward.sources.form13f import dataset_links, parse_dataset
from steward.sources.free_market import FreeSourceError, parse_alpha_daily
from steward.sources.market import (
    MarketDataError,
    parse_frankfurter,
    parse_nager,
    quote_unit_of,
    stooq_daily,
)
from steward.sources.pdmr_uk import parse_pdmr_notification
from steward.sources.twelvedata import classify_time_series
from steward.sources.yahoo import parse_chart

LIVE = Path(__file__).parent / "fixtures" / "live"


def load(name: str) -> Any:
    return json.loads((LIVE / name).read_text(encoding="utf-8"))


def client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --------------------------------------------------------------------------------------- TED


def test_ted_live_response_names_and_shapes_are_read() -> None:
    """VERIFIED live: field names, language maps keyed by lower-case ISO 639-3 codes, a list
    for `total-value-cur`, `total-value` absent on many awards, `+02:00` date suffix."""
    awards, total = parse_ted_response(load("ted_search_live.json"))
    assert total == 115 and len(awards) == 3
    siemens = awards[0]
    assert siemens.number == "599729-2026" and siemens.winners == ("Siemens AG",)
    assert siemens.title and siemens.title.startswith("Germany")  # the English title wins
    assert siemens.buyer and siemens.buyer.startswith("Fraunhofer")
    assert siemens.value is None and siemens.currency == "EUR"
    assert siemens.published == dt.date(2026, 9, 1)
    assert awards[2].value == Decimal(1760000) and awards[2].notice_type == "can-standard"


# ----------------------------------------------------------------------------------- Finnhub


def _finnhub_response(symbol: str) -> httpx.Response:
    recorded = load("finnhub_quote_live.json")["quotes"][symbol]
    return httpx.Response(recorded["status"], content=recorded["body"].encode())


async def test_finnhub_live_quote_behaviour_per_exchange() -> None:
    """VERIFIED live with a free key: US and Prague answer, Frankfurt/London are HTTP 403, an
    unknown symbol on a covered market is an all-zero 200 (missing coverage, not a denial)."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert "token" not in str(request.url)  # the key travels in a header
        return _finnhub_response(request.url.params["symbol"])

    async with client(handler) as http:
        aapl = await finnhub.quote(http, "k", "AAPL")
        cez = await finnhub.quote(http, "k", "CEZ.PR")
        zero = await finnhub.quote(http, "k", "ZZZZZ.XX")
        denied = []
        for symbol in ("SAP.DE", "VOD.L"):
            with pytest.raises(finnhub.FinnhubError) as caught:
                await finnhub.quote(http, "k", symbol)
            denied.append(caught.value)
    assert aapl is not None and aapl.price == Decimal("333.69")
    assert aapl.traded_at == dt.datetime(2026, 10, 2, 20, 0, tzinfo=dt.UTC)
    assert cez is not None and cez.price == Decimal(1339)
    assert zero is None  # `{"c":0,...,"d":null,"t":0}`
    assert [e.status_code for e in denied] == [403, 403]

    assert classify_finnhub(aapl, None)[0] is AccessState.AVAILABLE
    state, _, endpoint_level = classify_finnhub(None, denied[0])
    assert state is AccessState.PAID_ONLY and endpoint_level is False  # per exchange
    state, _, endpoint_level = classify_finnhub(zero, None)
    assert state is AccessState.NOT_COVERED and endpoint_level is False


def test_finnhub_invalid_key_is_401_and_endpoint_level() -> None:
    recorded = load("finnhub_quote_live.json")["invalid_key"]
    error = finnhub.FinnhubError("x", recorded["status"])
    assert classify_finnhub(None, error)[0] is AccessState.ACCESS_UNVERIFIED
    assert classify_finnhub(None, error)[2] is True


# ---------------------------------------------------------------------------------- OpenFIGI


def test_openfigi_live_mapping_resolves_the_london_listing() -> None:
    """VERIFIED live: the mapping fields, 164 listings for one ISIN of which only some are
    composites, the home-country listing found among the plain listings (LN)."""
    recorded = load("openfigi_mapping_live.json")
    listings = openfigi.parse_mapping(recorded["GB00BH4HKS39"][0])
    assert len(listings) == 20 and all(x.figi for x in listings)
    resolution = openfigi.resolve(listings, "GB00BH4HKS39")
    assert resolution.share_class_figi == "BBG001S6PJ31"
    assert resolution.home is not None and resolution.home.exch_code == "LN"
    assert resolution.home.ticker == "VOD"
    with pytest.raises(openfigi.OpenFigiError, match="Invalid idValue format"):
        openfigi.parse_mapping(recorded["invalid_idValue"][0])


async def test_openfigi_waits_the_ratelimit_reset_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """VERIFIED live: with a key the policy is `250;w=60` and `ratelimit-reset` is seconds."""
    headers = load("openfigi_mapping_live.json")["headers"]
    assert headers["ratelimit-policy"] == "250;w=60" and headers["ratelimit-reset"] == "60"
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(openfigi, "_sleep", fake_sleep)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"ratelimit-reset": headers["ratelimit-reset"]})
        return httpx.Response(200, json=[{"error": "No identifier found."}])

    async with client(handler) as http:
        assert await openfigi.map_isin(http, "GB00BH4HKS39", "k") == []
    assert slept == [60.0]


# --------------------------------------------------------------------------------- SEC FSDS


def test_fsds_dataset_names_follow_the_sec_site() -> None:
    """REGRESSION: the Notes data sets live under `financial-statement-notes-data-sets/` (the
    old URL, `...-and-notes-...`, is HTTP 404) and are MONTHLY from 2025_07; `2025q3_notes.zip`
    does not exist."""
    base = "https://www.sec.gov/files/dera/data/financial-statement-notes-data-sets/"
    assert fsds.dataset_url("2025_07") == base + "2025_07_notes.zip"
    assert fsds.dataset_url("2025q2") == base + "2025q2_notes.zip"
    assert fsds.dataset_url("2026-08") == base + "2026_08_notes.zip"
    for never_published in ("2025q3", "2026q1", "2025_06", "latest"):
        with pytest.raises(fsds.FsdsError):
            fsds.dataset_url(never_published)
    assert fsds.recent_datasets(dt.date(2026, 10, 3)) == ["2026_09", "2026_08", "2026_07"]
    assert fsds.recent_datasets(dt.date(2025, 8, 20)) == ["2025_07"]  # nothing earlier is monthly


def test_fsds_reader_decodes_a_real_notes_slice() -> None:
    """VERIFIED live (2025_07_notes.zip, one filing sliced): the columns, `.tsv`, `dimh`/`dimn`
    with no `segments` column in `num`. REGRESSION: dimensions were stored as the opaque hash;
    they are now the axis/member pairs of dim.tsv."""
    with zipfile.ZipFile(LIVE / "sec_notes_2025_07_slice.zip") as archive:
        assert set(archive.namelist()) == {"sub.tsv", "tag.tsv", "dim.tsv", "num.tsv", "txt.tsv"}
        assert "segments" not in archive.read("num.tsv").decode().split("\n", 1)[0]
    filings = fsds.read_filings(LIVE / "sec_notes_2025_07_slice.zip", ciks={"0000002969"})
    assert len(filings) == 1
    filing = filings[0]
    assert (filing.adsh, filing.form, filing.fy, filing.fp) == (
        "0000002969-25-000044",
        "10-Q",
        2025,
        "Q3",
    )
    assert filing.period == dt.date(2025, 6, 30) and not filing.superseded
    # "2025-07-31 06:57:00.0" US Eastern in the file -> UTC.
    assert filing.accepted_at.tzinfo is not None and filing.accepted_at.date() >= dt.date(
        2025, 7, 1
    )
    plain = [f for f in filing.facts if f.statement != "note" and not f.dimensions["members"]]
    dimensional = [f for f in filing.facts if f.dimensions["members"]]
    assert plain and dimensional
    assert not any(
        m[0] == "dimension" and m[1].startswith("0x")
        for f in dimensional
        for m in f.dimensions["members"]
    )
    assert any(
        ["BusinessSegments", "AmericasSegment"] in f.dimensions["members"] for f in dimensional
    )
    assert any(f.statement == "note" and f.text_block for f in filing.facts)
    assert all(f.source == "sec_fsds" and f.concept.startswith("us-gaap:") for f in plain)


async def test_fsds_job_rejects_a_quarter_the_sec_never_published() -> None:
    ctx = SimpleNamespace(params={"quarter": "2025q3"})
    with pytest.raises(JobFailed, match="monthly"):
        await fsds.run_fsds_backfill(ctx, None)  # type: ignore[arg-type]


# ------------------------------------------------------------------------- 13F and N-PORT


def test_13f_index_links_and_a_real_dataset_slice_parse() -> None:
    """VERIFIED live: the index page links (the newest sits under another SEC directory, which
    the link pattern follows), the tab-separated names, dd-MMM-yyyy dates, SH/PRN, put/call."""
    html = (
        '<a href="/files/datastandardsinnovation/data/form-13f-data-sets/'
        '01jun2026-31aug2026_form13f.zip">x</a>'
        '<a href="/files/structureddata/data/form-13f-data-sets/'
        '01mar2026-31may2026_form13f.zip">y</a>'
    )
    links = dataset_links(
        html, "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets"
    )
    assert links[0].startswith("https://www.sec.gov/files/datastandardsinnovation/")
    data = parse_dataset(LIVE / "sec_13f_2026_summer_slice.zip", {"037833100"})
    kinds = {f.submission_type for f in data.filings}
    assert kinds == {"13F-HR", "13F-HR/A"}
    assert any(f.amendment_type in ("RESTATEMENT", "NEW HOLDINGS") for f in data.filings)
    apple = [p for p in data.positions if p.cusip == "037833100"]
    assert apple and all(p.shares_type == "SH" and p.value_usd is not None for p in apple)
    assert all(f.filed.year == 2026 and f.period.year == 2026 for f in data.filings)
    assert any(
        p.shares_type == "PRN"
        for p in parse_dataset(
            LIVE / "sec_13f_2026_summer_slice.zip",
            set(),
            tracked_ciks={f.cik for f in data.filings},
        ).positions
    )


def test_nport_live_filing_parses_and_a_series_less_registrant_is_refused() -> None:
    """VERIFIED live: an NPORT-P/A of an ETF series; closed-end registrants have no seriesId
    and the parser (series keyed) refuses them with a typed error instead of guessing one."""
    report = nport.parse_nport((LIVE / "nport_sp_funds_live.xml").read_bytes())
    assert report.submission_type == "NPORT-P/A" and report.series_id == "S000085286"
    assert report.report_period_end == dt.date(2026, 7, 31) and len(report.holdings) == 5
    first = report.holdings[0]
    assert first.isin == "US8863647025" and first.cusip == "886364702"
    assert first.value_usd == Decimal("274308.93") and first.asset_cat == "EC"
    assert report.class_returns and report.flows["mon1Flow"]["sales"]
    with pytest.raises(nport.NportError, match="series id"):
        nport.parse_nport((LIVE / "nport_series_less_closed_end_live.xml").read_bytes())


# -------------------------------------------------------------------- EDGAR index, Form 4


def test_xbrl_documents_are_found_in_a_real_filing_index() -> None:
    """VERIFIED live: the inline-XBRL extracted instance is `<primary>_htm.xml` and the label
    linkbase `_lab.xml` in the folder listing."""
    names = [i["name"] for i in load("edgar_filing_index_live.json")["directory"]["item"]]
    filing = Filing(
        cik="0000002969",
        form="10-Q",
        accession="0000002969-25-000044",
        filing_date=dt.date(2025, 7, 31),
        report_date=dt.date(2025, 6, 30),
        primary_document="apd-20250630.htm",
        items="",
    )
    assert xbrl_documents(filing, names) == ("apd-20250630_htm.xml", "apd-20250630_lab.xml")


async def test_form4_is_fetched_from_the_raw_xml_not_the_rendered_view() -> None:
    """REGRESSION: EDGAR lists `xslF345X06/doc4.xml` (an HTML transform) as the primary
    document; fetching it first cost a request and a rejected page for every Form 4."""
    filing = Filing(
        cik="0000002969",
        form="4",
        accession="0001225208-26-008036",
        filing_date=dt.date(2026, 10, 1),
        report_date=dt.date(2026, 9, 30),
        primary_document="xslF345X06/doc4.xml",
        items="",
    )
    asked: list[str] = []

    class FakeEdgar:
        async def filing_files(self, filing: Filing) -> list[str]:
            return ["0001225208-26-008036-index.html", "doc4.xml", "ungerleiderpoa4.txt"]

        async def document(self, url: str, max_bytes: int = 0) -> bytes:
            asked.append(url)
            return b"<?xml version='1.0'?><ownershipDocument></ownershipDocument>"

    body, url = await insider_collect._load_filing(FakeEdgar(), filing)  # type: ignore[arg-type]
    assert url.endswith("/000122520826008036/doc4.xml") and asked == [url]
    assert b"ownershipDocument" in body


# ----------------------------------------------------------------------- iShares / Vanguard


def _ishares_snapshot() -> etf_issuers.FundSnapshot:
    return etf_issuers.FundSnapshot(
        provider="ishares",
        portfolio_id="253743",
        source="ishares_json",
        as_of=dt.date(2026, 10, 1),
        fund_name="iShares Core S&P 500 UCITS ETF",
        fund_currency="USD",
        country_basis="country_of_risk",
        currency_basis="issuer",
        rows=[],
        raw={
            "holdings": {"shareClass": " "},
            "keyFundFacts": load("ishares_keyfundfacts_live.json"),
        },
    )


def test_ishares_factsheet_fields_come_from_keyfundfacts_with_verified_names() -> None:
    """REGRESSION: the candidate names never occur in a live payload, and the holdings
    component carries none of these figures: expense ratio, net assets, benchmark and use of
    income are `emeaMgt`, `totalNetAssetsFundLevel`, `indexSeriesName` and `useOfProfitsCode`
    of the `keyFundFacts` component."""
    facts = extract_issuer_facts(_ishares_snapshot())
    assert facts.expense_ratio == Decimal("0.0007")  # emeaMgt 0.07 is a percent
    assert facts.total_assets == Decimal("159086514607.14")  # the fund, not the share class
    assert facts.benchmark_name == "S&P 500 Index"
    assert facts.distribution_policy == "accumulating"
    names = {k: v["name"] for k, v in facts.provenance.items()}
    assert names == {
        "expense_ratio": "emeaMgt",
        "total_assets": "totalNetAssetsFundLevel",
        "benchmark_name": "indexSeriesName",
        "distribution_policy": "useOfProfitsCode",
    }
    assert all(v["verified"] is True for v in facts.provenance.values())
    assert facts.missing() == ["nav_basis"]  # iShares states no NAV basis text
    assert unverified_fields("ishares") == ["nav_basis"]


def test_vanguard_live_profile_and_holdings_parse() -> None:
    """VERIFIED live: `profile.distributionStrategy` ("Accumulating Income") and
    `shareClassName` ("ETF"); the holdings and allocation shapes. The expense ratio, net assets
    and benchmark have no verified field (no introspection; guessed names are rejected)."""
    fund = load("vanguard_fund_live.json")
    snap = etf_issuers.parse_vanguard(fund, [load("vanguard_holdings_live.json")], "9679")
    assert snap.fund_currency == "USD" and snap.as_of == dt.date(2026, 8, 31)
    assert snap.rows[0].isin == "US67066G1040" and snap.rows[0].country == "US"
    snap.raw = {"fund": fund}
    facts = extract_issuer_facts(snap)
    assert facts.distribution_policy == "accumulating"
    assert facts.provenance["distribution_policy"]["verified"] is True
    assert facts.provenance["share_class"]["raw"] == "ETF"
    assert unverified_fields("vanguard") == [
        "expense_ratio",
        "total_assets",
        "benchmark_name",
        "nav_basis",
    ]
    assert etf_issuers.VANGUARD_FUND_QUERY.count("distributionStrategy") == 1


# ------------------------------------------------------------- Stooq, Yahoo, FX, holidays


async def test_stooq_bot_check_page_is_a_block_not_an_empty_history() -> None:
    """REGRESSION: Stooq answers non-browser clients HTTP 200 with an HTML proof-of-work page
    (or resets the connection). `parse_stooq` read it as "no data" and the gap looked like an
    unknown symbol forever. It is now a typed refusal; the challenge is not solved."""
    page = (LIVE / "stooq_bot_challenge_live.html").read_text(encoding="utf-8")
    assert "__verify" in page

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=page, headers={"content-type": "text/html; charset=utf-8"})

    async with client(handler) as http:
        with pytest.raises(MarketDataError, match="bot-check"):
            await stooq_daily(http, "vod.uk", dt.date(2026, 9, 25), dt.date(2026, 10, 2))


def test_yahoo_live_chart_quotes_london_in_pence() -> None:
    """VERIFIED live: `meta.currency` is "GBp" and the prices are pence (126.8 for Vodafone)."""
    chart = parse_chart("VOD.L", load("yahoo_chart_vod_l_live.json"))
    assert chart.meta.currency == "GBp" and quote_unit_of(chart.meta.currency) == "GBX"
    assert chart.meta.price == Decimal("126.80") and chart.meta.exchange_tz == "Europe/London"
    assert len(chart.bars) == 5 and chart.bars[-1].close == Decimal("126.80")
    assert chart.bars[-1].ts == dt.datetime(2026, 10, 2, 7, 0, tzinfo=dt.UTC)


def test_frankfurter_and_nager_live_shapes() -> None:
    rates = parse_frankfurter(load("frankfurter_live.json"))
    usd = next(r for r in rates if r.currency == "USD" and r.rate_date == dt.date(2026, 10, 2))
    assert usd.published_rate == Decimal("1.1225") and usd.source == "ecb"
    holidays = parse_nager("CZ", load("nager_cz_live.json"))
    assert holidays[0].date == dt.date(2026, 1, 1) and holidays[0].national is True
    assert holidays[0].regions == [] and holidays[0].types == ["Public"]


# ------------------------------------------------------------ Alpha Vantage, Twelve Data


async def test_alpha_vantage_demo_key_shapes() -> None:
    """VERIFIED live with the documented `demo` key (IBM only): the adjusted daily shape and
    the notice any other symbol gets, which is a key problem, not a quota or a paid endpoint.
    Wording for a real key's quota/premium denial needs a key (not verified)."""
    bars = parse_alpha_daily(load("alphavantage_daily_adjusted_demo_live.json"))
    assert len(bars) == 4 and bars[-1].day == dt.date(2026, 10, 2)
    assert bars[-1].close == Decimal("222.64") and bars[-1].split_coefficient == Decimal(1)
    notice = load("alphavantage_demo_notice_live.json")["body"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=notice)

    with pytest.raises(AlphaVantageError) as caught:
        async with client(handler) as http:
            await company_overview(http, "demo", "AAPL")
    assert caught.value.state is AccessState.ACCESS_UNVERIFIED
    assert "demo" in str(caught.value)
    with pytest.raises(FreeSourceError) as quota:
        parse_alpha_daily({"Information": notice["Information"]})
    assert quota.value.state is AccessState.ACCESS_UNVERIFIED


def test_twelve_data_demo_key_outcomes() -> None:
    """VERIFIED live with the documented `demo` key: a success body, and HTTP 401 / code 401
    for anything beyond the demo symbol. Plan messages of a real Basic key are not verifiable.
    REGRESSION from the public docs: 403 is "API key lacks permissions for the requested
    resource (upgrade required)"; the words "API key" made it read as a rejected key
    (endpoint-level, closing the endpoint for every exchange) instead of a plan denial."""
    ok = {
        "meta": {"symbol": "AAPL", "currency": "USD", "exchange": "NASDAQ", "mic_code": "XNGS"},
        "values": [{"datetime": "2026-10-02", "close": "333.69000"}],
        "status": "ok",
    }
    probe = classify_time_series(ok)
    assert probe.state is AccessState.AVAILABLE and probe.currency == "USD"
    denied = {
        "code": 401,
        "message": "The 'demo' API key is only used for initial familiarity. To become a full "
        "user, you can request your own API key at https://twelvedata.com/pricing.",
        "status": "error",
    }
    assert classify_time_series(denied).state is AccessState.ACCESS_UNVERIFIED
    forbidden = {"code": 403, "message": "API key lacks permissions (upgrade required)"}
    assert classify_time_series(forbidden).state is AccessState.PAID_ONLY


# ------------------------------------------------------------------------------- UK RNS


def test_rns_announcement_template_is_parsed_cleanly_from_the_live_page() -> None:
    """VERIFIED live: the announcement body is server-rendered and carries the MAR template.
    REGRESSION: the place of the transaction ran on into the issuer's boilerplate ("... (XLON)
    - ends - For more information ...") and the instrument kept the label words "and
    identification code"."""
    text = (LIVE / "rns_pdmr_announcement_live.txt").read_text(encoding="utf-8")
    assert "About Vodafone Group" in text  # the boilerplate that used to leak
    notification = parse_pdmr_notification(text)
    assert notification is not None
    assert notification.person_name == "Example Person"
    assert notification.issuer_name == "Vodafone Group Plc"
    assert notification.lei == "213800TB53ELEUKM7Q61"
    (transaction,) = notification.transactions
    assert transaction.venue == "London Stock Exchange (XLON)"
    assert transaction.instrument == (
        "Ordinary shares of US$0.20 20/21 each in Vodafone Group Plc (ISIN: GB00BH4HKS39)"
    )
    assert transaction.isin == "GB00BH4HKS39" and transaction.nature == "Sale of Ordinary shares"
    assert transaction.price == Decimal("1.26725") and transaction.currency == "GBP"
    assert transaction.volume == Decimal(500000)
    assert transaction.transaction_date == dt.date(2026, 9, 18)
    assert transaction.economic_class == "open_market_sell"
    assert INVESTEGATE_COMPANY.format(tidm="VOD").endswith("/company/VOD")


# ----------------------------------------------------------------------------------- Diffbot


def test_diffbot_investments_are_received_rounds_with_nested_investors() -> None:
    """REGRESSION (public ontology docs.diffbot.com/docs/ont-organization, read 2026-10-03;
    a live response needs a key): `investments` are investments RECEIVED by the organization,
    `{date, amount, series, investors: [entity]}`. The first version looked for an entity in the
    round itself (found none) and labelled the direction as the holding investing."""
    entity = {
        "name": "Holding Co",
        "investments": [
            {
                "date": {"str": "d2021-03-02", "precision": 3, "timestamp": 1614643200000},
                "amount": {"currency": "USD", "value": 5000000},
                "isCurrent": False,
                "series": "Series B",
                "investors": [
                    {"name": "Big Fund", "diffbotUri": "http://diffbot.com/entity/EBig"},
                    {"name": "Small Fund", "diffbotUri": "http://diffbot.com/entity/ESmall"},
                ],
            }
        ],
        "acquiredBy": [
            {
                "name": "Bigger Corp",
                "diffbotUri": "http://diffbot.com/entity/EBigger",
                "date": {"str": "d2023-01", "precision": 2, "timestamp": 1672531200000},
            }
        ],
    }
    relations = parse_diffbot_relations(entity)
    investors = [r for r in relations if r.provider_field == "investments"]
    assert [r.object_name for r in investors] == ["Big Fund", "Small Fund"]
    assert {(r.edge_type, r.direction) for r in investors} == {("investment", "incoming")}
    assert investors[0].valid_from == dt.date(2021, 3, 2)
    assert investors[0].detail["series"] == "Series B"
    acquirer = next(r for r in relations if r.provider_field == "acquiredBy")
    assert (acquirer.edge_type, acquirer.direction) == ("acquired", "incoming")
    assert acquirer.valid_from == dt.date(2023, 1, 1) and acquirer.detail["date_precision"] == 2


def test_the_recorded_live_fixtures_hold_no_secrets() -> None:
    """The fixtures are public data only: no key, token or e-mail address."""
    for path in LIVE.iterdir():
        if path.suffix == ".zip":
            with zipfile.ZipFile(path) as archive:
                text = " ".join(
                    archive.read(n).decode("utf-8", "replace") for n in archive.namelist()
                )
        else:
            text = path.read_text(encoding="utf-8")
        for needle in ("apikey=", "api_token=", "X-Finnhub-Token", "@gmail", "OPENFIGI_API_KEY"):
            assert needle not in text, (path.name, needle)


# ------------------------------------------------------------------------ D3.4 live replay


@pytest.mark.db
async def test_d34_exchange_probes_replay_the_live_finnhub_answers(
    backend_pool: asyncpg.Pool,
) -> None:
    """The probe run against the live Finnhub free plan (2026-10-03), replayed: US and PRAGUE
    answer (the earlier mock test assumed Prague had no coverage), Frankfurt and London are
    403 -> `paid_only` per exchange (the endpoint stays open), an unknown symbol is the
    all-zero 200 -> `not_covered`. The probe sends the key in a header, never the URL."""
    await backend_pool.execute(
        "update public.provider_access set state = 'available', hard_blocked = false"
        " where provider = 'finnhub' and endpoint = 'quote'"
    )
    targets = [
        exchange_probes.ProbeTarget("XNAS", "AAPL_US_EQ", "AAPL", None),
        exchange_probes.ProbeTarget("XETR", "SAPd_EQ", "SAP.DE", None),
        exchange_probes.ProbeTarget("XLON", "VODl_EQ", "VOD.L", None),
        exchange_probes.ProbeTarget("XPRA", "CEZ_EQ", "CEZ.PR", None),
        exchange_probes.ProbeTarget("XXXX", "NONE_EQ", "ZZZZZ.XX", None),
    ]
    seen_keys: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_keys.append("X-Finnhub-Token" in request.headers and "token" not in str(request.url))
        return _finnhub_response(request.url.params["symbol"])

    detail: dict[str, Any] = {}
    async with client(handler) as http:
        outcomes = await exchange_probes.run_exchange_probes(
            backend_pool, http, targets, finnhub_key="k", twelve_key=None, detail=detail
        )
    states = {o.mic: o.state for o in outcomes}
    assert states == {
        "XNAS": AccessState.AVAILABLE,
        "XETR": AccessState.PAID_ONLY,
        "XLON": AccessState.PAID_ONLY,
        "XPRA": AccessState.AVAILABLE,
        "XXXX": AccessState.NOT_COVERED,
    }
    assert all(seen_keys) and detail["twelve_data"] == "skipped: no key configured"
    gate = await backend_pool.fetchrow(
        "select state, hard_blocked from public.provider_access"
        " where provider = 'finnhub' and endpoint = 'quote'"
    )
    assert gate["state"] == "available" and gate["hard_blocked"] is False


def test_sec_mutual_fund_ticker_file_shape() -> None:
    """VERIFIED live: `company_tickers_mf.json` is `{"fields": [cik, seriesId, classId,
    symbol], "data": [[2110, "S000009184", "C000024954", "LACAX"], ...]}` (28,579 rows)."""
    from steward.ownership.nport_collect import parse_mf_tickers

    mapping = parse_mf_tickers((LIVE / "sec_company_tickers_mf_live.json").read_bytes())
    assert mapping["LACAX"] == ("0000002110", "S000009184", "C000024954")


# ------------------------------------------------------------------------------- EPO OPS


def test_ops_reference_guide_biblio_example_gives_a_filing_date() -> None:
    """IMPOSSIBLE-WITHOUT-KEY live (anonymous OPS is HTTP 403 "Fair Use policy"); checked
    against the EPO's published Reference Guide v1.3.20 example and ops.epo.org's XSDs.
    REGRESSION: the guide's biblio-search example gives `application-reference` an epodoc and an
    original document-id and no docdb one; only docdb was read, so no filing date was found."""
    xml = (LIVE / "ops_biblio_search_reference_guide.xml").read_text(encoding="utf-8")
    page = ops.parse_ops_page(xml, ("International Business Machines Corporation",))
    assert page.total == 10000 and page.documents == 1
    (family,) = page.families
    assert family.family_id == "37717388"
    assert family.applicants == ["INTERNATIONAL BUSINESS MACHINES CORPORATION"]
    assert family.title == "INJECTION MOLDED MICROLENSES FOR OPTICAL INTERCONNECTS"
    assert family.application_date == dt.date(2006, 7, 20)
    assert family.priority_date == dt.date(2006, 7, 20)  # no claim in the sample: the filing
    assert [(p.country, p.doc_number, p.kind) for p in family.publications] == [
        ("KR", "20100130646", "A")
    ]


def test_ops_legal_events_take_their_date_from_the_gazette_element() -> None:
    """REGRESSION (OPS Reference Guide v1.3.20 section 3.5 and ops_legal.xsd): `ops:legal`
    carries `code`, `desc`, `infl` and `dateMigr` ("00010101"), never `date`; the event dates
    are the child elements `L007EP` (Gazette DATE) and `L018EP`/`L019EP`. `date` was read, so
    every event was dateless and a lapse produced an expired family with no expiry date. `code`
    is space padded and `desc` wrapped over lines."""
    xml = (LIVE / "ops_legal_reference_guide.xml").read_text(encoding="utf-8")
    events = ops.parse_ops_legal(xml)["19768124"]
    assert [e.code for e in events] == ["AK", "AX", "RER"]
    assert [e.date for e in events] == [
        dt.date(2000, 5, 17),
        dt.date(2000, 5, 17),
        dt.date(2003, 8, 15),
    ]
    assert events[2].description == (
        "CEASED AS TO PARAGRAPH 5 LIT. 3 LAW INTRODUCING PATENT TREATIES"
    )
    assert events[2].influence == "-"
    family = ops.OpsFamily("19768124")
    family.publications.append(ops.OpsPublication("EP", "1000000", "B1", dt.date(2002, 1, 2)))
    status, expiry = ops.derive_legal_status(family, events, today=dt.date(2026, 10, 3))
    assert status == "expired" and expiry == dt.date(2003, 8, 15)
