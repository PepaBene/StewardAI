"""SEC XBRL fundamentals: the instance / label parser on trimmed real filings (Alphabet's
10-Q for Q2 2026, Novo Nordisk's and ASML's 20-F for 2025), the shares-outstanding chain,
the segment read model, the sec_xbrl job against a mocked EDGAR, and the 13F % of shares
outstanding."""

import datetime as dt
import json
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
from steward.config import HoldingProfilesConfig
from steward.config.holding_profiles import HoldingProfile
from steward.config.store import save_config
from steward.fundamentals.collect import run_sec_xbrl
from steward.fundamentals.read import build_segments, holding_segments, period_of
from steward.fundamentals.shares import basis_for, manual_figure, shares_chain
from steward.jobs.runner import JobContext, run_job
from steward.ownership.read import ADR_BASIS, US_BASIS, holding_ownership
from steward.sources.edgar import EdgarClient, Filing, latest_periodic, xbrl_documents
from steward.sources.xbrl import (
    Labels,
    XbrlError,
    ads_ratio,
    derived_label,
    instance_shares,
    parse_instance,
    parse_labels,
    segment_revenue,
)

FIXTURES = Path(__file__).parent / "fixtures" / "xbrl"
OWNER = "7f0c3d4e-0000-4000-8000-000000000001"

GOOG = "goog-20260630"
NVO = "nvo-20251231"
ASML = "asml-20251231"


def load(name: str) -> tuple[Any, Labels]:
    instance = parse_instance((FIXTURES / f"{name}_htm.xml").read_bytes())
    labels = parse_labels((FIXTURES / f"{name}_lab.xml").read_bytes())
    return instance, labels


def rows_for(name: str, kind: str, start: str, end: str) -> dict[str, Any]:
    instance, labels = load(name)
    parsed = segment_revenue(instance, labels)
    return {
        r.label: r
        for r in parsed.rows
        if r.kind == kind
        and r.start == dt.date.fromisoformat(start)
        and r.end == dt.date.fromisoformat(end)
    }


# --------------------------------------------------------------------------------- parser


def test_alphabet_q2_2026_revenue_by_region_and_segment_as_reported() -> None:
    geo = rows_for(GOOG, "geographic", "2026-04-01", "2026-06-30")
    assert {k: v.value for k, v in geo.items()} == {
        "United States": Decimal("60846000000"),
        "EMEA": Decimal("32501000000"),
        "APAC": Decimal("19317000000"),
        "Other Americas": Decimal("7026000000"),
    }
    assert geo["APAC"].member == "srt:AsiaPacificMember"
    assert geo["APAC"].label_source == "filing"
    assert geo["APAC"].concept == "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
    segments = rows_for(GOOG, "segment", "2026-04-01", "2026-06-30")
    assert set(segments) == {"Google Services", "Google Cloud", "Other Bets"}
    assert segments["Google Cloud"].value == Decimal("24768000000")
    # H1 is kept too (a year-to-date duration), and the undimensioned total.
    h1 = rows_for(GOOG, "geographic", "2026-01-01", "2026-06-30")
    assert h1["United States"].value == Decimal("114821000000")
    total = rows_for(GOOG, "total", "2026-04-01", "2026-06-30")
    assert total["Total revenue"].value == Decimal("119796000000")
    assert total["Total revenue"].concept == "us-gaap:Revenues"


def test_cross_tabs_are_skipped_not_double_counted() -> None:
    instance, labels = load(GOOG)
    parsed = segment_revenue(instance, labels)
    # Alphabet tags products only inside its segments (product x segment): all skipped.
    assert parsed.skipped_multi > 0
    assert not [r for r in parsed.rows if r.kind == "product"]
    # Duplicate facts (the same figure tagged in two tables) are kept once.
    keys = [(r.kind, r.member, r.start, r.end, r.unit) for r in parsed.rows]
    assert len(keys) == len(set(keys))


def test_novo_nordisk_subtotal_region_is_flagged_and_segments_deduplicated() -> None:
    geo = rows_for(NVO, "geographic", "2025-01-01", "2025-12-31")
    assert geo["International Operations"].subtotal is True
    assert not geo["USA"].subtotal and geo["USA"].value == Decimal("173166000000")
    counted = sum(r.value for r in geo.values() if not r.subtotal)
    assert counted == Decimal("309064000000")  # = total revenue
    segments = rows_for(NVO, "segment", "2025-01-01", "2025-12-31")
    # Tagged alone and with the operating-segments member: one row each.
    assert {k: v.value for k, v in segments.items()} == {
        "Obesity and Diabetes care": Decimal("289456000000"),
        "Rare disease": Decimal("19608000000"),
    }
    assert all(r.unit == "DKK" and r.concept == "ifrs-full:Revenue" for r in segments.values())


def test_asml_products_flag_the_systems_subtotal_and_countries_keep_filing_labels() -> None:
    products = rows_for(ASML, "product", "2025-01-01", "2025-12-31")
    assert products["Product"].subtotal is True
    assert products["NXE"].value == Decimal("10445800000")
    geo = rows_for(ASML, "geographic", "2025-01-01", "2025-12-31")
    assert geo["China"].value == Decimal("9519700000")
    assert geo["Taiwan"].value == Decimal("8337900000")
    assert geo["EMEA"].member == "asml:RestOfEuropeAndMiddleEastMember"
    # Two totals at different precision: the more precise one wins.
    total = rows_for(ASML, "total", "2025-01-01", "2025-12-31")
    assert total["Total revenue"].value == Decimal("32667300000")


def test_share_classes_from_the_instance() -> None:
    instance, labels = load(GOOG)
    shares = instance_shares(instance, labels)
    assert shares is not None and shares.as_of == dt.date(2026, 7, 15)
    assert {c.member: c.shares for c in shares.classes} == {
        "us-gaap:CommonClassAMember": Decimal("5868000000"),
        "us-gaap:CommonClassBMember": Decimal("835000000"),
        "goog:CapitalClassCMember": Decimal("5527000000"),
    }
    nvo, nvo_labels = load(NVO)
    nvo_shares = instance_shares(nvo, nvo_labels)
    assert nvo_shares is not None
    assert {c.label for c in nvo_shares.classes} == {"A Share Capital", "B Share Capital"}
    assert nvo.texts("dei:Security12bTitle")[-1].startswith("American Depositary Receipts")


def test_labels_and_derived_names() -> None:
    _, labels = load(GOOG)
    assert labels.get("us-gaap:AllOtherSegmentsMember") == "Other Bets"
    assert labels.get("us-gaap:AllOtherSegmentsMember", standard=True) == "Other Operating Segment"
    assert derived_label("country:TW") == "Taiwan"
    assert derived_label("asml:RestOfEuropeAndMiddleEastMember") == "Rest Of Europe And Middle East"


def test_a_document_with_a_dtd_is_refused() -> None:
    evil = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><xbrl>&a;</xbrl>'
    with pytest.raises(XbrlError):
        parse_instance(evil)
    with pytest.raises(XbrlError):
        parse_labels(evil)


def test_ads_ratio_sentences() -> None:
    tsm = (
        "calculated by including both common shares and shares held in the form of ADSs "
        "(each ADS represents five (5) common shares) held by such individuals"
    )
    assert ads_ratio(tsm) is not None and ads_ratio(tsm)[0] == Decimal(5)  # type: ignore[index]
    nvo = "American Depositary Receipts, each representing one B Share"
    assert ads_ratio(nvo)[0] == Decimal(1)  # type: ignore[index]
    assert ads_ratio("American Depositary Shares representing such Common Shares") is None
    assert ads_ratio("Ordinary Shares") is None


# ------------------------------------------------------------------------ filings, chain


def _filing(form: str, accession: str, filed: str, primary: str = "x-20251231.htm") -> Filing:
    return Filing(
        cik="0000000001",
        form=form,
        accession=accession,
        filing_date=dt.date.fromisoformat(filed),
        report_date=None,
        primary_document=primary,
        items="",
    )


def test_latest_annual_and_newer_quarter() -> None:
    k = _filing("10-K", "0000000001-26-000001", "2026-02-01")
    q_old = _filing("10-Q", "0000000001-25-000009", "2025-10-30")
    q_new = _filing("10-Q", "0000000001-26-000020", "2026-07-25")
    assert latest_periodic([q_old, k, q_new]) == (k, q_new)
    assert latest_periodic([q_old, k]) == (k, None)
    f = _filing("10-Q", "0001652044-26-000071", "2026-07-25", "goog-20260630.htm")
    files = ["goog-20260630.htm", "goog-20260630_htm.xml", "goog-20260630_lab.xml", "R1.htm"]
    assert xbrl_documents(f, files) == ("goog-20260630_htm.xml", "goog-20260630_lab.xml")


def _cf(cik: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"companyfacts_{cik}.json").read_text())
    return data


def test_chain_uses_current_companyfacts_cover_figure() -> None:
    latest = _filing("10-K", "0000950170-26-000001", "2026-07-30")
    figure = shares_chain(
        cik="0000789019",
        company_facts=_cf("0000789019"),
        latest=None,
        instance=None,
        instance_shares=None,
        instance_url=None,
        basis=basis_for(HoldingProfile(ticker="MSFT_US_EQ"), "0000789019"),
        companyfacts_url="cf",
    )
    assert figure is not None and figure.method == "dei_companyfacts"
    assert figure.shares == Decimal("7425545491") and figure.as_of == dt.date(2026, 7, 23)
    assert figure.raw["fact"]["accn"] == figure.accession
    # A later filing companyfacts has not caught up with, and no instance: still used.
    stale = shares_chain(
        cik="0000789019",
        company_facts=_cf("0000789019"),
        latest=_filing("10-Q", "0000950170-26-999999", "2026-10-30"),
        instance=None,
        instance_shares=None,
        instance_url=None,
        basis=basis_for(HoldingProfile(ticker="MSFT_US_EQ"), "0000789019"),
        companyfacts_url="cf",
    )
    assert stale is not None and stale.method == "dei_companyfacts" and stale.notes
    del latest


def test_chain_sums_share_classes_from_the_instance_when_companyfacts_has_none() -> None:
    instance, labels = load(GOOG)
    latest = _filing("10-Q", "0001652044-26-000071", "2026-07-25", "goog-20260630.htm")
    args: dict[str, Any] = {
        "cik": "0001652044",
        "company_facts": _cf("0001652044"),
        "latest": latest,
        "instance": instance,
        "instance_shares": instance_shares(instance, labels),
        "instance_url": "https://sec.test/goog-20260630_htm.xml",
        "companyfacts_url": "cf",
    }
    all_classes = shares_chain(**args, basis=basis_for(HoldingProfile(ticker="GOOGL_US_EQ"), None))
    assert all_classes is not None and all_classes.method == "dei_instance_classes"
    assert all_classes.shares == Decimal("12230000000")
    assert all(c["counted"] for c in all_classes.classes)
    class_a = HoldingProfile(
        ticker="GOOGL_US_EQ", share_basis={"share_classes": ["us-gaap:CommonClassAMember"]}
    )
    only_a = shares_chain(**args, basis=basis_for(class_a, "0001652044"))
    assert only_a is not None and only_a.shares == Decimal("5868000000")
    assert [c["member"] for c in only_a.classes if c["counted"]] == ["us-gaap:CommonClassAMember"]
    # Without the instance the balance-sheet figure from companyfacts is the fallback.
    fallback = shares_chain(
        **{**args, "instance": None, "instance_shares": None},
        basis=basis_for(HoldingProfile(ticker="GOOGL_US_EQ"), None),
    )
    assert fallback is not None and fallback.method == "us_gaap_common_stock"
    assert fallback.shares == Decimal("12230000000")


def test_chain_ifrs_fallback_and_manual_figure() -> None:
    figure = shares_chain(
        cik="0000353278",
        company_facts=_cf("0000353278"),
        latest=None,
        instance=None,
        instance_shares=None,
        instance_url=None,
        basis=basis_for(HoldingProfile(ticker="NOVOb_EQ"), "0000353278"),
        companyfacts_url="cf",
    )
    assert figure is not None and figure.method == "ifrs_number_of_shares"
    assert figure.shares == Decimal("4444000000")
    cez = HoldingProfile(
        ticker="CEZ_EQ",
        share_basis={
            "manual_shares_outstanding": 537989759,
            "manual_as_of": "2025-12-31",
            "manual_source": "CEZ shareholder structure",
        },
    )
    manual = manual_figure(cez)
    assert manual is not None and manual.method == "manual" and manual.shares == 537989759


def test_ads_ratio_defaults_and_profile_override() -> None:
    tsm = basis_for(HoldingProfile(ticker="TSM_US_EQ"), "0001046179")
    assert tsm.ads_ratio == 5 and tsm.ads_ratio_source == "default"
    assert basis_for(HoldingProfile(ticker="SAPd_EQ"), "0001000184").ads_ratio == 1
    custom = HoldingProfile(ticker="TSM_US_EQ", share_basis={"ads_ratio": 4})
    assert basis_for(custom, "0001046179").ads_ratio == 4


# --------------------------------------------------------------------------- read model


def _row(kind: str, label: str, value: int, start: str, end: str, **extra: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "member": extra.get("member", f"x:{label.replace(' ', '')}Member"),
        "label": label,
        "label_source": "filing",
        "concept": "us-gaap:Revenues",
        "period_start": dt.date.fromisoformat(start),
        "period_end": dt.date.fromisoformat(end),
        "value": Decimal(value),
        "unit": extra.get("unit", "USD"),
        "subtotal": extra.get("subtotal", False),
        "form": extra.get("form", "10-K"),
        "accession": extra.get("accession", "0000000001-26-000001"),
        "filed": dt.date.fromisoformat(extra.get("filed", "2026-02-01")),
        "instance_url": "https://www.sec.gov/Archives/edgar/data/1/000000000126000001/x_htm.xml",
        "label_url": None,
    }


def test_segments_pick_the_latest_period_of_the_basis_with_history() -> None:
    rows = [
        _row("geographic", "US", 60, "2024-01-01", "2024-12-31"),
        _row("geographic", "EU", 40, "2024-01-01", "2024-12-31"),
        _row("geographic", "US", 70, "2025-01-01", "2025-12-31"),
        _row("geographic", "EU", 30, "2025-01-01", "2025-12-31"),
        _row("geographic", "All", 100, "2025-01-01", "2025-12-31", subtotal=True),
        _row("total", "Total revenue", 101, "2025-01-01", "2025-12-31"),
        _row("geographic", "US", 20, "2026-04-01", "2026-06-30", form="10-Q"),
        _row("geographic", "US", 50, "2026-01-01", "2026-06-30", form="10-Q"),
    ]
    annual = build_segments("X", rows, None, manual=False)
    assert annual.basis == "annual" and annual.available_bases == ["annual", "quarter"]
    assert annual.period is not None and annual.period.label == "FY2025"
    assert [(r.label, r.share) for r in annual.geographic] == [
        ("US", 0.7),
        ("EU", 0.3),
        ("All", None),
    ]
    assert annual.total_revenue == 101 and annual.currency == "USD"
    assert [p.period.label for p in annual.history["geographic"]] == ["FY2024", "FY2025"]
    assert annual.source is not None and annual.source.kind == "sec_xbrl"
    quarter = build_segments("X", rows, "quarter", manual=False)
    assert quarter.period is not None and quarter.period.label == "3M to 30 Jun 2026"
    assert [r.value for r in quarter.geographic] == [20]
    assert period_of(dt.date(2026, 1, 1), dt.date(2026, 6, 30)).basis is None


# ---------------------------------------------------------------------- job and endpoints

GOOG_CIK = "0001652044"
NVO_CIK = "0000353278"
GOOG_Q = "0001652044-26-000071"
NVO_20F = "0000353278-26-000012"


def _submissions(cik: str) -> dict[str, Any]:
    filings = {
        GOOG_CIK: [
            ("10-Q", GOOG_Q, "2026-07-24", "2026-06-30", "goog-20260630.htm"),
            ("8-K", "0001652044-26-000070", "2026-07-23", "", "ex.htm"),
        ],
        NVO_CIK: [("20-F", NVO_20F, "2026-02-04", "2025-12-31", "nvo-20251231.htm")],
    }[cik]
    return {
        "cik": cik,
        "filings": {
            "recent": {
                "form": [f[0] for f in filings],
                "accessionNumber": [f[1] for f in filings],
                "filingDate": [f[2] for f in filings],
                "reportDate": [f[3] for f in filings],
                "primaryDocument": [f[4] for f in filings],
                "items": ["" for _ in filings],
            }
        },
    }


class Edgar:
    """A mocked EDGAR serving the fixtures; counts instance downloads."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.downloads: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if self.fail:
            return httpx.Response(503)
        for cik in (GOOG_CIK, NVO_CIK):
            if url.endswith(f"submissions/CIK{cik}.json"):
                return httpx.Response(200, json=_submissions(cik))
            if url.endswith(f"companyfacts/CIK{cik}.json"):
                return httpx.Response(200, json=_cf(cik))
        folders = {
            GOOG_Q.replace("-", ""): GOOG,
            NVO_20F.replace("-", ""): NVO,
        }
        for folder, name in folders.items():
            if folder in url:
                file = url.rsplit("/", 1)[1]
                if file == "index.json":
                    items = [{"name": f"{name}.htm"}, {"name": f"{name}_htm.xml"}]
                    items.append({"name": f"{name}_lab.xml"})
                    return httpx.Response(200, json={"directory": {"item": items}})
                if file.endswith(".xml"):
                    self.downloads.append(file)
                    return httpx.Response(200, content=(FIXTURES / file).read_bytes())
        return httpx.Response(404)


async def _seed(pool: asyncpg.Pool, extra_profiles: list[dict[str, Any]] | None = None) -> None:
    await pool.execute(
        "insert into instrument (t212_ticker, isin, name, currency, cik, security_type) values"
        " ('GOOGL_US_EQ', 'US02079K3059', 'Alphabet', 'USD', $1, 'Common Stock'),"
        " ('NOVOb_EQ', 'DK0062498333', 'Novo Nordisk', 'DKK', null, 'Common Stock'),"
        " ('CEZ_EQ', 'CZ0005112300', 'CEZ', 'CZK', null, 'Common Stock'),"
        " ('VWCEd_EQ', 'IE00BK5BQT80', 'Vanguard FTSE All-World', 'EUR', null, 'ETP')",
        GOOG_CIK,
    )
    profiles = [
        {"ticker": "NOVOb_EQ", "sec_cik": "353278"},
        {
            "ticker": "CEZ_EQ",
            "share_basis": {
                "manual_shares_outstanding": 537989759,
                "manual_as_of": "2025-12-31",
                "manual_source": "CEZ shareholder structure page",
            },
            "manual_segments": [
                {
                    "label": "Czech Republic",
                    "axis": "geographic",
                    "value": 250000000000,
                    "currency": "CZK",
                    "period_start": "2025-01-01",
                    "period_end": "2025-12-31",
                    "source": "Annual Report 2025, segment note",
                },
                {
                    "label": "Other countries",
                    "axis": "geographic",
                    "value": 50000000000,
                    "currency": "CZK",
                    "period_start": "2025-01-01",
                    "period_end": "2025-12-31",
                    "source": "Annual Report 2025, segment note",
                },
            ],
        },
        *(extra_profiles or []),
    ]
    async with pool.acquire() as conn:
        await save_config(
            conn,
            "holding_profiles",
            HoldingProfilesConfig.model_validate({"profiles": profiles}),
            created_by=None,
        )


async def _run(pool: asyncpg.Pool, edgar: Edgar, tickers: list[str]) -> str:
    async with httpx.AsyncClient(transport=httpx.MockTransport(edgar)) as http:
        client = EdgarClient(http, "StewardAI test test@example.com")
        results = []
        for ticker in tickers:

            async def handler(ctx: JobContext) -> None:
                await run_sec_xbrl(ctx, client)

            results.append(
                await run_job(
                    pool, "sec_xbrl", handler, trigger="manual", params={"ticker": ticker}
                )
            )
    return "succeeded" if all(r == "succeeded" for r in results) else "failed"


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


@pytest.mark.db
async def test_job_stores_segments_and_shares_and_skips_parsed_filings(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    await _seed(backend_pool)
    edgar = Edgar()
    assert await _run(backend_pool, edgar, ["GOOGL_US_EQ", "NOVOb_EQ"]) == "succeeded"
    assert sorted(edgar.downloads) == sorted(
        [f"{GOOG}_htm.xml", f"{GOOG}_lab.xml", f"{NVO}_htm.xml", f"{NVO}_lab.xml"]
    )
    filing = await backend_pool.fetchrow("select * from xbrl_filing where ticker = 'GOOGL_US_EQ'")
    assert filing["accession"] == GOOG_Q and filing["form"] == "10-Q"
    assert filing["instance_url"].endswith("/000165204426000071/goog-20260630_htm.xml")

    shares = await backend_pool.fetchrow(
        "select * from shares_outstanding where ticker = 'GOOGL_US_EQ'"
    )
    assert shares["method"] == "dei_instance_classes" and shares["shares"] == 12230000000
    assert shares["accession"] == GOOG_Q and shares["as_of"] == dt.date(2026, 7, 15)
    raw = json.loads(shares["raw"])
    assert raw["source"] == "instance" and len(raw["facts"]) == 3
    assert "EntityCommonStockSharesOutstanding" in raw["facts"][0]["xml"]

    nvo = await backend_pool.fetchrow("select * from shares_outstanding where ticker = 'NOVOb_EQ'")
    # companyfacts has no cover figure for NVO: the A + B dei facts of the 20-F.
    assert nvo["method"] == "dei_instance_classes" and nvo["shares"] == 4465000000
    # The ADS ratio from the cover page ("each representing one B Share").
    assert nvo["ads_ratio_reported"] == 1 and NVO_20F in nvo["ads_ratio_text"]

    # A second run: nothing new is downloaded except the class facts' instance.
    edgar.downloads.clear()
    assert await _run(backend_pool, edgar, ["NOVOb_EQ"]) == "succeeded"
    assert f"{NVO}_htm.xml" in edgar.downloads

    response = await api.get("/holdings/GOOGL_US_EQ/segments")
    assert response.status_code == 200
    body = response.json()
    assert body["basis"] == "quarter" and body["period"]["end"] == "2026-06-30"
    assert body["period"]["months"] == 3
    assert [r["label"] for r in body["geographic"]] == [
        "United States",
        "EMEA",
        "APAC",
        "Other Americas",
    ]
    assert body["source"]["kind"] == "sec_xbrl" and body["source"]["accession"] == GOOG_Q
    assert body["source"]["filing_url"].endswith("/000165204426000071/")
    assert body["product"] == []
    assert [p["period"]["end"] for p in body["history"]["segment"]] == [
        "2025-06-30",
        "2026-06-30",
    ]

    nvo_segments = (await api.get("/holdings/NOVOb_EQ/segments")).json()
    assert nvo_segments["basis"] == "annual" and nvo_segments["currency"] == "DKK"
    intl = next(r for r in nvo_segments["geographic"] if r["label"] == "International Operations")
    assert intl["subtotal"] is True and intl["share"] is None


@pytest.mark.db
async def test_manual_rows_and_failures_degrade_gracefully(
    backend_pool: asyncpg.Pool, api: httpx.AsyncClient
) -> None:
    await _seed(backend_pool)
    edgar = Edgar(fail=True)
    # EDGAR down: the holding's step records the error, the run still succeeds.
    assert await _run(backend_pool, edgar, ["GOOGL_US_EQ"]) == "succeeded"
    step = await backend_pool.fetchrow(
        "select detail from job_step where name = 'xbrl:GOOGL_US_EQ' order by id desc limit 1"
    )
    assert "HTTP 503" in json.loads(step["detail"])["error"]
    # CEZ: no CIK; the manual figure is stored and the manual segments are served.
    assert await _run(backend_pool, Edgar(), ["CEZ_EQ", "VWCEd_EQ"]) == "succeeded"
    cez = await backend_pool.fetchrow("select * from shares_outstanding where ticker = 'CEZ_EQ'")
    assert cez["method"] == "manual" and cez["shares"] == 537989759
    body = (await api.get("/holdings/CEZ_EQ/segments")).json()
    assert body["source"]["kind"] == "manual" and body["source"]["tier"] == "entered manually"
    assert "Annual Report 2025" in body["source"]["note"]
    assert [(r["label"], r["share"]) for r in body["geographic"]] == [
        ("Czech Republic", 250 / 300),
        ("Other countries", 50 / 300),
    ]
    empty = await holding_segments(backend_pool, "VWCEd_EQ")
    assert empty.period is None and empty.notes


@pytest.mark.db
async def test_ownership_percent_of_shares_outstanding_and_not_applicable(
    backend_pool: asyncpg.Pool,
) -> None:
    await _seed(
        backend_pool,
        [{"ticker": "TSM_US_EQ", "sec_cik": "1046179"}],
    )
    await backend_pool.execute(
        "insert into instrument (t212_ticker, isin, name, currency, cik) values"
        " ('TSM_US_EQ', 'US8740391003', 'TSMC ADR', 'USD', '0001046179')"
    )
    await backend_pool.execute("insert into form13f_dataset (url) values ('u')")
    await backend_pool.execute(
        "insert into form13f_filing (accession, cik, manager_name, period, filed,"
        " submission_type, dataset_url) values"
        " ('0000000001-26-000001', '0000000001', 'Big Fund', '2026-06-30', '2026-08-10',"
        " '13F-HR', 'u'),"
        " ('0000000002-26-000001', '0000000002', 'Small Fund', '2026-06-30', '2026-08-12',"
        " '13F-HR', 'u')"
    )
    await backend_pool.execute(
        "insert into institutional_holding (accession, infotable_sk, cusip, ticker, value_usd,"
        " shares, shares_type) values"
        " ('0000000001-26-000001', 1, '874039100', 'TSM_US_EQ', 1, 500000000, 'SH'),"
        " ('0000000002-26-000001', 1, '874039100', 'TSM_US_EQ', 1, 100000000, 'SH')"
    )
    await backend_pool.execute(
        "insert into shares_outstanding (ticker, cik, shares, as_of, method, accession, form,"
        " filed, raw) values ('TSM_US_EQ', '0001046179', 25932524521, '2025-12-31',"
        " 'dei_companyfacts', '0001628280-26-025362', '20-F', '2026-04-16', '{}')"
    )
    tsm = await holding_ownership(backend_pool, "TSM_US_EQ")
    assert tsm.applicable and tsm.shares_outstanding is not None
    assert tsm.shares_outstanding.ads_ratio == 5 and tsm.shares_outstanding.foreign_issuer
    expected = 600000000 * 5 / 25932524521
    assert tsm.pct_of_shares_outstanding == pytest.approx(expected)
    assert tsm.periods[0].pct_of_shares_outstanding == pytest.approx(expected)
    assert tsm.top_holders[0].pct_of_shares_outstanding == pytest.approx(
        500000000 * 5 / 25932524521
    )
    assert tsm.basis_label == ADR_BASIS

    cez = await holding_ownership(backend_pool, "CEZ_EQ")
    assert not cez.applicable and "outside the US (CZ)" in (cez.not_applicable_reason or "")
    etf = await holding_ownership(backend_pool, "VWCEd_EQ")
    assert not etf.applicable and "ETF" in (etf.not_applicable_reason or "")
    # A US company with no 13F data loaded yet is not "not applicable".
    googl = await holding_ownership(backend_pool, "GOOGL_US_EQ")
    assert googl.applicable and googl.pct_of_shares_outstanding is None
    assert US_BASIS.startswith("13F")
